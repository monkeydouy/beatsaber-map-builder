"""Measure generated maps against the ranked-map corpus.

Run after `fetch_ranked_corpus.py` to see where the difficulty profiles sit
relative to what real mappers ship:

    PYTHONPATH=. python tools/compare_to_corpus.py

Reports the percentile our output lands at within each difficulty's real
distribution. A well-calibrated profile puts default settings near the middle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.enums import Difficulty
from app.models.musical import AnalysisResult
from app.services.mapping.difficulty_profiles import PROFILES
from app.services.mapping.pipeline import GenerationOptions, generate_beatmap

CORPUS = Path("tests/fixtures/ranked_corpus.json")


def percentile_of(value: float, stats: dict[str, float]) -> str:
    """Roughly where `value` falls in a distribution described by percentiles."""
    marks = [(5, stats["p5"]), (25, stats["p25"]), (50, stats["p50"]),
             (75, stats["p75"]), (95, stats["p95"])]
    if value < marks[0][1]:
        return "<p5"
    for (low_p, low_v), (high_p, high_v) in zip(marks, marks[1:]):
        if low_v <= value <= high_v:
            span = high_v - low_v
            fraction = (value - low_v) / span if span > 0 else 0.0
            return f"~p{int(low_p + fraction * (high_p - low_p))}"
    return ">p95"


def band_for(stats: dict[str, float]) -> str:
    return f"{stats['p25']:.2f}-{stats['p75']:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path,
                        default=Path("tests/fixtures/analysis_cache.json"))
    parser.add_argument("--intensity", type=float, default=0.6)
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()

    corpus = json.loads(CORPUS.read_text())
    analysis = AnalysisResult.from_dict(json.loads(args.analysis.read_text()))

    print(f"corpus: {corpus['map_count']} ranked maps, {corpus['row_count']} rows")
    print(f"source: {analysis.duration:.0f}s at {analysis.bpm} BPM, "
          f"intensity {args.intensity}, seed {args.seed}\n")

    header = (f"{'difficulty':<12}{'song NPS':>10}{'vs corpus':>11}"
              f"{'corpus p25-p75':>18}{'NJS':>7}{'corpus NJS':>12}{'notes':>8}")
    print(header)
    print("-" * len(header))

    for difficulty in Difficulty:
        reference = corpus["difficulties"].get(difficulty.value)
        if not reference:
            continue
        result = generate_beatmap(
            analysis,
            GenerationOptions(
                difficulty=difficulty, intensity=args.intensity, seed=args.seed
            ),
        )
        stats = result.beatmap.statistics
        nps_ref = reference["nps"]
        njs_ref = reference["njs"]
        band = band_for(nps_ref)
        print(
            f"{difficulty.label:<12}{stats.song_nps:>10.2f}"
            f"{percentile_of(stats.song_nps, nps_ref):>11}"
            f"{band:>18}{result.beatmap.note_jump_speed:>7.1f}"
            f"{njs_ref['p50']:>12.1f}{stats.total_notes:>8}"
        )

    print(f"\n{'difficulty':<12}{'profile target':>18}{'corpus p25-p75':>18}"
          f"{'profile peak':>14}{'subdivisions':>16}")
    for difficulty in Difficulty:
        reference = corpus["difficulties"].get(difficulty.value)
        if not reference:
            continue
        profile = PROFILES[difficulty]
        nps_ref = reference["nps"]
        print(
            f"{difficulty.label:<12}"
            f"{f'{profile.target_nps_min}-{profile.target_nps_max}':>18}"
            f"{band_for(nps_ref):>18}"
            f"{profile.peak_nps:>14}"
            f"{profile.allowed_subdivisions!s:>16}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
