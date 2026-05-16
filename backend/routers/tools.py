"""
Tools router.

History: this module hosted three legacy endpoints that pre-dated Clerk auth
and the dedicated /rss/group/vibe flow:

    POST /api/tools/group-watchlist  — leaked watchlist intersections for any
                                       user_id the caller could enumerate.
    POST /api/tools/compatibility    — leaked another user's taste cosine /
                                       genre overlap with only "I'm in the
                                       pair" as the access check.
    POST /api/tools/update-popular   — let any authenticated user trigger an
                                       outbound scrape of Letterboxd + TMDB.
                                       The scheduler (scheduler.py) already
                                       runs this on a cron — the public route
                                       was a DOS amplifier with no upside.

All three were removed in the 2026-05 pre-launch security audit (H-3, H-4)
after confirming the frontend never called them. Group recommendations
now flow exclusively through /api/rss/group/vibe, which is opt-in by
shared Letterboxd username instead of integer ID enumeration.
"""
from fastapi import APIRouter

router = APIRouter()
