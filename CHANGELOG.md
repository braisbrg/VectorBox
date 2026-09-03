# Changelog

All notable changes to VectorBox. Format follows [Keep a Changelog](https://keepachangelog.com/1.1.0/);
versioning is [semver](https://semver.org/).

The entries below are written to be useful a year from now, so each one says **what was measured**,
not just what changed. Where a number appears, it was taken from the running system.

---

## [Unreleased]

### Security
- `pnpm audit` is now at **zero** advisories, down from one moderate: the postcss bump closed
  the last one left after the 3.1.0 overrides.

### Changed
- Dependency maintenance, clearing ten Dependabot PRs that had been open since May. Backend:
  `orjson`, `PyJWT`, `pydantic`, `pydantic-settings`, `openai`; `requirements.lock` regenerated
  with hashes and verified by installing it under `--require-hashes`. Frontend: `next` 16.2.11 →
  16.3.4, `react`/`react-dom` 19.2.8, `tailwindcss` 4.3.3, `tailwind-merge` 3.6.0, `postcss`
  8.5.26, `eslint` 9.39.5. Also the `infrastructure-critical` group: `fastapi` 0.136.1 →
  0.141.1, `sqlalchemy` 2.0.51, `alembic` 1.19.0, `scikit-learn` 1.9.0, `qdrant-client`
  1.19.0 and OpenTelemetry 1.44.0 (with the instrumentation packages moved to the matching
  0.65b0 — they are pinned to the SDK version and only move together). Major bumps the
  Dependabot groups exclude (`lucide-react` 1.x, `framer-motion` 13.x) and `@clerk/nextjs`
  were deliberately left out.

---

## [3.1.0] — 2026-09-02

MINOR, not MAJOR: feed rows, filters and mood axes were added, but **no endpoint was removed** —
which is what made 3.0.0 a MAJOR.

### Added
- **Source-side provider filtering.** The provider filter moved into each row's own query
  (`EXISTS` over `movie_availability`) instead of being applied after a bounded fetch.
  Post-filtering, `niche` dropped from 20 films to 3 and `random` disappeared entirely.
- **"Leaving Soon" row.** Cross-references `StreamingChange` with what TMDB still confirms, and
  drops films that remain available on another of your services. Head sorted by urgency
  (≤7 days), tail by quality.
- **Watchlist filter, done properly.** Available on the feed and in the Magic Box, via both a
  toggle and phrase detection. The SOURCE toggle switches the whole feed between the catalogue
  and your unwatched watchlist, composing with mood and providers.
- **Mood axes in version control.** `services/mood_axes.py` was declared the single source of
  truth for anchors, thresholds and quadrants while being untracked — so none of its documented
  numbers could be checked against a diff. It is now in git.
- **`docs/AUDIT_PLAYBOOK.md`** — the defect classes this repo actually commits, each with a
  measured instance and how to catch it.
- **`docs/HALLAZGOS_2026-08-19.md`** — why the taste centroid does not beat chance, and the five
  broken instruments that hid it.
- Distributed tracing for Signal B, the Groq call and the vector search. Signals A and C already
  had spans, so a slow search showed a gap with nothing in it.

### Changed
- **Anti-vector: fixed cosine thresholds → decile of the list itself.** The two hardcoded cosines
  demoted between 0 and 22 of 30 candidates depending on the user; one of them never fired at all.
  The rate is now constant by construction, which is a known trade and is documented as one.
- **Groq chain: `qwen3.6-27b` → `qwen3.8-27b`** (decommissioned 14 Sep). `enrich_vectors` no
  longer repeats the model ID — it derives it from `ENRICH_CHAIN`, so `services/llm_models.py`
  is finally the single source it always claimed to be.
- **One version number per deployable.** Three different values were live at once, including a
  hardcoded `V2.1.0_PROD` in the right console and a `"v3.0.0"` sitting inside the i18n message
  files, where it was identical in both languages — data, not a translation.
- Incremental RSS watchlist sync with a count-gated removal reconcile, replacing a full ~20-page
  scrape once per day per user.
- Director pages: case- and accent-insensitive lookup, split cache (public 6h / library never).
- "Random Top Picks" renamed to "Random Picks".

### Fixed
- **An availability writer that could add and update but never remove.** When a film leaves a
  country, TMDB stops returning that country's block, so the stale row kept asserting the
  provider forever. 47 of the 49 lying rows had been visited by the refresh *after* their own
  row's date — proof it was not a slow queue but an absent deletion. A second layer: `{}` was
  treated as a bad response when it actually means "not available anywhere".
- **`LIMIT` without `ORDER BY`, twice.** In `get_movies_to_refresh` the same hundred films came
  back run after run while others were never refreshed; in `compute_anti_vector`, 50 arbitrary
  negatives were fetched and then 49 of them discarded by the age decay, leaving the user with
  the most data with no anti-vector at all.
- **963 Qdrant points missing ten payload keys** — invisible to every filter, with no error.
  The 2026-07-30 write guard protected new points; it never repaired the old ones.
- **Seven `desc()` on nullable columns without `nullslast()`.** Postgres sorts NULLs first in
  DESC, so "most recently watched" was led by the films with no date at all.
- **Import re-embedded the whole library on every upload.** `enrich_movie` returned `True` on its
  no-change path and the bulk path read that as `needs_vector`: 59 of 59 untouched films asked
  for a new vector, then got re-encoded with the fallback recipe while keeping
  `has_enriched_embedding` set — replacing a Groq-enriched vector with a worse one.
- **The import wizard was unreachable.** Two onboarding guards lived in `AppShell` and the one
  that navigates won, so a fresh sign-up saw the ImportWizard for ~200 ms before being replaced.
  The Letterboxd ZIP had no entry point at all.
- **The similarity rail's vote floor deleted its best neighbours.** `vote_count >= 100` is a fame
  signal, not a quality one, and it removed exactly what the vector got right.
- Autocomplete: poster-less duplicate rows outranked the real film; it now understands directors.
- Streaming provider noise ("X Amazon Channel", "X with Ads") is dropped **at write time** —
  19 of the 55 distinct ES providers, none of them viewable with the base subscription.

### Security
- Nine transitive high-severity advisories closed by raising four overrides in
  `pnpm-workspace.yaml`; two of them already existed with a bound that had gone stale.

---

## [3.0.0] — 2026-07-11

**MAJOR.** Full brutalist "ACID" frontend migration wired to real data end to end, plus a
pre-release audit. MAJOR because public API endpoints were removed alongside the UI overhaul.

---

## Earlier releases

Tags `v2.0.0` through `v2.3.1` predate this file. Use `git log` and `git tag` for their history;
`BACKLOG.md` carries the decision record and bug history in full.

[3.1.0]: https://github.com/braisbrg/VectorBox/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/braisbrg/VectorBox/compare/v2.3.1...v3.0.0
