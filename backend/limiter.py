"""
Rate Limiter Configuration
Uses Redis backend for distributed rate limiting across workers.
"""
import os
from slowapi import Limiter
from slowapi.util import get_remote_address

# Get Redis URL from environment or default
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Initialize Limiter with Redis storage
# Auto-disable in Testing Mode
is_testing = os.getenv("TESTING_MODE", "False").lower() in ("true", "1", "yes")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=REDIS_URL,
    strategy="fixed-window",
    enabled=not is_testing
)
