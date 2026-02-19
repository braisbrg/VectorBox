#!/bin/bash
# VectorBox Master Setup Script (Linux/Mac Bash)
# v1.3 - "Logic Order Fix"

set -e  # Exit immediately if a command exits with a non-zero status.

# 0. Environment Configuration (Interactive)
if [ ! -f .env ]; then
    echo -e "\033[1;33m⚠️ .env file not found. Copying from .env.example...\033[0m"
    cp .env.example .env
fi

read -p "Is this a PRODUCTION deployment? (y/N) " is_prod

# Use sed for replacement. syntax varies between GNU/BSD (Mac).
# Assuming standard linux (GNU sed) or trying to be compatible.
if [[ "$is_prod" =~ ^[Yy]$ ]]; then
    echo -e "\033[1;32m🔒 Production Mode ENABLED (Secure Cookies ON).\033[0m"
    # Replace development with production
    sed -i 's/ENVIRONMENT=development/ENVIRONMENT=production/g' .env
    
    echo -e "\033[1;33mEnsure you have set strong passwords in .env for Production!\033[0m"
else
    echo -e "\033[1;36m🛠️ Development Mode (Localhost).\033[0m"
    # Ensure development is set
    sed -i 's/ENVIRONMENT=production/ENVIRONMENT=development/g' .env
fi

# 1. Limpieza de seguridad
if [[ "$1" == "--clean" ]]; then
    echo -e "\033[1;35m🧹 performing DEEP SYSTEM CLEAN (Nuclear Option)...\033[0m"
    docker-compose down -v --remove-orphans
    docker system prune -f
    docker builder prune -a -f
    echo -e "\033[1;36m✨ Clean Complete. Starting fresh.\033[0m"
else
    echo -e "\033[1;35m🛑 Cleaning up existing containers...\033[0m"
    docker-compose down --remove-orphans || echo "Cleanup had minor issues, proceeding anyway..."
fi

# 2. Arranque de Infraestructura
echo -e "\033[1;36m🚀 Starting VectorBox Infrastructure...\033[0m"
docker-compose up -d --build

# 3. Espera activa (Healthcheck)
echo -e "\033[1;33m⏳ Waiting for Database to be ready...\033[0m"
docker-compose exec -T backend python scripts/wait_for_db.py

# 4. Estructura de Datos (Tablas SQL)
echo -e "\033[1;33m🛠️ Applying Database Migrations...\033[0m"
docker-compose exec -T backend alembic upgrade head
echo -e "\033[1;32m✅ Database Schema Applied.\033[0m"

# 5. Datos (Semilla)
echo -e "\033[1;33m🌱 Seeding Database & Creating Collection (Limit: 200 movies)...\033[0m"
docker-compose exec -T backend python scripts/seed_db.py --limit 200
echo -e "\033[1;32m✅ Database Seeded & Collection Created.\033[0m"

# 6. Rendimiento (Índices Vectoriales)
echo -e "\033[1;33m⚡ Creating Qdrant Performance Indexes...\033[0m"
docker-compose exec -T backend python scripts/create_qdrant_indexes.py
echo -e "\033[1;32m✅ Qdrant Indexes Created.\033[0m"

# 7. Datos en Tiempo Real (Tendencias)
echo -e "\033[1;33m🔥 Fetching Trending Movies...\033[0m"
docker-compose exec -T backend python scripts/popular_scraper.py
echo -e "\033[1;32m✅ Trending Movies Updated.\033[0m"

# 8. Final
echo "------------------------------------------------"
echo -e "\033[1;36m🎉 VectorBox is ready and running!\033[0m"
echo "➡️ Frontend: http://localhost:3000"
echo "➡️ Backend:  http://localhost:8000"
echo "------------------------------------------------"