# The mapping engine

How audio becomes notes: what is measured, what is inferred, and what is refused.

## The mapping engine

The hard part of this project is not producing JSON. It is producing a map that
feels intentional. A few of the decisions that matter:

### Tempo is measured three times

librosa's tempo estimate has two independent problems. It routinely lands an
**octave** away — a 128 BPM track reported as 64 — and it is **quantised** to
autocorrelation bins, so even the right octave comes back as 129.2 instead of
128.

The octave is fixed by scoring every plausible ratio against the onset
envelope, weighted by a mild preference for tempi humans actually tap at. The
exact value is then refined by folding the onset envelope onto candidate beat
periods and finding the sharpest phase concentration, down to about 0.005 BPM.

That precision is not vanity. A 1% tempo error drifts the grid by more than a
second across a three-minute song, and every note in the back half lands late.

Then the song is checked for whether it holds one tempo at all.

### Songs that do not hold a tempo

Anything written to a click does. Anything played by humans does not: a live
take breathes, pushing into the chorus and settling in the verse, typically by
one to three percent. That is small enough to sound natural and large enough to
ruin a map, because the error accumulates — a 2% drift is a full beat out after
fifty bars.

So `BeatGrid` is a *piecewise* linear map. Local tempo is measured from
inter-beat intervals (with dropped and doubled beats folded back, so a tracker
slip does not masquerade as a tempo change), the curve is cut into runs of
roughly constant tempo, each run's tempo and phase are refined against its own
audio, and the boundaries are snapped to bar lines so every tempo change lands
on a whole number of bars.

Beat Saber carries this natively through v3 `bpmEvents`, so the map follows the
performance instead of fighting it. No mods are needed.

The detector is deliberately reluctant. A spurious tempo change is worse than a
missed one — a constant grid on drifting music degrades gracefully, a wrong
tempo map does not — so a piecewise fit is only accepted when it beats the
constant fit on the detected beats by a clear margin. A steady song produces a
single segment, an empty `bpmEvents` array, and byte-identical output to before.
Set `ENABLE_VARIABLE_TEMPO=false` to force a single BPM regardless.

### The bar is measured, not assumed

Assuming 4/4 is right most of the time and catastrophically wrong the rest of
it. In a waltz, "every fourth beat is a downbeat" accents beats 1, 2, 3, 1, 2,
3 in turn — the emphasis rotates through the bar, so the map stresses a pattern
the music does not have. That is worse than having no downbeat information,
because the rest of the pipeline trusts it. Measured on a 3/4 fixture, a
hardcoded 4 puts only 67% of its downbeats on the real beat 1; detecting the
meter puts 100% there.

Two independent measurements decide it, and they fail in different ways.
*Phase contrast* — how much louder the strongest position in the bar is than
the average beat — is sensitive but biased toward longer bars, since the best
of six positions beats the best of four by chance alone. *Accent
autocorrelation* — whether the pattern actually repeats at that period — has no
such bias and separates duple from triple decisively: a 4/4 groove
*anti*-correlates at lag 3, a waltz anti-correlates at lag 4. Multiplying them
cancels the bias in the first with the rigour of the second, a prior keeps 4 as
the default, and departing from it has to be earned.

How the beat *divides* is a separate question, measured separately from where
onsets actually fall rather than inferred from the bar length — a 4/4 blues
shuffle and a 6/8 ballad are different meters with the same answer. Quantising
a shuffle onto a duple grid drags every offbeat about 40 ms early, which reads
as sloppy timing rather than as the groove it is. When the beat divides in
three, each difficulty's subdivision ladder is read in the song's own terms:
"may use half-beats" becomes "may use third-beats", so a difficulty keeps its
offbeats instead of losing them for being the wrong arithmetic.

### Notes come from patterns, not from onsets

The generator never picks a position for a single note. It groups the selected
rhythm into *runs*, builds up to twenty candidate realisations per run
(different pattern shapes, mirrored or not, either hand leading), scores each
whole run against the parity engine, and commits the best one.

Scoring a run rather than a note is what produces flow: a note that looks good
in isolation but strands the next three is correctly rejected.

### Parity is modelled, not hoped for

Each hand carries a forehand/backhand state. Down-ish cuts require forehand and
leave backhand; up-ish cuts do the reverse; horizontals flip it. Breaking that
alternation is a "reset" — occasionally a deliberate device, usually
unplayable. Resets are permitted only with enough recovery time, penalised
otherwise, and rejected outright below a hard threshold.

The validator then re-derives parity from scratch as an independent second
opinion on the generator's own bookkeeping.

### Density is controlled, then measured honestly

The rhythm selector spends a per-section note budget derived from the
difficulty's target NPS and that section's energy, so quiet passages stay quiet
and drops get the detail. The budgets are renormalised across the song, so a
mostly-gentle track still reaches its difficulty's target overall.

Afterwards the difficulty controller measures rolling 1s/2s/4s density and
thins any window that exceeds the profile — dropping decoration (the second
half of a double) before structure (notes on the beat).

One detail worth naming: the rate for *n* notes inside a window is
`(n − 1) / window`, not `n / window`. The naive form double-counts endpoints
and reports a plain quarter-note stream at 128 BPM as 3.0 NPS when the player
is really hitting 2.13 — enough to make the controller strip notes out of a
perfectly reasonable Easy map.

### Walls are planned before notes, not filtered after them

The obvious order — generate the map, then add walls where they fit — does not
work. Every wall has to be vetoed the moment any note occupies its lane, and in
a dense map that is nearly always. What survives is a handful of walls that got
lucky, sitting wherever the notes happened to leave a gap, which is the
opposite of intentional.

So the order is inverted. `plan_obstacles` runs on the *rhythm* — it knows when
the map will ask for a swing, but not yet where — and reserves lane and row
space from the song's structure. The pattern generator then treats those
reservations as a hard constraint, relocating notes sideways first (a pattern's
vertical shape carries more of its character than its exact column) and
preferring lanes on the hand's own side.

Notes under a dodge wall also score better on the side the wall is herding the
player toward, so the wall reads as choreography rather than an absence.

### Everything is seeded

Each stage draws from its own derived RNG stream (`Random(f"{seed}:{stage}")`),
so the same seed always produces the same map, and toggling lighting cannot
shift a single note. Toggling *walls* does change the notes — that is the
routing above doing its job, and the test suite asserts it.

---

---

[← Back to the README](../README.md)
