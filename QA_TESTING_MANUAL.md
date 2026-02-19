# VectorBox v1.2 QA Testing Protocol

> **Role:** QA Lead / Release Certification  
> **Version:** 1.2.0 ("The Magic Update")  
> **Last Updated:** 2026-01-13

This document is a **step-by-step verification script** required to certify the VectorBox application for release. Each section must be completed in order. A **single FAIL** blocks the release.

---

## 📋 Pre-Flight Checklist

| # | Item | Status |
|:--|:-----|:------:|
| 1 | Docker Desktop Running | ☐ |
| 2 | `.env` file exists with valid API keys | ☐ |
| 3 | Chrome/Firefox with DevTools access | ☐ |
| 4 | Letterboxd Export ZIP (ratings.csv, watchlist.csv) | ☐ |

---

## 🔴 PHASE 1: Infrastructure — "The Hard Reset"

> **Goal:** Simulate a fresh install. Ensure all data is wiped and rebuilt from scratch.

### Step 1.1: Data Wipe
```powershell
# From project root directory
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

**Verification Checkpoints:**

| Checkpoint | Log Message | Pass? |
|:-----------|:------------|:-----:|
| **DB Ready** | `✅ Database Schema Applied` | ☐ |
| **Seeding** | `✅ Database Seeded & Collection Created` | ☐ |
| **Qdrant Indexes** | `✅ Qdrant Indexes Created` | ☐ |
| **Trending** | `✅ Trending Movies Updated` | ☐ |
| **Final** | `🎉 VectorBox is ready and running!` | ☐ |

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

---

## 🔐 PHASE 2: Auth Guard Gauntlet

> **Goal:** Verify auth flow is bulletproof. No "limbo" states.

### Step 2.1: Fresh Entry (Incognito)
1. Open **Chrome Incognito** (`Ctrl+Shift+N`).
2. Navigate to `http://localhost:3000`.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Redirect | Immediately redirects to `/login` | ☐ |
| No Flash | No flash of dashboard content before redirect | ☐ |

### Step 2.2: Brute Force Protection
1. Enter username: `test_user` (does not exist).
2. Enter PIN: `0000`.
3. Click "Login" **5 times rapidly**.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Rate Limit UI | Red error box appears | ☐ |
| Message | "Too many attempts. Try again in Xs." | ☐ |
| Button State | Login button disabled during cooldown | ☐ |

### Step 2.3: Registration Flow
1. Click "New Agent? Request Access".
2. Fill form:
   - Username: `QA_Tester`
   - PIN: `1234`
   - Confirm PIN: `1234`
   - Country: `ES`
3. Click "Create Profile".

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Success Animation | Checkmark animation plays | ☐ |
| Redirect | Redirects to Onboarding (NOT Feed) | ☐ |
| Current View | "Link Letterboxd" screen visible | ☐ |

### Step 2.4: The "Limbo" Check (Critical v1.1 Test)

**Test A: Skip Letterboxd Link**
1. While on "Link Letterboxd" screen, manually navigate to `/` in URL bar.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Bounce Back | Redirects back to "Link Letterboxd" | ☐ |
| No Feed Access | Cannot see any feed content | ☐ |

**Test B: Complete Linking**
1. Enter Letterboxd username (e.g., `dave`).
2. Click "Save" → **Confirmation screen appears**.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Confirm UI | Large neon username displayed | ☐ |
| Warning Text | "Are you sure this is your correct..." | ☐ |

3. Click "Yes, Link Account".

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Transition | Smoothly transitions to "Upload Data" screen | ☐ |

**Test C: Skip Upload**
1. While on "Upload Data" screen, manually navigate to `/`.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Bounce Back | Redirects back to "Upload Data" | ☐ |

**Test D: Complete Upload**
1. Drag & drop Letterboxd export ZIP.
2. Click "Start Upload".

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Progress Modal | Appears with step description | ☐ |
| Steps Visible | Shows "Processing export...", "Enriching...", "Clustering..." | ☐ |
| Completion | Auto-closes and redirects to Feed | ☐ |

> ❌ **FAIL if:** User can access Feed without completing all onboarding steps.

---

## ✨ PHASE 2.5: Magic UI & Micro-interactions

> **Goal:** Verify the "Quantum Leap" UI upgrade (v1.2). Ensure all Tweak/Magic effects are active.

### Step 2.5.1: Ambient Backgrounds (Auth Pages)
1. Navigate to `/login`.
2. Look at the background behind the login form.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Not Flat Black | Background shows subtle visual depth | ☐ |
| GridPattern Visible | Subtle grid lines (neon green tint) fade from center | ☐ |
| Radial Fade | Grid fades out at edges (`mask-image: radial-gradient`) | ☐ |
| Ambient Glow | Primary accent blur visible in corner (green or purple) | ☐ |

3. Navigate to `/register` and verify same effects.

### Step 2.5.2: Interactive Buttons (ShimmerButton)
1. While on `/login`, observe the "Login" button.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Shimmer Effect | Light reflection sweeps across button periodically | ☐ |
| Color Scheme | Black background, Neon Green text/border (#CCFF00) | ☐ |
| Hover State | Button inverts (green bg, black text) on hover | ☐ |
| Scale Animation | Slight scale-up on hover (physics-based) | ☐ |

2. Navigate to `/register` and verify button has shimmer (purple variant).

### Step 2.5.3: The "Alive" Search Bar (BorderBeam)
1. Log in and navigate to Feed.
2. Click on "AI Search" or Magic Box in the sidebar.
3. Observe the search input container.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| BorderBeam Active | Neon Green gradient visibly rotating around input | ☐ |
| Continuous Animation | Beam loops indefinitely (no pause) | ☐ |
| Terminal Aesthetic | Input resembles a sci-fi "processing" terminal | ☐ |
| Deep Analysis Toggle | Brain icon toggles enhanced glow state | ☐ |

### Step 2.5.4: Movie Card Physics (SpotlightCard + Zoom)
1. Navigate to Feed or Grid view.
2. Hover over any movie poster.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| **Spotlight Effect** | Radial glow follows your mouse cursor INSIDE the card | ☐ |
| Spotlight Color | Subtle neon green tint (rgba(204, 255, 0, ~0.06)) | ☐ |
| **Poster Zoom** | Image scales up smoothly (~110%) on hover | ☐ |
| Border Fixed | Card border does NOT scale (only image zooms) | ☐ |
| Overflow Hidden | Zoomed image does not overflow card bounds | ☐ |
| Grayscale Filter | Image becomes desaturated on hover (cinematic effect) | ☐ |

3. Test on at least 3 different cards in different sections.

> ❌ **FAIL if:** Any of the Magic UI effects are missing, using wrong colors (blue/purple), or causing performance issues.

---

## 📱 PHASE 3: Mobile UX Verification

> **Goal:** Verify responsive design and touch interactions.

### Step 3.1: Enter Mobile Mode
1. Open Chrome DevTools (`F12` or `Ctrl+Shift+I`).
2. Toggle Device Toolbar (`Ctrl+Shift+M`).
3. Select **"iPhone SE"** from device dropdown.
4. Refresh page (`F5`).

### Step 3.2: Hamburger Menu
1. Tap the hamburger icon (☰) in header.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Overlay | Full-screen overlay appears | ☐ |
| Backdrop | Uses `backdrop-blur-xl` (visible blur effect) | ☐ |
| Typography | Menu items in uppercase `Space Mono` | ☐ |
| Centering | Items are vertically centered | ☐ |
| Close | Tap "X" or outside → menu closes | ☐ |

### Step 3.3: Grid Layout Verification

| Device | Expected Columns | Pass? |
|:-------|:-----------------|:-----:|
| iPhone SE (375px) | **1 column** | ☐ |
| iPhone 14 Pro (393px) | **2 columns** | ☐ |
| Desktop (1200px+) | **4 columns** | ☐ |

### Step 3.4: Horizontal Feed Swipe
1. Navigate to Feed.
2. Swipe left on "Popular on Letterboxd" row.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Snap | Snaps to next item (scroll-snap) | ☐ |
| Arrows | Hidden or non-intrusive on touch | ☐ |
| Smooth | No jank or stuttering | ☐ |

---

## 🧠 PHASE 4: Algorithm & Logic Verification

> **Goal:** Verify "Trident" engine and "Hidden Gems" filtering logic.

### Step 4.1: Hidden Gems Audit
1. Navigate to Feed.
2. Locate "Hidden Gems" row.
3. Click on **any 3 movies** and check their details.

**Pass Criteria (ALL must be true for each movie):**

| Metric | Required Value | Movie 1 | Movie 2 | Movie 3 |
|:-------|:---------------|:-------:|:-------:|:-------:|
| VectorBox Score | > 75 | ☐ | ☐ | ☐ |
| Vote Count | 500 - 25,000 | ☐ | ☐ | ☐ |
| TMDB Popularity | < 20 | ☐ | ☐ | ☐ |

> ❌ **FAIL if:** Any mainstream blockbuster (Avengers, Barbie, Oppenheimer) appears in this row.

### Step 4.2: Trident Hybrid Picks Audit
1. Locate "Hybrid Picks for You" row.
2. Click any movie and view "Why Recommended".

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Signal A (Vector) | Shows similarity to watched movies | ☐ |
| Signal B (Auteur) | Shows director match (if applicable) | ☐ |
| Signal C (Crowd) | Shows crowd/popularity signal | ☐ |

### Step 4.3: Magic Box Stress Test
1. Open Magic Box (Sparkles icon).
2. Toggle "Deep Analysis" **ON**.
3. Enter query: `Horror but NOT paranormal`

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Response Time | < 8 seconds | ☐ |
| Results | Show slasher/psychological horror | ☐ |
| Exclusion | NO ghosts, demons, or possession films | ☐ |

**Additional Query Tests:**

| Query | Expected Behavior |
|:------|:------------------|
| `80s movies with synth soundtrack` | Returns Drive, Blade Runner, Tron |
| `French romantic comedies` | Returns Amélie, etc. Filter: Language=FR |
| `Movies like Interstellar but not space` | Should fail gracefully or interpret as abstract |

---

## 🛡️ PHASE 5: Security & Resilience

### Step 5.1: Dependency Audit
```powershell
docker-compose exec backend python scripts/security_audit.py
```

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Audit Runs | Script executes without crash | ☐ |
| Known Issues | Only `torch` CPU warnings (expected) | ☐ |
| Exit Code | `0` or advisory-only | ☐ |

### Step 5.2: Session Persistence Test
1. Log in as `QA_Tester`.
2. Navigate to Feed.
3. Open DevTools → Application → Cookies.
4. **Delete `vectorbox_token` cookie.**
5. Refresh page.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Logout | Immediately redirects to `/login` | ☐ |
| localStorage | `vectorbox_user` is cleared | ☐ |

### Step 5.3: localStorage Fallacy Test
1. Log in as `QA_Tester`.
2. Open DevTools → Application → Local Storage.
3. **Delete `vectorbox_user` entry** (keep cookie).
4. Refresh page.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Still Logged In | Remains on Feed (cookie is truth) | ☐ |
| Data Restored | `vectorbox_user` re-populated from API | ☐ |

### Step 5.4: API Resilience
```powershell
docker-compose stop backend
```
1. Refresh frontend.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Graceful Failure | Shows skeleton loaders or "System Offline" | ☐ |
| No Crash | No white screen of death | ☐ |
| Retry Button | Appears after timeout | ☐ |

```powershell
docker-compose start backend
```

---

## 💾 PHASE 6: Disaster Recovery Test

> **Goal:** Verify the "Smart Backup System" creates valid artifacts and persists them to the host.

### Step 6.1: Execute Backup
1. **Windows:** Run `./backup.ps1`
2. **Mac/Linux:** Run `./backup.sh`

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Execution | Script runs without error code | ☐ |
| Logs | "Backup Pipeline Completed Successfully!" | ☐ |

### Step 6.2: Host Persistence Verification
1. Open file explorer on your Host machine.
2. Navigate to the project root -> `backups/`.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Folder Exists | `backups/` directory exists | ☐ |
| Zip File | A file named `vectorbox_backup_{timestamp}.zip` exists | ☐ |
| Non-Empty | File size > 0 KB (likely > 1MB) | ☐ |
| Clean Repo | Run `git status` -> `backups/` is IGNORED (not shown) | ☐ |

---

## 🔭 PHASE 7: Observability Verification (Jaeger)

> **Goal:** Confirm distributed traces are flowing from the backend to Jaeger.

### Step 7.1: Open Jaeger UI
1. Navigate to `http://localhost:16686` in your browser.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| UI Loads | Jaeger search page is visible | ☐ |
| Service Listed | `vectorbox-backend` appears in the "Service" dropdown | ☐ |

### Step 7.2: Trigger a Traced Request
1. Log in and navigate to the Feed (triggers recommendation engine).
2. In Jaeger, select service `vectorbox-backend` and click **"Find Traces"**.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Traces Appear | At least 1 trace visible in the list | ☐ |
| HTTP Span | Root span shows `GET /api/recommendations/feed` | ☐ |
| DB Spans | Child spans show SQL queries (SQLAlchemy) | ☐ |

### Step 7.3: Trident Signal Visibility
1. Click on a feed trace to expand the nested timeline.

| Check | Expected Result | Pass? |
|:------|:----------------|:-----:|
| Signal A Span | `trident.signal_a.because_you_watched` visible | ☐ |
| Signal B Span | `trident.signal_b.your_taste` visible | ☐ |
| Signal C Span | `trident.signal_c.hidden_gems` visible | ☐ |
| Attributes | Each span shows `user_id` and `result_count` | ☐ |

### Step 7.4: Background Task Verification
1.  Check backend logs after loading the feed.
2.  Look for `Enriching movie [ID]...` messages appearing *after* the feed has apparently loaded.
3.  Confirm no "Blocking" warnings in logs.

> ❌ **FAIL if:** No traces appear after 30 seconds, or Trident spans are missing.

---

## ✅ PHASE 8: Final Certification

| Phase | Status |
|:------|:------:|
| Phase 1: Infrastructure | ☐ |
| Phase 2: Auth Guard | ☐ |
| Phase 2.5: Magic UI | ☐ |
| Phase 3: Mobile UX | ☐ |
| Phase 4: Algorithm Logic | ☐ |
| Phase 5: Security | ☐ |
| Phase 6: Disaster Recovery | ☐ |
| Phase 7: Observability | ☐ |

**Certification Decision:**

- [ ] **APPROVED** – All phases pass. Ready for `git tag v1.2.0`.
- [ ] **BLOCKED** – Critical failures documented. Requires fix and re-test.

---

**Signed:** ________________________  
**Date:** ________________________

---
*End of QA Testing Protocol*
