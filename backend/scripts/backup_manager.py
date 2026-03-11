import os
import shutil
import datetime
import subprocess
import requests
import glob
import zipfile
import logging
from pathlib import Path

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Config
BACKUP_DIR = Path("/app/backups")
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
TEMP_DIR = BACKUP_DIR / "temp"

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
        # postgresql+asyncpg://user:pass@host:port/db
        auth_host_db = db_url.split("://")[1]
        auth_part = auth_host_db.split("@")[0]
        host_db_part = auth_host_db.split("@")[1]
        
        PG_USER = auth_part.split(":")[0]
        PG_HOST = host_db_part.split(":")[0]
        PG_DB = host_db_part.split("/")[1].split("?")[0]
    except Exception:
        pass
# Password should be in env `PGPASSWORD` for pg_dump

REDIS_HOST = os.getenv("REDIS_HOST", "vectorbox-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

def ensure_dirs():
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True)
    if not TEMP_DIR.exists():
        TEMP_DIR.mkdir(parents=True)

def cleanup_temp():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
        logger.info("Temporary files cleaned up.")

def backup_qdrant():
    """Trigger snapshot and download"""
    logger.info("Step A: Creating Qdrant Snapshot...")
    
    # 1. Create Snapshot
    try:
        create_url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}/snapshots"
        resp = requests.post(create_url)
        resp.raise_for_status()
        data = resp.json()
        snapshot_name = data["result"]["name"]
        logger.info(f"Qdrant snapshot created: {snapshot_name}")
        
        # 2. Download Snapshot
        download_url = f"{QDRANT_URL}/collections/{COLLECTION_NAME}/snapshots/{snapshot_name}"
        save_path = TEMP_DIR / f"qdrant_snapshot_{TIMESTAMP}.snapshot"
        
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
        logger.info(f"Qdrant snapshot downloaded: {save_path.name}")
        return save_path
        
    except Exception as e:
        logger.error(f"Failed to backup Qdrant: {e}")
        raise

def backup_postgres():
    """Run pg_dump"""
    logger.info("Step B: Dumping Postgres Database...")
    
    dump_path = TEMP_DIR / f"postgres_dump_{TIMESTAMP}.sql"
    
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
    
    cmd = [
        "pg_dump",
        "-h", PG_HOST,
        "-U", PG_USER,
        "-d", PG_DB,
        "-f", str(dump_path)
    ]
    
    try:
        res = subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
        logger.info(f"Postgres dump created: {dump_path.name}")
        return dump_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to dump Postgres: {e.stderr}")
        # Explicitly check for tool existence
        try:
            subprocess.run(["pg_dump", "--version"], check=True, capture_output=True)
        except FileNotFoundError:
             logger.error("CRITICAL: pg_dump utility not found. Install postgresql-client in Dockerfile.")
        raise

def backup_redis():
    """Trigger Redis BGSAVE and copy important keys as JSON"""
    logger.info("Step C: Backing up Redis...")
    try:
        import redis as redis_client
        import time
        import json
        
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r = redis_client.from_url(redis_url)
        
        # Trigger BGSAVE y esperar a que complete
        r.bgsave()
        time.sleep(2)
        
        # Guardar las keys más importantes como JSON
        # (las keys de Redis son caché regenerable, no datos críticos)
        backup_data = {}
        for key in r.scan_iter("*"):
            try:
                key_str = key.decode('utf-8')
                ttl = r.ttl(key)
                # Solo respaldar keys sin TTL o con TTL > 1hora
                # (evitar caché temporal)
                if ttl == -1 or ttl > 3600:
                    val = r.get(key)
                    if val:
                        backup_data[key_str] = val.decode('utf-8', errors='ignore')
            except Exception:
                pass
        
        redis_backup_path = TEMP_DIR / "redis_backup.json"
        with open(redis_backup_path, 'w') as f:
            json.dump(backup_data, f)
        
        logger.info(f"Redis backup created: {redis_backup_path.name} "
                    f"({len(backup_data)} keys)")
        return redis_backup_path
    except Exception as e:
        logger.warning(f"Redis backup skipped: {e}")
        return None  # Non-fatal — Redis is regenerable

def create_archive(files):
    """Zip files together"""
    logger.info("Step D: Creating Final Archive...")
    
    zip_name = f"vectorbox_backup_{TIMESTAMP}.zip"
    zip_path = BACKUP_DIR / zip_name
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file in files:
            zipf.write(file, arcname=file.name)
            
    logger.info(f"Backup archive created: {zip_name} ({zip_path.stat().st_size / 1024 / 1024:.2f} MB)")
    return zip_path

def rotate_backups():
    """Keep only last 5 backups"""
    logger.info("Step E: Rotating Backups...")
    
    # Get all backups, sort by modification time (newest first)
    backups = sorted(BACKUP_DIR.glob("vectorbox_backup_*.zip"), key=os.path.getmtime, reverse=True)
    
    keep_count = 5
    if len(backups) > keep_count:
        to_delete = backups[keep_count:]
        for backup in to_delete:
            logger.info(f"Deleting old backup: {backup.name}")
            os.remove(backup)
    else:
        logger.info("Rotation not needed (<= 5 backups).")

def main():
    logger.info("Starting Backup Manager 1.0")
    ensure_dirs()
    
    try:
        qdrant_file = backup_qdrant()
        pg_file = backup_postgres()
        redis_file = backup_redis()
        
        files = [qdrant_file, pg_file]
        if redis_file:
            files.append(redis_file)
            
        create_archive(files)
        
        rotate_backups()
        
        logger.info("Backup Pipeline Completed Successfully! ✅")
        
    except Exception as e:
        logger.error(f"Backup Pipeline Failed! ❌ Error: {e}")
        exit(1)
    finally:
        cleanup_temp()

if __name__ == "__main__":
    main()
