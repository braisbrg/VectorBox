# Run the backfill script inside the backend container
docker-compose exec backend python scripts/backfill_collections.py 500
