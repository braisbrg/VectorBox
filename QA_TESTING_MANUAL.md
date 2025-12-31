# QA Manual: VectorBox v1.0 ("The Trident")

**Role:** QA Lead
**Date:** 2025-12-31
**Version:** Gold Master Candidate 1

This document outlines the manual verification steps required to certify the **VectorBox** application for release. It covers Infrastructure, Visuals (Acid Design), and Logic (Trident Engine).

---

## 1. Prerequisites (Infrastructure Check)

Before testing visuals, ensuring the engine is running.

1.  **Clean Start:**
    ```bash
    docker-compose down
    docker-compose up --build -d
    ```
2.  **Verify Containers:**
    Run `docker ps`. You should see 5 healthy containers:
    *   `cinematch-frontend` (Port 3000)
    *   `cinematch-backend` (Port 8000)
    *   `qdrant` (Port 6333)
    *   `redis` (Port 6379)
    *   `postgres` (Port 5432)

3.  **Log Check:**
    Run `docker-compose logs -f backend`. Ensure you see `"Application startup complete."` and **no repeated connection errors** to Qdrant or Postgres.

---

## 2. Visual "Acid Test" (The Aesthetic)

VectorBox uses the **Acid Design** system. Verify these non-negotiables:

### 🎨 Desktop
*   **Palette:** Deep Black (`#09090b` or similar) backgrounds. **NO** pure white backgrounds.
*   **Accent:** Neon Acid Green (`#CCFF00`) used for active states (toggles, buttons), shadows, and borders on focus.
*   **Typography:**
    *   Headings/Data: `Space Mono` (Monospace, technical feel).
    *   Body: `Inter` or `Space Grotesk` (Readable sans-serif).
*   **Badges:** "VB Score" badges should be purple/gradient, distinct from the Neon Green UI accents.

### 📱 Mobile (Critical)
*   **Navigation:**
    *   Hamburger menu icon visible on the top-right.
    *   **Action:** Tap the Hamburger.
    *   **Result:** A **full-screen overlay** should fade in with a backdrop blur (`backdrop-blur-xl`). Menu items should be large, centered, and uppercase `Space Mono`.
*   **Horizontal Feed:**
    *   Swipe left/right on "Popular on Letterboxd".
    *   **Feel:** It should snap to items (scroll-snap).
    *   **Arrows:** Should be hidden (touch-native) or non-intrusive.
*   **Grid:**
    *   Navigating to "Explore/Grid" view.
    *   Cards should be **1 column wide** (big posters) on narrow phones, and **2 columns** on larger phones. **NOT** 4 columns like desktop.

---

## 3. Functional Walkthrough

### A. Onboarding (First Run)
*   **Action:** Go to Settings -> "Upload Data".
*   **Test:** Drag & Drop `ratings.csv` (Letterboxd export).
*   **Verification:**
    *   Progress bar appears ("Analyzing Taste Profiles...").
    *   Toast notification on success: "Imported X ratings".
    *   **Time:** Should take < 1 minute for ~500 ratings.

### B. The Feed (Home)
Refresh the page (`F5`).

1.  **Row: "Popular on Letterboxd"**
    *   **Visual:** ~72 items.
    *   **Data:** Hover over a card. It should have a **Letterboxd Rating** (Star icon, e.g., 4.2).
    *   **Logic:** These are the *actual* trending movies this week. Click one -> Opens Letterboxd URL.

2.  **Row: "Hybrid Picks for You" (The Trident)**
    *   **Logic:** These are personalized.
    *   **Verification:** Spot check a movie. Is it directed by someone you rated highly? (Signal B). Or is it visually/thematically similar to your recent watches? (Signal A).

3.  **Row: "Hidden Gems"**
    *   **Data:** Click "Info" or check details.
    *   **Vote Count:** Must be between **1,000 and 25,000** votes.
    *   **Rating:** Must be > 7.0/10.
    *   **Goal:** No massive blockbusters (Avengers) and no obscure student films (5 votes).

4.  **Row: "Deep Dive"**
    *   **Logic:** Pure content similarity. "Since you watched X".
    *   **Check:** Does the reasoning make sense? "Because you watched *Alien* -> *Life*".

### C. The "Magic Box" (NLP Search)
*   **UI:** Click the "Sparkles" icon or Search bar.
*   **Input:** Type *"Sad 90s cyberpunk anime"*.
*   **Toggle:** Switch "Deep Analysis" **ON** (uses Llama 3.3).
*   **Result:**
    *   Response should appear in ~3-5 seconds.
    *   Should map to specific genres ("Animation", "Sci-Fi") and years ("1990-1999").
    *   Should list movies like *Ghost in the Shell*, *Evangelion*, *Akira*.

### D. Sync & Tools
*   **RSS Sync:**
    *   Go to Settings. Click "Sync RSS".
    *   Should match your recently watched movies on Letterboxd to the internal DB.
    *   Verify: "Last Sync: Just now".

---

## 4. Error States (Resilience)

1.  **API Down:**
    *   *Simulate:* Stop the backend (`docker-compose stop backend`).
    *   *Action:* Refresh Frontend.
    *   *Result:* Should show a graceful "System Offline" or Skeleton Loaders that eventually timeout to a "Retry" button. **NO** white screen of death.

2.  **Empty Data:**
    *   *Simulate:* New user with no uploads.
    *   *Result:* Feed should show "Upload your data to get started" or generic popular recommendations, not crash.

---

**End of QA Manual**
