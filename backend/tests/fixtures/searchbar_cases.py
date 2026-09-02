"""Banco de la SEARCHBAR (modo título / `/api/search/autocomplete`).

Distinto del golden set de la Magic Box: ahí se mide si una FRASE recupera un
conjunto de películas coherente; aquí se mide si quien escribe el nombre de una
película la ve, y arriba. Métrica distinta porque el fallo es distinto — en la
searchbar hay UNA respuesta correcta y la posición 1 es casi todo el producto.

Los tmdb_id se resolvieron contra TMDB por título+año y se revisaron a ojo antes
de escribirlos (2026-08-10). No hay ninguno puesto de memoria: una etiqueta mal
puesta convierte un acierto en un fallo y manda a alguien a cazar un bug que no
existe, que ya ha pasado dos veces en este repo. Un tercer aviso, de este mismo
día: escribí 22954 para `mother!` de memoria y 22954 es Invictus. Es 381283.

DOS CASOS SALEN EN PUESTO 2 Y NO SON UN BUG — no los persigas (medido 2026-08-10):

    "blade run"  Blade Runner 2049 tiene 15.440 votos, el original 15.222.
                 218 de diferencia sobre 15.000, y "blade run" es prefijo de ambas.
    "dracula"    la de 2025 tiene 1.404 votos y la de Lugosi 1.400. CUATRO.
                 Las dos son coincidencia exacta, así que el nivel no las separa.

Es el ruido decidiendo, no el ranking fallando; mañana pueden darse la vuelta solas.
Cambiar `_rank` por estos dos casos es elegir ruido — hace falta una población de
casos, no un ejemplo. Por eso el número que se mira es el top-3 (25/25), y el
"23/25 en puesto 1" se lee sabiendo que dos son empates técnicos.

`accept` es un CONJUNTO a propósito. Para "dracula" no existe una respuesta
correcta única — la de 1931 con Lugosi y la de Coppola de 1992 son las dos
defendibles — y forzar un id inventaría un fallo donde hay una ambigüedad real.
Cuando el caso SÍ tiene una sola respuesta, el conjunto tiene un elemento.
"""

# (query, accept, categoria, nota)
CASES = [
    # --- Título exacto, sin ambigüedad: la posición 1 no es negociable ---
    ("the godfather",            {238},     "exacto",   ""),
    ("pulp fiction",             {680},     "exacto",   ""),
    ("parasite",                 {496243},  "exacto",   "título internacional, original coreano"),
    ("seven samurai",            {346},     "exacto",   "título internacional, original japonés"),
    ("spirited away",            {129},     "exacto",   ""),

    # --- Prefijo / a medio escribir: el caso REAL, nadie termina de teclear ---
    ("seven sa",                 {346},     "prefijo",  "regresión conocida: TMDB colaba un film tagalo de 1 voto"),
    ("godfa",                    {238},     "prefijo",  ""),
    ("blade run",                {78},      "prefijo",  ""),
    ("interstel",                {157336},  "prefijo",  ""),

    # --- Erratas ---
    ("intersteller",             {157336},  "errata",   "una letra"),
    ("shawshenk redemption",     {278},     "errata",   ""),
    ("inglorious basterds",      {16869},   "errata",   "la errata que comete todo el mundo"),

    # --- Acentos y diacríticos ---
    ("amelie",                   {194},     "acentos",  "el título lleva Amélie"),
    ("rashomon",                 {548},     "acentos",  "transliteración de 羅生門"),

    # --- Título en castellano: la mitad del producto está en es ---
    ("el laberinto del fauno",   {1417},    "es",       "título ORIGINAL, servido como Pan's Labyrinth"),
    ("la vida es bella",         {637},     "es",       "original italiano, título es muy conocido"),
    ("el padrino",               {238},     "es",       "título es puro, sin relación con el original"),

    # --- Poco conocidas: aquí es donde la popularidad hunde lo correcto ---
    ("barrio",                   {32118},   "nicho",    "57 votos, León de Aranoa; salía en el puesto 14"),
    ("onibaba",                  {3763},    "nicho",    "491 votos"),
    ("come and see",             {25237},   "nicho",    ""),
    ("the sacrifice",            {24657},   "nicho",    "Tarkovski; el usuario lo reportó vacío"),

    # --- Ambiguas de verdad: varias respuestas defendibles ---
    ("dracula",                  {138, 6114},        "ambigua", "Lugosi 1931 / Coppola 1992"),
    ("mother",                   {30018, 381283},    "ambigua", "Bong 2009 / mother! 2017"),
    ("the hunt",                 {103663, 514847},   "ambigua", "Vinterberg 2012 / Blumhouse 2020"),
    ("stalker",                  {1398},             "ambigua", "Tarkovski gana a los thrillers homónimos"),
]

# El término nombra a un DIRECTOR: se comprueba la tarjeta, y que su filmografía
# entre en el desplegable en vez de quedarse debajo del corte.
DIRECTOR_CASES = [
    ("kurosawa",   "Akira Kurosawa",           {346}),
    ("lanthimos",  "Yorgos Lanthimos",         {792307, 38810}),
    ("tarkovsky",  "Andrei Tarkovsky",         {1398, 24657}),
    ("aranoa",     "Fernando León de Aranoa",  {32118}),
]

# Entradas que no deben tumbar nada ni devolver basura estructural.
JUNK_CASES = [
    "'; DROP TABLE movies; --",
    "../../etc/passwd",
    "<script>alert(1)</script>",
    "%%%",
    "a",          # por debajo del mínimo de 2 → lista vacía
    "  ",         # sólo espacios
    "x" * 300,    # largo absurdo
]
