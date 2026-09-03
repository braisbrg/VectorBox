"""Cliente de Streaming Availability (MovieOfTheNight) — vía DIRECTA.

Base `https://api.movieofthenight.com/v4` y cabecera `X-Api-Key`. **No es la de RapidAPI**,
que usa otra URL (`streaming-availability.p.rapidapi.com`, sin `/v4`) y otra cabecera
(`X-RapidAPI-Key`). La clave del proyecto se sacó del panel de MovieOfTheNight, así que es
la directa; por eso la variable se llama `MOVIEOFTHENIGHT_API_KEY` y no `RAPIDAPI_KEY`,
que nombraría al mercado y no al proveedor.

**La cuota es de 1000 peticiones al MES**, así que este cliente no se llama nunca desde el
camino de un request de usuario: sólo desde la pasada diaria del orquestador. Cada respuesta
trae `X-Quota-Granted/Used/Reset`, o sea que el consumo se MIDE y no se estima — se registra
en cada llamada para que agotar la cuota sea visible antes de que pase, no después.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.movieofthenight.com/v4"

# Servicios de la API ↔ IDs de proveedor de TMDB, que es lo que guarda `StreamingProvider`.
# Mapa explícito y corto a propósito: la API devuelve sus propios ids (`netflix`, `prime`)
# y nosotros guardamos números de TMDB, así que sin esto no se puede cruzar con lo que el
# usuario ha elegido. Los 11 disponibles en ES, verificados contra /countries/es el
# 2026-08-11. Si aparece uno nuevo, el que falte simplemente no se cruza — no rompe nada.
SERVICE_TO_TMDB_PROVIDER: Dict[str, int] = {
    "netflix": 8,
    "prime": 119,
    "disney": 337,
    "hbo": 1899,        # HBO Max
    "apple": 350,       # Apple TV+
    "mubi": 11,
    "skyshowtime": 1773,
    "crunchyroll": 283,
    "plutotv": 300,
    "curiosity": 190,
    "zee5": 232,
}

# Para pintar el nombre en la ficha. Sólo los que `.title()` escribe mal — los otros
# cuatro (netflix, mubi, crunchyroll, curiosity) salen bien solos y no hace falta
# repetirlos aquí para que luego se desincronicen.
SERVICE_DISPLAY_NAME: Dict[str, str] = {
    "prime": "Prime Video",
    "disney": "Disney+",
    "hbo": "HBO Max",
    "apple": "Apple TV+",
    "skyshowtime": "SkyShowtime",
    "plutotv": "Pluto TV",
    "zee5": "ZEE5",
}


def nombre_servicio(service_id: Optional[str]) -> str:
    return SERVICE_DISPLAY_NAME.get(service_id or "", (service_id or "").title())


# Sólo la suscripción cuenta como "está en tus servicios": que una película se pueda
# ALQUILAR en Prime no es que la tengas incluida, y mezclarlo llenaría las filas de
# títulos que en realidad hay que pagar aparte.
SUBSCRIPTION_TYPES = {"subscription", "addon"}


class StreamingAvailabilityClient:
    """Lector de `/changes`. Sin escrituras, sin caché propia: el resultado va a Postgres."""

    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key or os.getenv("MOVIEOFTHENIGHT_API_KEY")
        self.enabled = bool(self.api_key)
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-Api-Key": self.api_key or "", "User-Agent": "VectorBox/1.0"},
            timeout=timeout,
        )
        self.quota_granted: Optional[int] = None
        self.quota_used: Optional[int] = None
        self.requests_made = 0
        # "0 cambios" y "la API no contestó" salen idénticos si sólo se cuenta lo
        # devuelto — y ésa es exactamente la forma en que la Señal C estuvo muerta
        # semanas sin que nada fallara. El contador separa las dos.
        self.errors = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _registrar_cuota(self, headers) -> None:
        try:
            self.quota_granted = int(headers.get("X-Quota-Granted", 0)) or self.quota_granted
            self.quota_used = int(headers.get("X-Quota-Used", 0))
        except (TypeError, ValueError):
            pass

    async def fetch_changes(
        self,
        country: str,
        change_type: str,
        catalogs: List[str],
        max_pages: int = 4,
    ) -> List[dict]:
        """Devuelve [{tmdb_id, service, change_type, option_type, effective_at, link}].

        `max_pages` acota el gasto: cada página es UNA petición de las 1000 del mes, y la
        API pagina de 25 en 25 con cursor. Cuatro páginas por tipo y país son 100 cambios,
        de sobra para un día — y si un día hubiera más, se recogen en la pasada siguiente
        en vez de vaciar la cuota de golpe.
        """
        if not self.enabled:
            logger.info("[streaming] MOVIEOFTHENIGHT_API_KEY no configurada — nada que hacer")
            return []

        salida: List[dict] = []
        cursor: Optional[str] = None
        for _ in range(max_pages):
            params = {
                "country": country,
                "change_type": change_type,
                "item_type": "show",
                "show_type": "movie",
                "catalogs": ",".join(catalogs),
                # `desc` y no `asc`: con ascendente se piden las MÁS ANTIGUAS de la
                # ventana, y con `max_pages` cortando a 100 nunca se llega a las de
                # hoy. Así llegaron 42 "novedades" del 13-17 de julio, un mes viejas,
                # el 2026-08-12. Para `expiring` da igual el orden —la ventana entera
                # es futura— pero para `new` es la diferencia entre novedad y archivo.
                "order_direction": "desc",
            }
            if cursor:
                params["cursor"] = cursor
            try:
                r = await self._client.get("/changes", params=params)
                self.requests_made += 1
                self._registrar_cuota(r.headers)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"[streaming] {change_type} {country}: HTTP {e.response.status_code}")
                self.errors += 1
                break
            except Exception as e:
                logger.error(f"[streaming] {change_type} {country}: {type(e).__name__} {e}")
                self.errors += 1
                break

            data = r.json()
            shows = data.get("shows") or {}
            for c in data.get("changes") or []:
                show = shows.get(str(c.get("showId"))) or {}
                tmdb_id = _tmdb_id_de(show.get("tmdbId"))
                if tmdb_id is None:
                    continue
                salida.append({
                    "tmdb_id": tmdb_id,
                    "service": (c.get("service") or {}).get("id"),
                    "change_type": c.get("changeType"),
                    "option_type": c.get("streamingOptionType"),
                    "effective_at": _fecha_de(c.get("timestamp")),
                    "link": c.get("link"),
                })
            if not data.get("hasMore"):
                break
            cursor = data.get("nextCursor")
            if not cursor:
                break
        return salida


def _tmdb_id_de(valor: Optional[str]) -> Optional[int]:
    """`"movie/823464"` → `823464`. Las series vienen como `tv/...` y se descartan."""
    if not valor or not isinstance(valor, str):
        return None
    tipo, _, num = valor.partition("/")
    if tipo != "movie" or not num.isdigit():
        return None
    return int(num)


def _fecha_de(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
