# Difficulty system

What each difficulty means, and how the profiles were calibrated against real ranked maps.

## Difficulty system

Difficulty sets **hard boundaries**. Intensity moves you **within** them.
That separation is the reason "Expert at intensity 1.0" never quietly becomes
an Expert+ map.

| | Target NPS | Peak ceiling | Subdivisions | Crossovers | Real median NPS | Real median NJS |
|---|---|---|---|---|---|---|
| **Easy** | 1.4 – 2.7 | 4.0 | 1/2 beat | none | 1.93 | 12 |
| **Normal** | 2.1 – 3.9 | 5.6 | 1/2 beat | very rare | 2.87 | 14 |
| **Hard** | 3.0 – 5.1 | 7.2 | 1/4 beat | occasional | 3.77 | 15 |
| **Expert** *(default)* | 3.9 – 6.6 | 9.2 | 1/4 beat | controlled | 4.94 | 17 |
| **Expert+** | 5.4 – 8.5 | 11.9 | 1/6 beat, triplets | frequent | 6.89 | 19 |

The target bands are **not invented**. They are the interquartile range of
716 community-ranked, human-made, vanilla Standard maps —
2112 difficulty rows pulled from BeatSaver's public metadata
by [`tools/fetch_ranked_corpus.py`](../services/mapper-api/tools/fetch_ranked_corpus.py)
and committed as `tests/fixtures/ranked_corpus.json`. `target_nps_min` is the
corpus p25 and `target_nps_max` the p75, so default settings land near the
middle of what real mappers ship under that label rather than at the bottom
edge of it. Note jump speeds are calibrated the same way.

`tools/compare_to_corpus.py` reports where generated maps land in those
distributions, and the calibration is enforced by tests — retuning a profile
away from the corpus fails the build.

Each difficulty independently controls note density, allowed subdivisions,
minimum note spacing, pattern complexity, hand travel, crossover and double
frequency, wall frequency, recovery cadence, vision-block tolerance, permitted
grid rows and note jump speed.

Expert+ is **not** Expert with extra notes sprinkled in, and Easy is **not**
Expert with every second note deleted. Each difficulty re-runs rhythm selection
against the same musical timeline and picks the moments that matter at its own
level of detail.

### What the calibration changed, and what it could not

Before it, every difficulty was under-dense: generated maps landed between the
13th and 30th percentile of their own difficulty's real distribution. Three
things came out of fixing that.

**The bands moved up.** Roughly 30% across the board.

**Two difficulties could not reach their own targets.** Real Easy maps hit 2.74
NPS at p75, which whole beats alone cannot produce at an ordinary tempo — so
Easy was restricted to a density it could never achieve. Easy gained half-beats
and Hard gained quarter-beats. Easy's readability comes from its spacing floor
instead, which still forbids two half-beats in a row at typical tempos.

**`peak_nps` and `min_note_interval` contradicted each other.** They had been
set independently, and after the bands moved, four of five profiles had a
spacing floor slower than their own one-second ceiling — making that ceiling
unreachable by construction. The spacing floor is now the reciprocal of the
peak, and a test enforces it.

Two things the corpus could not calibrate:

- **Local peaks.** It reports whole-map averages, which say nothing about
  density inside any one second. `peak_nps` is still judgement — about 1.75x
  the target ceiling.
- **Wall frequency.** The corpus median is 34–47 obstacles per minute, which is
  not a dodging course, it is decoration: many maps use large numbers of tiny
  or off-grid walls for visual effect and the count cannot tell those apart
  from a wall the player must avoid. Matching that number would have been
  calibration in name only, so walls stay conservative.

One finding needed no change: ranked maps have a **median of zero parity
errors** at every difficulty, and zero at p75 for Hard and above. That is
direct evidence that the parity engine's strictness is right.

### Note jump speed

NJS is derived, not fixed: it scales with tempo, the map's realised density and
intensity. The spawn offset then reproduces Beat Saber's own half-jump
calculation so the time a note is *visible* matches the difficulty's target
(≈1.15 s on Easy down to ≈0.72 s on Expert+) across the whole tempo range.

---

---

[← Back to the README](../README.md)
