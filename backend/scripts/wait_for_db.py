import socket
import time
import os
import sys

def wait_for_postgres(host, port, timeout=60):
    start_time = time.time()
    while True:
        try:
            with socket.create_connection((host, port), timeout=1):
                print(f"✅ Postgres is ready at {host}:{port}")
                return True
        except OSError:
            if time.time() - start_time > timeout:
                print(f"❌ Timed out waiting for Postgres at {host}:{port}")
                sys.exit(1)
            print(f"⏳ Waiting for Postgres at {host}:{port}...")
            time.sleep(2)

if __name__ == "__main__":
    db_host = os.getenv("POSTGRES_HOST", "postgres")
    db_port = int(os.getenv("POSTGRES_PORT", 5432))
    wait_for_postgres(db_host, db_port)
