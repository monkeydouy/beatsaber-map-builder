"""Synthesise a deterministic test track.

Real music cannot live in the repository, so tests run against a generated
song with a known BPM and a real structure (intro / verse / chorus / break /
drop / outro) built from actual drum-like transients. It is loud, percussive
and unambiguous, which makes it a fair target for tempo and section detection.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100
BPM = 128.0
BEAT = 60.0 / BPM


def _envelope(length: int, decay: float) -> np.ndarray:
    return np.exp(-np.linspace(0, decay, length))


def kick(duration: float = 0.18) -> np.ndarray:
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    sweep = 110.0 * np.exp(-t * 26.0) + 45.0
    return np.sin(2 * np.pi * np.cumsum(sweep) / SAMPLE_RATE) * _envelope(n, 9.0) * 0.95


def snare(duration: float = 0.16) -> np.ndarray:
    n = int(SAMPLE_RATE * duration)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 1, n)
    t = np.arange(n) / SAMPLE_RATE
    body = np.sin(2 * np.pi * 190.0 * t) * 0.4
    return (noise * 0.65 + body) * _envelope(n, 16.0) * 0.6


def hat(duration: float = 0.05) -> np.ndarray:
    n = int(SAMPLE_RATE * duration)
    rng = np.random.default_rng(11)
    return rng.normal(0, 1, n) * _envelope(n, 40.0) * 0.22


def bass(freq: float, duration: float) -> np.ndarray:
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    wave = np.sign(np.sin(2 * np.pi * freq * t)) * 0.28
    return wave * _envelope(n, 3.0)


def lead(freq: float, duration: float) -> np.ndarray:
    n = int(SAMPLE_RATE * duration)
    t = np.arange(n) / SAMPLE_RATE
    wave = (
        np.sin(2 * np.pi * freq * t) * 0.5
        + np.sin(2 * np.pi * freq * 2 * t) * 0.22
        + np.sin(2 * np.pi * freq * 3 * t) * 0.10
    )
    return wave * _envelope(n, 4.5) * 0.34


def add(track: np.ndarray, sample: np.ndarray, at: float, gain: float = 1.0) -> None:
    start = int(at * SAMPLE_RATE)
    end = min(start + len(sample), len(track))
    if start >= len(track) or end <= start:
        return
    track[start:end] += sample[: end - start] * gain


#: (name, bars, kick/snare/hat/bass/lead gains)
ARRANGEMENT = [
    ("intro", 8, 0.35, 0.0, 0.5, 0.3, 0.25),
    ("verse", 16, 0.85, 0.7, 0.8, 0.8, 0.45),
    ("buildup", 8, 0.95, 0.85, 1.0, 0.9, 0.7),
    ("drop", 16, 1.0, 1.0, 1.0, 1.0, 1.0),
    ("break", 8, 0.2, 0.15, 0.3, 0.25, 0.35),
    ("chorus", 16, 1.0, 0.95, 0.95, 1.0, 0.95),
    ("outro", 8, 0.4, 0.25, 0.4, 0.35, 0.2),
]

SCALE = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25]


#: Tempo plan for the drifting variant: (bar index where it starts, BPM).
#: Modelled on how a live take actually moves — it pushes into the loud
#: section and settles again — rather than a single arbitrary jump.
DRIFT_PLAN = [(0, 128.0), (24, 133.0), (56, 126.0)]


@dataclass(frozen=True)
class Meter:
    """How a bar is built, so tests have unambiguous ground truth.

    `kick_beats` and `snare_beats` are indices within the bar. The accent
    pattern they create is exactly what the meter detector has to recover.
    """

    name: str
    beats_per_bar: int
    compound: bool
    kick_beats: tuple[int, ...]
    snare_beats: tuple[int, ...]
    #: Beats that get an extra-loud kick — the primary accent of the bar.
    primary_beats: tuple[int, ...] = (0,)
    #: Where hi-hats fall, as fractions of a beat.
    hat_offsets: tuple[float, ...] = (0.0, 0.5)


METERS = {
    # Straight 4/4: kick on 1 and 3, backbeat on 2 and 4.
    "4": Meter("4/4", 4, False, kick_beats=(0, 2), snare_beats=(1, 3)),
    # Waltz: one kick on the downbeat, lighter beats on 2 and 3.
    "3": Meter("3/4", 3, False, kick_beats=(0,), snare_beats=(1, 2)),
    # Sixteenth-note hats: dense enough that the top difficulties are limited
    # by their profile rather than by how much is going on in the music.
    "dense": Meter(
        "4/4 dense",
        4,
        False,
        kick_beats=(0, 2),
        snare_beats=(1, 3),
        hat_offsets=(0.0, 0.25, 0.5, 0.75),
    ),
    # A 4/4 shuffle: same bar as "4", but the offbeats sit two thirds of the
    # way through the beat rather than halfway. Straight quantisation drags
    # every one of them early, which is what makes this worth detecting.
    "swing": Meter(
        "4/4 shuffle",
        4,
        False,
        kick_beats=(0, 2),
        snare_beats=(1, 3),
        hat_offsets=(0.0, 2.0 / 3.0),
    ),
    # Compound duple, counted in eighths: primary accent on 1, secondary on 4.
    "6": Meter(
        "6/8",
        6,
        True,
        kick_beats=(0, 3),
        snare_beats=(2, 5),
        primary_beats=(0,),
        hat_offsets=(0.0,),
    ),
}


def _bar_starts(drift: bool, beats_per_bar: int) -> tuple[list[float], list[float]]:
    """Wall-clock start time and beat length for every bar in the song."""
    total_bars = sum(bars for _name, bars, *_gains in ARRANGEMENT)
    starts: list[float] = []
    beat_lengths: list[float] = []
    now = 0.0
    for bar in range(total_bars):
        if drift:
            bpm = DRIFT_PLAN[0][1]
            for start_bar, plan_bpm in DRIFT_PLAN:
                if bar >= start_bar:
                    bpm = plan_bpm
        else:
            bpm = BPM
        beat_length = 60.0 / bpm
        starts.append(now)
        beat_lengths.append(beat_length)
        now += beat_length * beats_per_bar
    return starts, beat_lengths


def build(drift: bool = False, meter: Meter | None = None) -> np.ndarray:
    meter = meter or METERS["4"]
    bar_starts, beat_lengths = _bar_starts(drift, meter.beats_per_bar)
    duration = bar_starts[-1] + beat_lengths[-1] * meter.beats_per_bar + 2.0
    track = np.zeros(int(duration * SAMPLE_RATE))

    rng = np.random.default_rng(42)
    bar_index = 0
    for _name, bars, kick_gain, snare_gain, hat_gain, bass_gain, lead_gain in ARRANGEMENT:
        for _ in range(bars):
            bar_start = bar_starts[bar_index]
            beat_length = beat_lengths[bar_index]
            for beat in range(meter.beats_per_bar):
                at = bar_start + beat * beat_length

                if kick_gain and beat in meter.kick_beats:
                    # The primary accent is what makes the bar findable.
                    emphasis = 1.0 if beat in meter.primary_beats else 0.62
                    add(track, kick(), at, kick_gain * emphasis)
                if snare_gain and beat in meter.snare_beats:
                    add(track, snare(), at, snare_gain)
                if hat_gain:
                    for offset in meter.hat_offsets:
                        add(
                            track,
                            hat(),
                            at + offset * beat_length,
                            hat_gain * (1.0 if offset == 0 else 0.7),
                        )
                if bass_gain and beat in meter.kick_beats:
                    add(
                        track,
                        bass(SCALE[bar_index % 3] / 4, beat_length * 0.9),
                        at,
                        bass_gain,
                    )
                if lead_gain and rng.random() < 0.55:
                    note = SCALE[rng.integers(0, len(SCALE))]
                    add(
                        track,
                        lead(note, beat_length * 0.75),
                        at
                        + (0.5 * beat_length if rng.random() < 0.3 else 0.0),
                        lead_gain,
                    )
            bar_index += 1

    peak = np.max(np.abs(track))
    if peak > 0:
        track = track / peak * 0.89
    return track


def main() -> int:
    args = [value for value in sys.argv[1:] if not value.startswith("--")]
    drift = "--drift" in sys.argv
    meter_key = "4"
    for value in sys.argv:
        if value.startswith("--meter="):
            meter_key = value.split("=", 1)[1]
    meter = METERS[meter_key]

    destination = Path(args[0] if args else "tests/fixtures/test_song.wav")
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, build(drift=drift, meter=meter), SAMPLE_RATE, subtype="PCM_16")
    label = "drifting" if drift else "constant"
    print(
        f"wrote {destination} ({label}, {meter.name}, "
        f"{destination.stat().st_size / 1024:.0f} KB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
