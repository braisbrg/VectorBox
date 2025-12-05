"""
Database module bridge.
Re-exports database configuration and session management from config.py
to maintain compatibility with existing imports.
"""
from config import engine, AsyncSessionLocal, Base, get_db, init_db
