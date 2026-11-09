# Changelog

All notable changes to VectorBox. Format follows [Keep a Changelog](https://keepachangelog.com/1.1.0/);
versioning is [semver](https://semver.org/).

The entries below are written to be useful a year from now, so each one says **what was measured**,
not just what changed. Where a number appears, it was taken from the running system.

---

## [3.2.1] — 2026-11-09

PATCH: dependencies only. No API changes. The backend lock changed, so the image has to be rebuilt
(`docker compose build backend`).

### Changed
- **Dependencies, from the Dependabot PRs of 2026-11-03, tested beyond what CI could see.**
  - **Frontend:**
    - TypeScript 7.0, `@types/node` 26, framer-motion 14;
    - the group with Clerk 7.9, React 19.3, React Query 5.105, Lucide 1.65 and Radix.

    Gates green, plus a pass in the browser at 375 px: landing, onboarding, feed, inline
    inspector, filter sheet (it opens, and closes in 0.7–1.1 s by its handle or its backdrop),
    «why this film», and the watchlist scope. No console errors.
  - **Backend:**
    - FastAPI 0.162, SQLAlchemy 2.1, Pydantic 2.14, openai 2.54, instructor 1.17, asyncpg 0.32;
    - raised floors for `python-dotenv`, `requests`, `mako` and `playwright`.

    `requirements.lock` is recompiled with hashes. Those PRs changed only `requirements.txt`, and
    since CI and the image install the lock, their green had tested none of it. Installed for
    real: 547 hermetic tests, the integration suite (34 passed, 7 skipped) and `pip-audit`
    clean. `diskcache`, which carried an advisory with no fix, is gone from the tree.
  - **Not upgraded:** `qdrant-client`, which moves with the Qdrant server it talks to.
- **Dependabot proposes `sentence-transformers` patches again.** Its ignore rule said `>=4` while
  the project is on 5, which excluded every update. It now ignores only the next major (`>=6`):
  changing the encoder means re-embedding the catalogue.

## [3.2.0] — 2026-11-08

MINOR: the import matches each film by what it is, and a new integrity check runs as maintenance
phase 12. No API changes.

The data these fixes protect was repaired by hand on 2026-11-03, before this release:
- 1,451 vectors re-encoded from their description;
- every Qdrant payload rewritten from Postgres;
- 20 library ratings moved to the film they were, and 20 removed (series, and one empty row,
  matched as films);
- clusters, mood axes and the neighbour table rebuilt.

### Added
- **`scripts/check_integrity.py` and maintenance phase 12.** Offline check of what the 2026-11-03
  audit found broken without a single error:
  - every Qdrant payload against `qdrant_payload(row)`;
  - every library film's vector, plus a sample of the rest, against the text production embeds
    today;
  - every Letterboxd URI against its row.

  It exits 1 while anything is off; `--repair` regenerates payloads and dense vectors from their
  source. The phase runs it after phases 3 and 4 (4 wrote descriptions without re-embedding) and
  before the re-cluster.

### Changed
- **The ZIP import asks Letterboxd when TMDB cannot tell which film a row is.** Two cases trigger
  it: no title matches, or several films share the title and year. The import then resolves the
  row's own Letterboxd link to its TMDB id: one request at a time, paced, cached 30 days, with no
  user data in it. Over the 889 library links checked on 2026-11-03, 20 were wrong, almost all
  Spanish or Galician films matched to a more popular English namesake (*As bestas* had become
  *Fantastic Beasts: The Secrets of Dumbledore*), plus series matched to whatever shared their
  name. A series page carries no TMDB id, so it now stays out of the library instead of coming in
  as a different film.

### Fixed
- **A film in your library could be a different film with a similar title.** Measured on
  2026-11-03: 18 rows of the catalogue carried another film's Letterboxd URI, and 7 ratings from 2
  users pointed at the wrong film. «X-Men» (2000) was the TV special *X-Men: The Mutant Watch*,
  «X2» a webcast, «Here We Are» *We Are Here*. Two causes:
  - TMDB search returned its first, most popular result whatever its title was. The import paths
    (ZIP, RSS, reconcile) now need a title match, the closest year wins among homonyms, and with
    no match the film is left unmatched and logged, instead of matched to something else.
  - A `letterboxd.com/tmdb/N` URI already names the film, but the ZIP import discarded it and
    searched by title. It now uses N.
- **`reconcile_letterboxd_movies.py` could not find this, and its `--fix` made it worse.** It
  searched each film by its own title, so a wrong row found itself; what it did flag were
  homonyms, and `--fix` moved ratings onto the more popular one. It now checks each film against
  its URI, without network.
- **A newly added film was invisible to the country, language, certification and adult filters
  until someone resynced the vector store.** The film factory and the ZIP import's batch wrote the
  Qdrant payload by hand: with the old `rating` key instead of `vote_average`, and without those
  fields. 1,584 points carried `rating` (measured 2026-11-03). Both now use the canonical builder.
  The guard test only watched one way of writing a point, so a new test builds a film through the
  factory and compares its point with the canonical payload.

---

## [3.1.3] — 2026-11-03

PATCH: security and tooling. No API changes.

### Security
- **Two months idle left 52 open Dependabot alerts (7 critical, 21 high) and 84 pip-audit findings
  in 13 packages.** Nothing in the code changed; the advisories landed while the project sat still.
  - Frontend: `next` 16.3.4 → 16.4.3 (three remote-code-execution advisories, two of them
    unauthenticated, fixed in 16.4.1), `axios` → 1.21.0, and the transitive `fast-uri`,
    `follow-redirects`, `sharp`, `source-map-js`, `brace-expansion`, `js-yaml`, `zod` and
    `minimatch`. `next` stops at 16.4.3 on purpose: 16.4.4 was one day old and pnpm's
    minimum-release-age policy rejects it.
  - Backend: `PyJWT` 2.13.0 → 2.15.1, OpenTelemetry 1.44.0 → 1.45.2 (instrumentations 0.65b0 →
    0.66b2), and new or raised floors for `pillow` (13.x, 39 advisories), `aiohttp`, `urllib3`,
    `filelock` (4.x), `anyio`, `certifi`, `dnspython`, `fsspec`, `msgpack` and `multidict`.
    The lock was recompiled from the existing pins: beyond these, only `yarl` (pulled by aiohttp)
    moved and one OpenTelemetry package (`opentelemetry-exporter-otlp-common`) came in.
  - Login was never exposed to the PyJWT algorithm-confusion advisories: it pins
    `algorithms=["RS256"]` and takes the key from Clerk's JWKS. It is upgraded anyway.
  - One new `transformers` advisory (PYSEC-2026-4174) has no published fix. It joins the list
    accepted by ID in `security_audit.py`, next to the others that wait for the encoder upgrade.
  - Measured after the change: `pnpm audit` finds nothing; `security_audit.py` finds nothing, with
    11 advisories accepted by ID (2 more for torch). The hermetic suite passes (523), and so do
    `test_trident_math.py` and `test_es_whitelist.py`. `verify_feed_parallelism.py` fails as it
    always has (9/10).

### Changed
- **CI and the local checks, measured and trimmed.**
  - The pre-commit hook made every commit pay **148 s** for the Python audit, and it needed the
    backend running. `security_audit.py` now passes `--disable-pip`: the lock already pins
    everything with hashes, so pip has nothing to re-resolve. It finds the same 11 accepted
    findings in **11 s**.
  - The hook is gone, and with it husky, lint-staged and ESLint. Next 16 removed `next lint`, and
    the config was in the legacy format, so none of it ran. The audits run in CI.
  - CI backend job:
    - Installs `requirements.lock` with hashes, which is what the image installs. Before, it
      installed `requirements.txt`, which failed and fell back to a subset.
    - Runs the hermetic pytest suite, which had never run in CI, and then the audit.
    - No longer starts the Postgres, Redis and Qdrant services, which no step used.
  - The three scripts CI ran are removed. Two of them tested copies of the functions, defined
    inside the script itself, and `verify_feed_parallelism.py` could not pass. New tests exercise
    the real `reciprocal_rank_fusion`, `calculate_quality_weight` and `filter_es_providers`. Each
    test fails when its function is broken (checked by mutating each one).
  - CI frontend job: pnpm is pinned to 12, and `pnpm audit --audit-level=high` runs after the
    build, so an npm timeout no longer hides the build result.
  - The Playwright job is removed until the e2e harness is rewritten for Clerk. It never ran.
  - Dependabot opens one grouped PR per ecosystem a month, instead of about 12 a week. Security
    updates still open on their own.
  - Measured locally on 2026-11-03: `tsc` 13 s, build 19 s, audit 11 s. pytest gave 529 passed in
    32 s with no environment variables other than a dummy `DATABASE_URL`; without it, collection
    fails. CI itself is unverified until this is pushed.

---

## [3.1.2] — 2026-09-03

PATCH: security only. No API changes.

### Fixed
- **The security gate had not audited anything for months.** `security_audit.py` ran
  `pip-audit --strict`, whose documented behaviour is to fail the whole audit when dependency
  collection fails — and one package cannot be collected: `torch` installs as `2.10.0+cpu`, a
  PEP 440 local version that does not exist on PyPI. pip-audit aborted there and audited zero
  packages; the wrapper skipped that ERROR line as known torch noise, saw no findings, and
  reported a clean bill of health.

  The same lock file audited without `--strict` reports **84 known vulnerabilities across 18
  packages**, including three HIGH in `cryptography` that GitHub had been flagging for three
  months.

  **Correction to the record: v3.1.0 and v3.1.1 were both tagged claiming "pip-audit clean".
  That claim was false in both.**

  The gate now has to prove it looked: pip-audit always ends with an explicit verdict, so the
  absence of one fails closed. And `torch` is no longer skipped — PyPI carries the same release
  without the local segment, so it is audited by base version in a second pass, which surfaced
  two torch advisories nobody could see.
- CI workflow jobs declared no `permissions`, so `GITHUB_TOKEN` arrived with write scope none of
  them need. One workflow-level `contents: read` block closes all three CodeQL alerts.

---

## [3.1.1] — 2026-09-03

PATCH: dependency maintenance and test coverage only. No API changes.

### Added
- Tests for the two dependency surfaces that the suite never touched: the sklearn contract
  used by K-Means cluster selection, and a live Qdrant neighbour search. Both upgrades
  (`scikit-learn` 1.9, `qdrant-client` 1.19) had passed 517 green tests without exercising
  either, and had to be checked by hand.

### Known issues
- The Magic Box era-relaxation safety net has stopped firing: it triggers on
  `results < 8` and the 1950s cyberpunk case now returns exactly 8, all of them padding at
  flat 73-77 scores. Deterministic, unrelated to this release's dependency work, and tracked
  in `BACKLOG.md` — a threshold compared against a *count* switches itself off as the
  catalogue grows.

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
`CUADERNO.md` (the lab notebook) carries the decision record and bug history in full, and
`BACKLOG.md` lists what is still open.

[3.2.1]: https://github.com/braisbrg/VectorBox/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/braisbrg/VectorBox/compare/v3.1.3...v3.2.0
[3.1.3]: https://github.com/braisbrg/VectorBox/compare/v3.1.2...v3.1.3
[3.1.2]: https://github.com/braisbrg/VectorBox/compare/v3.1.1...v3.1.2
[3.1.1]: https://github.com/braisbrg/VectorBox/compare/v3.1.0...v3.1.1
[3.1.0]: https://github.com/braisbrg/VectorBox/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/braisbrg/VectorBox/compare/v2.3.1...v3.0.0
