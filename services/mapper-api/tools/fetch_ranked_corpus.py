"""Fetch difficulty statistics for community-ranked Beat Saber maps.

This is a *development* tool, not part of the service. It exists so the
difficulty profiles in `difficulty_profiles.py` can be justified by what real
mappers actually ship rather than by what seemed reasonable when they were
written.

Only public map *metadata* is fetched — per-difficulty note counts, NPS, NJS,
walls — never map files or audio. The output is an aggregate summary, which is
what gets committed; the per-map rows stay local.

    python tools/fetch_ranked_corpus.py --pages 40 --out tests/fixtures/ranked_corpus.json

Please leave the rate limit alone. BeatSaver is a volunteer-run service.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

API_ROOT = "https://api.beatsaver.com"
USER_AGENT = "SaberMapperAI-calibration/1.0 (+difficulty profile research)"

#: One request per this many seconds. Do not lower it.
REQUEST_INTERVAL = 1.2
REQUEST_TIMEOUT = 20

#: Difficulty names as BeatSaver reports them, in our own order.
DIFFICULTY_ORDER = ("Easy", "Normal", "Hard", "Expert", "ExpertPlus")

#: Percentiles kept for each metric.
PERCENTILES = (5, 25, 50, 75, 95)


def _request(path: str) -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"{API_ROOT}/{path}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  request failed ({exc}); continuing", file=sys.stderr)
        return None


def _is_vanilla(diff: dict[str, Any]) -> bool:
    """Only maps our generator could plausibly have produced are comparable.

    A Noodle Extensions map can put notes anywhere in the room; comparing our
    4x3-grid output against its density would be measuring the wrong thing.
    """
    return not (diff.get("ne") or diff.get("me") or diff.get("chroma"))


def collect(pages: int) -> list[dict[str, Any]]:
    """Walk the ranked-map listing and pull out Standard difficulty rows."""
    rows: list[dict[str, Any]] = []
    seen_maps: set[str] = set()

    for page in range(pages):
        # `automapper=false` is inverted upstream and returns nothing, so the
        # filtering is done on the response field instead.
        query = urllib.parse.urlencode({"sortOrder": "Rating", "ranked": "true"})
        payload = _request(f"search/text/{page}?{query}")
        time.sleep(REQUEST_INTERVAL)
        if not payload:
            continue

        docs = payload.get("docs") or []
        if not docs:
            print(f"  page {page}: empty, stopping")
            break

        for doc in docs:
            if doc.get("automapper"):
                continue
            if not (doc.get("ranked") or doc.get("blRanked")):
                continue
            map_id = doc.get("id")
            if not map_id or map_id in seen_maps:
                continue
            seen_maps.add(map_id)

            versions = doc.get("versions") or []
            if not versions:
                continue
            for diff in versions[0].get("diffs") or []:
                if diff.get("characteristic") != "Standard":
                    continue
                if diff.get("difficulty") not in DIFFICULTY_ORDER:
                    continue
                if not _is_vanilla(diff):
                    continue
                seconds = float(diff.get("seconds") or 0.0)
                notes = int(diff.get("notes") or 0)
                if seconds < 30 or notes < 30:
                    continue
                parity = diff.get("paritySummary") or {}
                rows.append(
                    {
                        "map_id": map_id,
                        "difficulty": diff["difficulty"],
                        "nps": float(diff.get("nps") or 0.0),
                        "notes": notes,
                        "njs": float(diff.get("njs") or 0.0),
                        "offset": float(diff.get("offset") or 0.0),
                        "obstacles": int(diff.get("obstacles") or 0),
                        "bombs": int(diff.get("bombs") or 0),
                        "events": int(diff.get("events") or 0),
                        "seconds": seconds,
                        "parity_errors": int(parity.get("errors") or 0),
                        "parity_warns": int(parity.get("warns") or 0),
                    }
                )

        print(f"  page {page}: {len(seen_maps)} maps, {len(rows)} difficulty rows")

    return rows


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    result = {
        f"p{p}": round(
            ordered[min(int(len(ordered) * p / 100), len(ordered) - 1)], 4
        )
        for p in PERCENTILES
    }
    result["mean"] = round(statistics.fmean(ordered), 4)
    result["min"] = round(ordered[0], 4)
    result["max"] = round(ordered[-1], 4)
    return result


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-map rows into the committed reference statistics."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["difficulty"]].append(row)

    summary: dict[str, Any] = {
        "source": "beatsaver.com public map metadata",
        "filter": "ranked, human-made, Standard characteristic, no NE/ME/Chroma",
        "map_count": len({row["map_id"] for row in rows}),
        "row_count": len(rows),
        "difficulties": {},
    }

    for difficulty in DIFFICULTY_ORDER:
        group = grouped.get(difficulty, [])
        if not group:
            continue
        walls_per_minute = [
            row["obstacles"] / (row["seconds"] / 60.0) for row in group if row["seconds"] > 0
        ]
        summary["difficulties"][difficulty] = {
            "count": len(group),
            "nps": _percentiles([row["nps"] for row in group]),
            "njs": _percentiles([row["njs"] for row in group]),
            "notes": _percentiles([float(row["notes"]) for row in group]),
            "seconds": _percentiles([row["seconds"] for row in group]),
            "walls_per_minute": _percentiles(walls_per_minute),
            "bombs": _percentiles([float(row["bombs"]) for row in group]),
            "parity_errors": _percentiles([float(row["parity_errors"]) for row in group]),
        }

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=40, help="listing pages to walk")
    parser.add_argument(
        "--out", type=Path, default=Path("tests/fixtures/ranked_corpus.json")
    )
    parser.add_argument("--raw", type=Path, default=None, help="also dump per-map rows")
    args = parser.parse_args()

    print(f"fetching up to {args.pages} pages from {API_ROOT} (1 request / {REQUEST_INTERVAL}s)")
    rows = collect(args.pages)
    if not rows:
        print("no rows collected", file=sys.stderr)
        return 1

    summary = summarise(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}: {summary['map_count']} maps, {summary['row_count']} rows")

    if args.raw:
        args.raw.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"wrote {args.raw}")

    for difficulty, stats in summary["difficulties"].items():
        nps = stats["nps"]
        print(
            f"  {difficulty:<11} n={stats['count']:<5} "
            f"NPS p25={nps['p25']:<6} p50={nps['p50']:<6} p75={nps['p75']:<6} p95={nps['p95']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
