"""Domain enumerations shared across analysis, mapping and export."""

from __future__ import annotations

from enum import Enum


class Difficulty(str, Enum):
    EASY = "Easy"
    NORMAL = "Normal"
    HARD = "Hard"
    EXPERT = "Expert"
    EXPERT_PLUS = "ExpertPlus"

    @property
    def rank(self) -> int:
        """Ordinal used for ordering comparisons (0 = easiest)."""
        return _DIFFICULTY_ORDER.index(self)

    @property
    def beatsaber_rank(self) -> int:
        """`_difficultyRank` value expected by Beat Saber's Info.dat."""
        return {0: 1, 1: 3, 2: 5, 3: 7, 4: 9}[self.rank]

    @property
    def label(self) -> str:
        """Human-facing label (Beat Saber displays Expert+ with a plus)."""
        return "Expert+" if self is Difficulty.EXPERT_PLUS else self.value

    @property
    def beatmap_filename(self) -> str:
        return f"{self.value}Standard.dat"

    @classmethod
    def parse(cls, raw: str) -> Difficulty:
        """Accept the several spellings clients use (Expert+, expert_plus, ...)."""
        key = raw.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
        aliases = {
            "easy": cls.EASY,
            "normal": cls.NORMAL,
            "hard": cls.HARD,
            "expert": cls.EXPERT,
            "expertplus": cls.EXPERT_PLUS,
            "expert+": cls.EXPERT_PLUS,
            "ex+": cls.EXPERT_PLUS,
        }
        if key not in aliases:
            raise ValueError(f"Unsupported difficulty: {raw!r}")
        return aliases[key]


_DIFFICULTY_ORDER = [
    Difficulty.EASY,
    Difficulty.NORMAL,
    Difficulty.HARD,
    Difficulty.EXPERT,
    Difficulty.EXPERT_PLUS,
]

DIFFICULTY_ORDER = tuple(_DIFFICULTY_ORDER)


class MappingStyle(str, Enum):
    BALANCED = "balanced"
    DANCE = "dance"
    TECHNICAL = "technical"


class Hand(int, Enum):
    """Colour index used by Beat Saber: 0 = red/left, 1 = blue/right."""

    LEFT = 0
    RIGHT = 1

    @property
    def other(self) -> Hand:
        return Hand.RIGHT if self is Hand.LEFT else Hand.LEFT


class CutDirection(int, Enum):
    """Beat Saber cut-direction indices."""

    UP = 0
    DOWN = 1
    LEFT = 2
    RIGHT = 3
    UP_LEFT = 4
    UP_RIGHT = 5
    DOWN_LEFT = 6
    DOWN_RIGHT = 7
    ANY = 8


#: Unit vector describing where the saber travels for each cut direction.
DIRECTION_VECTORS: dict[CutDirection, tuple[float, float]] = {
    CutDirection.UP: (0.0, 1.0),
    CutDirection.DOWN: (0.0, -1.0),
    CutDirection.LEFT: (-1.0, 0.0),
    CutDirection.RIGHT: (1.0, 0.0),
    CutDirection.UP_LEFT: (-0.7071, 0.7071),
    CutDirection.UP_RIGHT: (0.7071, 0.7071),
    CutDirection.DOWN_LEFT: (-0.7071, -0.7071),
    CutDirection.DOWN_RIGHT: (0.7071, -0.7071),
    CutDirection.ANY: (0.0, 0.0),
}

#: Swing angle in degrees (0 = to the right, 90 = up). ANY has no angle.
DIRECTION_ANGLES: dict[CutDirection, float] = {
    CutDirection.RIGHT: 0.0,
    CutDirection.UP_RIGHT: 45.0,
    CutDirection.UP: 90.0,
    CutDirection.UP_LEFT: 135.0,
    CutDirection.LEFT: 180.0,
    CutDirection.DOWN_LEFT: 225.0,
    CutDirection.DOWN: 270.0,
    CutDirection.DOWN_RIGHT: 315.0,
}

DOWNWARD_CUTS = frozenset(
    {CutDirection.DOWN, CutDirection.DOWN_LEFT, CutDirection.DOWN_RIGHT}
)
UPWARD_CUTS = frozenset({CutDirection.UP, CutDirection.UP_LEFT, CutDirection.UP_RIGHT})
HORIZONTAL_CUTS = frozenset({CutDirection.LEFT, CutDirection.RIGHT})

#: Mirror map used to flip a pattern across the vertical centre line.
MIRRORED_DIRECTION: dict[CutDirection, CutDirection] = {
    CutDirection.UP: CutDirection.UP,
    CutDirection.DOWN: CutDirection.DOWN,
    CutDirection.LEFT: CutDirection.RIGHT,
    CutDirection.RIGHT: CutDirection.LEFT,
    CutDirection.UP_LEFT: CutDirection.UP_RIGHT,
    CutDirection.UP_RIGHT: CutDirection.UP_LEFT,
    CutDirection.DOWN_LEFT: CutDirection.DOWN_RIGHT,
    CutDirection.DOWN_RIGHT: CutDirection.DOWN_LEFT,
    CutDirection.ANY: CutDirection.ANY,
}


class Parity(str, Enum):
    """Approximate wrist state of a hand between swings."""

    FOREHAND = "forehand"  # ready to swing downward
    BACKHAND = "backhand"  # ready to swing upward


class SectionType(str, Enum):
    INTRO = "intro"
    VERSE = "verse"
    PRE_CHORUS = "pre_chorus"
    CHORUS = "chorus"
    BUILDUP = "buildup"
    DROP = "drop"
    BREAK = "break"
    BRIDGE = "bridge"
    OUTRO = "outro"
    LOW_ENERGY = "low_energy"
    MEDIUM_ENERGY = "medium_energy"
    HIGH_ENERGY = "high_energy"
    TRANSITION = "transition"


class MusicalEventType(str, Enum):
    KICK = "kick"
    SNARE = "snare"
    PERCUSSION = "percussion"
    ACCENT = "accent"
    DOWNBEAT = "downbeat"
    MELODY = "melody"
    TRANSITION = "transition"
    GENERIC_ONSET = "generic_onset"
    BEAT = "beat"


class ObstacleKind(str, Enum):
    DODGE_LEFT = "dodge_left"
    DODGE_RIGHT = "dodge_right"
    CROUCH = "crouch"


class JobStatus(str, Enum):
    CREATED = "CREATED"
    ACQUIRING_AUDIO = "ACQUIRING_AUDIO"
    NORMALIZING_AUDIO = "NORMALIZING_AUDIO"
    ANALYZING_AUDIO = "ANALYZING_AUDIO"
    DETECTING_BEATS = "DETECTING_BEATS"
    ANALYZING_SECTIONS = "ANALYZING_SECTIONS"
    BUILDING_EVENT_TIMELINE = "BUILDING_EVENT_TIMELINE"
    ANALYZED = "ANALYZED"
    SELECTING_RHYTHM = "SELECTING_RHYTHM"
    GENERATING_PATTERNS = "GENERATING_PATTERNS"
    VALIDATING_PARITY = "VALIDATING_PARITY"
    GENERATING_OBSTACLES = "GENERATING_OBSTACLES"
    GENERATING_LIGHTING = "GENERATING_LIGHTING"
    VALIDATING_MAP = "VALIDATING_MAP"
    EXPORTING = "EXPORTING"
    PACKAGING = "PACKAGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Statuses from which no further transition happens on its own.
TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ANALYZED})


class SourceType(str, Enum):
    UPLOAD = "upload"
    YOUTUBE = "youtube"
