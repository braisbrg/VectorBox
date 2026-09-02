"""Golden set de relevancia para la búsqueda — Fase 0, 2026-08-03.

Qué NO es: `test_embeddings_golden_set.py` mide la *receta del embedding*
(película → vecinos, coherencia temática). Esto mide *consulta → resultados*.
Son cosas distintas y ambas hacen falta.

Cómo se construyó, para que los números se puedan creer:

  * **Pooling estilo TREC.** Los candidatos salen de CUATRO recuperadores — el
    pipeline con canal léxico apagado, con él encendido, un denso crudo sin
    acantilado, y un léxico crudo — agrupados por consulta. Así el conjunto no
    queda casado con el sistema de hoy: si mañana un híbrido saca una película
    que hoy no sale, ya está etiquetada en vez de contar como irrelevante por
    no haberla visto nunca. 260 películas distintas sobre 12 consultas.
  * **Etiquetado desde la sinopsis**, no de memoria, para las que no se
    reconocen de título.
  * **Relevancia a la consulta COMPLETA**, restricciones incluidas. `Night of
    the Living Dead` (1968) es cine de zombis y aun así puntúa 0 en "zombis
    40s": la época es parte de lo que se pidió. La red de seguridad —que
    devuelve deliberadamente cine de fuera del filtro, marcado— tiene sus
    propios tests en `test_search_quality_panel.py` y no se mide aquí.

Escala: 2 = es exactamente lo que se pidió · 1 = defendible, adyacente o
parcial · 0 = no es respuesta. **Lo que no aparece cuenta como 0**, así que una
película que se cuele mañana no puntúa gratis.

Aviso de interpretación: hay consultas donde el catálogo responde casi todo
(duelo, anime 90s puntúan 2 en casi todo el pool). Ahí el nDCG discrimina poco y
la métrica útil es Recall@20. No es un defecto del conjunto, es una propiedad
del catálogo, y conviene recordarla antes de celebrar un 0.98.
"""

# Los `semantic_query` son los que el parser produce de verdad para esas frases.
QUERIES = {
    "duelo": dict(semantic_query="grief, mourning, loss, quiet sorrow, contemplative"),
    "soledad urbana": dict(semantic_query="loneliness, isolation, urban alienation, solitude"),
    "giallo italiano": dict(
        semantic_query="giallo, stylish murder mystery, lurid, baroque violence",
        countries=["Italy"]),
    "slasher 80s": dict(
        semantic_query="slasher, masked killer, teenagers stalked, gore",
        year_min=1980, year_max=1989),
    "thrillers coreanos": dict(
        semantic_query="thriller, suspense, mystery, crime, tension",
        countries=["South Korea"]),
    "noir 40s": dict(
        semantic_query="film noir, private eye, femme fatale, corruption",
        year_min=1940, year_max=1949),
    "anime 90s": dict(
        semantic_query="anime, hand-drawn animation, japanese, fantastical",
        countries=["Japan"], year_min=1990, year_max=1999),
    "kung fu 70s": dict(
        semantic_query="martial arts, kung fu, duels, revenge, shaolin",
        year_min=1970, year_max=1979),
    "atracos 70s": dict(
        semantic_query="heist, stylish, caper, robbery, crime, gangster",
        year_min=1970, year_max=1979),
    "zombis 40s": dict(
        semantic_query="zombie outbreak, undead, survival horror, infection",
        year_min=1940, year_max=1949),
    "found footage 60s": dict(
        semantic_query="found footage, handheld camera, first person horror",
        year_min=1960, year_max=1969),
    "cyberpunk": dict(
        semantic_query="cyberpunk, neon, hackers, megacorporations, dystopia"),
}

# tmdb_id -> grado. Ausente = 0.
GRADES = {
    "duelo": {
        352197: 2, 465672: 2, 56978: 2, 315872: 2, 4496: 2, 202506: 2,
        145147: 2, 131836: 2, 668091: 2, 15584: 2, 758866: 2, 404141: 2,
        334541: 2, 16619: 2, 749004: 2, 34653: 2, 560644: 2, 255706: 2,
        260683: 2, 1329909: 2, 1678374: 2, 345920: 2, 245687: 2, 391804: 2,
        413762: 2, 248212: 2, 157827: 2, 25051: 2, 2103: 2, 376866: 2,
        577084: 1,   # ruptura amorosa, no duelo por muerte
        723419: 1,   # el muerto es un dispositivo de terror
    },
    "soledad urbana": {
        31026: 2, 144: 2, 21135: 2, 57564: 2, 24166: 2, 662400: 2, 11985: 2,
        57209: 2, 153: 2, 86814: 2, 12474: 2, 83540: 2, 27324: 2, 216156: 2,
        162500: 2, 27171: 2, 63555: 2, 213592: 2,
        102001: 1, 27102: 1, 153770: 1, 54326: 1, 172878: 1, 488453: 1, 39210: 1,
        # 0: I Am Legend (post-apocalíptico), Baober in Love, Classical Period
    },
    "giallo italiano": {
        20126: 2, 11906: 2, 29702: 2, 28055: 2, 20115: 2, 20345: 2, 5486: 2,
        62789: 1, 150587: 1, 485894: 1, 890541: 1, 768334: 1, 823999: 1,
        56800: 1, 14029: 1, 889699: 1,
        # 0: Le Doulos, Crimson Rivers II, The Day of the Beast, Caligula,
        #    Hannibal, The Name of the Rose, Star Wars III, A Haunting in
        #    Venice, Chronicle of Anna Magdalena Bach
    },
    "slasher 80s": {
        40969: 2, 36599: 2, 39874: 2, 13555: 2, 24124: 2, 9725: 2, 47886: 2,
        27475: 2, 9728: 2, 13567: 2, 377: 2, 9730: 2, 10014: 2, 9731: 2,
        10131: 2, 11357: 2, 10281: 2, 10283: 2,
        40952: 1,    # psicópatas en asedio, slasher-adyacente
        # 0: Heathers (comedia negra)
    },
    "thrillers coreanos": {
        4689: 2, 25649: 2, 165213: 2, 269494: 2, 293413: 2, 401133: 2,
        488623: 2, 432836: 2, 581528: 2, 705996: 2, 800345: 2, 938008: 2,
        849869: 2, 838209: 2, 973628: 2, 1461254: 2,
        # 0: Secret in Their Eyes (el remake ESTADOUNIDENSE), Vanguard (china)
    },
    "noir 40s": {
        17801: 2, 16703: 2, 996: 2, 1939: 2, 17136: 2, 37992: 2, 17058: 2,
        25413: 2, 14638: 2, 25736: 2, 21336: 2, 20298: 2, 26038: 2, 36814: 2,
        26167: 2, 20362: 2,
        35896: 1, 126265: 1,   # melodrama y drama social con aire noir
    },
    "anime 90s": {
        39323: 2, 39100: 2, 39102: 2, 11621: 2, 21057: 2, 39105: 2, 44251: 2,
        39106: 2, 42994: 2, 39108: 2, 39148: 2, 125521: 2, 128: 2, 26945: 2,
        16198: 2,
    },
    "kung fu 70s": {
        96144: 2, 44154: 2, 12481: 2, 11713: 2, 9462: 2, 21964: 2, 9461: 2,
        192871: 2, 49636: 2, 11841: 2, 11537: 2, 13333: 2, 11230: 2, 54182: 2,
        18257: 1,    # blaxploitation con artes marciales
        76294: 1,    # King Hu, wuxia monástico más que kung fu
        # 0: The Killer Elite (Peckinpah)
    },
    "atracos 70s": {
        9277: 2, 15371: 2, 31656: 2, 11583: 2, 2153: 2, 65066: 2, 11657: 2,
        968: 2, 46059: 2, 8348: 2, 16246: 2, 5916: 2, 42741: 2, 23397: 2,
        11843: 2, 993: 2,
        5854: 1, 336: 1, 79645: 1, 19017: 1, 14839: 1, 19827: 1, 21949: 1,
        # 0: Super Fly, Pink Panther Strikes Again, A Clockwork Orange,
        #    The Godfather Part II, The Man Who Would Be King, Raining in
        #    the Mountain
    },
    "zombis 40s": {
        27130: 2,    # I Walked with a Zombie — la única de verdad
        31498: 1, 29239: 1, 29242: 1, 32023: 1, 3103: 1, 30793: 1, 3074: 1,
        3076: 1, 13666: 1,   # el ciclo Universal: no-muertos y monstruos de los 40
        # 0: Night of the Living Dead (1968 y 1990), Day of the Dead, Land of
        #    the Dead — cine de zombis correcto, ÉPOCA equivocada. Y Dr Jekyll,
        #    Black Friday, Invisible Man Returns, Cat People, Dead of Night,
        #    Secret Beyond the Door.
    },
    "found footage 60s": {
        26508: 2, 85535: 2, 85692: 2, 44783: 2, 109398: 2, 286302: 2,
        32688: 1, 94580: 1, 195056: 1, 11167: 1,
        # 0: Night of the Living Dead, Repulsion, Planet of the Vampires,
        #    The Trial, I Saw What You Did, Profound Desires of the Gods
    },
    "cyberpunk": {
        9323: 2, 14092: 2, 1706919: 2, 78: 2, 149: 2, 603: 2, 281: 2,
        9886: 2, 10428: 2, 5548: 2, 11633: 2, 10881: 2, 55931: 2, 20526: 2,
        473072: 2,
        5549: 1, 97020: 1, 8202: 1, 18501: 1, 31867: 1, 11525: 1, 949536: 1,
        406761: 1, 10803: 1,
    },
}

# ── familia B: lookup de entidad (la barra de búsqueda) ──────────────────────
#
# Aquí la relevancia no es graduada: o aciertas la película o no. Se mide MRR@5,
# porque lo que importa es en qué puesto aparece la correcta. Los ids están
# verificados contra el catálogo, no puestos de memoria — un id inventado
# convierte el test en decoración.
ENTITY_QUERIES = [
    # (lo que se teclea, ids aceptables, por qué está en la lista)
    ("el padrino", {238}, "title_es: la búsqueda por título en español"),
    ("la naranja mecanica", {185}, "acentos: 'mecánica' sin tilde"),
    ("deprisa deprisa", {47211}, "puntuación: el título es 'Deprisa, deprisa'"),
    ("amelie", {194}, "acentos: 'Amélie'"),
    ("el imperio contraataca", {1891}, "title_es de una saga"),
    ("kurosawa", {346, 11645, 12493, 3782, 548, 11878},
     "nombre de DIRECTOR, no de película: vale cualquier Kurosawa"),
    ("james bond", {36557, 658, 37724, 657, 646, 370172},
     "franquicia: ninguna película se llama así, vale cualquier Bond"),
    ("blade runner", {78}, "título directo, control"),
    ("mother", {30018}, "811 títulos duplicados: la de Bong Joon-ho, no la otra de 2009"),
    ("the hunt", {103663}, "duplicado: la de Vinterberg, no la de Blumhouse"),
]
