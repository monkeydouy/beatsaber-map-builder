# Security and limitations

What the app defends against, and what it still gets wrong.

## Security notes

Every upload and URL is treated as untrusted.

- **Uploads** are size-limited while streaming, validated by extension, MIME
  type and an actual FFmpeg probe, and stored under a UUID path. The client's
  filename is never used for storage — only for display and for picking an
  extension.
- **YouTube URLs** are parsed, never forwarded. The host must be on a small
  allowlist, and an 11-character video id is extracted and used to rebuild a
  canonical `https://www.youtube.com/watch?v=<id>`. Nothing the user typed
  reaches yt-dlp, which removes SSRF and argument-injection as a class.
  Localhost, IP literals, private ranges, non-HTTP schemes and playlist bulk
  downloads are all rejected.
- **Subprocesses** are always invoked with argument lists, never a shell
  string, always with a timeout.
- **Job ids** must parse as UUIDs, and resolved job paths are checked to stay
  inside the storage root.
- **Storage is not served statically.** Downloads go through one endpoint that
  serves a single known filename from a validated job directory.
- **Errors** shown to users never contain stack traces, filesystem paths,
  subprocess commands or internal state; the detail is logged server-side with
  the job id.
- Both containers run as non-root users.

---

## Known limitations

Stated plainly, because a generated map that oversells itself wastes your time:

- **Section labels are heuristic.** Boundaries are usually right; whether a
  span is called "chorus" or "pre-chorus" often is not. The labels drive
  relative density and lighting, so a mislabel costs texture, not playability.
- **Instrument classification is approximate.** Band-limited spectral flux
  separates kicks, snares and hats reasonably on clear mixes. On dense or
  heavily compressed masters it blurs, and events fall back to generic onsets.
- **Very sparse or very dense music resists its target NPS.** A map cannot
  represent more moments than the music contains: on a track whose finest
  detail is eighth notes, Expert+ will land near the bottom of its range — on
  the sparse test fixture it uses 98% of every musical event available and
  still falls short. The validator reports this rather than inventing notes.
- **The corpus is ranked maps, which skew harder than the average upload.**
  They are the most consistent reference for what a difficulty label means, but
  a map calibrated to them sits toward the demanding end of its class.
- **Tempo tracking handles drift, not rubato.** Gradual push-and-settle and
  step changes are fitted piecewise; genuinely free time, ritardandos and
  anything without a steady pulse underneath are not. The detector reports its
  confidence, and falls back to a single tempo whenever the piecewise fit is
  not clearly better.
- **`bpmEvents` are newer ground than the rest of the format.** The base game
  handles them, but some third-party editors and older tooling are less
  reliable with variable-tempo maps. They are only emitted for songs that
  actually need them; `ENABLE_VARIABLE_TEMPO=false` turns the feature off.
- **Meter detection covers 2, 3, 4 and 6 beats per bar.** 5/4, 7/8 and other
  odd meters fall back to 4, because offering them costs more in false
  positives than it wins in true ones. 6/8 usually reports as 3 — both group
  the eighths correctly, and the distinction between them is genuinely hard to
  make from accent strength alone; what matters is that it is not forced onto
  4. Music with no clear accent pattern also falls back to 4 rather than
  inventing a meter.
- **Walls are still deliberately conservative.** Planning them first means they
  land reliably rather than by luck, but the placement rate is intentionally low
  (roughly 0–5 per song, rising with difficulty). They mark structure; they are
  not a dodging course.
- **Bombs are off by default** and intentionally minimal.
- **Lighting is vanilla and rule-based.** It follows the music's structure and
  reads well, but it is not a hand-crafted light show.
- **One difficulty per ZIP.** The exporter already accepts a list of beatmaps
  and `Info.dat` is built from it, so multi-difficulty packages are a small
  change — but the MVP ships one.
- **Jobs live in one process.** State is in memory with a disk mirror; there is
  no shared queue, so this does not scale horizontally as-is. The `JobManager`
  seam exists precisely to make that swap contained.

---

---

[← Back to the README](../README.md)
