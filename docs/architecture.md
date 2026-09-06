# Architecture

How the pieces fit together, and the boundaries that were drawn on purpose.

## Architecture

```
apps/web/                        Next.js 16 · React 19 · TypeScript · Tailwind
  app/                           App Router pages and global styles
  components/                    Presentational components, no data fetching
  lib/                           API client, polling hook, formatters
  types/                         Shared response types

services/mapper-api/             FastAPI · Pydantic · librosa · numpy
  app/
    api/                         Routes and request-scoped dependencies
    models/                      Domain types (enums, musical IR, beatmap)
    schemas/                     Request/response validation
    services/
      media/                     Acquisition + normalisation
        base.py                    AudioSource interface, NormalizedMedia
        upload_source.py           Uploaded files
        youtube_source.py          YouTube (URL allowlist, canonical rebuild)
        normalizer.py              FFmpeg → analysis.wav + song.ogg
        probe.py                   Safe subprocess wrapper
      analysis/                  Difficulty-independent DSP
        audio_analyzer.py          Beats, onsets, energy bands
        tempo.py                   Octave correction, fine refinement, drift
        meter.py                   Bar length, downbeat phase, subdivision
        section_analyzer.py        Structural segmentation
        event_detector.py          Musical event timeline
      mapping/                   The mapping engine
        config.py                  Scoring weights, style bias
        difficulty_profiles.py     The five difficulty envelopes
        rhythm_selector.py         Which moments this difficulty represents
        pattern_library.py         Patterns as data, with difficulty metadata
        pattern_generator.py       Candidate generation and scoring
        parity_engine.py           Swing parity and hand-flow model
        difficulty_controller.py   Rolling NPS control and recovery
        obstacle_generator.py      Wall planning (runs before notes)
        lighting_generator.py      Vanilla lighting
        map_validator.py           Playability and structural validation
        pipeline.py                Stage orchestration
      export/                    Serialisation
        beatsaber_exporter.py      v3 difficulty + v2 Info.dat
        cover_art.py               Generated 512×512 cover
        package_builder.py         Flat-root ZIP
      jobs/                      Job lifecycle
        store.py                   State, disk mirror, path safety
        manager.py                 Bounded-concurrency worker
        workflows.py               Analysis and generation workflows
        cleanup.py                 Retention sweep

  tools/                         Development tools, not shipped in the image
    fetch_ranked_corpus.py         Pull ranked-map statistics from BeatSaver
    compare_to_corpus.py           Report where our output lands against them

storage/jobs/                    Per-job working directories (UUID names)
```

### Design boundaries worth knowing

- **Analysis never sees a difficulty.** `services/analysis` produces one
  timeline; `services/mapping` interprets it five different ways. This is what
  makes "generate another difficulty" nearly free.
- **Mapping never sees Beat Saber JSON.** The engine emits `BeatNote`,
  `Obstacle` and `LightEvent`; `export/beatsaber_exporter.py` is the only
  module that knows the file format. A v4 writer would be a sibling function.
- **Media acquisition is behind one interface.** Nothing downstream of
  `AudioSource.acquire()` knows whether the audio came from an upload or a
  video.
- **The job manager is one small class.** Swapping the in-process worker for
  Redis/RQ or Celery means reimplementing `JobManager.submit`, not touching
  the API layer.

---

---

[← Back to the README](../README.md)
