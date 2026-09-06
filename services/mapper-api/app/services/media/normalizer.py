"""Convert any acquired media into the two canonical audio artefacts.

* ``analysis.wav`` — 44.1 kHz mono PCM, used for DSP.
* ``song.ogg``     — 44.1 kHz stereo Vorbis, shipped inside the map package.

Loudness handling is deliberately conservative: we measure peak level with
ffmpeg's ``volumedetect`` and then apply a *pure gain*. A constant gain cannot
introduce latency, resampling artefacts or clipping, so timing integrity — the
one thing a rhythm game cannot tolerate losing — is preserved exactly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.media.base import MediaAcquisitionError
from app.services.media.probe import run_binary

logger = logging.getLogger(__name__)

ANALYSIS_FILENAME = "analysis.wav"
SONG_FILENAME = "song.ogg"

#: Leave this much headroom below full scale after gain is applied.
TARGET_PEAK_DBFS = -1.0
#: Never boost by more than this; avoids pumping up near-silent recordings.
MAX_GAIN_DB = 6.0

_MAX_VOLUME = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


@dataclass(slots=True)
class NormalizationResult:
    analysis_path: Path
    song_path: Path
    gain_db: float
    sample_rate: int


async def _measure_gain_db(source: Path) -> float:
    """Return the gain that brings the file's peak to `TARGET_PEAK_DBFS`."""
    settings = get_settings()
    argv = [
        settings.ffmpeg_binary,
        "-nostdin",
        "-hide_banner",
        "-i",
        str(source),
        "-vn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    code, _stdout, stderr = await run_binary(
        argv,
        timeout=settings.ffmpeg_timeout_seconds,
        friendly_error="Could not analyse the audio levels.",
    )
    text = stderr.decode(errors="replace")
    if code != 0:
        logger.warning("volumedetect_failed", extra={"stderr": text[-400:]})
        return 0.0

    peak_match = _MAX_VOLUME.search(text)
    mean_match = _MEAN_VOLUME.search(text)
    if not peak_match:
        return 0.0

    peak_db = float(peak_match.group(1))
    gain = TARGET_PEAK_DBFS - peak_db
    # Only ever quieten loud material a little; boost quiet material modestly.
    gain = max(min(gain, MAX_GAIN_DB), -12.0)

    if mean_match and float(mean_match.group(1)) > -8.0:
        # Already heavily limited master: don't push it any further.
        gain = min(gain, 0.0)
    return round(gain, 2)


async def normalize_media(source: Path, destination_dir: Path) -> NormalizationResult:
    """Produce `analysis.wav` and `song.ogg` from an arbitrary media file."""
    settings = get_settings()
    destination_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = destination_dir / ANALYSIS_FILENAME
    song_path = destination_dir / SONG_FILENAME
    sample_rate = settings.analysis_sample_rate

    gain_db = await _measure_gain_db(source)
    gain_filter = f"volume={gain_db}dB"

    analysis_argv = [
        settings.ffmpeg_binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-map_metadata",
        "-1",
        "-af",
        gain_filter,
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-acodec",
        "pcm_s16le",
        str(analysis_path),
    ]
    code, _out, err = await run_binary(
        analysis_argv,
        timeout=settings.ffmpeg_timeout_seconds,
        friendly_error="Audio conversion failed.",
    )
    if code != 0 or not analysis_path.exists():
        logger.error("ffmpeg_analysis_failed", extra={"stderr": err.decode(errors="replace")[-400:]})
        raise MediaAcquisitionError("Audio conversion failed.", detail="ffmpeg analysis pass")

    song_argv = [
        settings.ffmpeg_binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-map_metadata",
        "-1",
        "-af",
        gain_filter,
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "libvorbis",
        "-qscale:a",
        "6",
        str(song_path),
    ]
    code, _out, err = await run_binary(
        song_argv,
        timeout=settings.ffmpeg_timeout_seconds,
        friendly_error="Audio conversion failed.",
    )
    if code != 0 or not song_path.exists():
        logger.error("ffmpeg_song_failed", extra={"stderr": err.decode(errors="replace")[-400:]})
        raise MediaAcquisitionError("Audio conversion failed.", detail="ffmpeg ogg pass")

    logger.info(
        "audio_normalized",
        extra={"gain_db": gain_db, "sample_rate": sample_rate},
    )
    return NormalizationResult(
        analysis_path=analysis_path,
        song_path=song_path,
        gain_db=gain_db,
        sample_rate=sample_rate,
    )
