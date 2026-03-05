# VectorBox QA Certification Runbook

**Date:** 2026-03-05  
**Version:** v1.2 Gold Master Candidate  

## 1. Executive Summary
A full E2E certification pass was executed on the VectorBox application. All systems were wiped, seeded, and tested under strict QA protocols. The build is **CERTIFIED GREEN** for deployment.

---

## 2. Certification Matrix

| Phase | Component | Subsystem | Status | Notes |
|-------|-----------|-----------|--------|-------|
| -1 | Dependencies | Frontend UI | ✅ Pass | Merged `framer-motion`, `lucide-react`. Build succeeds. |
| -1 | Dependencies | Frontend Tools | ✅ Pass | Merged `eslint`, `lint-staged`. |
| -1 | Dependencies | Backend Core | ✅ Pass | Safe bumps (`groq`, `bcrypt`, `sentence-transformers`) merged. Vector dims verified at 384. |
| -1 | Dependencies | High Risk | ⚠️ Held | Deferring `openai`, `fastapi`, `curl-cffi` to post-launch. |
| 0 | Data Reset | PostgreSQL | ✅ Pass | Cleanly wiped users, ratings, and clusters. |
| 0 | Data Reset | Qdrant | ✅ Pass | Re-created `movies` collection and ingested 192 seed movies successfully. |
| 1 | Auth Layer | Registration | ✅ Pass | User `qa_vecbox` created cleanly. |
| 1 | Auth Layer | Login | ✅ Pass | Case-insensitive auth normalized and verified. |
| 2 | Seed Data | K-Means | ✅ Pass | 25 ratings imported. 1 taste cluster generated dynamically. |
| 3 | NLP Search | Semantic Match| ✅ Pass | Magic Search interpreted queries and surfaced highly relevant vector matches. |
| 3 | NLP Search | Item-to-Item | ✅ Pass | Intent parsing accurately mapped "Movies like Inception". |
| 4 | Recs Engine | Parallel Feed | ✅ Pass | Trident engine returned 6 active sections in <2.5s. |
| 4 | Recs Engine | Signal A | ✅ Pass | "Because you watched..." dynamically generated items. |
| 4 | Recs Engine | Anti-Pattern | ✅ Pass | Explicit low-rated movies (Transformers, Furious 7) strictly omitted. |
| 5 | Security | IDOR | ✅ Pass | Hard 403 Forbidden intercept verified on foreign cluster access. |
| 5 | Health | Memory | ✅ Pass | Zero MissingGreenlet database session leaks observed. |
| 6 | UX / Visual | Main App | ✅ Pass | Spotlights, BorderBeams, Genre filters (19 mapping), Providers all verified. |
| 7 | Mobile UX | Responsive | ✅ Pass | Layout structural integrity maintained at 390x844 viewports. |

---

## 3. Post-QA Maintenance script

Save this as `backend/scripts/post_qa_cleanup.py` if a fast wipe is needed post-launch:

```python
import asyncio
from sqlalchemy import text
from config import AsyncSessionLocal

async def wipe_qa():
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM user_clusters WHERE user_id IN (SELECT id FROM users WHERE username = 'qa_vecbox')"))
        await db.execute(text("DELETE FROM user_ratings WHERE user_id IN (SELECT id FROM users WHERE username = 'qa_vecbox')"))
        await db.execute(text("DELETE FROM users WHERE username = 'qa_vecbox'"))
        await db.commit()
        print("QA footprints scrubbed successfully from PostgreSQL.")

if __name__ == "__main__":
    asyncio.run(wipe_qa())
```
