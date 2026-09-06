"""Vanilla lighting.

Lighting is derived purely from the musical timeline — never from the notes —
so it stays coherent whichever difficulty the player picks. Only standard
event types are emitted; nothing here needs Chroma.
"""

from __future__ import annotations

import logging
from random import Random

from app.models.beatmap import LightEvent
from app.models.enums import MusicalEventType, SectionType
from app.models.musical import BeatGrid, MusicalEvent, Section

logger = logging.getLogger(__name__)

# Vanilla `basicBeatmapEvents` type ids.
EVENT_BACK_LASERS = 0
EVENT_RING_LIGHTS = 1
EVENT_LEFT_LASERS = 2
EVENT_RIGHT_LASERS = 3
EVENT_CENTRE_LIGHTS = 4
EVENT_RING_SPIN = 8
EVENT_RING_ZOOM = 9
EVENT_LEFT_LASER_SPEED = 12
EVENT_RIGHT_LASER_SPEED = 13

# Vanilla light values.
VALUE_OFF = 0
VALUE_BLUE_ON = 1
VALUE_BLUE_FLASH = 2
VALUE_BLUE_FADE = 3
VALUE_RED_ON = 5
VALUE_RED_FLASH = 6
VALUE_RED_FADE = 7

#: Lighting activity per section type, 0-1.
SECTION_ACTIVITY: dict[SectionType, float] = {
    SectionType.INTRO: 0.25,
    SectionType.OUTRO: 0.25,
    SectionType.BREAK: 0.20,
    SectionType.LOW_ENERGY: 0.25,
    SectionType.VERSE: 0.45,
    SectionType.MEDIUM_ENERGY: 0.50,
    SectionType.PRE_CHORUS: 0.60,
    SectionType.BUILDUP: 0.70,
    SectionType.BRIDGE: 0.50,
    SectionType.TRANSITION: 0.65,
    SectionType.CHORUS: 0.85,
    SectionType.HIGH_ENERGY: 0.85,
    SectionType.DROP: 1.00,
}


def generate_lighting(
    events: list[MusicalEvent],
    sections: list[Section],
    grid: BeatGrid,
    rng: Random,
) -> list[LightEvent]:
    """Build a vanilla light show from the musical timeline."""
    if not sections:
        return []

    lights: list[LightEvent] = []
    section_lookup = {section.index: section for section in sections}

    # Establish a base state so the stage is never left dark at the start.
    lights.append(LightEvent(beat=0.0, event_type=EVENT_BACK_LASERS, value=VALUE_BLUE_FADE))
    lights.append(LightEvent(beat=0.0, event_type=EVENT_LEFT_LASER_SPEED, value=2))
    lights.append(LightEvent(beat=0.0, event_type=EVENT_RIGHT_LASER_SPEED, value=2))

    # Section transitions: reset the stage and mark the change.
    for section in sections:
        beat = max(section.start_beat, 0.0)
        activity = SECTION_ACTIVITY.get(section.type, 0.5)
        speed = 1 + int(round(activity * 7))

        lights.append(LightEvent(beat=beat, event_type=EVENT_LEFT_LASER_SPEED, value=speed))
        lights.append(LightEvent(beat=beat, event_type=EVENT_RIGHT_LASER_SPEED, value=speed))

        if section.type in {SectionType.DROP, SectionType.CHORUS, SectionType.HIGH_ENERGY}:
            lights.append(LightEvent(beat=beat, event_type=EVENT_RING_SPIN, value=0))
            lights.append(
                LightEvent(beat=beat, event_type=EVENT_RING_LIGHTS, value=VALUE_RED_FLASH)
            )
            lights.append(
                LightEvent(beat=beat, event_type=EVENT_CENTRE_LIGHTS, value=VALUE_RED_FLASH)
            )
        elif section.type in {SectionType.BREAK, SectionType.LOW_ENERGY, SectionType.OUTRO}:
            lights.append(LightEvent(beat=beat, event_type=EVENT_CENTRE_LIGHTS, value=VALUE_OFF))
            lights.append(
                LightEvent(beat=beat, event_type=EVENT_BACK_LASERS, value=VALUE_BLUE_FADE)
            )
        else:
            lights.append(LightEvent(beat=beat, event_type=EVENT_RING_ZOOM, value=0))
            lights.append(
                LightEvent(beat=beat, event_type=EVENT_BACK_LASERS, value=VALUE_BLUE_FADE)
            )

    # Pulse on the beat, with density and colour following section energy.
    warm = True
    for event in events:
        section = section_lookup.get(event.section_index)
        activity = SECTION_ACTIVITY.get(section.type, 0.5) if section else 0.5

        is_structural = event.is_downbeat or event.event_type in {
            MusicalEventType.ACCENT,
            MusicalEventType.TRANSITION,
        }
        if not is_structural:
            # Off-beat pulses only in busy sections, and only sometimes.
            if event.event_type not in {MusicalEventType.KICK, MusicalEventType.SNARE}:
                continue
            if rng.random() > activity * 0.55:
                continue

        beat = max(event.quantized_beat, 0.0)
        strong = event.strength > 0.55 or event.is_downbeat
        warm = not warm

        if event.event_type is MusicalEventType.KICK or event.is_downbeat:
            value = VALUE_RED_FLASH if strong else VALUE_RED_FADE
            lights.append(LightEvent(beat=beat, event_type=EVENT_CENTRE_LIGHTS, value=value))
        elif event.event_type is MusicalEventType.SNARE:
            target = EVENT_LEFT_LASERS if warm else EVENT_RIGHT_LASERS
            lights.append(
                LightEvent(
                    beat=beat,
                    event_type=target,
                    value=VALUE_BLUE_FLASH if strong else VALUE_BLUE_FADE,
                )
            )
        else:
            lights.append(
                LightEvent(
                    beat=beat,
                    event_type=EVENT_BACK_LASERS,
                    value=VALUE_RED_FLASH if strong else VALUE_BLUE_FADE,
                )
            )

    lights.sort(key=lambda light: (light.beat, light.event_type))
    logger.info("lighting_generated", extra={"events": len(lights)})
    return lights
