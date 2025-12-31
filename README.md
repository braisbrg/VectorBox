# 🎬 VectorBox (v1.0)
> *The "Trident" Engine: Semantic. Auteur. Crowd.*

**VectorBox** (formerly CineMatch AI) is a next-generation movie recommendation platform that fuses **AI Semantic Search**, **Auteur/Director Bias**, and **Crowd Wisdom** into a single, cohesive feed.

Built with **Acid Design** aesthetics (`#CCFF00`), it offers a premium, mobile-first experience for power users of Letterboxd.

---

## 🌟 Key Features

### 🔱 The "Trident" Engine
Our hybrid recommendation algorithm uses **Reciprocal Rank Fusion (RRF)** to combine three signals:
1.  **Signal A (Vibe):** Vector search (Qdrant) finds movies with similar plots and themes to your history.
2.  **Signal B (Auteur):** Explicitly boosts movies from directors you love (5-star history).
3.  **Signal C (Crowd):** Collaborative filtering from TMDB ("People who liked X also liked Y").

### 🧠 "Magic Box" Neural Search
Describe exactly what you want: *"Depressing 90s anime that feels like Ghost in the Shell"*.
- **Tier 1:** Fast search (groq/llama-4-scout).
- **Tier 2 (Deep Analysis):** Complex reasoning (llama-3.3-70b) to deconstruct abstract requests.

### 📱 Acid Design
- **Mobile First:** Full-screen touch menus, snap-scrolling, and responsive grids.
- **Aesthetic:** High-contrast Neon Green on Deep Black. `Space Mono` typography.

---

## 🏗️ Architecture

- **Backend:** FastAPI (Async), SQLAlchemy (AsyncPG), Pydantic.
- **Frontend:** Next.js 14 (App Router), TailwindCSS, Framer Motion.
- **Database:** PostgreSQL (Metadata) + Qdrant (Vectors).
- **AI:** CPU-Optimized PyTorch (`all-MiniLM-L6-v2`) + Groq API.
- **Infrastructure:** Docker Compose with Multi-Stage builds.

---

## 🚀 Quick Start

### 1. Requirements
*   Docker & Docker Compose
*   TMDB API Key
*   Groq API Key (Optional, for Magic Box)

### 2. Install
```bash
git clone https://github.com/your-repo/VectorBox.git
cd VectorBox

# Set up Environment
cp .env.example .env
# Edit .env and add your TMDB_API_KEY
```

### 3. Run
```bash
docker-compose up --build
```
*   **Frontend:** `http://localhost:3000`
*   **Backend:** `http://localhost:8000`

### 4. Ingest Logic
1.  **Upload:** Drop your `ratings.csv` export from Letterboxd.
2.  **Cluster:** The system runs K-Means to identify your "Taste Clusters" (e.g., "80s Horror").
3.  **Explore:** Browse the "Hybrid Picks" feed.

---

## 📁 Project Structure

```
VectorBox/
├── backend/            # FastAPI implementation
│   ├── scripts/        # Maintenance (seed_db, scraper)
│   ├── services/       # Core Logic (The Trident)
│   └── routers/        # API Endpoints
├── frontend/           # Next.js App
│   ├── components/     # UI Components (Acid Design)
│   └── lib/            # API Clients
└── docker-compose.yml  # Orchestration
```

## 🛡️ Credits

*   **TMDB** for metadata.
*   **Letterboxd** for the community data structure.
*   **Qdrant** for vector search.

**v1.0 Gold Master**
