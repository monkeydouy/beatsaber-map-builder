"""Musical intermediate representation produced by the analysis stage.

These structures sit between raw DSP output and the mapping engine. They are
deliberately free of any Beat Saber concept so the same timeline can drive any
number of difficulties or, later, an entirely different game format.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.models.enums import MusicalEventType, SectionType


@dataclass(frozen=True, slots=True)
class TempoSegment:
    """A stretch of the song held at one tempo.

    `start_beat` is the grid-beat index at `start_time`, so consecutive
    segments chain: the beat count never restarts at a tempo change.
    """

    start_time: float
    start_beat: float
    bpm: float

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    def to_dict(self) -> dict[str, float]:
        return {
            "start_time": round(self.start_time, 6),
            "start_beat": round(self.start_beat, 6),
            "bpm": round(self.bpm, 4),
        }


class BeatGrid:
    """The song's beat coordinate system.

    Most music holds one tempo, and for that case this is exactly what it used
    to be: a single linear map, ``time = offset + beat * 60 / bpm``. Live
    recordings, tape transfers and anything played to a human rather than a
    click do not hold one tempo, so the grid is really a *piecewise* linear map
    built from `TempoSegment`s. A constant-tempo song is simply the
    single-segment case, and produces byte-identical output to before.

    Two coordinate systems matter and must not be confused:

    * **grid beats** — what the whole mapping engine works in. Beat 0 sits at
      the song's first detected beat (`offset`), which is rarely t=0.
    * **song beats** — what Beat Saber uses, counting from t=0.

    `song_beat()` converts between them, and is the only thing the exporter
    needs to know about.
    """

    __slots__ = (
        "_segments",
        "beats_per_bar",
        "confidence",
        "duration",
        "triple_subdivision",
    )

    def __init__(
        self,
        bpm: float | None = None,
        offset: float = 0.0,
        beats_per_bar: int = 4,
        confidence: float = 0.0,
        segments: Sequence[TempoSegment] | None = None,
        duration: float = 0.0,
        triple_subdivision: bool = False,
    ) -> None:
        if segments:
            ordered = tuple(sorted(segments, key=lambda item: item.start_time))
        else:
            if bpm is None:
                raise ValueError("BeatGrid needs either a bpm or segments")
            ordered = (TempoSegment(start_time=offset, start_beat=0.0, bpm=bpm),)
        self._segments = ordered
        self.beats_per_bar = beats_per_bar
        self.confidence = confidence
        self.duration = duration
        #: True when the beat divides in three (shuffle, swing, compound time)
        #: rather than in two. Drives quantisation, not bar length.
        self.triple_subdivision = triple_subdivision

    # -- shape -------------------------------------------------------------

    @property
    def segments(self) -> tuple[TempoSegment, ...]:
        return self._segments

    @property
    def is_variable(self) -> bool:
        return len(self._segments) > 1

    @property
    def offset(self) -> float:
        """Wall-clock time of grid beat 0."""
        return self._segments[0].start_time

    @property
    def bpm(self) -> float:
        """The base tempo — what goes in Info.dat's `_beatsPerMinute`."""
        return self._segments[0].bpm

    @property
    def representative_bpm(self) -> float:
        """Duration-weighted mean tempo.

        Used where a single number has to stand in for the whole song — note
        jump speed, and the seconds-per-beat conversions in the rhythm
        selector. On a constant-tempo song this is just the tempo.
        """
        if len(self._segments) == 1:
            return self._segments[0].bpm
        total_weight = 0.0
        weighted = 0.0
        for index, segment in enumerate(self._segments):
            if index + 1 < len(self._segments):
                span = self._segments[index + 1].start_time - segment.start_time
            else:
                span = max(self.duration - segment.start_time, 0.0)
            if span <= 0:
                span = 1.0
            weighted += segment.bpm * span
            total_weight += span
        return weighted / total_weight if total_weight else self._segments[0].bpm

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.representative_bpm

    # -- conversions -------------------------------------------------------

    def _segment_for_time(self, time: float) -> TempoSegment:
        chosen = self._segments[0]
        for segment in self._segments:
            if segment.start_time <= time:
                chosen = segment
            else:
                break
        return chosen

    def _segment_for_beat(self, beat: float) -> TempoSegment:
        chosen = self._segments[0]
        for segment in self._segments:
            if segment.start_beat <= beat:
                chosen = segment
            else:
                break
        return chosen

    def time_to_beat(self, time: float) -> float:
        segment = self._segment_for_time(time)
        return segment.start_beat + (time - segment.start_time) * segment.bpm / 60.0

    def beat_to_time(self, beat: float) -> float:
        segment = self._segment_for_beat(beat)
        return segment.start_time + (beat - segment.start_beat) * 60.0 / segment.bpm

    def bpm_at(self, time: float) -> float:
        return self._segment_for_time(time).bpm

    # -- Beat Saber's coordinate system ------------------------------------

    def song_segments(self) -> tuple[TempoSegment, ...]:
        """The same tempo map expressed in Beat Saber's beat space.

        Beat Saber counts beat 0 at t=0, while our grid starts at the first
        detected beat. The first segment is therefore extended backwards to the
        origin at its own tempo, and every later segment's beat index is
        recomputed in that space.
        """
        base = self._segments[0]
        result = [TempoSegment(start_time=0.0, start_beat=0.0, bpm=base.bpm)]
        for segment in self._segments[1:]:
            previous = result[-1]
            start_beat = (
                previous.start_beat
                + (segment.start_time - previous.start_time) * previous.bpm / 60.0
            )
            result.append(
                TempoSegment(
                    start_time=segment.start_time,
                    start_beat=start_beat,
                    bpm=segment.bpm,
                )
            )
        return tuple(result)

    def song_beat(self, time: float) -> float:
        """Beat value in Beat Saber's coordinates (beat 0 at t=0)."""
        segments = self.song_segments()
        chosen = segments[0]
        for segment in segments:
            if segment.start_time <= time:
                chosen = segment
            else:
                break
        return chosen.start_beat + (time - chosen.start_time) * chosen.bpm / 60.0

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "bpm": round(self.bpm, 4),
            "offset": round(self.offset, 6),
            "beats_per_bar": self.beats_per_bar,
            "triple_subdivision": self.triple_subdivision,
            "confidence": round(self.confidence, 4),
            "duration": round(self.duration, 4),
            "segments": [segment.to_dict() for segment in self._segments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BeatGrid:
        raw = data.get("segments") or []
        segments = [
            TempoSegment(
                start_time=item["start_time"],
                start_beat=item["start_beat"],
                bpm=item["bpm"],
            )
            for item in raw
        ]
        return cls(
            bpm=data.get("bpm"),
            offset=data.get("offset", 0.0),
            beats_per_bar=data.get("beats_per_bar", 4),
            confidence=data.get("confidence", 0.0),
            segments=segments or None,
            duration=data.get("duration", 0.0),
            triple_subdivision=data.get("triple_subdivision", False),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if not self.is_variable:
            return f"BeatGrid(bpm={self.bpm:.3f}, offset={self.offset:.3f})"
        tempos = ", ".join(f"{segment.bpm:.2f}" for segment in self._segments)
        return f"BeatGrid(variable, {len(self._segments)} segments: {tempos})"


@dataclass(slots=True)
class Section:
    """A contiguous structural span of the song."""

    index: int
    start: float
    end: float
    start_beat: float
    end_beat: float
    type: SectionType
    #: Rank-normalised 0-1 position among this song's sections. Good for
    #: *labelling* — it adapts to each song — but it says nothing about how
    #: much louder one section actually is than another.
    intensity: float
    #: Mean RMS relative to the song's loudest section, 0-1. This is the one
    #: that carries real dynamics, and the one note density follows.
    loudness: float = 1.0
    energy: float = 0.0
    onset_density: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def contains(self, time: float) -> bool:
        return self.start <= time < self.end

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        return data


@dataclass(slots=True)
class MusicalEvent:
    """One musically meaningful moment in the song."""

    timestamp: float
    beat: float
    strength: float
    confidence: float
    section_index: int
    event_type: MusicalEventType
    low_energy: float = 0.0
    mid_energy: float = 0.0
    high_energy: float = 0.0
    onset_strength: float = 0.0
    percussive: float = 0.0
    harmonic: float = 0.0
    #: Beat position after snapping to the difficulty-independent analysis grid.
    quantized_beat: float = 0.0
    #: Denominator of the beat subdivision this event snaps to (1 = whole beat).
    division: int = 1
    #: Absolute snapping error in beats.
    snap_error: float = 0.0
    #: True when the event lands on the first beat of a bar.
    is_downbeat: bool = False
    #: Position within the bar, in beats (0 .. beats_per_bar).
    bar_phase: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type.value
        return data


@dataclass(slots=True)
class AudioFeatures:
    """Frame-level DSP features, kept coarse enough to serialise cheaply."""

    frame_times: list[float] = field(default_factory=list)
    rms: list[float] = field(default_factory=list)
    onset_strength: list[float] = field(default_factory=list)
    spectral_centroid: list[float] = field(default_factory=list)
    spectral_bandwidth: list[float] = field(default_factory=list)
    low_energy: list[float] = field(default_factory=list)
    mid_energy: list[float] = field(default_factory=list)
    high_energy: list[float] = field(default_factory=list)
    percussive_energy: list[float] = field(default_factory=list)
    harmonic_energy: list[float] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisResult:
    """Everything the mapper needs to know about the song."""

    duration: float
    sample_rate: int
    grid: BeatGrid
    beats: list[float]
    beat_confidence: list[float]
    downbeats: list[float]
    onsets: list[float]
    onset_strengths: list[float]
    sections: list[Section]
    events: list[MusicalEvent]
    features: AudioFeatures
    tempo_candidates: list[dict[str, float]] = field(default_factory=list)

    @property
    def bpm(self) -> float:
        return self.grid.bpm

    def section_at(self, time: float) -> Section | None:
        for section in self.sections:
            if section.contains(time):
                return section
        return self.sections[-1] if self.sections else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "grid": self.grid.to_dict(),
            # Kept alongside `grid` because the job API and the frontend read
            # these directly, and a single number is what they want to show.
            "bpm": round(self.grid.bpm, 4),
            "offset": round(self.grid.offset, 6),
            "beats_per_bar": self.grid.beats_per_bar,
            "tempo_confidence": round(self.grid.confidence, 4),
            "duration": round(self.duration, 3),
            "sample_rate": self.sample_rate,
            "beats": [round(value, 4) for value in self.beats],
            "beat_confidence": [round(value, 4) for value in self.beat_confidence],
            "downbeats": [round(value, 4) for value in self.downbeats],
            "onsets": [round(value, 4) for value in self.onsets],
            "onset_strengths": [round(value, 4) for value in self.onset_strengths],
            "tempo_candidates": self.tempo_candidates,
            "sections": [section.to_dict() for section in self.sections],
            "events": [event.to_dict() for event in self.events],
            "features": {
                "frame_times": [round(value, 4) for value in self.features.frame_times],
                "rms": [round(value, 5) for value in self.features.rms],
                "onset_strength": [round(v, 5) for v in self.features.onset_strength],
                "spectral_centroid": [round(v, 2) for v in self.features.spectral_centroid],
                "spectral_bandwidth": [round(v, 2) for v in self.features.spectral_bandwidth],
                "low_energy": [round(v, 5) for v in self.features.low_energy],
                "mid_energy": [round(v, 5) for v in self.features.mid_energy],
                "high_energy": [round(v, 5) for v in self.features.high_energy],
                "percussive_energy": [round(v, 5) for v in self.features.percussive_energy],
                "harmonic_energy": [round(v, 5) for v in self.features.harmonic_energy],
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisResult:
        features_raw = data.get("features", {})
        features = AudioFeatures(
            frame_times=list(features_raw.get("frame_times", [])),
            rms=list(features_raw.get("rms", [])),
            onset_strength=list(features_raw.get("onset_strength", [])),
            spectral_centroid=list(features_raw.get("spectral_centroid", [])),
            spectral_bandwidth=list(features_raw.get("spectral_bandwidth", [])),
            low_energy=list(features_raw.get("low_energy", [])),
            mid_energy=list(features_raw.get("mid_energy", [])),
            high_energy=list(features_raw.get("high_energy", [])),
            percussive_energy=list(features_raw.get("percussive_energy", [])),
            harmonic_energy=list(features_raw.get("harmonic_energy", [])),
        )
        sections = [
            Section(
                index=item["index"],
                start=item["start"],
                end=item["end"],
                start_beat=item["start_beat"],
                end_beat=item["end_beat"],
                type=SectionType(item["type"]),
                intensity=item["intensity"],
                loudness=item.get("loudness", 1.0),
                energy=item.get("energy", 0.0),
                onset_density=item.get("onset_density", 0.0),
            )
            for item in data.get("sections", [])
        ]
        events = [
            MusicalEvent(
                timestamp=item["timestamp"],
                beat=item["beat"],
                strength=item["strength"],
                confidence=item["confidence"],
                section_index=item["section_index"],
                event_type=MusicalEventType(item["event_type"]),
                low_energy=item.get("low_energy", 0.0),
                mid_energy=item.get("mid_energy", 0.0),
                high_energy=item.get("high_energy", 0.0),
                onset_strength=item.get("onset_strength", 0.0),
                percussive=item.get("percussive", 0.0),
                harmonic=item.get("harmonic", 0.0),
                quantized_beat=item.get("quantized_beat", item["beat"]),
                division=item.get("division", 1),
                snap_error=item.get("snap_error", 0.0),
                is_downbeat=item.get("is_downbeat", False),
                bar_phase=item.get("bar_phase", 0.0),
            )
            for item in data.get("events", [])
        ]
        return cls(
            duration=data["duration"],
            sample_rate=data.get("sample_rate", 44100),
            grid=(
                BeatGrid.from_dict(data["grid"])
                if "grid" in data
                else BeatGrid(
                    bpm=data["bpm"],
                    offset=data.get("offset", 0.0),
                    beats_per_bar=data.get("beats_per_bar", 4),
                    confidence=data.get("tempo_confidence", 0.0),
                    duration=data.get("duration", 0.0),
                )
            ),
            beats=list(data.get("beats", [])),
            beat_confidence=list(data.get("beat_confidence", [])),
            downbeats=list(data.get("downbeats", [])),
            onsets=list(data.get("onsets", [])),
            onset_strengths=list(data.get("onset_strengths", [])),
            sections=sections,
            events=events,
            features=features,
            tempo_candidates=list(data.get("tempo_candidates", [])),
        )
