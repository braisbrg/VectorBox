import pytest
import sys
import os

# Ensure we can import from root
sys.path.append(os.getcwd())

from utils.decorators import safe_execution
from models.schemas import FeedSection

# Mock Service Class
class MockService:
    @safe_execution(fallback_return="fallback_occurred")
    async def risky_method(self, user_id: int):
        raise ValueError("Simulated Failure")

    @safe_execution(fallback_return=None)
    async def risky_method_none(self):
        raise KeyError("Another Failure")

    @safe_execution(fallback_return=[], log_level=40)
    async def risky_method_list(self):
        raise Exception("List Failure")

@pytest.mark.asyncio
async def test_safe_execution_handles_exception():
    service = MockService()
    
    # Test 1: String Fallback
    result = await service.risky_method(user_id=123)
    assert result == "fallback_occurred"
    
    # Test 2: None Fallback
    result = await service.risky_method_none()
    assert result is None
    
    # Test 3: List Fallback
    result = await service.risky_method_list()
    assert result == []

# Stub test for imported modules to verify app code imports work
from services.feed_service import FeedService

def test_feed_service_importable():
    assert FeedService is not None
