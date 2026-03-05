#!/bin/bash
set -e
echo "🗄️  Starting VectorBox backup..."
docker-compose exec -T backend python scripts/backup_manager.py
echo ""
echo "📦 Latest backups:"
ls -lh ./backups/vectorbox_backup_*.zip 2>/dev/null \
  | tail -5 || echo "No backups found in ./backups/"
