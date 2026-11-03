"""El feed construye sus 12 filas EN PARALELO, y cada una con SU sesión.

`README.md` lo promete, y lo único que decía comprobarlo era
`scripts/verify_feed_parallelism.py`: cronometraba 9 `asyncio.sleep` de mentira dentro
de un `asyncio.gather` sin importar nada del feed, así que habría pasado con el feed
secuencial o con una sesión compartida. Y desde `e81099b` (2026-04-07), que quitó un
nombre de su lista sin bajar `TASK_COUNT = 10`, fallaba siempre: medido el 2026-11-03,
9 de 10 en 0,2 s, igual en local que en la CI.

Esto llama a `FeedService.get_main_feed` de verdad, con stubs sólo en la frontera
(sesiones, caché, constructores de fila). El paralelismo se mide contando cuántas filas
están a medias a la vez, no con un tope de tiempo: un tope en un runner cargado da rojos
falsos, y la cuenta no depende del reloj.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.feed_service as feed_service
from models.schemas import FeedItem, FeedSection

# Las 12 tareas de get_main_feed, con la clave de caché de cada una como id de fila.
ROWS = {
    "popular_letterboxd", "picked_for_you", "because_you_watched", "hidden_gems",
    "niche_picks", "wildcard", "random_picks", "auteur", "cult_actor",
    "available_now", "upcoming", "leaving_soon",
}


class _Session:
    """Una AsyncSession sin base de datos: las consultas previas al gather reciben vacío."""

    async def scalar(self, *_):
        return False

    async def execute(self, *_):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


class _SessionLocal:
    """Como AsyncSessionLocal: cada `async with` abre una sesión NUEVA."""

    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, *_):
        return False


class _Rows:
    """Todos los constructores de fila: apuntan qué sesión recibió cada uno y cuántos van a la vez."""

    def __init__(self):
        self.sessions = []  # referencias fuertes, para que ningún id() se reutilice
        self.in_flight = 0
        self.peak = 0

    def builder(self, row_id, session_at):
        async def build(*args, **_):
            return await self.enter(row_id, args[session_at])
        return build

    async def enter(self, row_id, session):
        self.sessions.append(session)
        item = FeedItem(id=len(self.sessions), title=row_id)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return FeedSection(id=row_id, title=row_id, items=[item])


@pytest.mark.asyncio
async def test_main_feed_builds_its_12_rows_in_parallel_each_with_its_own_session(monkeypatch):
    rows = _Rows()

    class _Recommender:
        def __init__(self, db, **_):
            self.db = db

        async def get_auteur_section(self, *_, **__):
            return await rows.enter("auteur", self.db)

        async def get_cult_actor_section(self, *_, **__):
            return await rows.enter("cult_actor", self.db)

    engine = SimpleNamespace(
        _get_anti_vector=AsyncMock(return_value=None),
        get_upcoming_section=rows.builder("upcoming", 1),
        get_leaving_soon_section=rows.builder("leaving_soon", 0),
    )
    monkeypatch.setattr(feed_service, "RecommendationEngine", lambda **_: engine)
    monkeypatch.setattr(feed_service, "AsyncSessionLocal", _SessionLocal)
    monkeypatch.setattr(feed_service, "_get_cached_section", AsyncMock(return_value=None))
    monkeypatch.setattr(feed_service, "_cache_section", AsyncMock())
    monkeypatch.setattr(feed_service, "ProviderService", lambda *_: None)
    monkeypatch.setattr(feed_service, "RecommendationService", _Recommender)

    svc = feed_service.FeedService()
    for method, row_id in [
        ("get_popular_on_letterboxd_section", "popular_letterboxd"),
        ("get_hybrid_picks_section", "picked_for_you"),
        ("get_because_you_watched_section", "because_you_watched"),
        ("get_hidden_gems_section", "hidden_gems"),
        ("get_niche_picks_section", "niche_picks"),
        ("get_wildcard_section", "wildcard"),
        ("get_random_recommendations_section", "random_picks"),
        ("get_available_now_section", "available_now"),
    ]:
        setattr(svc, method, rows.builder(row_id, 1))

    # Con servicios, para que «Se va pronto» llegue a construirse.
    resp = await svc.get_main_feed(
        user_id=1, country_code="ES", streaming_providers=[8],
        tmdb=None, qdrant=None, redis_client=object(),
    )

    assert rows.peak == 12, (
        f"sólo {rows.peak} de las 12 filas llegaron a estar a la vez en marcha: "
        "el feed ya no las construye en paralelo"
    )
    assert len(rows.sessions) == 12
    assert len({id(s) for s in rows.sessions}) == 12, (
        "dos filas compartieron una sesión, y una AsyncSession no aguanta tareas concurrentes"
    )
    assert {s.id for s in resp.feed} == ROWS
