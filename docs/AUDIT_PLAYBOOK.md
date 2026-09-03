# Playbook de auditoría — los fallos que ESTE repo comete de verdad

No es una lista de buenas prácticas. Es el catálogo de las clases de defecto **encontradas
aquí**, con su instancia real, el número medido y cómo cazarlas. Sirve para auditar dirigido
en vez de genérico. Barrido de `CLAUDE.md`, `BACKLOG.md` (5.015 líneas), `AGENTS.md` y
`STACK_RULES.md` el 2026-08-18.

**No duplica** las listas que ya se mantienen en otro sitio: los 8+ anti-patrones de código
(singletons, sesiones compartidas, N+1, IDOR, tipos de ID) viven en `AGENTS.md §Anti-Patterns`
y `STACK_RULES.md`. Aquí está lo que aquellos no cubren: **los fallos que no se ven leyendo el
código**, y las trampas de medición que hicieron perder sesiones enteras.

Tres reglas que salen de repetir los mismos errores:

> **1. Una guardia no es una reparación.** El test protege escrituras futuras; el dato viejo
> sigue roto hasta que alguien lo barre. Pasó siete semanas con el payload de Qdrant.
>
> **2. Un monitor que sólo sabe decir «bien» es peor que ninguno.** Varias entradas de abajo
> son alarmas que nunca podían dispararse.
>
> **3. La mitad de los hallazgos de una auditoría son falsos positivos.** En la de 2026-06-03
> se descartó ~la mitad; la tabla B-01…B-13 del BACKLOG lista 6 confirmados como falsos.
> Verificar contra el código vivo **antes** de tocar. La sección F es la lista de lo que NO
> hay que "arreglar".

---

## A. Ya cazado automáticamente

Si una reaparece, el test se borró o se rodeó. Mirar el test antes que el código.

| guardia | clase que cierra | qué encontró al escribirse |
|---|---|---|
| `test_invariants_static.py` | `== True`, `KEYS *`, `await from_url` | escaneo estático de todo `backend/` |
| `test_qdrant_filter_contract.py` | claves de filtro que nadie atiende | 3 (`mpaa_ratings`, `min_oscar_wins`, `exclude_adult`) |
| `test_no_dead_parameters.py` | parámetros que nadie lee | 8 reales, y **descartó 4 que sí hacen falta** |
| `test_qdrant_payload_writers.py` | payloads que borran campos | 5 escritores |
| `test_nlp_language_guard.py` | el parser inventando campos opcionales | `original_language` fabricado 6 de 10 veces |
| `test_provider_source_filter.py` | que proveedor/watchlist sigan filtrando en ORIGEN | correlación, país y claves JSONB de la SQL compilada |
| `test_watchlist_phrase.py` | «de mi lista» disparando de más o de menos | trampas reales (*la lista de Schindler*) |
| `test_nullable_desc_ordering.py` | `ORDER BY <nullable> DESC` sin `nullslast()` | **7 sitios**, 6 reales — entre ellos «lo último que viste» |
| `test_no_dead_props.py` | props declaradas que ningún componente lee | **10**, 9 en `movie-card` |

---

## B. Datos y estado — hay que barrerlo, no se ve leyendo código

### B1. Clave ausente en el payload = película invisible, sin error
**2026-08-18:** 931 de 21.374 puntos (4,4%) sin **diez** claves. Llevaban `rating`, el nombre
viejo de `vote_average`. Forrest Gump, Finding Nemo, Apocalypse Now. Todo filtro de Qdrant los
descartaba, sin una línea en ningún log. ⚠ La guardia (A, fila 4) existía desde 2026-07-30 y
**no reparó nada**.
```powershell
docker compose exec backend python scripts/sync_qdrant_payload.py --fill-missing --dry-run
```
Debe decir **0**. Trampa: los `None` no son hueco (una clave ausente y una a null filtran
igual) — el primer ensayo dio 6.193 "incompletos" de los que 5.878 eran ruido.

### B2. Un filtro no es un filtro, es su SELECTIVIDAD
El barrido de 10 sitios (2026-07-29) midió año/género/duración/VBS —que conservan más de media
base— y concluyó que `niche` y `wildcard` «ya llevan holgura». **Plataformas** conserva un
tercio y las mataba: `niche` 20→3, `wildcard` 10→3, `random` desaparecía. **Watchlist**
conserva el 4%. **Cómo cazarlo:** tasa de supervivencia por fila, por filtro. Heredar el
veredicto de otro filtro es el error.

### B3. Un número documentado que ya no se reproduce
El golden set baja solo cuando el enriquecimiento hace visibles películas sin etiquetar. Los
ejes de mood: CLAUDE.md decía `r=+0.01` y hoy son **−0,094** (coseno entre ejes) y **−0,188**
(proyecciones), **independientes del tamaño del catálogo** (±0,0002 del 25% al 100%).
**Cómo cazarlo:** re-medir los números que sostienen una decisión de diseño. Antes de llamarlo
deriva, descartar que la definición sea otra — aquí hubo que probar dos.

### B4. Dos nombres para el mismo campo tras un renombrado
`rating` → `vote_average`: el código leía uno y el 4,4% del dato tenía el otro. **Cómo
cazarlo:** al renombrar, **contar filas con el nombre viejo** antes de dar el cambio por hecho.

### B5. `desc()` sobre columna nullable sin `.nullslast()`
Postgres ordena NULL **primero** en DESC: no falla, no avisa, devuelve el orden contrario. Las
4 barridas el 2026-08-11 (*The Dark Knight* encabezando «lo último que has visto») y una quinta
el 2026-08-17: «Top Rated in Your Watchlist» abría con las **17 películas SIN puntuar** de u212.
**Ya cubierto** por `test_nullable_desc_ordering.py` (2026-08-18), que al escribirse
encontró 7 sitios más: la peor, «Last Watched» de u212 devolviendo *Barbie and the Diamond
Castle* (sin fecha) en vez de *The Devil Wears Prada* (7 ago).

### B6. La columna significa otra cosa
`created_at` es la fecha de import —idéntica para cientos de filas del mismo ZIP—, así que todo
decay que la lea es una constante disfrazada. **Se coló tres veces.** Y `watched_date` mezclaba
tres fechas distintas de Letterboxd. **Cómo cazarlo:** mirar la **distribución real** de todo
campo temporal usado en un cálculo (cuántos valores distintos, cuántos idénticos), no su nombre.

### B7. Un umbral que no puede dispararse nunca
El suelo de confianza del showcase (`MIN_MEAN_SCORE = 55`) leía una escala cuyo mínimo es 60.
Parecía una red y no lo era; se borró el 2026-08-11. **Cómo cazarlo:** por cada umbral,
comprobar que el rango de lo que mide lo cruza de verdad.

### B8. Caché sin versionar junto a lo que la invalida
`signal_cache:*` **no** lo versiona `FEED_CACHE_VERSION`: hay que vaciarlo a mano cuando cambia
la lógica de señales. Y las secciones refrescan TTL en cache-hit, así que **lo rancio se
autoperpetúa**.

### B9. Fuentes sin rastrear
**2026-08-18:** 60 ficheros nunca añadidos a git — 6 servicios, 1 router, 6 migraciones, 28
tests. `mood_axes.py`, declarado «única fuente de verdad» en CLAUDE.md, **sin una línea de
historia**, y por eso no se pudo comprobar si sus anclas habían cambiado.
```powershell
git status --porcelain -uall | Select-String '^\?\?'
```
Vacío salvo `.bak`. Ojo: CLAUDE.md, BACKLOG.md y `docs/DEPLOYMENT.md` son gitignored a
propósito, **y ripgrep no los ve** — un renombrado parece completo cuando no lo está.

### B10. Promediar vectores sin centrar (el centroide que sólo dice «esto es una película»)
Promediar N vectores unitarios **amplifica la componente que comparten y cancela las
distintivas**. En un espacio anisótropo —dos películas al azar a coseno 0,48— eso colapsa
el centroide hacia el centro del catálogo, y **rápido**: con 10 películas al azar ya estás
a 0,950 del centro; con 50, a 0,989.

Medido en producción (2026-08-19): **el centroide de gusto de u210 está a 0,971 del centro
del catálogo**. Su vector de gustos es un 97% «esto es una película». Perverso: cuantas
más películas puntúa un usuario, PEOR es su vector.

Centrar (α=0.5) antes de promediar lo arregla — el suelo de ruido pasa de 0,468 a 0,067.
De 7 sitios que promedian vectores en el repo, **sólo `compute_mood_axes.py` centra**.

**Cómo cazarlo:** `grep -rn "\.mean(axis=0)" backend --include=*.py`, y por cada uno
preguntar si lo que sale se compara luego por coseno. Si sí, medir su coseno contra el
centroide global: por encima de ~0,95 el objeto no distingue nada.

⚠ Ojo con el corolario, que también está medido: **arreglar la geometría no arregla el
producto**. Con los centroides centrados, la señal inter-director sigue perdiendo contra
«recomienda directores famosos». Geometría y utilidad son dos mediciones distintas.

### B10b. Un fichero temporal en `backend/` hace fallar un test AJENO
Los guardias estáticos hacen `rglob("*.py")` y leen cada fichero. Un script de medición
copiado a `backend/` y borrado a mitad de una corrida hizo fallar
`test_invariants_static[redis-from-url-is-sync]` **en otra sesión** (2026-08-19).

**La señal de que la alarma es falsa: el invariante que falla CAMBIA DE NOMBRE al re-ejecutar**
—`redis-from-url-is-sync`, luego `qdrant-id-confusion`—. Un fallo real es estable. Tomado al
pie de la letra habría mandado a alguien a cazar un bug de Redis que no existe.

**Los scripts de medición van al scratchpad, nunca a `backend/`.**

### B11. Un dato perecedero tratado como permanente
La ventana de `expiring` dura ~7 días; con la ingesta parada cinco, las candidatas con calidad
cayeron de 41 a 3. No se rompió nada: caducaron. **Cómo cazarlo:** por cada fuente externa,
cuánto vive su dato y qué pasa si el job no corre. Si la respuesta es «la fila miente», la fila
debe ocultarse sola.

### B12. `LIMIT` sin `ORDER BY` — la cola que no rota
`get_movies_to_refresh` seleccionaba las películas a refrescar con `query.limit(100)` y **sin
orden**. Sin `ORDER BY`, Postgres devuelve lo que le convenga del heap: con 179 elegibles y
limit 100, la misma centena puede volver run tras run y las otras 79 no refrescarse jamás.
Medido el 2026-08-19: **38 filas de `movie_availability` en ES ancladas en marzo-mayo** (hasta
5 meses) mientras el resto del país estaba al día — el badge de «disponible en» las afirmaba
igual, sin marca de duda. No es lentitud de la cola: es que no había cola.

Y el arreglo tiene su propia trampa, espejo de B5: en **ASC** Postgres pone los NULL al
**FINAL**, y aquí `last_metadata_refresh IS NULL` significa «no se ha refrescado nunca» — las
422 más urgentes, las últimas. Hace falta `.asc().nulls_first()`.

**Cómo cazarlo:** todo `.limit(n)` sobre una selección que se consume por lotes necesita orden
explícito. La pregunta que lo revela: *si ejecuto esto dos veces seguidas, ¿sale lo mismo?* Si
la respuesta es «no lo sé», la rotación no rota. Cubierto ahora por
`test_nullable_desc_ordering.py::test_la_rotacion_de_refresh_tiene_orden`.

**Segunda instancia el mismo día, encontrada por otra sesión y en otro fichero:**
`compute_anti_vector` pedía `LIMIT 50` sobre las negativas del usuario sin orden, y luego el
decay por antigüedad las descartaba. u210 tenía 615 negativas y de las 50 arbitrarias que
devolvía Postgres **49 caían bajo el peso mínimo** — el usuario con más datos se quedaba sin
anti-vector. Dos apariciones independientes en un día miden bien la frecuencia real de esta
clase: no es rara, es la forma por defecto de escribir un `LIMIT` cuando no piensas en el orden.

### B13. Un escritor que sabe añadir pero no sabe QUITAR
`_guardar_disponibilidad` recorría `for pais in datos` — sólo los países que TMDB devuelve.
Cuando una película deja de estar en España, TMDB deja de mandar el bloque `ES` y **la fila
vieja no se toca jamás**: sigue afirmando el proveedor para siempre. 49 filas de ES lo hacían
con hasta 5 meses, y **47 habían sido visitadas por ese mismo refresco después de la fecha de
la fila** — la prueba de que no era una cola lenta sino una limpieza inexistente. *A Serbian
Film* se refrescó el 13-08 y estrenó fila de otro país ese día, con la de ES clavada en abril.

Segunda capa: el guardia `if not datos: return` trataba `{}` como respuesta mala. Pero si la
llamada falla, `refresh_movie` sale con `None` antes; llegar aquí con `{}` significa «no está
en ningún sitio, en ningún país». Tres películas más (*Tallulah*, *A Free Man*, *Emotional
Architecture 1959*) llevaban meses afirmando Netflix/Filmin/Arte por esa lectura.

**Cómo cazarlo:** por cada escritor de estado derivado de una fuente externa, preguntar *qué
pasa cuando la fuente deja de mencionar algo que antes mencionaba*. Si la respuesta es «no
pasa nada», el dato viejo es permanente. Un `upsert` en bucle sobre lo que la API devuelve
tiene siempre esta forma. Y ojo con el hermano: **una cota de frescura al LEER esconde el
síntoma sin arreglar la causa** — necesaria (un falso positivo aquí se paga abriendo la app y
no estando), pero no es la reparación. Cubierto por `test_provider_source_filter.py`.

---

## C. Medición — cuando el fallo es el instrumento

Las reglas largas están en CLAUDE.md (*Measuring*, *Comparing two numbers*). Aquí van **las
instancias concretas**, que es lo que hace reconocible la trampa.

### C1. Una cifra tomada a través del parser no determinista
«thrillers coreanos 1 → 20» era **1 → 3**. El salto vino de que el parser, *en esa ejecución*,
también puso `original_language="ko"`, que Qdrant sí filtra. Variabilidad del parser atribuida
al arreglo; el mensaje del commit `5a43f3c` lo atribuye mal. **Regla:** toda cifra que dependa
del parser necesita `--repeat` y el contador de filas sin parsear. Lo medible sin LLM se mide
sin LLM (`forced_intent`, `verify_search_branches.py` 11/11 sin tocar Groq).

### C2. La medición consumió el presupuesto que la hacía válida
Las cifras agregadas del panel (32/40, 34/40, 33/41) se tomaron con Groq cayéndose: la corrida
honesta dio **26 de 41 filas sin parsear** y `TPD: Limit 200000, Used 199761` — el diario
agotado por las propias corridas del panel.

### C3. Un monitor instrumentado sobre un campo que otra rama pisa
El audit detectaba «parser caído» buscando texto en `intent.reasoning`, pero las ramas
*catalogue* y *audience* **sobrescriben** ese campo, y son justo las ramas donde cae una
ejecución sin parser. **Toda fila degradada se leía como aprobado limpio.** Ahora lee
`resp.degraded`, que lo pone el código que decide la rama.

### C4. Una ventana fija más llamadas lentas = «el límite no limita»
6 llamadas seguidas sin 429 porque cada una hacía un parse de Groq (~4s) y **cruzaron el borde
del minuto**: la ventana fija veía 3 y 3. Para probar un límite por minuto hacen falta
peticiones **rápidas** (un título exacto corta a item-to-item antes del LLM, ~200ms).

### C5. Buscar la clave equivocada y concluir que no hay nada
«Redis no guarda ninguna clave del limitador»: la clave se nombra por **RUTA**
(`/api/search/try`), no por función, y se buscaba `try_search`. Además el primer escaneo corrió
pasado el TTL de 60s.

### C6. Un log leído fuera de hora
`column movies.mood_gravedad does not exist` parecía tener la búsqueda caída. El log era
**anterior** a que la migración se aplicara, en un reinicio de la propia sesión. **Antes de
abrir un «X está roto» a partir de una traza pegada: mirar la hora y re-ejecutar.**

### C7. Un contador de fallos que reporta el último síntoma, no la causa
`enrich_vectors` decía «chain quota exhausted» y la cuota estaba **intacta**: las 76 pendientes
tenían la sinopsis vacía y la guarda anti-alucinación las rechazaba **sin llamar a la API** —
por eso no había ni un warning de Groq. Enriquecibles de verdad: **0 de 76**. Y como seguían
en la query de candidatos, volvían en cada ejecución y disparaban el corte de 8 fallbacks.
**Doble lección:** un candidato imposible que no se excluye de la query miente para siempre, y
un diagnóstico intermedio correcto (el techo real de 8000 TPM) puede **no ser la causa**.

### C8. El suelo de ruido hay que medirlo sobre la cadena ENTERA
Al comprobar los ejes de mood, la primera versión submuestreaba sólo las proyecciones con las
direcciones ya fijadas — y así no se prueba el camino que importa, porque las direcciones salen
de vectores *centrados* y la población podría moverlas. (Resultó que no las mueve, ±0,0002,
pero eso había que medirlo.)

### C9. Hipótesis que parecían la respuesta y no lo eran
Anotadas para no volver a perseguirlas: **recall de HNSW** bajo `HasIdCondition` (refutada con
`exact=True`: resultados idénticos) y el **`score_threshold`** (el router pasa 0.3 y los
candidatos estaban en 0.50). La causa real era una clave ausente en el payload.

### C10. El reloj del contenedor no es el tuyo
Con el proyecto sin abrir dos semanas, el stack quedó suspendido y al reanudarse los contenedores
arrancaron con el reloj **congelado en 2026-08-19**, saltando al 2026-09-02 real sólo cuando
Docker los reinició a mitad de sesión. `docker compose ps` lo delata: «creados hace 13 días, Up
26 minutes».

Todo lo que se compare contra `now()` —cotas de frescura, decay, ventanas de caducidad— cambia
de veredicto. **Mordió dos veces el mismo día**, y en direcciones opuestas: primero dijo que el
filtro de frescura de disponibilidad costaba el **0,3%** (inofensivo) y luego el **99,9%** (letal);
con el reloj bueno y el refresco al día, el número real es **~2%**. El mismo SQL, el mismo dato,
tres respuestas.

Lo peor del caso: la segunda lectura salió **99,4% visible**, que está cerca del 98,3% correcto
— acertó por casualidad, con el reloj mal. Un número que resulta ser aproximadamente cierto por
accidente es más peligroso que uno claramente absurdo, porque no dispara ninguna alarma.

**Cómo cazarlo:** antes de creerte cualquier medición con fechas, `docker compose exec <svc> date`
contra la del host, **y `select now()` en Postgres**, que es quien evalúa el filtro. Un `max(fecha)`
sospechosamente redondo o idéntico a una fecha de sesión anterior es la otra señal.

---

## D. Forma del código — defectos que las auditorías encontraron

### D1. Un refresco que degrada silenciosamente el dato bueno
**REV-2:** cualquier refresco de metadatos (RSS, enriquecido en background, `reenrich_movies`)
**reemplazaba vectores enriquecidos por Groq** con la receta de reserva, porque el re-encode no
pasaba `text_override`. **Cómo cazarlo:** por cada camino de refresco, preguntar qué campo
enriquecido puede pisar.

### D2. Una variable asignada dentro de un `if` dentro de un bucle
**REV-1:** `streaming_providers` sólo se asignaba si la película estaba en el mapa → las que no
(TMDB devuelve `None` para las que no tienen proveedores) heredaban **las de la película
anterior**, o reventaban con `UnboundLocalError` → 500.

### D3. Un método que no existe en la versión instalada
**REV-3:** `redis.aclose()` no existe en redis-py 4.6 → `AttributeError` en **cada apagado**, y
`close_services()` nunca corría. Nadie lo notó.

### D4. Check-then-act donde hay concurrencia
Upserts de rating y el lock del cache-stampede eran SELECT-y-luego-decidir: un doble clic metía
la unique index en un 500. Todos a `INSERT … ON CONFLICT DO UPDATE` y `SET NX EX` con token.

### D5. Una rama que existe para restringir y no restringe
Con `audience_request` puesto y `include_genres` vacío, la rama caía a la barra de calidad
pelada: *«a movie parents and kids will both enjoy»* devolvía **Athlete A** (documental sobre
abusos) y *El espíritu de la colmena* a VBS 85. El mismo fallo confiado-y-equivocado que la rama
existía para arreglar.

### D6. Guardias deterministas que sólo corren en el camino feliz
`finalize_intent` se llamaba únicamente desde el parse con éxito, así que con Groq limitado se
perdían **todas** — y son reglas de texto puro que no necesitan modelo. Ahora las 4 vías de
rendición también finalizan.

### D7. La instalación eléctrica llega y falta el interruptor
`scope: "watchlist" | "global"` estaba en el shell, el contexto y la llamada; `right-console`
declaraba `onScopeChange` y **nunca lo llamaba**: el feed llevaba meses clavado en «global». La
chapa de `leaving_soon` viajaba entera y `movie-card` sólo la pintaba si `type === "upcoming"`.
**Ya cubierto** por `test_no_dead_props.py` (2026-08-18), que encontró otras 10: nueve en
`movie-card`, con `movie-carousel` calculándolas y pasándolas una por una a un componente que
las tiraba. Restos de la migración de UI.

### D8. SQLAlchemy: EXISTS que se auto-correlaciona
Al unir `movie_availability` en la consulta principal, el `EXISTS` de `apply_rail_filters` sobre
**esa misma tabla** se quedó sin FROM (*returned no FROM clauses due to auto-correlation*). Si
una query une una tabla que además aparece en el EXISTS de un helper: aliasar, o no pasarle ese
filtro.

### D9. `if not x` donde hay que distinguir vacío de ausente
`if movie.keywords is None:` **nunca** `if not movie.keywords:` — regla inamovible del repo.

---

## E. Dependencias externas — cómo mienten

### E1. Un 403 idéntico para lo que existe y lo que no
**letterboxd.com devuelve 403 a cualquier URL** tras Cloudflare (medido: mismo 403 para un
perfil real y uno inventado). Cualquier check que lea «no es 200» como «no existe» rechaza el
100% de los casos — así estuvo roto `PATCH /users/{id}/link-letterboxd` desde siempre,
invisible porque ningún botón llegaba a él. Usar `ScraperService._fetch_with_curl_cffi`.
Corolario: un 403 de `/v1/models` con `urllib` era **Cloudflare bloqueando ese cliente**, no la
clave — con el `AsyncOpenAI` de la app los tres modelos respondían.

### E2. Una señal entera muerta y en silencio
Trakt empezó a devolver 403 y la **Señal C estuvo muerta** sin que nada fallara. **Cómo
cazarlo:** por cada fuente externa, una comprobación que distinga «devolvió vacío» de «no
respondió».

### E3. Un modelo retirado por el proveedor
`qwen3-32b` fue dado de baja por Groq. **Verificar en vivo contra `/v1/models`** — las listas de
terceros están rancias. Un solo fichero a editar: `services/llm_models.py`.

### E4. Un dato que existe pero no en el idioma que pides
47 películas invisibles porque TMDB no tenía su sinopsis **en inglés**. Existían con otra
lengua original.

### E5. Un límite que no es el que crees
El techo de Groq que ata no es el diario (200k TPD) sino **8000 tokens por MINUTO**, y el
`max_retries=3` de instructor multiplica el consumo encima del reintento del SDK.

---

## F. Lo que NO hay que "arreglar" — falsos positivos verificados

Reintroducir un "arreglo" aquí es un retroceso. Comprobado contra código vivo:

- **`current_user` sin leer en `link_letterboxd` NO es código muerto** — `Depends(...)` autoriza
  al llamante como efecto. Borrarlo quita una comprobación de seguridad. Un parámetro puede no
  leerse si algo **fuera del cuerpo** lo consume.
- **El commit por ítem del RSS es idempotente** y re-ejecutable (REL-5).
- **Los índices de watchlist/watched ya existen** (PERF-5).
- **Los embeddings ya van envueltos en executor** (CONC-4).
- **Las «5 carreras de hilos» son falsas** bajo asyncio de un solo hilo.
- **`clustering_service.py:760` no sufre inanición**: sobre-pide `PER_ANCHOR_LIMIT + 5` y da
  19,0/20 de media incluso con 2.196 vistas.
- **SEC-GROUP** (descubrimiento público de grupos) está **aceptado por diseño** — no re-marcarlo.
- **Un sinsentido durante una caída devuelve películas y está bien así**: medido, los grupos
  sinsentido y reales se solapan ±0,076, ningún umbral los separa, y la respuesta lleva
  `degraded` para que la UI lo diga.
- **Que un eje de mood correlacione con un género no lo invalida**: la prueba que decide es si
  discrimina DENTRO del género.

---

## G. Rutina mínima de auditoría

1. `docker compose exec backend python -m pytest -q` — la sección A entera.
2. `sync_qdrant_payload.py --fill-missing --dry-run` → debe dar **0** (B1).
3. `git status --porcelain -uall` → sin `??` salvo `.bak` (B9).
4. Re-medir un número de los que sostienen una decisión (B3) — elegir uno distinto cada vez.
5. Por cada filtro nuevo desde la última auditoría: tasa de supervivencia por fila (B2).
6. `docker compose restart backend` / `up -d --build frontend` **antes** de creerse cualquier
   medida: un type-check que pasa no es un render que pasa.

> Al escribir una guardia nueva, **medir su tasa de falsos positivos antes de dejarla**. La de
> `nullslast` sacó 21 sitios en su primera versión y sólo 3 eran reales: miraba la línea en vez
> de la sentencia, y trataba como nullable toda columna que el esquema permitía, cuando
> `created_at` y compañía llevan `default=` y tienen **cero** NULL. Un check que grita en falso
> se borra, y entonces la regla queda otra vez sin enforcement.
