# VectorBox Master Setup Script (Windows/PowerShell)
# v1.3 - "Logic Order Fix"

param([switch]$clean)

$ErrorActionPreference = "Stop"

# 0. Environment Configuration (Interactive)
if (-not (Test-Path ".env")) {
    Write-Host "⚠️ .env file not found. Copying from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

$isProd = Read-Host "Is this a PRODUCTION deployment? (y/N)"
$content = Get-Content ".env"

if ($isProd -match "^[Yy]") {
    Write-Host "🔒 Production Mode ENABLED (Secure Cookies ON)." -ForegroundColor Green
    $content = $content -replace "ENVIRONMENT=development", "ENVIRONMENT=production"
    
    $ComposeCmd = @("docker-compose", "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml")
    
    # Optional: Secure Database Creds Warning
    Write-Warning "Ensure you have set strong passwords in .env for Production!"
    Write-Host ""
    Write-Host "⚠️  Remember to set real values in .env:" -ForegroundColor Yellow
    Write-Host "   SECRET_KEY     — run: openssl rand -hex 32" -ForegroundColor Yellow
    Write-Host "   POSTGRES_PASSWORD — use a strong random password" -ForegroundColor Yellow
    Write-Host "   All API keys (GROQ, TMDB, OMDB)" -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "🛠️ Development Mode (Localhost)." -ForegroundColor Cyan
    $content = $content -replace "ENVIRONMENT=production", "ENVIRONMENT=development"
    $ComposeCmd = @("docker-compose")
}
$content | Set-Content ".env"

function Invoke-Compose {
    & $ComposeCmd[0] ($ComposeCmd[1..($ComposeCmd.Length-1)] + $args)
}


# 1. Limpieza de seguridad / Deep Clean
if ($clean) {
    Write-Host "🧹 performing DEEP SYSTEM CLEAN (Nuclear Option)..." -ForegroundColor Magenta
    try {
        Invoke-Compose down -v --remove-orphans
        docker system prune -f
        docker builder prune -a -f
        Write-Host "✨ Clean Complete. Starting fresh." -ForegroundColor Cyan
    } catch {
        Write-Warning "Deep clean had some issues, verifying..."
    }
} else {
    Write-Host "🛑 Cleaning up existing containers..." -ForegroundColor Magenta
    Invoke-Compose down --remove-orphans
    if ($LASTEXITCODE -ne 0) { Write-Warning "Cleanup had minor issues, proceeding anyway..." }
}

# 2. Arranque de Infraestructura
Write-Host "🚀 Starting VectorBox Infrastructure..." -ForegroundColor Cyan
Invoke-Compose up -d --build
if ($LASTEXITCODE -ne 0) { Write-Error "Docker Compose failed"; exit 1 }

# 3. Espera activa (Healthcheck)
Write-Host "⏳ Waiting for Database to be ready..." -ForegroundColor Yellow
Invoke-Compose exec -T backend python scripts/wait_for_db.py
if ($LASTEXITCODE -ne 0) { Write-Error "Database wait failed"; exit 1 }

# 4. Estructura de Datos (Tablas SQL)
Write-Host "🛠️ Applying Database Migrations..." -ForegroundColor Yellow
Invoke-Compose exec -T backend alembic upgrade head
if ($LASTEXITCODE -ne 0) { Write-Error "Migration failed"; exit 1 }
Write-Host "✅ Database Schema Applied." -ForegroundColor Green

# 5. Datos (Semilla) - CRÍTICO: Esto debe ir ANTES de los índices para crear la colección
Write-Host "🌱 Seeding Database & Creating Collection (Limit: 200 movies)..." -ForegroundColor Yellow
Invoke-Compose exec -T backend python scripts/seed_db.py --limit 20
if ($LASTEXITCODE -ne 0) { Write-Error "Seeding failed (Check backend logs)"; exit 1 }
Write-Host "✅ Database Seeded & Collection Created." -ForegroundColor Green

# 6. Rendimiento (Índices Vectoriales) - Ahora sí funcionará porque la colección existe
Write-Host "⚡ Creating Qdrant Performance Indexes..." -ForegroundColor Yellow
Invoke-Compose exec -T backend python scripts/create_qdrant_indexes.py
if ($LASTEXITCODE -ne 0) { Write-Error "Index creation failed"; exit 1 }
Write-Host "✅ Qdrant Indexes Created." -ForegroundColor Green

# 7. Datos en Tiempo Real (Tendencias)
Write-Host "🔥 Fetching Trending Movies..." -ForegroundColor Yellow
Invoke-Compose exec -T backend python scripts/popular_scraper.py
if ($LASTEXITCODE -ne 0) { Write-Error "Trend fetch failed"; exit 1 }
Write-Host "✅ Trending Movies Updated." -ForegroundColor Green

# 8. Final
Write-Host "------------------------------------------------" -ForegroundColor White
Write-Host "🎉 VectorBox is ready and running!" -ForegroundColor Cyan
Write-Host "➡️ Frontend: http://localhost:3000"
Write-Host "➡️ Backend:  http://localhost:8000"
Write-Host "------------------------------------------------" -ForegroundColor White