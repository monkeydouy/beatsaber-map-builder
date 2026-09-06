"""YouTube acquisition adapter.

Security posture: the user's URL is *parsed*, never *forwarded*. We extract an
11-character video id, validate the host against an allowlist, and then rebuild
a canonical `https://www.youtube.com/watch?v=<id>` URL. Nothing the user typed
reaches yt-dlp, which removes the whole class of SSRF / argument-injection
problems. yt-dlp itself is always invoked with an argument list, never a shell.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import get_settings
from app.models.enums import SourceType
from app.services.media.base import AudioSource, MediaAcquisitionError, NormalizedMedia

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)

VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

_PATH_PREFIXES = ("/embed/", "/shorts/", "/v/", "/live/")


class YouTubeUnavailableError(MediaAcquisitionError):
    """The video exists but cannot be processed (private, live, too long...)."""


def extract_video_id(raw_url: str) -> str:
    """Validate a user URL and return just the video id.

    Raises `MediaAcquisitionError` for anything that is not a plain YouTube
    video link — including IP literals, non-HTTP schemes and private hosts.
    """
    candidate = (raw_url or "").strip()
    if not candidate:
        raise MediaAcquisitionError("Please enter a YouTube URL.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise MediaAcquisitionError("That does not look like a valid YouTube URL.") from exc

    if parsed.scheme not in {"http", "https"}:
        raise MediaAcquisitionError("Only YouTube web links are supported.")

    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS:
        raise MediaAcquisitionError(
            "Only youtube.com and youtu.be links are supported.",
            detail=f"rejected host {host!r}",
        )

    video_id: str | None = None
    if host.endswith("youtu.be"):
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
    else:
        query = parse_qs(parsed.query)
        if query.get("v"):
            video_id = query["v"][0]
        else:
            for prefix in _PATH_PREFIXES:
                if parsed.path.startswith(prefix):
                    video_id = parsed.path[len(prefix) :].split("/", 1)[0]
                    break

    if not video_id or not VIDEO_ID_PATTERN.match(video_id):
        raise MediaAcquisitionError(
            "That URL does not point at a single YouTube video.",
            detail="no valid video id in URL",
        )
    return video_id


def canonical_url(video_id: str) -> str:
    """Rebuild a safe URL. Only ever called with an id we validated ourselves."""
    if not VIDEO_ID_PATTERN.match(video_id):  # defensive: never trust a caller
        raise MediaAcquisitionError("Invalid video reference.")
    return f"https://www.youtube.com/watch?v={video_id}"


class YouTubeSource(AudioSource):
    """Fetches a single YouTube video's audio track into job storage."""

    def __init__(self, raw_url: str, destination_dir: Path, *, max_duration: float) -> None:
        self._video_id = extract_video_id(raw_url)
        self._destination = destination_dir
        self._max_duration = max_duration
        self._metadata: dict[str, object] | None = None

    @property
    def video_id(self) -> str:
        return self._video_id

    @property
    def url(self) -> str:
        return canonical_url(self._video_id)

    async def fetch_metadata(self) -> dict[str, object]:
        """Read video metadata without downloading the media."""
        from app.services.media.probe import run_binary

        settings = get_settings()
        if not settings.enable_youtube:
            raise MediaAcquisitionError("YouTube input is disabled on this server.")

        argv = [
            settings.ytdlp_binary,
            "--no-playlist",
            "--skip-download",
            "--no-warnings",
            "--dump-single-json",
            "--socket-timeout",
            "20",
            "--",
            self.url,
        ]
        code, stdout, stderr = await run_binary(
            argv,
            timeout=settings.youtube_metadata_timeout_seconds,
            friendly_error="Could not reach YouTube. Please try again.",
        )
        if code != 0:
            raise self._classify_error(stderr.decode(errors="replace"))

        try:
            info = json.loads(stdout or b"{}")
        except json.JSONDecodeError as exc:
            raise MediaAcquisitionError(
                "Could not read that video's details.", detail="yt-dlp json unparsable"
            ) from exc

        self._validate_info(info)
        self._metadata = {
            "video_id": self._video_id,
            "title": str(info.get("title") or "Untitled"),
            "artist": str(info.get("artist") or info.get("uploader") or "Unknown Artist"),
            "channel": str(info.get("channel") or info.get("uploader") or ""),
            "duration": float(info.get("duration") or 0.0),
            "thumbnail": info.get("thumbnail"),
            "url": self.url,
        }
        return self._metadata

    def _validate_info(self, info: dict[str, object]) -> None:
        if info.get("_type") == "playlist" or "entries" in info:
            raise MediaAcquisitionError(
                "Please link a single video rather than a playlist.",
                detail="playlist payload returned",
            )
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise YouTubeUnavailableError("Live streams are not supported.")
        if info.get("availability") in {"private", "premium_only", "needs_auth", "subscriber_only"}:
            raise YouTubeUnavailableError("That video is not publicly available.")

        duration = float(info.get("duration") or 0.0)
        if duration <= 0:
            raise YouTubeUnavailableError("Could not determine the video's length.")
        if duration > self._max_duration:
            minutes = int(self._max_duration // 60)
            raise YouTubeUnavailableError(f"Videos longer than {minutes} minutes are not supported.")

    @staticmethod
    def _classify_error(stderr: str) -> MediaAcquisitionError:
        """Map yt-dlp stderr onto a user-safe message. stderr is never shown."""
        lowered = stderr.lower()
        logger.warning("ytdlp_failed", extra={"stderr": stderr[-500:]})
        if "private video" in lowered:
            return YouTubeUnavailableError("That video is private.")
        if "members-only" in lowered or "join this channel" in lowered:
            return YouTubeUnavailableError("That video is members-only.")
        if "is not available" in lowered or "unavailable" in lowered or "removed" in lowered:
            return YouTubeUnavailableError("That video is unavailable.")
        if "live event" in lowered or "is live" in lowered:
            return YouTubeUnavailableError("Live streams are not supported.")
        if "age" in lowered and "confirm" in lowered:
            return YouTubeUnavailableError("That video is age-restricted and cannot be processed.")
        if "sign in" in lowered or "cookies" in lowered or "bot" in lowered:
            return YouTubeUnavailableError(
                "YouTube blocked this request. Try uploading the MP3 instead."
            )
        return MediaAcquisitionError("Could not download audio from that video.")

    async def acquire(self) -> NormalizedMedia:
        from app.services.media.probe import run_binary

        settings = get_settings()
        metadata = self._metadata or await self.fetch_metadata()

        self._destination.mkdir(parents=True, exist_ok=True)
        # Fixed output template: yt-dlp never chooses a path from remote data.
        output_template = str(self._destination / "source.%(ext)s")
        argv = [
            settings.ytdlp_binary,
            "--no-playlist",
            "--no-warnings",
            "--no-continue",
            "--no-part",
            "--restrict-filenames",
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--format",
            "bestaudio/best",
            "--socket-timeout",
            "20",
            "--retries",
            "3",
            "--max-filesize",
            f"{settings.max_upload_mb * 4}M",
            "--output",
            output_template,
            "--",
            self.url,
        ]
        code, _stdout, stderr = await run_binary(
            argv,
            timeout=settings.youtube_download_timeout_seconds,
            friendly_error="Downloading that video took too long.",
        )
        if code != 0:
            raise self._classify_error(stderr.decode(errors="replace"))

        downloaded = self._destination / "source.mp3"
        if not downloaded.exists():
            candidates = sorted(self._destination.glob("source.*"))
            if not candidates:
                raise MediaAcquisitionError("Could not extract audio from that video.")
            downloaded = candidates[0]

        return NormalizedMedia(
            source_type=SourceType.YOUTUBE,
            source_path=downloaded,
            title=str(metadata.get("title") or "Untitled"),
            artist=str(metadata.get("artist") or "Unknown Artist"),
            duration=float(metadata.get("duration") or 0.0),
            thumbnail=metadata.get("thumbnail"),  # type: ignore[arg-type]
            original_url=self.url,
        )
