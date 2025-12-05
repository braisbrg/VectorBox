import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), "backend"))

print("Verifying imports...")

try:
    from services.movie_service import MovieService
    print("✅ MovieService imported")
except Exception as e:
    print(f"❌ MovieService import failed: {e}")

try:
    from routers.rss import router as rss_router
    print("✅ RSS Router imported")
except Exception as e:
    print(f"❌ RSS Router import failed: {e}")

try:
    from routers.similar import router as similar_router
    print("✅ Similar Router imported")
except Exception as e:
    print(f"❌ Similar Router import failed: {e}")

try:
    from services.rss_service import RSSService
    print("✅ RSSService imported")
except Exception as e:
    print(f"❌ RSSService import failed: {e}")

try:
    from routers.upload import router as upload_router
    print("✅ Upload Router imported")
except Exception as e:
    print(f"❌ Upload Router import failed: {e}")

print("Verification complete.")
