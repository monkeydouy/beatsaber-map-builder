"""Waveform analysis: tempo, beats, onsets and frame-level energy features.

This stage is intentionally difficulty-agnostic. It runs once per song and its
output is reused for every difficulty the user later asks for.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from app.models.musical import AudioFeatures, BeatGrid
from app.services.analysis.meter import (
    MeterEstimate,
    SubdivisionEstimate,
    detect_meter,
    detect_subdivision,
)
from app.services.analysis.tempo import (
    TempoCandidate,
    beat_consistency,
    estimate_tempo,
    fit_beat_grid,
    grid_fit_quality,
    prepare_envelope,
)

logger = logging.getLogger(__name__)

#: Frequency band edges in Hz used for kick / snare / hat discrimination.
BAND_EDGES = ((20.0, 250.0), (250.0, 2000.0), (2000.0, 11000.0))


@dataclass(slots=True)
class RawAnalysis:
    """Everything `audio_analyzer` extracts, before structure/event detection."""

    duration: float
    sample_rate: int
    hop_length: int
    grid: BeatGrid
    beats: np.ndarray
    beat_confidence: np.ndarray
    downbeats: np.ndarray
    onsets: np.ndarray
    onset_strengths: np.ndarray
    frame_times: np.ndarray
    onset_envelope: np.ndarray
    band_envelopes: np.ndarray  # shape (3, n_frames)
    rms: np.ndarray
    spectral_centroid: np.ndarray
    spectral_bandwidth: np.ndarray
    percussive_energy: np.ndarray
    harmonic_energy: np.ndarray
    chroma: np.ndarray
    mfcc: np.ndarray
    tempo_candidates: list[TempoCandidate]
    meter: MeterEstimate | None = None
    subdivision: SubdivisionEstimate | None = None

    def to_features(self) -> AudioFeatures:
        return AudioFeatures(
            frame_times=self.frame_times.tolist(),
            rms=self.rms.tolist(),
            onset_strength=self.onset_envelope.tolist(),
            spectral_centroid=self.spectral_centroid.tolist(),
            spectral_bandwidth=self.spectral_bandwidth.tolist(),
            low_energy=self.band_envelopes[0].tolist(),
            mid_energy=self.band_envelopes[1].tolist(),
            high_energy=self.band_envelopes[2].tolist(),
            percussive_energy=self.percussive_energy.tolist(),
            harmonic_energy=self.harmonic_energy.tolist(),
        )


def _normalize(values: np.ndarray) -> np.ndarray:
    """Scale to 0-1 using a robust upper percentile to resist single spikes."""
    if values.size == 0:
        return values
    ceiling = float(np.percentile(values, 99.0))
    if ceiling <= 0:
        ceiling = float(values.max()) or 1.0
    return np.clip(values / ceiling, 0.0, 1.0)


def _band_onset_envelopes(
    stft_magnitude: np.ndarray, sample_rate: int, n_fft: int
) -> np.ndarray:
    """Per-band spectral flux, used to tell a kick from a hi-hat."""
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)
    envelopes = np.zeros((len(BAND_EDGES), stft_magnitude.shape[1]), dtype=float)
    for index, (low, high) in enumerate(BAND_EDGES):
        mask = (frequencies >= low) & (frequencies < high)
        if not mask.any():
            continue
        band = stft_magnitude[mask, :]
        flux = np.diff(band, axis=1, prepend=band[:, :1])
        envelopes[index] = _normalize(np.maximum(flux, 0.0).sum(axis=0))
    return envelopes


def beat_accent(
    beats: np.ndarray,
    onset_envelope: np.ndarray,
    frame_times: np.ndarray,
    low_envelope: np.ndarray,
) -> np.ndarray:
    """How emphatic each detected beat is.

    Weighted toward the low band because the bar is marked by the kick far more
    reliably than by overall loudness — a busy hi-hat pattern is loud on every
    beat and says nothing about where the bar begins.
    """
    if beats.size == 0:
        return np.array([])
    return _sample(low_envelope, frame_times, beats) + 0.5 * _sample(
        onset_envelope, frame_times, beats
    )


def _detect_downbeats(beats: np.ndarray, meter: MeterEstimate) -> np.ndarray:
    """The beats that start a bar, given the detected meter."""
    if beats.size < meter.beats_per_bar * 2:
        return beats[:1] if beats.size else np.array([])
    return beats[meter.phase :: meter.beats_per_bar]


def _sample(values: np.ndarray, times: np.ndarray, at: np.ndarray) -> np.ndarray:
    if values.size == 0 or times.size == 0:
        return np.zeros_like(at)
    return np.interp(at, times, values, left=0.0, right=0.0)


def analyze_audio(
    path: Path,
    *,
    sample_rate: int = 44100,
    hop_length: int = 512,
    progress: Callable[[str, int, str], None] | None = None,
    allow_variable_tempo: bool = True,
) -> RawAnalysis:
    """Run the full DSP pass over `analysis.wav`.

    This is CPU-bound and blocking; callers run it in a worker thread. The
    optional `progress` callback is invoked between stages so the UI can show
    real movement rather than freezing on one percentage for ten seconds.
    """

    def report(status: str, percent: int, label: str) -> None:
        if progress is not None:
            progress(status, percent, label)

    report("ANALYZING_AUDIO", 32, "Loading audio")
    audio, sr = librosa.load(str(path), sr=sample_rate, mono=True)
    duration = float(len(audio)) / sr
    if duration <= 1.0:
        raise ValueError("Audio is too short to analyse.")

    report("ANALYZING_AUDIO", 38, "Computing spectrum")
    n_fft = 2048
    stft = np.abs(librosa.stft(audio, n_fft=n_fft, hop_length=hop_length))
    # No `n_fft` here, deliberately. librosa's STFT centres frame i on sample
    # i * hop_length (center=True), and passing n_fft adds a half-window offset
    # meant for center=False. With it, `frame_times` sat 23 ms — two whole hop
    # frames — ahead of the onset and beat times, which are computed without
    # it. Every frame-level feature sampled at an event was therefore read
    # from the wrong place: onset strength came back as zero for two thirds of
    # events (including every kick), and the band energies driving instrument
    # classification were measuring the moment *after* each transient.
    frame_times = librosa.frames_to_time(
        np.arange(stft.shape[1]), sr=sr, hop_length=hop_length
    )

    report("ANALYZING_AUDIO", 44, "Separating percussion")
    harmonic_spec, percussive_spec = librosa.decompose.hpss(stft)
    percussive_energy = _normalize(percussive_spec.sum(axis=0))
    harmonic_energy = _normalize(harmonic_spec.sum(axis=0))

    onset_envelope = librosa.onset.onset_strength(
        S=librosa.amplitude_to_db(percussive_spec, ref=np.max),
        sr=sr,
        hop_length=hop_length,
    )
    onset_envelope = np.asarray(onset_envelope, dtype=float)
    # onset_strength can return one extra/fewer frame than the STFT.
    onset_envelope = _match_length(onset_envelope, stft.shape[1])

    report("ANALYZING_AUDIO", 52, "Measuring energy bands")
    band_envelopes = _band_onset_envelopes(stft, sr, n_fft)
    rms = _normalize(librosa.feature.rms(S=stft, hop_length=hop_length)[0])
    spectral_centroid = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
    spectral_bandwidth = librosa.feature.spectral_bandwidth(S=stft, sr=sr)[0]

    # --- tempo -----------------------------------------------------------
    report("DETECTING_BEATS", 58, "Detecting BPM")
    raw_estimates: list[float] = []
    try:
        global_tempo = librosa.feature.tempo(
            onset_envelope=onset_envelope, sr=sr, hop_length=hop_length, aggregate=np.median
        )
        raw_estimates.extend(float(value) for value in np.atleast_1d(global_tempo))
        windowed = librosa.feature.tempo(
            onset_envelope=onset_envelope, sr=sr, hop_length=hop_length, aggregate=None
        )
        windowed = np.atleast_1d(windowed).ravel()
        if windowed.size:
            for percentile in (25, 50, 75):
                raw_estimates.append(float(np.percentile(windowed, percentile)))
    except Exception:  # pragma: no cover - librosa version differences
        logger.warning("tempo_estimator_failed", exc_info=True)
    if not raw_estimates:
        raw_estimates = [120.0]

    best_tempo, ranked = estimate_tempo(onset_envelope, frame_times, duration, raw_estimates)

    report("DETECTING_BEATS", 66, "Tracking beats")
    _, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_envelope,
        sr=sr,
        hop_length=hop_length,
        bpm=best_tempo.bpm,
        trim=False,
    )
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    beats = np.asarray(beats, dtype=float)

    # A constant grid is the right answer for most music. `fit_beat_grid` only
    # returns a piecewise one when the beats measurably disagree with a single
    # tempo — see `tempo.py` for why it is deliberately reluctant.
    report("DETECTING_BEATS", 70, "Checking for tempo drift")
    grid, drift = fit_beat_grid(
        beats,
        prepare_envelope(onset_envelope),
        frame_times,
        round(best_tempo.bpm, 3),
        float(best_tempo.offset),
        duration,
        beats_per_bar=4,
        allow_variable=allow_variable_tempo,
    )
    consistency = beat_consistency(beats, grid.representative_bpm)
    fit_quality = grid_fit_quality(best_tempo.bpm, best_tempo.offset, beats)
    grid.confidence = float(
        np.clip(
            0.35 * consistency
            + 0.35 * fit_quality
            + 0.30 * min(best_tempo.contrast / 6.0, 1.0),
            0.0,
            1.0,
        )
    )

    beat_confidence = _sample(_normalize(onset_envelope), frame_times, beats)
    # --- meter -----------------------------------------------------------
    report("DETECTING_BEATS", 74, "Detecting time signature")
    # Both terms must be on the same scale. `band_envelopes` is already
    # normalised; passing the raw onset envelope let it dominate by an order of
    # magnitude and drown out the low-band accent that actually marks the bar.
    accent = beat_accent(
        beats, _normalize(onset_envelope), frame_times, band_envelopes[0]
    )
    meter = detect_meter(accent)
    grid.beats_per_bar = meter.beats_per_bar
    downbeats = _detect_downbeats(beats, meter)

    # --- onsets ----------------------------------------------------------
    report("DETECTING_BEATS", 72, "Detecting onsets")
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sr,
        hop_length=hop_length,
        backtrack=False,
        units="frames",
    )
    onsets = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)
    onsets = np.asarray(onsets, dtype=float)
    onset_strengths = _sample(_normalize(onset_envelope), frame_times, onsets)

    # How the beat divides is a separate question from how long the bar is: a
    # 4/4 shuffle and a 6/8 ballad are different meters with the same answer.
    subdivision = detect_subdivision(np.array([grid.time_to_beat(t) for t in onsets]))
    grid.triple_subdivision = subdivision.triple

    # --- structure features ----------------------------------------------
    report("ANALYZING_SECTIONS", 78, "Extracting structure features")
    chroma = librosa.feature.chroma_stft(S=stft**2, sr=sr)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(stft**2), n_mfcc=13)

    logger.info(
        "audio_analysis_completed",
        extra={
            "bpm": grid.bpm,
            "meter": meter.label,
            "beats_per_bar": meter.beats_per_bar,
            "meter_confidence": round(meter.confidence, 3),
            "subdivision": subdivision.label,
            "variable_tempo": grid.is_variable,
            "tempo_segments": len(grid.segments),
            "duration": round(duration, 2),
            "beats": int(beats.size),
            "onsets": int(onsets.size),
            "tempo_confidence": round(grid.confidence, 3),
            **{f"drift_{key}": value for key, value in drift.items()},
        },
    )

    return RawAnalysis(
        duration=duration,
        sample_rate=sr,
        hop_length=hop_length,
        grid=grid,
        beats=beats,
        beat_confidence=beat_confidence,
        downbeats=np.asarray(downbeats, dtype=float),
        onsets=onsets,
        onset_strengths=onset_strengths,
        frame_times=frame_times,
        onset_envelope=_normalize(onset_envelope),
        band_envelopes=band_envelopes,
        rms=rms,
        spectral_centroid=spectral_centroid,
        spectral_bandwidth=spectral_bandwidth,
        percussive_energy=percussive_energy,
        harmonic_energy=harmonic_energy,
        chroma=chroma,
        mfcc=mfcc,
        tempo_candidates=ranked,
        meter=meter,
        subdivision=subdivision,
    )


def _match_length(values: np.ndarray, length: int) -> np.ndarray:
    if values.size == length:
        return values
    if values.size > length:
        return values[:length]
    return np.pad(values, (0, length - values.size), mode="edge")
