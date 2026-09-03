"""Ejes de mood — las dos direcciones que el espacio de embeddings soporta.

ÚNICO fichero que se toca para reajustar un eje, igual que `llm_models.py` con
los modelos de Groq.

De dónde salen (medido 2026-08-04/05, sondas en el scratchpad):
  · Se probaron 12 moods unipolares al estilo StoryGraph. Los autovalores de su
    matriz de correlación fueron 4.85, 3.90, 1.04, 0.68, ... : **doce etiquetas
    viven en tres dimensiones** (Kaiser, autovalor > 1). Enseñar doce sería
    mentir con decimales.
  · Los moods TEMÁTICOS aguantan (épico z=3.42, extraño 3.20, aventurero 2.82) y
    los AFECTIVOS se caen (`esperanzador` z=0.73, no es una dirección). Coherente
    con que el embedding sea tema puro por decisión de 2026-06-27.
  · `divertido` correlaba +0.78 con el género Comedy y su top eran parodias
    (*Disaster Movie*): es el género con pasos extra. Sólo entra como POLO
    NEGATIVO de gravedad, nunca como eje propio.
  · Los contrastes BIPOLARES recuperan más independencia que los unipolares:
    restar un polo cancela la componente compartida ("seriedad"). Por eso los
    tres ejes se definen aquí como polo+ menos polo−, no como una sola nube.

Y lo que la medición prohíbe, que es tan importante como lo que permite:
  **el mood FILTRA, el mood no PUNTÚA.** Como rasgo de ranking pierde contra el
  embedding del que sale (−2.6pt IC95 [−4.4, −0.9], pareado sobre las mismas
  particiones). Meterlo en la fórmula empeora el feed. Su valor es dejar al
  usuario pilotar una dimensión que su historial no contiene: qué le apetece hoy.
"""
from typing import Dict, List, Sequence

# Centrado de anisotropía: dos películas al azar están a coseno 0.482 sin restar
# nada, y ahí ninguna dirección discrimina. Con α=0.5 baja a 0.072. Mismo valor
# que la fusión de grupos, y por la misma razón.
CENTERING_ALPHA = 0.5

# Grupos de anclas. Un grupo NO es un eje: son las piezas con las que se montan
# los dos. Cada película tiene que existir en el catálogo por título exacto.
ANCHOR_GROUPS: Dict[str, List[str]] = {
    "tenso": ["Uncut Gems", "Sicario", "No Country for Old Men", "Whiplash",
              "Prisoners", "Good Time", "Nightcrawler", "The Wages of Fear"],
    "oscuro": ["Se7en", "Requiem for a Dream", "Come and See", "Funny Games",
               "Threads", "Angst"],
    "misterioso": ["Zodiac", "The Third Man", "Mulholland Drive", "Memento",
                   "The Prestige", "Knives Out"],
    "reflexivo": ["Solaris", "2001: A Space Odyssey", "Paterson", "The Tree of Life",
                  "Stalker", "Wings of Desire"],
    "divertido": ["Airplane!", "The Big Lebowski", "Dumb and Dumber", "Hot Fuzz",
                  "Monty Python and the Holy Grail", "Superbad"],
    "aventurero": ["Raiders of the Lost Ark", "The Goonies", "Jurassic Park",
                   "Indiana Jones and the Last Crusade", "The Mummy",
                   "Pirates of the Caribbean: The Curse of the Black Pearl"],
    "triste": ["Manchester by the Sea", "Grave of the Fireflies", "Blue Valentine",
               "Amour", "Tokyo Story", "Past Lives"],
    "romantico": ["Before Sunrise", "Casablanca", "In the Mood for Love",
                  "Notting Hill", "Titanic", "La La Land"],
    "relajante": ["My Neighbor Totoro", "Perfect Days", "Kiki's Delivery Service",
                  "Chef", "Julie & Julia", "Babe"],
    "extrano": ["Eraserhead", "Mulholland Drive", "Holy Motors", "Naked Lunch",
                "The Lobster", "Being John Malkovich"],
}

# Los dos ejes, con los grupos que cargaban |>=0.30| en cada componente del PCA.
# El nombre de la columna es el de la clave.
AXES: Dict[str, Dict[str, Sequence[str]]] = {
    # 43% de la varianza. Lo pesado y serio frente al entretenimiento ligero.
    "gravedad": {
        "pos": ["oscuro", "misterioso", "tenso", "reflexivo"],
        "neg": ["divertido", "aventurero"],
    },
    # 29%. Sentimiento humano sincero frente a la excentricidad de género.
    "humanidad": {
        "pos": ["triste", "romantico", "relajante"],
        "neg": ["extrano"],
    },
}

# NO hay tercer eje, y no por falta de intentos (2026-08-05):
#   · `epico+aventurero` vs `extrano` (lo que decía el PCA) -> r=+0.67 con
#     humanidad: compartían el polo negativo y medían media cosa cada una.
#   · `epico+aventurero` vs cine de cámara -> r=-0.72 con gravedad: `aventurero`
#     ya es el polo negativo de gravedad, y lo intimista es serio.
# Ambos solapes eran previsibles leyendo bien el PCA: la tercera componente valía
# el 9% de la varianza con autovalor 1.03, justo en el límite de Kaiser. No es un
# eje, es una mezcla de los otros dos. Un tercer intento sería buscar el polo que
# dé el número bonito, que es exactamente cómo se selecciona ruido.
# Los dos que quedan están a r=+0.01 y suman el 72% de la varianza.


# Lo que el usuario toca son estados de ánimo, no ejes. Un slider de "gravedad
# 0-100" no se lo cree nadie; "algo reconfortante" sí, y por debajo es este par
# de rangos. El 60/40 deja una banda muerta en el centro a propósito: una
# película del montón en los dos ejes no pertenece a ningún cuadrante y no debe
# colarse en los cuatro.
HI, LO = 60.0, 40.0

# Dos cuadrantes necesitan además un umbral que NO es de mood, y por la misma
# razón: el extremo bajo de `humanidad` mezcla dos cosas opuestas — espectáculo
# mainstream y rareza de autor. Ambas son poco cálidas y están en polos
# contrarios de accesibilidad, y el eje no las distingue. Medido en el feed del
# 212 (2026-08-06): "palomitas" devolvía *El fantasma de la libertad* de Buñuel y
# *Underground* de Kusturica. Con el suelo de votos devuelve Avatar y los
# Vengadores, que es lo que la palabra promete.
# Escala TMDB (`vote_count`), no IMDb: son las dos columnas y se parecen en nada
# (ratio mediano medido ~41). 2500 aquí equivale a las ~100k de IMDb con las que
# se hizo la comprobación, y deja 508 películas.
POPCORN_MIN_VOTES = 2_500
# Y "reconfortante" colaba relleno de animación DC (Justice League vs Teen
# Titans) que es ligero e inofensivo pero no reconforta a nadie.
COMFORTING_MIN_VBS = 55.0
# El VBS por sí solo NO los quitó: están bien hechos y pasaban de 55. Lo que los
# delata es la humanidad, porque el extremo alto del eje mide "no es rara" además
# de "es cálida", y una peli del montón aterriza en 60 por defecto. Las que de
# verdad reconfortan están mucho más arriba (Totoro 90, The Farewell 98, Moonrise
# Kingdom 94) y los intrusos rozaban el 60 (Justice League vs Teen Titans 60,
# Superman II 63). Con 70 desaparecen y quedan 1429 películas.
COMFORTING_MIN_HUMANIDAD = 70.0

# La otra mitad de "palomitas": la MISMA caja de mood, al otro lado del suelo de
# votos. Al filtrarla salían Buñuel, Kusturica y Sanjuro, y no eran un error de
# clasificación — eran un grupo con nombre propio: comedia clásica, cine-concierto
# y formalismo juguetón (Keaton, Tati, Lubitsch, Spinal Tap, Stop Making Sense).
# Ligero de peso, raro de forma y enterrado por la popularidad.
#
# No es un tercer eje de mood: la rareza es metadato (`vote_count`), la misma
# dimensión que ya usan `hidden_gems` y la pata *gems* del trident. El espacio de
# embeddings sigue dando dos direcciones.
#
# El suelo de calidad hace casi todo el trabajo: sin él la misma caja devuelve
# *Mortal Kombat II* y *Masters of the Universe*.
#
# 75 y no 60 porque con 60 volvía a colarse la animación DC directa a vídeo por
# la fila anclada al gusto (Superman/Batman 67, Justice League 65, Teen Titans
# 70, Boss Level 60), mientras que lo que este cuadrante existe para sacar vive
# muy por encima: Phantom of Liberty 78, Sherlock Jr. 89, Sanjuro 90, Spinal Tap
# 90, PlayTime 91, Stop Making Sense 96. El corte va en el hueco real entre 70 y
# 78, no en el número que deje la lista más bonita. Quedan 183 películas.
DEEP_CUT_MIN_VBS = 75.0

QUADRANTS: Dict[str, Dict[str, float]] = {
    # "algo que me remueva" — densa y humana: Past Lives, Shoplifters, Los 400 golpes.
    # Sin suelos: que salgan hallazgos oscuros aquí es la gracia, no un defecto.
    "moving":     {"mood_gravedad_min": HI, "mood_humanidad_min": HI},
    # "algo oscuro" — densa y de género: Gone Girl, Das Boot, 2001, Se7en.
    # Se llamaba "intenso" y la palabra prometía adrenalina que 2001 no da; lo que
    # de verdad une a este cuadrante es ser fría y sin sentimentalismo.
    "dark":       {"mood_gravedad_min": HI, "mood_humanidad_max": LO},
    # "algo reconfortante" — ligera y humana: Totoro, Little Miss Sunshine.
    "comforting": {"mood_gravedad_max": LO, "mood_humanidad_min": COMFORTING_MIN_HUMANIDAD,
                   "mood_min_vbs": COMFORTING_MIN_VBS},
    # "palomitas" — ligera, de género Y masiva.
    "popcorn":    {"mood_gravedad_max": LO, "mood_humanidad_max": LO,
                   "mood_min_votes": POPCORN_MIN_VOTES},
    # "una rareza" — la misma caja que palomitas, al otro lado del suelo de votos.
    # Mismo umbral en las dos para que ninguna película se quede en tierra de nadie
    # ni salga en las dos: `min` es >=, `max` es <.
    "deep_cut":   {"mood_gravedad_max": LO, "mood_humanidad_max": LO,
                   "mood_max_votes": POPCORN_MIN_VOTES,
                   "mood_min_vbs": DEEP_CUT_MIN_VBS},
}

# Por qué `mood_min_votes` y no la `min_vote_count` que ya existe: las claves del
# rail son filtros DE SALIDA por decisión de 2026-07-07 y el mood va a ORIGEN.
# Reutilizar el nombre haría que el slider de Q del rail cambiase de semántica en
# cuanto hubiera un ánimo puesto. Mismo campo del payload, distinto momento.


def unit(v):
    """L2 por filas. numpy se importa dentro para que este módulo lo pueda leer
    quien sólo quiera las anclas (routers, tests de contrato) sin cargar numpy."""
    import numpy as np
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def center(vectors, alpha: float = CENTERING_ALPHA):
    """Resta alpha·centroide global y renormaliza. Sin esto no hay nada que medir."""
    import numpy as np
    X = unit(np.asarray(vectors, dtype=np.float32))
    return unit(X - alpha * unit(X.mean(axis=0, keepdims=True)))


def axis_directions(group_vectors: Dict[str, "object"]) -> Dict[str, "object"]:
    """{eje: dirección unitaria} a partir de {grupo: vector medio ya centrado}.

    Un eje es polo+ menos polo−; un grupo que falte se omite del polo, y si un
    polo se queda vacío el eje entero no se emite — mejor sin eje que con un eje
    que en realidad mide media cosa.
    """
    import numpy as np
    out = {}
    for axis, poles in AXES.items():
        pos = [group_vectors[g] for g in poles["pos"] if g in group_vectors]
        neg = [group_vectors[g] for g in poles["neg"] if g in group_vectors]
        if not pos or not neg:
            continue
        out[axis] = unit(np.mean(pos, axis=0) - np.mean(neg, axis=0))
    return out


def to_percentile(scores):
    """Coseno crudo -> 0-100 por rango percentil dentro del catálogo.

    Misma escala que `vectorbox_score`, para que un filtro de rango en Qdrant se
    escriba igual y el slider de la UI se lea igual. El coseno crudo vive en un
    ~[-0.6, 0.6] que no significa nada para nadie; el percentil sí ("esta película
    está en el 10% más denso").
    """
    import numpy as np
    s = np.asarray(scores, dtype=np.float64)
    order = s.argsort()
    ranks = np.empty(len(s), dtype=np.float64)
    ranks[order] = np.arange(len(s), dtype=np.float64)
    return np.round(100.0 * ranks / max(len(s) - 1, 1), 1)
