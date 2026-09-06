"""ffprobe/ffmpeg wrappers.

Every external binary is invoked with an argument list — never a shell string —
so user-supplied values can never be interpreted as commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.media.base import MediaAcquisitionError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProbeResult:
    duration: float
    sample_rate: int
    channels: int
    title: str | None
    artist: str | None
    codec: str | None


async def run_binary(
    argv: list[str],
    *,
    timeout: float,
    friendly_error: str,
) -> tuple[int, bytes, bytes]:
    """Run an external binary safely, with a hard timeout.

    Returns ``(returncode, stdout, stderr)``. Raises `MediaAcquisitionError`
    only for conditions the caller cannot recover from (missing binary,
    timeout); a non-zero exit is returned so callers can inspect stderr.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        logger.error("binary_missing", extra={"binary": argv[0]})
        raise MediaAcquisitionError(friendly_error, detail=f"missing binary {argv[0]}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        logger.error("binary_timeout", extra={"binary": argv[0], "timeout": timeout})
        raise MediaAcquisitionError(friendly_error, detail="external tool timed out") from exc

    return process.returncode or 0, stdout, stderr


async def probe_audio(path: Path) -> ProbeResult:
    """Read stream metadata with ffprobe and confirm an audio stream exists."""
    settings = get_settings()
    argv = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        str(path),
    ]
    code, stdout, stderr = await run_binary(
        argv,
        timeout=settings.ffmpeg_timeout_seconds,
        friendly_error="Could not read the audio file.",
    )
    if code != 0:
        logger.warning("ffprobe_failed", extra={"stderr": stderr.decode(errors="replace")[:500]})
        raise MediaAcquisitionError(
            "That file does not contain readable audio.", detail="ffprobe returned non-zero"
        )

    try:
        payload = json.loads(stdout or b"{}")
    except json.JSONDecodeError as exc:
        raise MediaAcquisitionError(
            "That file does not contain readable audio.", detail="ffprobe output unparsable"
        ) from exc

    audio_streams = [
        stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"
    ]
    if not audio_streams:
        raise MediaAcquisitionError(
            "That file does not contain readable audio.", detail="no audio stream"
        )

    stream = audio_streams[0]
    fmt = payload.get("format", {})
    tags = {key.lower(): value for key, value in (fmt.get("tags") or {}).items()}

    duration = 0.0
    for candidate in (fmt.get("duration"), stream.get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    return ProbeResult(
        duration=duration,
        sample_rate=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        title=(tags.get("title") or "").strip() or None,
        artist=(tags.get("artist") or tags.get("album_artist") or "").strip() or None,
        codec=stream.get("codec_name"),
    )
