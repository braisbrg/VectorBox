# Update Project Dependencies Script
# Run this script to update frontend and backend dependencies to the latest stable versions.

Write-Host "Starting Project Update..." -ForegroundColor Cyan

# 1. Frontend Updates
Write-Host "Updating Frontend Dependencies..." -ForegroundColor Yellow
Set-Location frontend
# Install latest versions of core packages
npm install next@latest react@latest react-dom@latest eslint-config-next@latest framer-motion@latest lucide-react@latest tailwindcss@latest --legacy-peer-deps
# Update all other packages to latest minor/patch
npm update
# Fix vulnerabilities
npm audit fix --force
Set-Location ..

# 2. Backend Updates
Write-Host "Updating Backend Dependencies..." -ForegroundColor Yellow
Set-Location backend
# Upgrade pip
python -m pip install --upgrade pip
# Upgrade all packages in requirements.txt (assuming they are unpinned or using >=)
# Note: To force upgrade even if pinned, we can try to install specific packages
python -m pip install --upgrade fastapi uvicorn sqlalchemy psycopg[binary] pydantic qdrant-client
# Install/Update everything else
python -m pip install -r requirements.txt --upgrade
Set-Location ..

Write-Host "Update Complete!" -ForegroundColor Green
Write-Host "Please run 'npm run build' in frontend and restart backend to verify."
