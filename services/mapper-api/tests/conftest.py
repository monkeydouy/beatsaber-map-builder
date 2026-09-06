"""Shared fixtures.

The audio fixture is *generated*, not committed: a synthetic track with a known
BPM and a real arrangement is both reproducible and a fair target for tempo and
section detection, without putting 13 MB of audio in the repository.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
WAV_PATH = FIXTURE_DIR / "test_song.wav"
DRIFT_WAV_PATH = FIXTURE_DIR / "drift_song.wav"
WALTZ_WAV_PATH = FIXTURE_DIR / "waltz_song.wav"
SWING_WAV_PATH = FIXTURE_DIR / "swing_song.wav"
SIX_EIGHT_WAV_PATH = FIXTURE_DIR / "six_eight_song.wav"
DENSE_WAV_PATH = FIXTURE_DIR / "dense_song.wav"
CACHE_PATH = FIXTURE_DIR / "analysis_cache.json"

#: The BPM the fixture is synthesised at; tempo detection is checked against it.
FIXTURE_BPM = 128.0
FIXTURE_DURATION = 152.0


@pytest.fixture(scope="session")
def fixture_wav() -> Path:
    """Generate the synthetic test track once per session."""
    if not WAV_PATH.exists():
        subprocess.run(
            [sys.executable, str(FIXTURE_DIR / "make_fixture.py"), str(WAV_PATH)],
            check=True,
            capture_output=True,
        )
    return WAV_PATH


@pytest.fixture(scope="session")
def drifting_wav() -> Path:
    """A take that speeds up and settles again, the way a live one would.

    Ground truth: 128 BPM, up to 133 at bar 24, back to 126 at bar 56.
    """
    if not DRIFT_WAV_PATH.exists():
        subprocess.run(
            [
                sys.executable,
                str(FIXTURE_DIR / "make_fixture.py"),
                str(DRIFT_WAV_PATH),
                "--drift",
            ],
            check=True,
            capture_output=True,
        )
    return DRIFT_WAV_PATH


#: The tempo plan `drifting_wav` is built from, for tests to assert against.
DRIFT_TRUTH = [(0.0, 128.0), (96.0, 133.0), (224.0, 126.0)]


def _build_fixture(destination: Path, *flags: str) -> Path:
    if not destination.exists():
        subprocess.run(
            [sys.executable, str(FIXTURE_DIR / "make_fixture.py"), str(destination), *flags],
            check=True,
            capture_output=True,
        )
    return destination


@pytest.fixture(scope="session")
def waltz_wav() -> Path:
    """3/4. The meter that the old hardcoded 4 got confidently wrong."""
    return _build_fixture(WALTZ_WAV_PATH, "--meter=3")


@pytest.fixture(scope="session")
def swing_wav() -> Path:
    """4/4 whose offbeats sit two thirds through the beat, not halfway."""
    return _build_fixture(SWING_WAV_PATH, "--meter=swing")


@pytest.fixture(scope="session")
def six_eight_wav() -> Path:
    """Compound time, counted in eighths."""
    return _build_fixture(SIX_EIGHT_WAV_PATH, "--meter=6")


@pytest.fixture(scope="session")
def dense_wav() -> Path:
    """Sixteenth-note hats: dense enough that the difficulty profile, rather
    than the amount of music, is what caps note density."""
    return _build_fixture(DENSE_WAV_PATH, "--meter=dense")


@pytest.fixture(scope="session")
def waltz_raw(waltz_wav: Path):
    from app.services.analysis.audio_analyzer import analyze_audio

    return analyze_audio(waltz_wav)


@pytest.fixture(scope="session")
def swing_raw(swing_wav: Path):
    from app.services.analysis.audio_analyzer import analyze_audio

    return analyze_audio(swing_wav)


@pytest.fixture(scope="session")
def six_eight_raw(six_eight_wav: Path):
    from app.services.analysis.audio_analyzer import analyze_audio

    return analyze_audio(six_eight_wav)


@pytest.fixture(scope="session")
def drifting_raw(drifting_wav: Path):
    """Analyse the drifting take once and share it across the module."""
    from app.services.analysis.audio_analyzer import analyze_audio

    return analyze_audio(drifting_wav)


@pytest.fixture(scope="session")
def drifting_raw_constant(drifting_wav: Path):
    """The same take, forced onto a single tempo, for comparison."""
    from app.services.analysis.audio_analyzer import analyze_audio

    return analyze_audio(drifting_wav, allow_variable_tempo=False)


@pytest.fixture(scope="session")
def analysis(fixture_wav: Path):
    """Analyse the fixture once and reuse it across the whole test session.

    Analysis is the expensive part (~10 s); caching it on disk keeps repeated
    local runs fast while still exercising the real DSP at least once.
    """
    from app.models.musical import AnalysisResult

    if CACHE_PATH.exists():
        try:
            return AnalysisResult.from_dict(json.loads(CACHE_PATH.read_text()))
        except (json.JSONDecodeError, KeyError):
            CACHE_PATH.unlink(missing_ok=True)

    from app.services.analysis.audio_analyzer import analyze_audio
    from app.services.analysis.event_detector import build_events
    from app.services.analysis.section_analyzer import analyze_sections

    raw = analyze_audio(fixture_wav)
    sections = analyze_sections(raw, raw.grid)
    events = build_events(raw, raw.grid, sections)
    result = AnalysisResult(
        duration=raw.duration,
        sample_rate=raw.sample_rate,
        grid=raw.grid,
        beats=raw.beats.tolist(),
        beat_confidence=raw.beat_confidence.tolist(),
        downbeats=raw.downbeats.tolist(),
        onsets=raw.onsets.tolist(),
        onset_strengths=raw.onset_strengths.tolist(),
        sections=sections,
        events=events,
        features=raw.to_features(),
        tempo_candidates=[candidate.to_dict() for candidate in raw.tempo_candidates],
    )
    CACHE_PATH.write_text(json.dumps(result.to_dict()))
    return result


@pytest.fixture
def make_map(analysis):
    """Generate a beatmap from the shared analysis with sensible defaults."""
    from app.models.enums import Difficulty, MappingStyle
    from app.services.mapping.pipeline import GenerationOptions, generate_beatmap

    def _make(
        difficulty: Difficulty = Difficulty.EXPERT,
        style: MappingStyle = MappingStyle.BALANCED,
        intensity: float = 0.6,
        seed: int = 1234,
        **kwargs,
    ):
        return generate_beatmap(
            analysis,
            GenerationOptions(
                difficulty=difficulty,
                style=style,
                intensity=intensity,
                seed=seed,
                **kwargs,
            ),
        )

    return _make
