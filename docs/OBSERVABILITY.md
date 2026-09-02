# Observabilidad — Jaeger, OTel y contadores Redis

Decisión **0.4** del plan de landing, cerrada el 2026-07-27: se mide con **OpenTelemetry +
contadores en Redis**, no con una herramienta externa. Motivo: la instrumentación ya está
puesta, no añade dependencias y **no pone cookies**, así que no arrastra banner de
consentimiento.

Este documento cubre las dos mitades: los **trazas** (ya funcionando, infrautilizadas) y los
**contadores de embudo** (4 de 7 construidos, 2026-08-11 — ver §4).

---

## 1. Qué hay montado ahora mismo

| Pieza | Dónde | Estado |
|---|---|---|
| `setup_telemetry()` | `backend/telemetry.py` | ✅ se llama en el `lifespan` de `main.py` |
| Instrumentación FastAPI | `FastAPIInstrumentor` | ✅ una traza por petición HTTP |
| Instrumentación SQLAlchemy | `SQLAlchemyInstrumentor` | ✅ cada consulta a Postgres es un span |
| Instrumentación Redis | `RedisInstrumentor` | ✅ cada GET/SET es un span |
| Spans manuales del trident | `recommendation_engine.py:432, :914` | ⚠️ solo Señal A y Señal C |
| Contadores de embudo | — | ❌ no existen |

**Exportador:** OTLP/gRPC a `http://jaeger:4317`, servicio `vectorbox-backend`
(`docker-compose.yml:108-109`). Si Jaeger no está, `setup_telemetry()` avisa y la app sigue
funcionando — el fallo nunca tumba el backend.

---

## 2. Jaeger, de principio a fin

### Abrirlo

```powershell
docker compose up -d jaeger      # ya arranca con el stack completo
start http://127.0.0.1:16686     # UI
```

Los puertos están atados a `127.0.0.1` a propósito: `16686` (UI), `4317` (OTLP gRPC, el que
usa el backend) y `4318` (OTLP HTTP, sin usar). Nada de esto se expone fuera de la máquina.

### La pestaña **Search** — encontrar una petición

1. **Service**: `vectorbox-backend`.
2. **Operation**: el nombre de la ruta, p. ej. `POST /api/search/natural`. Déjalo en
   `all` si buscas por otra cosa.
3. **Tags**: filtro por atributos del span. Los que sirven aquí:
   - `http.status_code=500` — solo lo que petó.
   - `http.status_code=429` — quién está tocando el límite.
   - `http.target=/api/search/natural` — una ruta concreta sin depender del nombre.
   - `error=true` — cualquier span marcado como error, a cualquier profundidad.
4. **Lookback**: por defecto la última hora. Para un incidente de ayer, `Custom Time Range`.
5. **Min/Max Duration**: **la más útil de todas**. `minDuration=2s` te lista solo las
   peticiones lentas. Así es como se cazó el bug de `max_retries` — filtrando por `>10s`
   aparecían solo parses de Magic Box.

### Leer una traza

Cada barra es un span. La anidación es la pila de llamadas:

```
POST /api/search/natural                      1.42s   ← span raíz (FastAPI)
├── parse_user_intent                         0.89s   ← LLM
├── SELECT movies WHERE tmdb_id IN (...)      0.11s   ← SQLAlchemy, automático
└── redis GET tmdb:movie:1234                 0.002s  ← Redis, automático
```

Qué mirar, por orden:

- **Huecos entre spans.** Si el raíz dura 1,4 s y los hijos suman 0,3 s, ese segundo perdido
  es tiempo que nadie está midiendo: falta un span manual ahí.
- **El mismo span repetido N veces.** Es un N+1. Si ves 40 `SELECT movies WHERE id = ?`
  seguidos, eso es una consulta que debería ser un `IN (...)`.
- **Spans largos sin hijos.** Trabajo de CPU o una espera de red sin instrumentar.
- **`error=true` en rojo.** Abre el span y mira `Logs` → ahí está la excepción.

### La pestaña **Compare** — antes y después

Selecciona dos trazas en los resultados y pulsa *Compare*. Es la forma honesta de demostrar
una mejora de rendimiento: se ve qué span desapareció o se encogió. Úsala al cerrar cualquier
tarea que diga "esto va más rápido".

### La pestaña **System Architecture**

`DAG` dibuja qué servicio llama a cuál, contando llamadas reales de las trazas. Con un solo
servicio backend aporta poco; empezará a servir el día que haya un worker separado.

### Lo que Jaeger **no** hace

No es analítica de producto ni almacenamiento a largo plazo. `all-in-one` guarda las trazas
**en memoria**: `docker compose restart jaeger` y se pierde todo. Sirve para diagnosticar
ahora, no para responder "cuánta gente creó perfil el mes pasado" — eso es la sección 4.

---

## 3. Añadir un span cuando el automático no llega

Las instrumentaciones cubren HTTP, SQL y Redis. Todo lo demás —una llamada a Groq, un cálculo
de VBS, una búsqueda en Qdrant— es invisible salvo que lo marques.

```python
from telemetry import get_tracer

_tracer = get_tracer(__name__)          # a nivel de módulo, una sola vez

async def parse_user_intent(user_query: str) -> MovieSearchIntent:
    with _tracer.start_as_current_span("magicbox.parse") as span:
        span.set_attribute("query.length", len(user_query))
        span.set_attribute("model", primary_model)
        intent = await _call_llm(...)
        span.set_attribute("intent.has_language", intent.original_language is not None)
        span.set_attribute("intent.fell_back", "All models failed" in (intent.reasoning or ""))
        return intent
```

Reglas que evitan trazas inútiles:

- **Nombra por operación de negocio, no por función**: `magicbox.parse`, no `parse_user_intent`.
  El patrón `dominio.acción` ya está en uso (`trident.signal_a.because_you_watched`).
- **Atributos escalares y de cardinalidad baja.** `model`, `cache_hit`, `result_count`. Nunca
  metas la consulta del usuario ni un `user_id` — ver la sección 5.
- **Un span por unidad de trabajo que puedas querer cronometrar por separado.** Si nunca vas a
  preguntarte "¿cuánto tardó esto?", no lo instrumentes.
- **Marca los errores**: `span.set_status(Status(StatusCode.ERROR))` y `span.record_exception(e)`.

### Los tres huecos que hay hoy

Por orden de utilidad, dado lo que ya nos ha mordido:

1. **`magicbox.parse`** — la llamada a Groq no tiene span propio. Es exactamente donde vivían
   los 16 s de mediana del bug de `max_retries`, y hubo que encontrarlos leyendo logs a mano.
2. **`qdrant.search`** — las búsquedas vectoriales no aparecen. Cuando el feed va lento no se
   puede saber si es Qdrant o Postgres.
3. **`trident.signal_b`** — la Señal A y la C tienen span, la B no. Un trident a medio medir
   miente por omisión.

---

## 4. Contadores de embudo en Redis *(construido 2026-08-11, 4 de 7)*

> **Estado.** `services/metrics.py` existe con `bump()` tal como está diseñada abajo,
> más `read_today()`. Contando ya: `search.degraded`, `landing.query.chip` (con
> idioma), `landing.query.free` y `landing.cta.profile`.
>
> **Y el `POST /api/metrics/{evento}` público NO se ha abierto, a propósito.** Al ir
> a construirlo se comprobó que cinco de los seis eventos de frontend ya tienen una
> puerta de servidor **limpia**, o sea consumida por un solo sitio:
>
> | evento | puerta que ya existía |
> |---|---|
> | `landing.query.chip` | `GET /search/showcase` — sólo lo consumen los chips |
> | `landing.query.free` | `POST /search/try` — anónima por diseño |
> | `landing.cta.profile` | `POST /onboarding/init-session`, y sólo cuando se crea un invitado NUEVO (contar la llamada sería contar montajes de página) |
> | `landing.view` | no está en la API: sale del log de acceso del frontend |
> | `landing.mode.title` | **sin medida limpia** — el modo título llama a `autocomplete`, que la app con sesión también usa |
>
> Así que el endpoint compraría un evento limpio (`landing.mode.title`) y un
> `landing.view` más cómodo, a cambio de una **escritura sin autenticar a Redis desde
> internet** que hay que limitar, validar y vigilar. No paga. El ratio chip/free —el
> que este documento llama decisivo— ya se puede leer.
>
> El conjunto cerrado de los siete nombres vive en `services/metrics.py`, no en un
> endpoint, para que la validación no dependa de que el endpoint se acuerde de
> validar el día que se abra.

Las trazas responden *"¿por qué esta petición fue lenta?"*. No responden *"¿cuánta gente pulsa
crear perfil?"*. Para eso, contadores planos.

### Diseño

```python
# services/metrics.py  (nuevo)
from datetime import date

async def bump(redis, event: str, *, lang: str = "-") -> None:
    """Contador diario por evento. Sin PII, sin cookies, sin sesión."""
    key = f"metrics:{date.today().isoformat()}:{event}:{lang}"
    await redis.incr(key)
    await redis.expire(key, 60 * 60 * 24 * 90)   # 90 días y se borra solo
```

### Qué contar en la landing

| Evento | Qué contesta |
|---|---|
| `landing.view` | denominador de todo lo demás |
| `landing.query.chip` | cuánta gente usa un ejemplo precocinado |
| `landing.query.free` | cuánta escribe lo suyo — **el ratio chip/free decide si el caché de la Fase 1 sirve** |
| `landing.mode.title` | si el modo título se descubre o queda muerto |
| `landing.cta.profile` | clics en crear perfil |
| `landing.cta.letterboxd` | clics en importar |
| `search.degraded` | veces que el parser cayó al fallback o falló |

Con esos siete tienes el embudo entero y sabes si la landing nueva bate a la vieja.

### Leerlos

```powershell
docker compose exec backend python -c "
import asyncio, os
from datetime import date
import redis.asyncio as aioredis
async def main():
    r = aioredis.from_url(os.getenv('REDIS_URL','redis://redis:6379'), decode_responses=True)
    try:
        hoy, cursor = date.today().isoformat(), 0
        out = {}
        while True:
            cursor, keys = await r.scan(cursor, match=f'metrics:{hoy}:*', count=200)
            for k in keys:
                out[k] = await r.get(k)
            if cursor == 0: break
        for k in sorted(out): print(f'  {k:<45} {out[k]}')
    finally:
        await r.close()
asyncio.run(main())
"
```

`SCAN`, nunca `KEYS` — regla de `STACK_RULES.md` §2, y `test_invariants_static.py` la
comprueba.

---

## 5. Qué NO puede entrar en una traza ni en un contador

Esto es lo que mantiene la decisión 0.4 libre de banner de consentimiento. Romperlo cambia la
naturaleza legal de lo que se recoge.

- **Nunca la consulta del usuario** como atributo del span. Es texto libre: puede contener
  cualquier cosa. Mide `query.length`, no `query`.
- **Nunca `user_id`, correo, nombre ni IP.** Ni siquiera con hash: un identificador estable es
  un identificador.
- **Nunca la cookie `vb_anon_session`** ni ningún token.
- **Los contadores son agregados y punto.** `metrics:2026-07-27:landing.view:es` es un número.
  En cuanto le añades algo que distinga a una persona, deja de ser un contador y pasa a ser
  seguimiento.

Con esa línea puesta, no hay dato personal, no hace falta consentimiento y no hay banner.

---

## 6. Comandos

```powershell
# UI
start http://127.0.0.1:16686

# ¿está llegando algo?
docker compose logs backend --since 5m | Select-String "OTel"
#   → "[OTel] Tracer initialized — service='vectorbox-backend' endpoint='http://jaeger:4317'"

# generar una traza a mano
curl -X POST http://127.0.0.1:8000/api/search/natural -H "Content-Type: application/json" -d '{\"query\":\"algo lento sobre el duelo\"}'

# Jaeger sin trazas: casi siempre es esto
docker compose restart backend      # el tracer se inicializa en el lifespan

# vaciar las trazas (all-in-one guarda en memoria)
docker compose restart jaeger
```

**Aviso:** `jaeger` está en `docker-compose.yml` sin perfil, así que arranca siempre. Para
producción hay que decidir si se despliega o si el exportador apunta a otro sitio — hoy, si no
existe, el backend arranca igual y solo deja un WARNING.
