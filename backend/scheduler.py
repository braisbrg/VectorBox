import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def run_trending_update():
    logger.info("Running scheduled job: Update Letterboxd Popular")
    try:
        from scripts.maintenance_orchestrator import phase_popular_refresh
        stats = await phase_popular_refresh(dry_run=False)
        logger.info(f"Popular refresh done: {stats}")
    except Exception as e:
        logger.error(f"Trending update failed: {e}")

async def warm_showcase_if_cold():
    """Rellena las filas del showcase que estén FRÍAS. Nada más.

    `GET /search/showcase` devuelve 503 si la fila no está en Redis, y eso es
    deliberado: el momento en que ese endpoint puede disparar una búsqueda, deja de
    tener la entrada cerrada que justifica que sea público. Así que la fila tiene que
    estar caliente ANTES, y hasta ahora eso dependía de que un humano se acordara de
    tirar `scripts/warm_showcase.py` después de cada deploy. El 2026-08-10 no se
    acordó nadie: `grief` y `loneliness` servían 503 en producción.

    Sólo lo que falta, y por dos razones. Una, coste: cada fila es un parse más una
    búsqueda vectorial, y en régimen normal esto son 6 GET a Redis y ni una llamada.
    Dos, el límite: `/api/search/try` va a 5/minuto por IP y este bucle es el único
    llamador que se lo trip a sí mismo, de ahí los 13 s entre calentamientos reales
    (el mismo pacing que el script).

    Una fila degradada no se cachea — `warm_one` rechaza las respuestas con modelos
    caídos y las de confianza baja — así que si Groq está mal, la fila sigue fría y
    la próxima pasada lo reintenta. Es lo correcto: mejor 503 que una estantería con
    respuestas que el catálogo no sabe dar.
    """
    import httpx
    import redis.asyncio as aioredis

    from scripts.warm_showcase import BASE_URL, warm_one
    from services import showcase_service

    redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379"), decode_responses=True
    )
    try:
        frias = [
            (q["slug"], lang)
            for q in showcase_service.SHOWCASE_QUERIES
            for lang in ("es", "en")
            if await showcase_service.read(redis, q["slug"], lang) is None
        ]
        if not frias:
            return
        logger.info("[showcase] %d filas frías: %s", len(frias), frias)
        async with httpx.AsyncClient(base_url=BASE_URL) as client:
            for i, (slug, lang) in enumerate(frias):
                if i:
                    await asyncio.sleep(13)
                await warm_one(client, redis, slug, lang, False)
    except Exception as e:
        logger.error(f"[showcase] warm falló: {e}")
    finally:
        await redis.close()


def start_scheduler():
    # Run every day at 00:00 UTC
    scheduler.add_job(
        run_trending_update,
        CronTrigger(hour=0, minute=0),
        id="update_popular",
        replace_existing=True
    )
    # A los 3 minutos del arranque y luego cada 6 h. Los 3 minutos son para que
    # uvicorn ya escuche y el modelo de embeddings esté caliente: este job se llama
    # a sí mismo por HTTP. Y el intervalo corto no cuesta nada cuando todo está
    # caliente, que es el caso el 99% del tiempo — el TTL del showcase es de 7 días,
    # así que lo que esto cubre de verdad es el deploy con Redis nuevo.
    scheduler.add_job(
        warm_showcase_if_cold,
        IntervalTrigger(hours=6),
        id="warm_showcase",
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=3),
        replace_existing=True
    )
    scheduler.start()
    logger.info("Scheduler started.")
