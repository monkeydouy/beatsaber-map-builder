"""Measure how human mappers actually build maps.

Reads real Beat Saber levels (a folder of extracted maps) and reports the
things our generator is judged on: how hands alternate across a lane sweep,
how much silence is left before the first note, note density, and how often a
hand is asked to swing the same way twice.

    python tools/analyse_reference_maps.py ../../examples/good-songs

`examples/` is deliberately not in the repository: the maps and music in
it belong to their authors. Put a folder of extracted community levels
there yourself to calibrate against.

Handles both v2 (`_notes`) and v3 (`colorNotes`) difficulty files.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: v2 stores red/blue as `_type`; v3 as `c`. 0 is the left (red) hand.
LEFT, RIGHT = 0, 1


@dataclass(slots=True)
class Note:
    beat: float
    x: int
    y: int
    hand: int
    direction: int


def read_json(path: Path) -> dict[str, Any]:
    # Some community maps ship with a UTF-8 BOM.
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_notes(path: Path) -> list[Note]:
    data = read_json(path)
    if "colorNotes" in data:
        rows = data["colorNotes"]
        notes = [Note(n["b"], n["x"], n["y"], n["c"], n["d"]) for n in rows]
    else:
        rows = data.get("_notes", [])
        notes = [
            Note(n["_time"], n["_lineIndex"], n["_lineLayer"], n["_type"], n["_cutDirection"])
            for n in rows
            if n.get("_type") in (0, 1)
        ]
    notes.sort(key=lambda note: note.beat)
    return notes


def sweeps(notes: list[Note], min_length: int = 3) -> list[list[Note]]:
    """Runs of consecutive notes moving steadily across the lanes.

    This is the shape the hands have to trade off cleanly — a descending
    4-3-2-1 run played entirely with one hand is the motion that reads as
    stuttering rather than flowing.
    """
    found: list[list[Note]] = []
    run = [notes[0]] if notes else []
    for previous, note in zip(notes, notes[1:]):
        step = note.x - previous.x
        gap = note.beat - previous.beat
        going = run[-1].x - run[-2].x if len(run) >= 2 else step
        same_way = step != 0 and (going == 0 or (step > 0) == (going > 0))
        if same_way and abs(step) <= 2 and 0 < gap <= 1.0:
            run.append(note)
        else:
            if len(run) >= min_length:
                found.append(run)
            run = [previous, note] if 0 < gap <= 1.0 and step != 0 else [note]
    if len(run) >= min_length:
        found.append(run)
    return found


def describe(name: str, notes: list[Note], bpm: float) -> dict[str, Any]:
    if not notes:
        return {}
    runs = sweeps(notes)
    alternating = 0
    for run in runs:
        hands = [note.hand for note in run]
        if all(a != b for a, b in zip(hands, hands[1:])):
            alternating += 1

    repeats = 0
    pairs = 0
    for hand in (LEFT, RIGHT):
        own = [note for note in notes if note.hand == hand]
        for a, b in zip(own, own[1:]):
            pairs += 1
            if a.direction == b.direction and b.beat - a.beat < 1.0 and a.direction != 8:
                repeats += 1

    span = notes[-1].beat - notes[0].beat
    return {
        "name": name,
        "notes": len(notes),
        "lead_in_s": notes[0].beat * 60.0 / bpm,
        "nps": len(notes) / (span * 60.0 / bpm) if span > 0 else 0.0,
        "sweeps": len(runs),
        "alternating_sweeps": alternating / len(runs) if runs else float("nan"),
        "rushed_repeats": repeats / max(pairs, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()

    rows = []
    for directory in sorted(args.folder.iterdir()):
        info_path = directory / "Info.dat"
        if not directory.is_dir() or not info_path.exists():
            continue
        info = read_json(info_path)
        bpm = float(info["_beatsPerMinute"])
        for group in info.get("_difficultyBeatmapSets", []):
            if group.get("_beatmapCharacteristicName") != "Standard":
                continue
            for entry in group.get("_difficultyBeatmaps", []):
                path = directory / entry["_beatmapFilename"]
                if not path.exists():
                    continue
                summary = describe(
                    f"{directory.name[:22]} {entry['_difficulty']}",
                    load_notes(path),
                    bpm,
                )
                if summary:
                    rows.append(summary)

    header = (f"{'map / difficulty':<34}{'notes':>7}{'NPS':>7}{'lead-in':>9}"
              f"{'sweeps':>8}{'alternating':>13}{'rushed repeats':>16}")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['name']:<34}{row['notes']:>7}{row['nps']:>7.2f}"
            f"{row['lead_in_s']:>8.1f}s{row['sweeps']:>8}"
            f"{row['alternating_sweeps']:>12.0%}{row['rushed_repeats']:>15.2%}"
        )

    if rows:
        print("\nacross the corpus:")
        alt = [r["alternating_sweeps"] for r in rows if r["alternating_sweeps"] == r["alternating_sweeps"]]
        print(f"  lane sweeps played with alternating hands: {sum(alt)/len(alt):.0%}")
        print(f"  same-hand direction repeats under a beat : "
              f"{sum(r['rushed_repeats'] for r in rows)/len(rows):.2%}")
        leads = sorted(r["lead_in_s"] for r in rows)
        print(f"  silence before the first note            : "
              f"min {leads[0]:.1f}s  median {leads[len(leads)//2]:.1f}s  max {leads[-1]:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
