# 🎬 CineMatch AI

**Advanced Movie Recommendation System** powered by Machine Learning, Semantic Search, and Letterboxd Data.

## 🌟 Features

### Core Capabilities
- **Mood-Based Recommendations**: K-Means clustering identifies your distinct taste profiles (e.g., "80s Horror", "French Drama")
- **Semantic Search**: Sentence transformers understand movie themes beyond simple genre matching
- **Streaming Integration**: Filter recommendations by your streaming services (Netflix, HBO, etc.) via JustWatch/TMDB
- **Group Watchlist**: Find movie intersections across multiple users' watchlists
- **Compatibility Test**: Calculate taste similarity between users using cosine similarity

### Technical Highlights
- **Dynamic Clustering**: `n_clusters = min(5, max(2, total_movies // 20))`
- **Hybrid Recommendation Engine**: Combines clustering, vector search, and hard filters
- **Production-Grade Security**: Rate limiting, CORS, input validation, non-root containers
- **Intelligent Caching**: Redis caching (24h for watch providers, 7d for metadata)

---

## 🏗️ Architecture

### Tech Stack

**Backend**
- FastAPI (Python 3.11+)
- PostgreSQL (relational data)
- Qdrant (vector database)
- Redis (caching)
- Sentence-Transformers (`all-MiniLM-L6-v2`)
- Scikit-learn (K-Means clustering)

**Frontend**
- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS + Shadcn/UI
- Framer Motion
- React Query

**Infrastructure**
- Docker Compose orchestration
- Non-root containers
- Health checks

---

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- TMDB API Key (provided in `.env`)

### 1. Clone & Setup

```bash
git clone <your-repo>
cd cinematch-ai
cp .env.example .env
```

### 2. Start Services

```bash
docker-compose up --build
```

This will start:
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/api/docs
- PostgreSQL (port 5432)
- Qdrant (port 6333)
- Redis (port 6379)

### 3. Upload Your Letterboxd Data

1. Export your data from [Letterboxd Settings → Data](https://letterboxd.com/settings/data/)
2. Upload `ratings.csv` via the frontend
3. Wait for clustering to complete (~30s for 1000 movies)
4. Select a mood and get recommendations!

---

## 🔒 Security Features

### Backend
- ✅ Rate limiting (SlowAPI) - 5 uploads/min, 40 TMDB requests/10s
- ✅ CORS with strict origin control
- ✅ Trusted Host middleware
- ✅ Input validation (Pydantic with regex, length limits)
- ✅ File upload sanitization (10MB limit, CSV validation)
- ✅ SQL injection prevention (SQLAlchemy ORM)
- ✅ Security headers (X-Frame-Options, CSP, HSTS)
- ✅ Non-root Docker user

### Frontend
- ✅ Next.js security headers
- ✅ TMDB image optimization
- ✅ XSS protection via React
- ✅ Environment variable isolation

### Dependency Management
- **Strict Versioning**: All dependencies are pinned to specific versions to prevent supply chain attacks via semantic version sliding.
- **Frontend Audit**: Run `npm run security-check` to scan for vulnerabilities.
- **Backend Audit**: Run `.\audit_backend.ps1` to scan Python dependencies.
- **CI/CD**: Docker builds automatically fail if vulnerabilities are detected.

### How to Update Dependencies
1. **Frontend**:
   ```bash
   cd frontend
   npm update <package>
   # Manually update package.json to remove ^ or ~
   npm run security-check
   ```
2. **Backend**:
   ```bash
   cd backend
   pip install <package> --upgrade
   pip freeze > requirements.txt
   .\audit_backend.ps1
   ```

---

## 📁 Project Structure

```
cinematch-ai/
├── backend/
│   ├── main.py                 # FastAPI app with security middleware
│   ├── config.py               # Database configuration
│   ├── models/
│   │   ├── database.py         # SQLAlchemy models
│   │   └── schemas.py          # Pydantic schemas
│   ├── services/
│   │   ├── tmdb_client.py      # TMDB API wrapper + Redis caching
│   │   ├── csv_parser.py       # Letterboxd CSV parser
│   │   ├── embedding_service.py # Sentence-Transformers
│   │   ├── clustering_service.py # K-Means clustering logic
│   │   └── qdrant_service.py   # Vector database operations
│   ├── routers/
│   │   ├── upload.py           # CSV upload endpoints
│   │   ├── recommendations.py  # Recommendation endpoints
│   │   └── tools.py            # Group watchlist, compatibility
│   └── requirements.txt
├── frontend/
│   ├── app/
│   │   ├── layout.tsx          # Root layout
│   │   ├── page.tsx            # Homepage
│   │   └── globals.css         # Tailwind + Shadcn theme
│   ├── components/
│   │   ├── upload-zone.tsx     # Drag-and-drop upload
│   │   ├── mood-selector.tsx   # Cluster selection
│   │   └── recommendation-grid.tsx # Movie grid
│   ├── lib/
│   │   ├── api.ts              # API client
│   │   └── utils.ts            # Utilities
│   └── package.json
├── docker-compose.yml
└── .env
```

---

## 🧪 API Endpoints

### Upload
- `POST /api/upload/ratings` - Upload ratings.csv
- `POST /api/upload/watched` - Upload watched.csv

### Recommendations
- `GET /api/recommendations/clusters/{user_id}` - Get user's taste clusters
- `POST /api/recommendations/by-mood` - Get recommendations for a cluster

### Tools
- `POST /api/tools/group-watchlist` - Multi-user watchlist intersection
- `POST /api/tools/compatibility` - User compatibility score

---

## 🎯 How It Works

### 1. Data Ingestion
- User uploads Letterboxd CSV
- Backend parses and validates data
- Fetches metadata from TMDB (title, genres, synopsis, keywords)
- Generates embeddings using `all-MiniLM-L6-v2`
- Stores vectors in Qdrant

### 2. Clustering (Solving the "Average Taste" Problem)
Instead of averaging all likes into one vector:
- Retrieves all user's rated movies
- Weights vectors by rating (5 stars = 1.0, 0.5 stars = 0.1)
- Calculates optimal clusters: `n = min(5, max(2, movies // 20))`
- Runs K-Means clustering
- Labels clusters by dominant genres + era (e.g., "90s Action/Thriller")

### 3. Recommendations
- User selects a "mood" (cluster)
- Computes cluster center vector (mean of cluster movies)
- Performs vector search in Qdrant
- Applies hard filters (year, genre, runtime)
- Checks streaming availability via TMDB
- Returns top N similar movies not yet watched

---

## 🔧 Configuration

### Environment Variables

```env
# TMDB API (provided)
TMDB_API_KEY=6076974c7c4a3e31be5c972d5c842f40
TMDB_READ_TOKEN=eyJhbGciOiJIUzI1NiJ9...

# Database
DATABASE_URL=postgresql://cinematch_user:cinematch_pass@localhost:5432/cinematch

# Redis
REDIS_URL=redis://localhost:6379

# Qdrant
QDRANT_URL=http://localhost:6333

# Default Country (ISO 3166-1 alpha-2)
DEFAULT_COUNTRY=ES
```

---

## 📊 Performance Benchmarks

- CSV Processing: <30s for 1000 movies
- Clustering: <10s for 500 rated movies
- Recommendation Query: <2s with filters
- Frontend Load: <3s initial

---

## 🛠️ Development

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Run Tests

```bash
cd backend
pytest tests/
```

---

## 🚢 Deployment

### Production Checklist
- [ ] Change `JWT_SECRET` in `.env`
- [ ] Update CORS origins in `backend/main.py`
- [ ] Update Trusted Hosts in `backend/main.py`
- [ ] Set `ENVIRONMENT=production`
- [ ] Use managed PostgreSQL/Redis
- [ ] Configure SSL/TLS
- [ ] Set up monitoring (Sentry, Datadog)

---

## 📝 License

MIT License - See LICENSE file

---

## 🙏 Acknowledgments

- **TMDB** for movie metadata and streaming data
- **Letterboxd** for the amazing movie tracking platform
- **Sentence-Transformers** for semantic embeddings
- **Qdrant** for vector search capabilities

---

**Built with ❤️ for movie enthusiasts**
