# 🚀 VectorBox Deployment Guide (v1.1 Gold Master)

This guide covers deploying VectorBox on a **Raspberry Pi 5** (or similar Linux server).

**Recommended Architecture: Hybrid**
- **Frontend:** Hosted on [Vercel](https://vercel.com) (Global CDN, fast UI).
- **Backend/DBs:** Hosted on **Raspberry Pi** (Data sovereignty, Cost/Efficiency).
- **Connectivity:** Secured via [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/) (No port forwarding required).

---

## 1. Preparation (The Move)

### A. Environment Check
Ensure your Raspberry Pi is running a 64-bit OS (Raspberry Pi OS Lite 64-bit is recommended).

### B. Increase Swap Space (CRITICAL)
Qdrant and Postgres can be memory-intensive during initial ingestion. Increase swap to prevent crashes.

```bash
# Edit swap configuration
sudo nano /etc/dphys-swapfile

# Change CONF_SWAPSIZE to 4096 (4GB)
CONF_SWAPSIZE=4096

# Apply changes
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

### C. Clone & Config
```bash
# Clone repository
git clone https://github.com/your-username/LetterboxRecommender.git vectorbox
cd vectorbox

# Create Production Environment File
cp .env.example .env
nano .env
```

**Key Production Variables (.env):**
```ini
ENVIRONMENT=production
# Connection Strings (Docker Internal)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/vectorbox
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333

# Security
JWT_SECRET=generate_a_strong_secret_here_openssl_rand_base64_32
ALLOWED_ORIGINS=https://your-vercel-app.vercel.app,http://localhost:3000

# API Keys (MUST BE SET)
TMDB_API_KEY=...
TMDB_READ_TOKEN=...
OMDB_API_KEY=...
GROQ_API_KEY=...
```

---

## 2. Hybrid Setup (Recommended)

### A. Deploy Backend (Raspberry Pi)
Run the backend, database, vector DB, and cache services.

```bash
# Build and run containers (excluding frontend)
docker-compose up -d --build backend postgres qdrant redis
```

**Verify Status:**
```bash
docker-compose ps
docker stats
```

### B. Setup Cloudflare Tunnel
Expose your local backend securely without opening router ports.

1.  **Install Cloudflared:**
    ```bash
    curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
    sudo dpkg -i cloudflared.deb
    ```
2.  **Authenticate & Create Tunnel:**
    ```bash
    cloudflared tunnel login
    cloudflared tunnel create vectorbox-api
    ```
3.  **Run Tunnel:**
    Forward traffic to your local Docker backend port (8000).
    ```bash
    cloudflared tunnel run --url http://localhost:8000 vectorbox-api
    ```
    *(Note: For permanent running, install it as a service using `cloudflared service install`)*.

4.  **Assign Domain:**
    In Cloudflare Dashboard > Zero Trust > Tunnels, assign a domain (e.g., `api.yourdomain.com`) to this tunnel.

### C. Deploy Frontend (Vercel)
1.  Push your code to GitHub.
2.  Import project into Vercel.
3.  **Environment Variables (Vercel):**
    - `NEXT_PUBLIC_API_URL`: `https://api.yourdomain.com` (Your Cloudflare Tunnel URL)
4.  Deploy.

---

## 3. Full Self-Hosted Setup (Alternative)

If you prefer to run *everything* on the Pi (Frontend included):

1.  **Update .env:**
    - `NEXT_PUBLIC_API_URL=http://localhost:8000` (or your Pi's LAN IP).

2.  **Run Everything:**
    ```bash
    docker-compose up -d --build
    ```
3.  **Access:**
    - Open `http://<raspberry-pi-ip>:3000` in your browser.

---

## 4. Data Migration & Initialization

**Strategy: Fresh Start (Cleanest)**
Since this is a major version upgrade, it's best to seed the database fresh on the production hardware.

### A. Initialize Database
Run the seeder script inside the backend container. This initializes the Schema, Qdrant Collection, and essential metadata.

```bash
docker-compose exec backend python scripts/seed_db.py
```

### B. Populate User Data
1.  Log in to the new deployment.
2.  Go to **Settings** (or the onboarding flow).
3.  **Upload your `watched.csv`** from Letterboxd.
    - The new **Ingestion Pipeline** (optimized batches) will process this efficiently.
    - **Note:** This triggers background jobs to fetch metadata and generate embeddings. It may take 10-20 minutes for a large library on a Pi, but the UI will remain responsive.

---

## 5. Maintenance

### Updating the App
```bash
# Pull latest code
git pull origin main

# Rebuild containers (minimal downtime)
docker-compose up -d --build backend
```

### Monitoring
- **Check Logs:** `docker-compose logs -f backend`
- **Resource Usage:** `docker stats`
- **Health Checks:** The API has a `/health` endpoint (if implemented) or check root `/`.

### Troubleshooting
- **"Circuit Breaker Open" logs:** TMDB is likely down or rate-limiting. Wait 60s.
- **Qdrant OOM (Out of Memory):** Check swap space usage (`free -h`). Ensure you applied the 4GB swap fix.
