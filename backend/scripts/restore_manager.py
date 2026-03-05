import os
import shutil
import argparse
import subprocess
import requests
import glob
import zipfile
import logging
from pathlib import Path
import time

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Config
TEMP_DIR = Path("/app/backups/temp_restore")

# Service Config
QDRANT_HOST = os.getenv("QDRANT_HOST", "vectorbox-qdrant")
QDRANT_URL = f"http://{QDRANT_HOST}:6333"
COLLECTION_NAME = "movies"

db_url = os.getenv("DATABASE_URL", "")
PG_HOST = os.getenv("POSTGRES_HOST", "vectorbox-postgres")
PG_USER = os.getenv("POSTGRES_USER", "postgres")
PG_DB = os.getenv("POSTGRES_DB", "vectorbox")

if db_url and "://" in db_url:
    try:
        auth_host_db = db_url.split("://")[1]
        auth_part = auth_host_db.split("@")[0]
        host_db_part = auth_host_db.split("@")[1]
        
        PG_USER = auth_part.split(":")[0]
        PG_HOST = host_db_part.split(":")[0]
        PG_DB = host_db_part.split("/")[1].split("?")[0]
    except Exception:
        pass
REDIS_HOST = os.getenv("REDIS_HOST", "vectorbox-redis")

def ensure_dirs():
    if not TEMP_DIR.exists():
        TEMP_DIR.mkdir(parents=True)

def cleanup_temp():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
        logger.info("Temporary files cleaned up.")

def restore_postgres(sql_file, dry_run=False):
    logger.info("Restoring Postgres database...")
    if dry_run:
        logger.info(f"[DRY-RUN] Will terminate existing connections and replay {sql_file.name}")
        return True

    # Ensure PGPASSWORD is set
    env = os.environ.copy()
    if "PGPASSWORD" not in env:
        if "POSTGRES_PASSWORD" in env:
            env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
        elif "DATABASE_URL" in env:
            try:
                env["PGPASSWORD"] = env["DATABASE_URL"].split("://")[1].split("@")[0].split(":")[1]
            except Exception:
                pass

    # 1. Terminate existing connections
    kill_cmd = [
        "psql",
        "-h", PG_HOST,
        "-U", PG_USER,
        "-d", "postgres",
        "-c", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{PG_DB}' AND pid <> pg_backend_pid();"
    ]
    try:
        subprocess.run(kill_cmd, env=env, check=True, stdout=subprocess.DEVNULL)
        logger.info("Terminated existing Postgres connections.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to terminate connections: {e}")
        # Proceed anyway

    # 2. Replay SQL
    # Drop and recreate DB logic typically handled by pg_restore if dumped in custom format, 
    # but since this is plaintext SQL from pg_dump, we just feed it into psql.
    # Note: For full clean, we might want to DROP DATABASE vectorbox and CREATE.
    # But running the dump will usually try to overwrite or fail if tables exist. 
    # The dump should include logic to drop/replace if `pg_dump --clean` was used.
    # Since we didn't use --clean in backup_manager.py, we'll try applying it.
    
    restore_cmd = [
        "psql",
        "-h", PG_HOST,
        "-U", PG_USER,
        "-d", PG_DB,
        "-f", str(sql_file)
    ]
    try:
        subprocess.run(restore_cmd, env=env, check=True)
        logger.info("Postgres restoration completed.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to restore Postgres: {e}")
        return False

def restore_qdrant(snapshot_file, dry_run=False):
    logger.info("Restoring Qdrant vectors...")
    if dry_run:
        logger.info(f"[DRY-RUN] Will delete collection '{COLLECTION_NAME}' and upload snapshot {snapshot_file.name}")
        return True

    # 1. Delete existing collection
    delete_url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}"
    try:
        resp = requests.delete(delete_url)
        # Even if 404, we proceed. It might not exist.
        if resp.status_code not in (200, 404):
            logger.warning(f"Failed to delete existing collection. Status: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Could not reach Qdrant to delete collection: {e}")

    # Wait a sec
    time.sleep(1)

    # 2. Upload snapshot
    upload_url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}/snapshots/upload"
    try:
        with open(snapshot_file, 'rb') as f:
            files = {'snapshot': (snapshot_file.name, f)}
            resp = requests.post(upload_url, files=files)
            resp.raise_for_status()
        logger.info("Qdrant restoration completed.")
        return True
    except Exception as e:
        logger.error(f"Failed to restore Qdrant: {e}")
        return False

def restore_redis(rdb_file, dry_run=False):
    logger.info("Restoring Redis cache...")
    if dry_run:
        logger.info(f"[DRY-RUN] Will copy {rdb_file.name} to redis container and restart it")
        return True

    # Copy the rdb directly back to the volume via docker cp
    try:
        subprocess.run([
            "docker", "cp",
            str(rdb_file),
            "vectorbox-redis:/data/dump.rdb"
        ], check=True)
        
        # Redis needs to restart to load dump.rdb
        logger.info("Restarting Redis container...")
        subprocess.run(["docker", "restart", "vectorbox-redis"], check=True)
        logger.info("Redis restoration completed.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to restore Redis: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Restore VectorBox from a backup archive.")
    parser.add_argument("backup_zip", type=str, help="Path to the backup ZIP file to restore.")
    parser.add_argument("--dry-run", action="store_true", help="List what would be restored without doing it")
    
    args = parser.parse_args()
    zip_path = Path(args.backup_zip)
    
    if not zip_path.exists():
        logger.error(f"Backup file not found: {zip_path}")
        exit(1)
        
    ensure_dirs()
    
    try:
        logger.info(f"Extracting backup archive: {zip_path.name}")
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(TEMP_DIR)
            
        sql_files = list(TEMP_DIR.glob("postgres_dump_*.sql"))
        qdrant_snapshots = list(TEMP_DIR.glob("qdrant_snapshot_*.snapshot"))
        redis_dumps = list(TEMP_DIR.glob("redis_dump_*.rdb"))
        
        if not sql_files or not qdrant_snapshots:
            logger.error("Invalid backup archive: missing PostgreSQL or Qdrant dump.")
            exit(1)
            
        sql_file = sql_files[0]
        qdrant_snapshot = qdrant_snapshots[0]
        redis_dump = redis_dumps[0] if redis_dumps else None
        
        pg_status = restore_postgres(sql_file, args.dry_run)
        qd_status = restore_qdrant(qdrant_snapshot, args.dry_run)
        rd_status = False
        
        if redis_dump:
            rd_status = restore_redis(redis_dump, args.dry_run)
            
        # Summary
        logger.info("====================================")
        logger.info("Restore Summary:")
        logger.info(f"Postgres: {'✅ restored' if pg_status else '❌ failed'}")
        logger.info(f"Qdrant: {'✅ restored' if qd_status else '❌ failed'}")
        if redis_dump:
            logger.info(f"Redis: {'✅ restored' if rd_status else '❌ failed'}")
        else:
            logger.info("Redis: ⚠️ skipped (no dump found)")
            
        if pg_status and qd_status:
            logger.info("Restore complete ✅")
        else:
            logger.warning("Restore finished with errors ❌")
            
    except Exception as e:
        logger.error(f"Restore pipeline failed: {e}")
        exit(1)
    finally:
        if not args.dry_run:
            cleanup_temp()

if __name__ == "__main__":
    main()
