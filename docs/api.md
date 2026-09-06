# API reference

The HTTP surface: uploading, generating, polling and downloading.

## API overview

Interactive docs at `http://localhost:8000/docs`.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/api/health` | Service health and available tooling |
| `GET` | `/api/difficulties` | Difficulty options with their NPS ranges |
| `GET` | `/api/styles` | Mapping style options |
| `POST` | `/api/jobs/upload` | Upload audio (multipart `file`), starts analysis |
| `POST` | `/api/jobs/youtube/preview` | Video details, before anything downloads |
| `POST` | `/api/jobs/youtube` | Start analysis for a confirmed video |
| `GET` | `/api/jobs/{id}` | Poll job status, progress, analysis and result |
| `POST` | `/api/jobs/{id}/generate` | Generate a map for one difficulty |
| `GET` | `/api/jobs/{id}/download` | Download the ZIP |
| `DELETE` | `/api/jobs/{id}` | Discard a job and its files |

### Generating a map

```http
POST /api/jobs/{id}/generate
Content-Type: application/json

{
  "title": "Song Name",
  "artist": "Artist",
  "mapper": "SaberMapper AI",
  "difficulty": "Expert",
  "style": "balanced",
  "intensity": 0.7,
  "seed": 12345
}
```

`difficulty` accepts `Easy`, `Normal`, `Hard`, `Expert`, `ExpertPlus` (and
tolerates `Expert+` / `expert_plus`). It defaults to `Expert`. `style` is
`balanced`, `dance` or `technical`. `intensity` runs 0.0–1.0. Omit `seed` and
one is generated and returned with the result.

### Job states

```
CREATED → ACQUIRING_AUDIO → NORMALIZING_AUDIO → ANALYZING_AUDIO
        → DETECTING_BEATS → ANALYZING_SECTIONS → BUILDING_EVENT_TIMELINE
        → ANALYZED
                ↓ (per generation request)
        SELECTING_RHYTHM → GENERATING_PATTERNS → VALIDATING_PARITY
        → GENERATING_OBSTACLES → GENERATING_LIGHTING → VALIDATING_MAP
        → EXPORTING → PACKAGING → COMPLETED

Any stage → FAILED
```

`ANALYZED` is the resting state a song sits in between generations. The
frontend polls `GET /api/jobs/{id}` every 1.2 s and renders the server's own
progress — no simulated timers.

---

---

[← Back to the README](../README.md)
