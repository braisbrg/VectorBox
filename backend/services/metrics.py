"""Contadores diarios planos en Redis. Sin PII, sin cookies, sin sesión.

Las trazas de OTel responden "¿por qué esta petición fue lenta?". No responden
"¿cuánta gente pulsa crear perfil?" ni "¿cuántas veces se cayó el parser". Para eso,
un número por evento y día. Diseño en `docs/OBSERVABILITY.md` §4.

La forma de la clave es `metrics:{fecha}:{evento}:{idioma}` y el TTL de 90 días se
renueva en cada incremento, así que un contador que deja de usarse se borra solo.

Lo que NO puede entrar aquí está en `docs/OBSERVABILITY.md` §5, y es lo que mantiene
esto sin banner de consentimiento: nunca la consulta del usuario (es texto libre),
nunca `user_id`, correo, nombre ni IP —ni con hash, porque un identificador estable
es un identificador—, y nunca la cookie de sesión. Un agregado deja de ser un
contador y pasa a ser seguimiento en cuanto distingue a una persona.

Se leen con SCAN, nunca KEYS (`STACK_RULES.md` §2, y `test_invariants_static.py` lo
comprueba). El one-liner está en el §4 del documento.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

TTL_SECONDS = 60 * 60 * 24 * 90  # 90 días

# Conjunto CERRADO. Un contador con nombre libre es una escritura sin límite a
# Redis, y este módulo es la puerta por la que entraría el día que se añada el
# `POST /api/metrics/{evento}` que necesitan los seis eventos de frontend. Que la
# lista viva aquí y no en el endpoint significa que la validación no depende de que
# el endpoint se acuerde de validar.
EVENTS = frozenset({
    "landing.view",
    "landing.query.chip",
    "landing.query.free",
    "landing.mode.title",
    "landing.cta.profile",
    "landing.cta.letterboxd",
    "search.degraded",
})


async def bump(redis, event: str, *, lang: str = "-") -> None:
    """Suma uno al contador del día. Nunca levanta.

    Un contador que puede tumbar lo que mide es peor que no tener contador, así que
    esto se come sus propios errores y sigue: Redis caído, un `redis` a None (el
    `get_redis` del proyecto devuelve None a propósito cuando no hay), o un evento
    que no está en la lista. Los dos últimos se registran, porque un contador que
    no cuenta en silencio es exactamente el fallo que este módulo existe para
    evitar.
    """
    if event not in EVENTS:
        logger.warning("[metrics] evento desconocido, no se cuenta: %r", event)
        return
    if redis is None:
        return
    key = f"metrics:{date.today().isoformat()}:{event}:{lang}"
    try:
        await redis.incr(key)
        await redis.expire(key, TTL_SECONDS)
    except Exception as e:
        logger.warning("[metrics] %s falló: %s", key, e)


async def read_today(redis, prefix: Optional[str] = None) -> dict:
    """Los contadores de hoy, con SCAN. Para el script de lectura y los tests."""
    if redis is None:
        return {}
    patron = f"metrics:{date.today().isoformat()}:{prefix or ''}*"
    out: dict = {}
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=patron, count=200)
        for k in keys:
            out[k] = await redis.get(k)
        if cursor == 0:
            return out
