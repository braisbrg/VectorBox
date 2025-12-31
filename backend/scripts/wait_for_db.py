import logging
import socket
import time
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def wait_for_db():
    host = os.getenv("POSTGRES_SERVER", "postgres") # Matches docker-compose service name default
    port = int(os.getenv("POSTGRES_PORT", 5432))
    
    logger.info(f"Waiting for database at {host}:{port}...")
    
    start_time = time.time()
    while True:
        try:
            with socket.create_connection((host, port), timeout=1):
                logger.info("Database is ready!")
                return
        except (OSError, ConnectionRefusedError):
            if time.time() - start_time > 60:
                logger.error("Timeout waiting for database.")
                sys.exit(1)
            time.sleep(1)
            logger.info("Waiting...")

if __name__ == "__main__":
    wait_for_db()
