# VectorBox QA Testing Protocol

> **Role:** QA Lead / Release Certification
> **Version:** 1.7.2 (Optimization Sprint)
> **Last Updated:** 2026-04-08

This document is the **complete verification script** for the VectorBox application. Each phase must be completed in order. A **single FAIL** in a critical check blocks the release.

---

## 📋 Pre-Flight Checklist

Before starting, confirm **all** of the following:

| # | Item | Status |
|:--|:-----|:------:|
| 1 | Docker Desktop running | ☐ |
| 2 | `.env` file exists with valid keys (see §0) | ☐ |
| 3 | Chrome/Firefox with DevTools access | ☐ |
| 4 | Letterboxd export ZIP available (`ratings.csv`, `watchlist.csv`) | ☐ |
| 5 | Project is on `main` branch, git tree is clean | ☐ |

### § 0: Required `.env` Keys

Verify all the following keys are present and non-empty in `.env`:

```
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
REDIS_URL
QDRANT_HOST, QDRANT_PORT
TMDB_API_KEY
OMDB_API_KEY
GROQ_API_KEY
SECRET_KEY              # Session token signing
OTEL_EXPORTER_OTLP_ENDPOINT   # e.g. http://jaeger:4317
OTEL_SERVICE_NAME       # e.g. vectorbox-backend
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| No key is literally `"your_key_here"` | All real values | ☐ |
| No extra spaces or quotes around values | Clean `.env` format | ☐ |

---

## 🔴 PHASE 1: Infrastructure — "The Hard Reset"

> **Goal:** Simulate a fresh install. All data wiped and rebuilt from scratch.

### Step 1.1: Data Wipe
```powershell
docker-compose down -v
```
**Expected:** All containers stopped. Volumes `postgres_data`, `qdrant_data`, `redis_data` deleted.

### Step 1.2: Master Setup

**Windows:**
```powershell
./setup.ps1
```
**Linux/Mac:**
```bash
chmod +x setup.sh && ./setup.sh
```

> **Note:** The setup scripts automatically detect `ENVIRONMENT=production` in your `.env` file and will append `-f docker-compose.prod.yml` to all internal database commands to enforce security parameters like `POSTGRES_PASSWORD`.

Wait for the full sequence. Verify each checkpoint in the logs:

| Checkpoint | Log Message | Pass? |
|:-----------|:------------|:-----:|
| Postgres Ready | `until pg_isready ... done` | ☐ |
| DB Ready | `✅ Database Schema Applied` | ☐ |
| Seeding | `✅ Database Seeded & Collection Created` | ☐ |
| Redis Flush | `FLUSHALL ASYNC` | ☐ |
| Feed Warmup | `SUCCESS: Feed is hot and caching guard passed.` | ☐ |
| Final | `🎉 VectorBox is ready and running!` | ☐ |

### Step 1.3: Container Health
```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
```

| Container | Expected Status |
|:----------|:----------------|
| `vectorbox-frontend` | Up (healthy) |
| `vectorbox-backend` | Up (healthy) |
| `vectorbox-postgres` | Up (healthy) |
| `vectorbox-qdrant` | Up |
| `vectorbox-redis` | Up (healthy) |
| `vectorbox-jaeger` | Up |

> ❌ **FAIL if:** Any container shows `Restarting`, `Exited`, or is missing.

### Step 1.4: API Health Check
```powershell
curl http://localhost:8000/health
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| HTTP 200 or 503 | `{"status": "ok", "components": {"postgres": "ok", ...}}` | ☐ |
| Deep Check | Verifies connections to Postgres, Redis, and Qdrant | ☐ |
| Backend responds within 2s | No timeout | ☐ |

### Step 1.5: Frontend Loads
Navigate to `http://localhost:3000`.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Page loads | Shows `/login` page (redirect) | ☐ |
| No console errors | DevTools console is clean | ☐ |
| No Next.js build errors | No red error overlays | ☐ |

---

## 🔐 PHASE 2: Auth Guard Gauntlet

> **Goal:** Verify auth flow is bulletproof — no "limbo" states.

### Step 2.1: Fresh Entry (Incognito)
1. Open Chrome Incognito (`Ctrl+Shift+N`).
2. Navigate to `http://localhost:3000`.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Redirect | Immediately redirects to `/login` | ☐ |
| No flash | No flash of dashboard content before redirect | ☐ |

### Step 2.2: Brute Force Protection (Rate Limiting)
1. Enter username: `test_user` (does not exist).
2. Enter PIN: `0000`.
3. Click "Login" **5 times rapidly**.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Rate limit UI | Red error box appears | ☐ |
| Message | "Too many attempts. Try again in Xs." | ☐ |
| Button disabled | Login button disabled during cooldown | ☐ |

### Step 2.3: Registration Flow
1. Click "New Agent? Request Access".
2. Fill in: Username `QA_Tester`, PIN `1234`, Confirm `1234`, Country `ES`.
3. Click "Create Profile".

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Success animation | Checkmark animation plays | ☐ |
| Redirect | Goes to Onboarding, NOT Feed | ☐ |
| Current view | "Link Letterboxd" screen visible | ☐ |

### Step 2.4: Onboarding Limbo Guard (Critical)

**Test A — Skip Letterboxd link:**
Navigate manually to `/` while on "Link Letterboxd" screen.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Bounce back | Redirects back to "Link Letterboxd" | ☐ |
| No feed access | Cannot see any feed content | ☐ |

**Test B — Complete Letterboxd link:**
1. Enter a Letterboxd username (e.g., `dave`). Click Save.
2. Confirm screen shows large neon username and warning text.
3. Click "Yes, Link Account".

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Confirm UI | Large neon username displayed | ☐ |
| Smooth transition | Moves to "Upload Data" screen | ☐ |

**Test C — Skip upload:**
Navigate manually to `/` while on "Upload Data" screen.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Bounce back | Redirects back to "Upload Data" | ☐ |

**Test D — Complete upload:**
1. Drag & drop Letterboxd export ZIP. Click "Start Upload".

**Test E — Verify UI Data Flow (v1.4):**
1. After upload completes, verify Feed page loads immediately.
2. Verify "Recently Ingested" user context is correctly reflected in the sidebar profile.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Progress modal | Appears with step description | ☐ |
| Steps visible | "Processing export…", "Enriching…", "Clustering…" | ☐ |
| Completion | Auto-closes and redirects to Feed | ☐ |

> ❌ **FAIL if:** User can access Feed without completing all onboarding steps.

### Step 2.5: IDOR Access Control
1. Log in as `QA_Tester`. Note the user ID from DevTools → Network → any `/api/` request.
2. Copy the session cookie (`vectorbox_token`).
3. Open a new incognito window, register a second user `QA_Tester2`.
4. Using DevTools `fetch()` in `QA_Tester2`'s session, try to access `QA_Tester`'s data:

```js
fetch('/api/recommendations/feed?user_id=<QA_Tester_ID>')
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| No user_id param accepted | Returns data for QA_Tester2 (from token, not param) | ☐ |
| No 500 leaking data | Response is user2's data or 403 | ☐ |

---

## ✨ PHASE 3: Magic UI & Micro-Interactions

> **Goal:** Verify Tweak/Magic effects are all active and correct.

### Step 3.1: Auth Pages — Ambient Background
Navigate to `/login`.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| GridPattern | Subtle neon-green grid lines visible | ☐ |
| Radial fade | Grid fades at edges | ☐ |
| Ambient glow | Accent blur visible in corner | ☐ |

Navigate to `/register` — verify same effects.

### Step 3.2: ShimmerButton
On `/login`, observe the login button.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Shimmer effect | Light sweep across button periodically | ☐ |
| Color | Black bg, Neon Green text/border (#CCFF00) | ☐ |
| Hover inverts | Green bg, black text on hover | ☐ |
| Scale on hover | Slight scale-up (physics-based) | ☐ |

### Step 3.3: BorderBeam (Magic Search)
1. Log in. Open Magic Box (✦ icon in sidebar).
2. Observe the search input container.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| BorderBeam active | Neon green gradient rotating around input | ☐ |
| Continuous loop | Never pauses | ☐ |
| Deep Analysis toggle | Brain icon changes glow state | ☐ |

### Step 3.4: SpotlightCard (Movie Cards)
Hover over any movie poster in the Feed.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Spotlight | Radial glow follows mouse cursor inside card | ☐ |
| Poster zoom | Image scales ~110% on hover | ☐ |
| Border fixed | Card border does NOT scale | ☐ |
| Overflow hidden | Zoomed image stays inside card bounds | ☐ |

> ❌ **FAIL if:** Effects missing, wrong colors (blue/purple instead of neon green), or causes visual jank.

---

## 📱 PHASE 4: Mobile UX

> **Goal:** Verify responsive layout and touch interactions.

### Step 4.1: Enter Mobile Mode
1. Open DevTools (`F12`). Toggle Device Toolbar (`Ctrl+Shift+M`).
2. Select **iPhone SE** (375px).

### Step 4.2: Grid Layout

| Device | Expected Columns | Pass? |
|:-------|:-----------------|:-----:|
| iPhone SE (375px) | **1 column** | ☐ |
| iPhone 14 Pro (393px) | **2 columns** | ☐ |
| Desktop (1200px+) | **4 columns** | ☐ |

### Step 4.3: Hamburger Menu
Tap the ☰ icon in the header.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Overlay | Full-screen overlay | ☐ |
| Backdrop blur | `backdrop-blur-xl` visible | ☐ |
| Typography | Uppercase Space Mono | ☐ |
| Close | Tap X or outside → menu closes | ☐ |

### Step 4.4: Horizontal Feed Swipe
Swipe left on "Popular on Letterboxd" row.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Snap | Snaps to next item (scroll-snap) | ☐ |
| Smooth | No jank | ☐ |

---

## 🧠 PHASE 5: Algorithm & Feed Verification

> **Goal:** Verify the Trident engine, feed sections, and Magic Box work correctly.

### Step 5.0: QA Data Preparation (Deterministic Seed)
Before testing the algorithms, inject the controlled QA user profile to ensure deterministic feed populations.

```powershell
docker-compose exec backend python scripts/seed_qa_user.py
```

Log out of any existing sessions and **log in** using:
- **Username:** `qa_vecbox`
- **PIN:** `1234`

### Step 5.1: Feed Sections Present
Log in and navigate to the Feed. Verify all sections are rendered:

| Section | Present? |
|:--------|:--------:|
| Available Now (Watchlist streaming) | ☐ |
| Popular on Letterboxd | ☐ |
| Because you watched [X] | ☐ |
| Cult Actors (Auteur 2.0) | ☐ |
| Your Taste [Cluster] (e.g. "A24 Dread") | ☐ |
| Hidden Gems | ☐ |
| Comfort Zone / Wildcard | ☐ |

| Random Picks | ☐ |

### Step 5.2: Hidden Gems Audit
Click any 3 movies from the "Hidden Gems" row and verify:

> **Note:** Signal C now uses **DB-first discovery**. It queries the database for high-quality, low-popularity films before applying vector-weighted re-ranking. Thresholds are **dynamic** based on user history size.

| Metric | Required (Rich Profile) | Movie 1 | Movie 2 | Movie 3 |
|:-------|:---------|:-------:|:-------:|:-------:|
| VectorBox Score | > 70 | ☐ | ☐ | ☐ |
| Vote Count | 500–25,000 | ☐ | ☐ | ☐ |
| TMDB Popularity | < 20 | ☐ | ☐ | ☐ |

> ❌ **FAIL if:** A mainstream blockbuster (Avengers, Barbie, Oppenheimer) appears here.

### Step 5.3: Trident Signals — "Why Recommended"
Click any movie → "Why Recommended".

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Signal A (Vector) | Shows similarity to watched movies | ☐ |
| Signal Auteur | Shows director or cult actor match (if applicable) | ☐ |
| Signal C (Hidden Gems) | Shows hidden gem discovery signal | ☐ |
| Medoid Stability | For "Your Taste" cluster lists, verify the header uses a human-readable 2-4 word LLM label (e.g. "Neon-noir Revenge") rather than abstract math parameters | ☐ |

### Step 5.4: Magic Box NLP Query
Open Magic Box. Toggle "Deep Analysis" ON. Enter query: `Horror but NOT paranormal`

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Response time | < 8 seconds | ☐ |
| Results | Slasher / psychological horror | ☐ |
| Exclusion | No ghost/demon/possession films | ☐ |
| Standard Quality | Films are generally >65 VectorBox Score | ☐ |

**Trashy Intent Bypass Test:**
Enter query: `so bad it's good campy 80s horror`

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Bypass | `quality_gate_bypass` is triggered | ☐ |
| Results | Includes lower-scored films (e.g. 30-50 range) | ☐ |

**Additional query tests:**

| Query | Expected Response |
|:------|:-----------------|
| `80s movies with synth soundtrack` | Drive, Blade Runner, Tron-type results |
| `French romantic comedies` | Amélie etc., Language filter = FR |
| `Movies like Interstellar but not space` | Graceful fallback or abstract interpretation |


### Step 5.5: Background Task Enrichment
After loading the Feed, check backend logs:

```powershell
docker-compose logs --tail=50 backend
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Enrichment in BG | `Enriching movie [ID]...` appears AFTER feed response | ☐ |
| Trident Signal Timing | `[TRIDENT] Signal A/Auteur/C took Xms` logs visible | ☐ |
| Non-blocking | No "blocking" or "event loop" warnings | ☐ |

### Step 5.6: Chaos Monkey Fallback Verification
Ensure the LLM dual-model cascading fallback is functioning correctly and successfully triggers an explicit failure block if all Groq connections drop.

```powershell
docker-compose run --rm backend python scripts/verify_nlp_fallback.py
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Execution | Script runs without unhandled exceptions | ☐ |
| Output | `✅ FALLBACK CHAIN SUCCESS` is printed | ☐ |

### Step 5.7: Spanish Provider Whitelist
Ensure the isolated pure function accurately filters out unauthorized streaming providers.

```powershell
docker-compose run --rm backend python scripts/test_es_whitelist.py
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Execution | Script runs without assertion errors | ☐ |
| Output | `✅ WHITELIST FILTER SUCCESS` is printed | ☐ |

### Step 5.8: Feed Parallelism Verification (Automated)
Verify that the 10 feed tasks execute concurrently via `asyncio.gather`.

```powershell
docker-compose exec backend python scripts/verify_feed_parallelism.py
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Execution | Script runs without errors | ☐ |
| Concurrency | `✅ FEED PARALLELISM VERIFIED` — total time < 0.4s | ☐ |
| Session Isolation | 10 unique sessions (no sharing) confirmed | ☐ |

### Step 5.9: IDOR Automated Security Test
Verify `/hidden-gems` endpoint rejects unauthenticated and forged requests.

```powershell
docker-compose exec backend python scripts/test_idor_hidden_gems.py
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Unauthenticated | Returns 401 (Not Authenticated) | ☐ |
| Forged user_id | Returns 401 (user_id query param ignored) | ☐ |
| Output | `✅ IDOR PROTECTION VERIFIED` | ☐ |

### Step 5.10: Trident Math Verification
Verify sigmoid curve and RRF fusion calculations match expected formulas.

```powershell
docker-compose exec backend python scripts/test_trident_math.py
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Sigmoid | score=50→~0.09, score=65→0.50, score=80→~0.91 | ☐ |
| RRF | Movie in 3 lists scores higher than movie in 1 list | ☐ |
| Output | `✅ TRIDENT MATH VERIFIED` | ☐ |

### Step 5.11: RSS Sync Resilience (v1.4)
1. Link an account with active Letterboxd activity.
2. Trigger "Sync Letterboxd Now" from Settings.
3. Verify backend logs show successful XML parsing and NO 422 Unprocessable Entity errors.
4. Verify new movies appear in Watchlist/Feed.

### Step 5.12: Cluster Rotation Logic
1. Log in as `qa_vecbox`. Note the title of the "Your Taste" section (e.g., "Neo-Noir").
2. Clear Redis cache: `docker-compose exec redis redis-cli FLUSHALL`.
3. Refresh the page.
4. Verify the "Your Taste" section has rotated to a DIFFERENT cluster name (e.g., "80s Cyberpunk").

### Step 5.13: Movie Rejection (Anti-Vector)
1. Find a movie in your feed you don't like.
2. Click "Not Interested" (Rejection icon).
3. Verify the movie disappears immediately.
4. Refresh the page and verify it does NOT reappear in ANY Trident section (Vector, Taste, or Discovery).
5. Verify `user_ratings` table has `is_rejected = true` for that movie.

---

## 🌐 PHASE 6: Internationalization (i18n)

> **Goal:** Verify all user-facing strings are localized correctly.

### Step 6.1: Language Switch
1. In the sidebar or settings, switch language from EN to ES.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| All labels switch | Buttons, section titles, tooltips all in Spanish | ☐ |
| No raw keys | No `dashboard.system_ready` style raw keys visible | ☐ |
| Feed section names | "Porque viste…", "Joyas Ocultas", etc. | ☐ |

### Step 6.2: Date Localization
Check any movie with a recent release date.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Date format | Uses locale-appropriate format (DD/MM for ES, MM/DD for EN) | ☐ |
| Release warning | Shows "Estreno próximo" / "Coming Soon" correctly | ☐ |

---

## 🛡️ PHASE 7: Security Audit

> **Goal:** Verify all security tooling passes cleanly with no warnings.

### Step 7.1: Backend Dependency Audit (Hash-Verified)
```powershell
docker-compose exec backend python scripts/security_audit.py
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Finds lockfile | Prints `Found requirements.lock. Using hashed dependencies.` | ☐ |
| No extra warnings | **No** `"Consider using a tool like pip-compile"` warning | ☐ |
| Torch note | `Suppressed known 'torch+cpu not found on PyPI'` (expected, not an error) | ☐ |
| Exit code | `Security Audit Passed! No known vulnerabilities found.` | ☐ |

> ℹ️ The torch CPU-wheel note is expected — torch is served from the PyTorch wheel index, not PyPI, so PyPI's vulnerability DB cannot look it up. This is not a vulnerability.

### Step 7.2: Frontend Dependency Audit
```powershell
cd frontend && pnpm audit
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Result | `No known vulnerabilities found` | ☐ |
| No high/critical | Zero high or critical severity items | ☐ |

### Step 7.3: Frontend Install Clean
```powershell
cd frontend && pnpm install
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Zero peer dep warnings | No `WARN unmet peer` lines | ☐ |
| ESLint version | `eslint@9.x` installed | ☐ |

> ℹ️ ESLint is pinned to `9.x`. Do NOT upgrade to ESLint 10 until all plugins support it.

### Step 7.4: Pre-Commit Hook Dry Run
```powershell
git stash
git commit --allow-empty -m "test: QA dry run"
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| pnpm audit hook | Passes silently | ☐ |
| Backend audit hook | Passes silently | ☐ |
| Commit succeeds | No `--no-verify` needed | ☐ |

```powershell
git reset HEAD~1  # clean up test commit
```

### Step 7.5: Session Security
1. Log in as `QA_Tester`. Navigate to Feed.
2. DevTools → Application → Cookies → **Delete `vectorbox_token`**.
3. Refresh page.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Logout | Immediately redirects to `/login` | ☐ |
| localStorage cleared | `vectorbox_user` wiped | ☐ |

**Reverse (localStorage deleted, cookie kept):**
1. Log in. DevTools → Application → Local Storage → **Delete `vectorbox_user`**.
2. Refresh.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Still logged in | Stays on Feed (cookie is truth) | ☐ |
| Data restored | `vectorbox_user` re-populated from API | ☐ |

### Step 7.6: API Resilience
```powershell
docker-compose stop backend
```
Refresh frontend.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Graceful failure | `<AcidError />` component renders with "DATA_STREAM_INTERRUPTED" | ☐ |
| Custom styling | Acid design glitch text is applied | ☐ |
| No white screen | No unhandled React crash | ☐ |

```powershell
docker-compose start backend
```

### Step 7.7: HTTP Security Headers
```powershell
curl -I http://localhost:3000
```

| Header | Expected Value | Pass? |
|:-------|:--------------|:-----:|
| `X-Frame-Options` | `DENY` | ☐ |
| `X-Content-Type-Options` | `nosniff` | ☐ |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | ☐ |

---

## 🔭 PHASE 8: Observability (Jaeger)

> **Goal:** Confirm distributed traces flow correctly from backend to Jaeger.

### Step 8.1: Jaeger UI
Navigate to `http://localhost:16686`.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| UI loads | Jaeger search page visible | ☐ |
| Service listed | `vectorbox-backend` in Service dropdown | ☐ |

### Step 8.2: Trigger & Find a Trace
1. Log in and load the Feed (triggers recommendation engine).
2. In Jaeger: select `vectorbox-backend` → click "Find Traces".

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Traces found | ≥ 1 trace in list | ☐ |
| HTTP span | Root span shows `GET /api/recommendations/feed` | ☐ |
| DB spans | Child spans show SQLAlchemy queries | ☐ |
| Cache spans | Redis spans visible | ☐ |

### Step 8.3: Trident Signal Spans
Click a feed trace to expand the nested timeline.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Signal A span | `trident.signal_a.because_you_watched` visible | ☐ |
| Signal B span | `trident.signal_b.your_taste` visible | ☐ |
| Signal C span | `trident.signal_c.hidden_gems` visible | ☐ |
| Attributes | Each span has `user_id` + `result_count` | ☐ |

> ❌ **FAIL if:** No traces appear after 30 seconds, or Trident custom spans are missing.

---

## 💾 PHASE 9: Disaster Recovery

> **Goal:** Verify the Smart Backup System creates valid, host-persisted artifacts.

### Step 9.1: Execute Backup
**Windows:**
```powershell
./backup.ps1
```
**Linux/Mac:**
```bash
./backup.sh
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Execution | Completes without error | ☐ |
| Log | "Backup Pipeline Completed Successfully!" | ☐ |

### Step 9.2: Verify Host Persistence
Navigate to `./backups/` in project root.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Folder exists | `backups/` directory present | ☐ |
| ZIP exists | `vectorbox_backup_{timestamp}.zip` | ☐ |
| Non-empty | File size > 1 MB | ☐ |
| Git ignored | `git status` shows `backups/` NOT tracked | ☐ |

### Step 9.3: Verify Restoration (Dry Run)
Use the filename found in Step 9.2 to perform a dry run of the restoration script.

```powershell
docker-compose exec backend python scripts/restore_manager.py /app/backups/vectorbox_backup_{timestamp}.zip --dry-run
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Execution | Completes without error | ☐ |
| Preview Logs | Output properly previews destructive actions without executing them | ☐ |
| Status | Postgres, Qdrant, and Redis report `[DRY-RUN]` operations | ☐ |

---

## 🤖 PHASE 10: Automated E2E Suite

> **Goal:** Playwright automation confirms all core flows pass.

Prerequisites: App running via docker-compose up -d

> **Architecture:** The Playwright config uses a 3-stage project pipeline:
> 1. **Base projects** (3 browsers) run Phase 1, 2, 7 — no `storageState`
> 2. **Setup** runs `auth.setup.ts` → logs in as `qa_vecbox` → saves fresh `user.json`
> 3. **Authed projects** (3 browsers) run Phase 3, 4, 5, 12 — use `storageState`
>
> The `auth.setup.ts` must run AFTER Phase 7 (which rotates the token via `loginAs()`).

### Run full suite (all browsers):
  cd frontend && npx playwright test

### Run specific phase:
  cd frontend && npx playwright test phase2-auth
  cd frontend && npx playwright test phase5-feed
  cd frontend && npx playwright test --project="Mobile Safari"

### Run with UI (interactive):
  cd frontend && npx playwright test --ui

### View report:
  cd frontend && npx playwright show-report e2e/reports

| Test Suite            | Command                           | Expected |
|:----------------------|:----------------------------------|:---------|
| Phase 1 Infrastructure| playwright test phase1            | PASS     |
| Phase 2 Auth & IDOR   | playwright test phase2            | PASS     |
| Phase 3 Magic UI      | playwright test phase3            | PASS     |
| Phase 4 Mobile UX     | playwright test phase4            | PASS     |
| Phase 5 Feed & Search | playwright test phase5            | PASS     |
| Phase 7 Security      | playwright test phase7            | PASS     |
| Phase 12 Web Quality  | playwright test phase12           | PASS     |
| All (Desktop)         | playwright test --project=Desktop | PASS     |
| All (Mobile)          | playwright test --project=Mobile  | PASS     |

**Expected Totals:** 109 tests (106 pass, 3 skipped), ~3 min runtime.

> ❌ **FAIL if:** Any Playwright test reports `FAIL` or `ERROR`.

---

## 🗄️ PHASE 11: Database & Lockfile Integrity

> **Goal:** Ensure Postgres migrations, Qdrant indexes, and the hashed lockfile are correct.

### Step 11.1: Postgres Schema Check
```powershell
docker-compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\dt"
```

| Table | Exists? |
|:------|:-------:|
| `users` | ☐ |
| `movies` | ☐ |
| `user_ratings` | ☐ |
| `user_clusters` | ☐ |

### Step 11.2: Qdrant Collection Check
```powershell
curl http://localhost:6333/collections
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Collection exists | `movies` collection listed | ☐ |
| Vectors > 0 | `vectors_count` > 0 | ☐ |

### Step 11.3: Hashed Lockfile Freshness
```powershell
git log --oneline -- backend/requirements.txt backend/requirements.lock
```

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| In sync | `requirements.lock` was committed in the same commit as any `requirements.txt` change | ☐ |
| File exists | `backend/requirements.lock` exists on disk | ☐ |

> ⚠️ **IMPORTANT:** Any time `requirements.txt` is changed, the lockfile MUST be regenerated:
> ```powershell
> docker-compose exec backend pip-compile requirements.txt --generate-hashes -o requirements.lock
> ```
> Then commit both files together.

---

## 🎨 PHASE 12: Web Quality Audit (Addy Osmani)

> **Goal:** Ensure Frontend meets Core Web Vitals, Accessibility, and Best Practices.

### Step 12.1: Core Web Vitals (Performance)
1. Open DevTools (`F12`) → Network tab. Set throttling to **Fast 3G**.
2. Refresh the Feed page.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| LCP Priority | The first row of movie posters loads immediately without delays | ☐ |
| No Layout Shifts | `Space Mono` font loads without jumping the UI (CLS) | ☐ |

### Step 12.2: Accessibility (Keyboard & Contrast)
1. Tab through the movie feed.

| Check | Expected | Pass? |
|:------|:---------|:-----:|
| Keyboard Traps | Pressing `Enter` on `<button>` descendants (Flip, Why) does NOT trigger the parent link | ☐ |
| Screen Reader | Search bar announces "Loading" and "Results found" via `aria-live` | ☐ |
| Contrast | All small text (`text-[10px]`) is at least `zinc-400` on black backgrounds | ☐ |

---

## ✅ PHASE 13: Final Certification

Complete this scorecard only after all phases above pass.

| Phase | Description | Status |
|:------|:-----------|:------:|
| Phase 1 | Infrastructure & Health | ☐ |
| Phase 2 | Auth Guard & IDOR | ☐ |
| Phase 3 | Magic UI & Micro-interactions | ☐ |
| Phase 4 | Mobile UX | ☐ |
| Phase 5 | Algorithm & Feed | ☐ |
| Phase 6 | Internationalization | ☐ |
| Phase 7 | Security Audit (Backend + Frontend) | ☐ |
| Phase 8 | Observability / Jaeger | ☐ |
| Phase 9 | Disaster Recovery | ☐ |
| Phase 10 | Automated E2E Suite | ☐ |
| Phase 11 | DB & Lockfile Integrity | ☐ |
| Phase 12 | Web Quality Audit (Addy Osmani) | ☐ |

**Certification Decision:**

- [ ] **✅ APPROVED** — All phases pass. Ready for `git tag`.
- [ ] **❌ BLOCKED** — Critical failures documented below. Requires fix and re-test.

**Failures (if any):**

| Phase | Step | Description |
|:------|:-----|:-----------|
| | | |

---

**Signed:** _______________________
**Date:** _______________________

---
*End of QA Testing Protocol — VectorBox v1.7.2*
