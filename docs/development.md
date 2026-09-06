# Local development

Running the two services outside Docker, configuring them, and running the tests.

## Local development

### Backend

Requires **Python 3.12+** and **FFmpeg** on your `PATH`.

```bash
cd services/mapper-api

python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
```

Installing FFmpeg:

| Platform | Command                                        |
| -------- | ---------------------------------------------- |
| macOS    | `brew install ffmpeg`                          |
| Debian   | `sudo apt install ffmpeg`                       |
| Windows  | `winget install Gyan.FFmpeg`, or use Docker    |

`yt-dlp` is installed with the Python requirements. If the YouTube tab is
greyed out, the API could not find the binary — `/api/health` reports which
tools it can see.

### Frontend

Requires **Node 20+**.

```bash
cd apps/web
npm install
npm run dev            # http://localhost:3000
```

Point it at a backend elsewhere with `apps/web/.env.local`:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

---

## Configuration

All settings are environment variables; see [`.env.example`](../.env.example).

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `APP_ENV` | `development` | Environment label |
| `LOG_LEVEL` | `INFO` | Log verbosity |
| `STORAGE_PATH` | `storage` | Where job data is written |
| `MAX_UPLOAD_MB` | `50` | Upload size limit |
| `MAX_AUDIO_DURATION_SECONDS` | `600` | Longest song accepted |
| `MAX_CONCURRENT_JOBS` | `2` | Concurrent CPU-heavy jobs |
| `JOB_RETENTION_HOURS` | `6` | How long job data is kept |
| `CLEANUP_INTERVAL_SECONDS` | `900` | Retention sweep interval |
| `FFMPEG_TIMEOUT_SECONDS` | `120` | Per-conversion timeout |
| `YOUTUBE_DOWNLOAD_TIMEOUT_SECONDS` | `120` | Per-download timeout |
| `FFMPEG_BINARY` / `FFPROBE_BINARY` / `YTDLP_BINARY` | tool name | Binary overrides |
| `ENABLE_YOUTUBE` | `true` | Set `false` to remove the YouTube tab |
| `ALLOWED_ORIGINS` | `http://localhost:3000,…` | CORS allowlist |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | API URL baked into the web bundle |
| `ENABLE_VARIABLE_TEMPO` | `true` | Allow piecewise tempo maps (v3 `bpmEvents`) for songs that drift |
| `DEBUG_MAPPING` | `false` | Write mapping debug artefacts |

With `DEBUG_MAPPING=true`, each job directory also receives
`musical_events.json`, `selected_rhythm.json`, `planned_walls.json`,
`generated_patterns.json` and `difficulty_analysis.json`. These are for tuning the algorithm and are never
served over HTTP.

---

## Testing

```bash
cd services/mapper-api
source .venv/bin/activate

pytest                      # full suite
pytest -m "not slow"        # skip the tests that run the real DSP
ruff check app tests        # lint
```

```bash
cd apps/web
npm run build               # includes a TypeScript check
npm run typecheck
```

The suite covers timing and quantisation, tempo octave correction, parity
rules and transition scoring, difficulty profile ordering, density measurement,
Beat Saber serialisation, ZIP structure, input validation and SSRF containment,
and API behaviour.

The mapping-quality tests are the interesting ones. They assert that every
difficulty produces a valid, playable map; that each difficulty is measurably
denser than the one below it; that no hand is ever asked to make three rapid
identical cuts; that positions and patterns stay varied; that the same seed
reproduces a map byte for byte and a different seed does not; and that
intensity never pushes a map outside its difficulty's envelope.

There are five audio fixtures — straight 4/4, a 3/4 waltz, a 4/4 shuffle, a 6/8
and a sixteenth-note-dense variant — so meter and subdivision detection are checked against material whose
ground truth is known by construction, and the difficulty calibration is
checked on a source dense enough that the profile rather than the music is
what limits it. One test states the regression directly:
a waltz forced back onto 4/4 must scatter its downbeats.

The tempo tests include the one that carries the most weight: a variable-tempo
map is exported, then replayed through an *independently written* model of how
Beat Saber interprets `bpmEvents`, and every note is asserted to land at the
wall-clock time the mapper intended. Writing the checker from the format's
semantics rather than from `BeatGrid` is the point — a mistake in the exporter
cannot hide behind the same mistake in the check. There is also a second audio
fixture, a take that speeds up at bar 24 and settles at bar 56, which the
detector has to find without being told.

The wall tests assert the invariant that motivates the ordering: no note ever
sits inside a wall, a dodge wall's lane is left completely empty (not merely
sparse), a crouch wall's top row is untouched, and dodge walls measurably push
the notes to the far side.

They run against a **synthesised** audio fixture — a 128 BPM track with a real
arrangement, built by `tests/fixtures/make_fixture.py` on first run. That keeps
the tests reproducible and keeps 13 MB of audio out of the repository, while
still giving tempo and structure detection a fair target.

---

---

[← Back to the README](../README.md)
