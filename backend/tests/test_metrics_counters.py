"""Contadores de embudo: la forma de la clave, el conjunto cerrado, y que no muerdan.

Lo que este test protege de verdad es la tercera parte. Un contador que puede tumbar
lo que mide es peor que no tener contador — y en este repo ya pasó lo contrario, un
monitor que sólo sabía decir "bien" (ver `audit_score_surfaces`). Así que `bump` se
come Redis caído, un cliente a None y un evento desconocido, y la petición sigue.

El conjunto cerrado vive en `services/metrics.py` y no en el futuro
`POST /api/metrics/{evento}` a propósito: así la validación no depende de que el
endpoint se acuerde de validar. Un nombre libre ahí es una escritura sin límite a
Redis desde internet.
"""
import asyncio
from datetime import date

import pytest

from services import metrics


class _RedisFalso:
    def __init__(self, revienta=False):
        self.incrs = []
        self.expires = []
        self.revienta = revienta

    async def incr(self, key):
        if self.revienta:
            raise ConnectionError("redis caído")
        self.incrs.append(key)
        return len(self.incrs)

    async def expire(self, key, ttl):
        self.expires.append((key, ttl))


def test_key_shape_matches_the_documented_design():
    r = _RedisFalso()
    asyncio.run(metrics.bump(r, "search.degraded"))
    assert r.incrs == [f"metrics:{date.today().isoformat()}:search.degraded:-"]


def test_language_lands_in_the_key():
    r = _RedisFalso()
    asyncio.run(metrics.bump(r, "landing.view", lang="es"))
    assert r.incrs[0].endswith(":landing.view:es")


def test_ttl_is_ninety_days_and_renews_on_every_bump():
    r = _RedisFalso()
    asyncio.run(metrics.bump(r, "landing.view"))
    asyncio.run(metrics.bump(r, "landing.view"))
    assert len(r.expires) == 2
    assert all(ttl == 60 * 60 * 24 * 90 for _, ttl in r.expires)


def test_unknown_event_is_refused():
    """Sin esto, el endpoint público del futuro es texto libre escribiendo en Redis."""
    r = _RedisFalso()
    asyncio.run(metrics.bump(r, "landing.view; DROP"))
    asyncio.run(metrics.bump(r, "cualquier.cosa"))
    assert r.incrs == []


def test_the_seven_funnel_events_are_all_accepted():
    r = _RedisFalso()
    for e in ("landing.view", "landing.query.chip", "landing.query.free",
              "landing.mode.title", "landing.cta.profile", "landing.cta.letterboxd",
              "search.degraded"):
        asyncio.run(metrics.bump(r, e))
    assert len(r.incrs) == 7
    assert len(metrics.EVENTS) == 7, "si crece la lista, que sea a la vista"


def test_no_redis_is_not_an_error():
    # `dependencies.get_redis` devuelve None a propósito cuando Redis no está.
    asyncio.run(metrics.bump(None, "search.degraded"))


def test_a_broken_redis_never_breaks_the_request():
    asyncio.run(metrics.bump(_RedisFalso(revienta=True), "search.degraded"))


def test_search_bumps_degraded_from_a_single_place():
    """Un bump por rama se desincroniza; todas leen la misma variable."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "routers" / "search.py").read_text(encoding="utf-8")
    assert src.count('metrics.bump(redis, "search.degraded")') == 1
    i = src.index("degraded = parse_failed(intent)")
    assert 'metrics.bump(redis, "search.degraded")' in src[i:i + 600], (
        "el contador va junto a donde nace `degraded`, no en cada return"
    )


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_"):
            _f()
    print("ok")
