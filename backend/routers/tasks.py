"""
Task Status Router for Background Job Progress
"""
from fastapi import APIRouter, HTTPException
from models.schemas import TaskStatusResponse
from services.task_store import get_task_store
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


from dependencies import get_current_user
from fastapi import Depends
from models.schemas import TokenResponse

@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    current_user: TokenResponse = Depends(get_current_user)
):
    """
    Get the status of a background task.
    Enforces ownership: Users can only see their own tasks.
    """
    task_store = get_task_store()
    status = await task_store.get_status(task_id)
    
    if not status:
        raise HTTPException(
            status_code=404,
            detail="Task not found or expired"
        )
    
    # Security: IDOR Check
    # If the task has a user_id recorded, check against current_user
    task_owner = status.get("user_id")
    if task_owner and task_owner != current_user.user_id:
        logger.warning(f"IDOR Attempt: User {current_user.user_id} tried to access Task {task_id} owned by {task_owner}")
        raise HTTPException(
            status_code=403,
            detail="Access Denied: You do not own this task."
        )
    
    return TaskStatusResponse(
        task_id=status["task_id"],
        status=status["status"],
        progress=status["progress"],
        step=status.get("step")
    )
