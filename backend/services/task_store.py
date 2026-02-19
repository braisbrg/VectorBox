"""
Task Store Service for Background Job Progress Tracking
Uses Redis for persistence across workers
"""
import uuid
import json
import logging
from typing import Optional, Dict
from datetime import datetime
from redis import asyncio as aioredis
import os

logger = logging.getLogger(__name__)


class TaskStore:
    """Redis-based task progress store for background job tracking"""
    
    TASK_PREFIX = "task:"
    EXPIRY_SECONDS = 3600  # Tasks expire after 1 hour
    
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._redis = None
    
    async def _get_redis(self) -> aioredis.Redis:
        """Lazy Redis connection"""
        if self._redis is None:
            self._redis = aioredis.from_url(
                self.redis_url, 
                encoding="utf8", 
                decode_responses=True
            )
        return self._redis
    
    def generate_task_id(self) -> str:
        """Generate a unique task ID"""
        return str(uuid.uuid4())
    
    async def create_task(self, task_id: str, total_steps: int = 100, step: str = "Initializing...", user_id: int = None) -> None:
        """Create a new task with initial status"""
        redis = await self._get_redis()
        task_data = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "total_steps": total_steps,
            "step": step,
            "user_id": user_id,  # Security: Ownership
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        await redis.setex(
            f"{self.TASK_PREFIX}{task_id}",
            self.EXPIRY_SECONDS,
            json.dumps(task_data)
        )
        logger.info(f"Created task: {task_id} for user {user_id}")
    
    async def update_progress(
        self, 
        task_id: str, 
        progress: int, 
        step: str = None,
        status: str = "processing"
    ) -> None:
        """Update task progress and optional step description"""
        redis = await self._get_redis()
        key = f"{self.TASK_PREFIX}{task_id}"
        
        # Get existing task
        existing = await redis.get(key)
        if existing:
            task_data = json.loads(existing)
            task_data["progress"] = min(progress, 100)
            task_data["status"] = status
            task_data["updated_at"] = datetime.utcnow().isoformat()
            if step:
                task_data["step"] = step
            
            await redis.setex(key, self.EXPIRY_SECONDS, json.dumps(task_data))
            logger.debug(f"Updated task {task_id}: {progress}% - {step}")
    
    async def complete_task(self, task_id: str, step: str = "Complete!") -> None:
        """Mark task as completed"""
        await self.update_progress(task_id, 100, step, status="completed")
        logger.info(f"Completed task: {task_id}")
    
    async def fail_task(self, task_id: str, error: str = "Task failed") -> None:
        """Mark task as failed"""
        redis = await self._get_redis()
        key = f"{self.TASK_PREFIX}{task_id}"
        
        existing = await redis.get(key)
        if existing:
            task_data = json.loads(existing)
            task_data["status"] = "failed"
            task_data["step"] = error
            task_data["updated_at"] = datetime.utcnow().isoformat()
            
            await redis.setex(key, self.EXPIRY_SECONDS, json.dumps(task_data))
        logger.error(f"Failed task {task_id}: {error}")
    
    async def get_status(self, task_id: str) -> Optional[Dict]:
        """Get current task status"""
        redis = await self._get_redis()
        key = f"{self.TASK_PREFIX}{task_id}"
        
        data = await redis.get(key)
        if data:
            return json.loads(data)
        return None


# Singleton instance for easy import
_task_store = None

def get_task_store() -> TaskStore:
    """Get or create singleton TaskStore instance"""
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store
