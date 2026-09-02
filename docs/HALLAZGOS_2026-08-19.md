# Hallazgos del 2026-08-19 — el espacio vectorial y el motor de recomendación

Resumen consolidado de una sesión larga, escrito para que sobreviva al contexto. El detalle
con todas las tablas está en `BACKLOG.md`; esto es lo que hay que recordar y lo que hay que
hacer.

---

## 1. EL HALLAZGO CENTRAL

**Promediar vectores en un espacio anisótropo destruye la señal. Comparar por pares apenas la
toca.** Ésa es la frase que resume la sesión, y explica por qué una decisión de agosto y otra
de hoy pueden ser las dos correctas.

| medida | crudo | centrado |
|---|---|---|
| PARES película↔película: ruido medio | 0,467 | 0,063 |
| PARES: **d'** | **3,91** | **4,76** |
| CENTROIDE de usuario: distancia al centro del catálogo | **0,971** | 0,676 |
| CENTROIDE: **d'** | **−0,33** | **+0,65** |

> **El d' del centroide es NEGATIVO en producción.** Las películas favoritas de un usuario
> puntúan PEOR contra su propio centroide que películas al azar, porque la media de 696 films
> diversos cae más cerca de los hubs genéricos que de cualquiera de los 696.

Y el colapso es rápido: promediando películas **al azar**, con K=10 el centroide ya está a
0,950 del centro del catálogo; con K=50, a 0,989.

**De 7 sitios que promedian vectores, sólo `compute_mood_axes` centra.**

---

## 2. POR QUÉ AGOSTO DIJO "NO CENTRAR" Y SIGUE TENIENDO RAZÓN

`experiment_centering.py` (2026-08-11) midió **pares**, no centroides: ¿centrar mejora la
escala de similitud entre películas? Respuesta correcta para esa pregunta — casi nada, a
cambio de reordenar el 25% de «más como esto» y obligar a re-indexar y re-clusterizar.

**No cambió el dato: cambió la pregunta.** Las dos decisiones son compatibles:
no centrar el índice almacenado (agosto) **y** centrar al calcular centroides (hoy). Y ésta
no exige re-indexar, que era el coste que mató a aquélla.

---

## 3. QUÉ GANA Y QUÉ NO — con controles de contaminación aplicados

Instrumento: **hold-out temporal** (`bench_person_discovery.py`, `bench_vector_space.py`).
Corte D → señal construida sólo con lo anterior → objetivos = lo que el usuario amó después.

| variante | K=10 | K=100 | K=300 | Q media top-30 |
|---|---|---|---|---|
| `solo_VBS` (ignora el vector) | 9% | 14% | 27% | 96,8 |
| **producción (crudo 70/30)** | **8%** | **13%** | **28%** | 95,3 |
| **ABTT k=1, 70/30** | **12%** | **17%** | **40%** | 94,5 |

**Dos controles que se llevaron por delante la mitad del titular:**

1. **¿descubrir o "más de lo mismo"?** El 39% de objetivos son de directores nunca vistos.
   Restringiendo a ésos, la ganancia de K=10 y K=100 **desaparece**.
2. **folds solapados** inflaban la significancia. Con folds independientes los intervalos se
   ensanchan como debían.

Tras ambos, ABTT vs `solo_VBS`, **sólo directores nuevos, folds independientes**:

| K | diferencia |
|---|---|
| 10 | +0,0pp [−2,3, +2,3] no concluyente |
| 100 | +2,4pp [−3,1, +7,9] no concluyente |
| **300** | **+14,9pp [+7,4, +22,4] ABTT MEJOR** |

**Lo único demostrado es la cola profunda.** En los 10 puestos visibles, empate.

---

## 4. LO QUE EL OJO VE Y LA MÉTRICA NO PUEDE CONFIRMAR: LA ÉPOCA

| usuario | su gusto | producción | ABTT |
|---|---|---|---|
| u210 año medio | 1996 | 1983 | **1997** |
| u212 año medio | **2012** | **1959** | **2002** |
| u212 % de 2010+ | 61% | **7%** | **63%** |

u212 ama cine de 2016 y producción le sirve Sunset Boulevard, Psycho, El gran dictador,
Luces de la ciudad — **todo 1931-1975**. ABTT le da Get Out (2017), Soul (2020), Hunt for the
Wilderpeople (2016), The Mitchells vs. the Machines (2021).

**ABTT acierta la época de cada usuario sin ningún prior.** Los priors explícitos que se
probaron (fijo, adaptativo, cuota) arreglaban a un usuario destruyendo al otro; éste no,
porque la época va implícita en lo que el vector centrado codifica.

Además, ABTT **cambia el top-10 por completo**: 0% de solape con ordenar por VBS puro, frente
al 40-60% de producción. El top SÍ se mueve; lo que falta es potencia para probar que mejora.

---

## 5. DECISIONES PASADAS RE-DERIVADAS

| decisión | veredicto |
|---|---|
| "no centrar Qdrant" (2026-08-11) | **SIGUE BIEN** — medía pares, y su coste sigue sin compensar |
| "el orden RRF no aporta, `solo_VBS` empata" (2026-08-11) | **CIERTO del espacio crudo, FALSO en general.** Con el espacio arreglado el orden gana |
| "el tope de calidad: quitarlo NO" (2026-08-11) | **SIGUE BIEN**, y pedía justo esta métrica |
| `CENTERING_ALPHA = 0.5` para centroides | **es la peor de las buenas** — α=0.75, α=1.0 y ABTT k=1 la baten |
| `k ≈ d/100` del paper All-but-the-Top | **NO transfiere**: k=1 gana a k=7 en dos tareas independientes |

---

## 6. INSTRUMENTOS ROTOS CAZADOS (cinco, todos míos)

Ésta es la lección de método de la sesión: **el riesgo no estaba en las ideas, estaba en los
denominadores.**

1. **Medir la coherencia en espacio crudo** → dio por muerta una vía que funciona.
2. **Leer `ci95()` como (lo, hi)** cuando devuelve **(media, semiancho)** → convirtió
   resultados significativos en "ambiguos" y al revés. Se detectó porque un intervalo salió
   con el límite inferior MAYOR que el superior.
3. **Canonicidad sin controlar** → "el VBS gana a todo". Los futuros de estos usuarios SON el
   canon. Se corrige estratificando por bandas ESTRECHAS de VBS (±2 puntos); con quintiles no
   basta, y ése fue un cuarto instrumento roto dentro del tercero.
4. **Dividir por todos los objetivos** cuando la fila tiene 10 huecos → un 22% del techo
   alcanzable se reportó como un "3%" alarmante.
5. **Medir a la altura equivocada** (top-10 cuando el centroide alimenta un pool de 200) →
   "no implementar" sobre un cambio que sí aporta donde se usa.

---

## 6b. EL ANTI-VECTOR NO SE DISTINGUE DEL AZAR (medido después de que aterrizara 1a)

Con el anti-vector ya en su versión de decil, se midió lo único que justifica que exista:
**¿separa lo que el usuario va a detestar de lo que va a amar?** Hold-out temporal, anti-vector
construido sólo con negativas anteriores al corte, objetivos = lo puntuado después.

| control | folds | AUC de producción (α=0) | veredicto |
|---|---|---|---|
| contra películas **al azar** | 6 | 0,584 | **sesgo de selección** |
| contra futuras **que le gustaron**, ventana 1 año, cortes trimestrales | 20 | 0,543 [0,517, 0,569] | **folds solapados** |
| **contra futuras que le gustaron, ventanas DISJUNTAS** | **17** | **0,527 [0,475, 0,579]** | **incluye 0,500** |

**El intervalo bueno contiene el azar.** Y las dos lecturas que parecían buenas eran instrumento:
la primera comparaba contra películas al azar, cuando una futura negativa es algo que el usuario
**eligió ver** — medía «lo que vería», no «lo que le disgusta»; la segunda usaba ventanas de un
año con cortes trimestrales, o sea folds que comparten 9 meses. El ancla del azar da 0,500 en la
tercera y daba **0,436** en la primera, que ya avisaba.

Y son **17 folds de UN usuario**: u212 nunca junta 8 negativas y 8 positivas en la misma ventana
de 92 días. La regla de «cuenta quién está de verdad en la muestra» otra vez.

**Centrar no lo salva**: −0,009 [−0,107, +0,089] pareado. Y va en direcciones opuestas según el
usuario (u210 0,551→0,493, u212 0,520→0,635), el mismo patrón que los priors de época del §4.

### El anti-vector es el octavo sitio que promedia sin centrar
`compute_anti_vector` hace `np.sum(w·v)/Σw` sobre vectores crudos, así que le pasa lo del §1:

| usuario | negativas usadas | coseno con el centro del catálogo | ρ con «lo genérico» |
|---|---|---|---|
| u210 | 50 | **0,980** | **0,945** |
| u212 | 32 | 0,810 | 0,603 |
| u280 | 3 | 0,524 | −0,180 |

Para u210 el anti-vector **es** el centro del catálogo: el decil que penaliza es el 10% más
genérico. Restarle el centro entero invierte el defecto en vez de arreglarlo (ρ pasa de +0,945
a −0,903: penaliza lo más raro), y ya se ha visto arriba que en la tarea real no cambia nada.

**Qué hacer:** *no* revertir 1a — arregló un defecto real (la tasa oscilaba del 0% al 73% según
el usuario) y lo hizo bien. Pero **no construir nada encima** —ni la v2 de percentil sobre el
catálogo que propone su propio docstring— hasta que la señal pase esta prueba. Y repetirla en
cuanto haya un tercer usuario con `watched_date`.

---

## 7. LO QUE FALTA ANTES DE TOCAR PRODUCCIÓN

1. ~~**Rehacer esto sobre el pipeline COMPLETO.**~~ **RESUELTO, y era más pequeño de lo que este
   documento decía.** El anti-vector se aplica sólo sobre `raw_recs[:30]` y termina en
   `adjusted_head + tail`: **el conjunto de los 30 primeros no cambia, sólo su orden interno**.
   Ninguna película del puesto 31 en adelante puede subir ni bajar. Así que el percentil, K=100 y
   K=300 son **matemáticamente inmunes** a él, y lo único que podía moverse era K=10 — donde el
   §3 ya reporta empate. La frase original («puede tapar o inventar cualquiera de estos efectos»)
   era una alarma sin comprobar, del tipo que este mismo documento avisa de no abrir.
   ⚠ La copia de BYW (`recommendation_engine.py:810`) **sí** actúa sobre todos los candidatos
   antes del MMR, así que ahí sí cambia el conjunto. Lo de arriba vale para la Señal A, no para BYW.
2. **Más usuarios con `watched_date`.** Sólo 210 y 212 lo tienen, y uno aporta tres cuartas
   partes de los folds. Cualquier política ajustada ahora estaría ajustada a dos puntos.
3. **No tocar `similar.py`** — muestra números calibrados sobre cosenos crudos.
4. Si se cambia el espacio, el pool de joyas ocultas lo pide hoy a Qdrant (crudo). La vía sin
   re-indexar es mantener la matriz centrada en memoria (21.405 × 768 ≈ **65 MB**).

---

## 8. HIGIENE: ficheros temporales en `backend/`

Un `strat.py` mío en `backend/` hizo **fallar un test de otra sesión**: los guardias estáticos
hacen `rglob("*.py")` y leen cada fichero; el mío desapareció entre el glob y la lectura. La
señal de que era falso: el invariante que fallaba **cambiaba de nombre** en cada re-ejecución.

**Los scripts de medición van al scratchpad, nunca a `backend/`.**
