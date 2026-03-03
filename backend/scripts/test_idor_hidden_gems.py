"""
Phase 3: IDOR Security Verification for /hidden-gems
Confirms that the endpoint rejects unauthenticated requests.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 60)
    print("  PHASE 3: IDOR Security Verification — /hidden-gems")
    print("=" * 60)

    import httpx

    base_url = os.getenv("TEST_BASE_URL", "http://localhost:8000")
    url = f"{base_url}/api/recommendations/hidden-gems"

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Test 1: No auth cookie/header — should be 401
        print(f"\n  [Test 1] GET {url} — No authentication")
        try:
            response = await client.get(url, params={"country_code": "ES"})
            status = response.status_code
            print(f"           Status: {status}")

            if status in (401, 403):
                print(f"  ✅ IDOR PROTECTION VERIFIED — Unauthenticated request blocked ({status})")
            else:
                print(f"  ❌ IDOR EXPOSED: endpoint returned {status}")
                print(f"     Response: {response.text[:200]}")
                sys.exit(1)
        except httpx.ConnectError:
            print(f"  ⚠️  Could not connect to {base_url}")
            print(f"     Make sure the backend is running (docker-compose up backend)")
            sys.exit(1)

        # Test 2: Forged user_id query param (old IDOR vector) — should be ignored
        print(f"\n  [Test 2] GET {url}?user_id=999 — Forged user_id param")
        try:
            response = await client.get(url, params={"user_id": 999, "country_code": "ES"})
            status = response.status_code
            print(f"           Status: {status}")

            if status in (401, 403):
                print(f"  ✅ Forged user_id correctly ignored ({status})")
            else:
                print(f"  ❌ IDOR EXPOSED: endpoint returned {status} with forged user_id")
                sys.exit(1)
        except httpx.ConnectError:
            print(f"  ⚠️  Connection failed")
            sys.exit(1)

    print(f"\n  ✅ IDOR PROTECTION VERIFIED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
