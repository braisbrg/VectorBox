"""`match_score` lleva una similitud o no lleva nada. Nunca un número inventado.

Historia, medida el 2026-08-10. El campo se llamaba "match" y cargaba seis cosas
distintas: una constante en 11 de los 13 productores del feed (1.0/0.95/0.9/0.85 —
las cuatro saturan a 99, así que 459 de 459 items servidos valían exactamente
99.0), `vectorbox_score/100` metido por un normalizador de SIMILITUD, literales
98/90/88, VBS crudo, una fórmula inline sobre `vote_average`, y en BYW/hidden gems
un coseno ya alterado (×0.3 de penalización anti-vector; `calidad*0.7 +
similitud*0.3` más boost de exotismo).

Nadie lo pintaba —`matchScore` aparece dos veces en `movie-card.tsx`, el tipo y el
destructuring, y en ningún sitio más del frontend— así que no engañaba al usuario.
Pero sí cegaba `scripts/test_feed_per_profile.py`, el único diagnóstico que compara
el feed de dos perfiles: su columna de score valía 99 siempre. Un monitor que sólo
sabe decir "bien" es peor que ningún monitor.

Este test protege lo único que importa: que nadie vuelva a rellenarlo con un número
que no sea un parecido. Un `1.0` es lo primero que escribe quien añade una fila.
"""
import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
EXCLUIDOS = {"tests", ".venv", "__pycache__", "alembic", "migrations"}


def _fuentes():
    return [
        p for p in BACKEND.rglob("*.py")
        if not (EXCLUIDOS & set(p.relative_to(BACKEND).parts))
    ]


def _arg_score(call: ast.Call):
    if len(call.args) >= 2:
        return call.args[1]
    return next((k.value for k in call.keywords if k.arg == "score"), None)


def test_create_feed_item_never_receives_a_literal_score():
    """Una constante ahí es una similitud inventada, y la escala la sube a 99."""
    culpables = []
    for p in _fuentes():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            nombre = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if nombre != "create_feed_item":
                continue
            arg = _arg_score(node)
            if arg is None:
                continue
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                culpables.append(
                    f"{p.relative_to(BACKEND)}:{node.lineno} score={arg.value}"
                )
    assert not culpables, (
        "score debe ser un coseno película→película SIN alterar, o None:\n  "
        + "\n  ".join(culpables)
    )


def test_feed_item_match_score_is_never_a_literal_number():
    """Ni por la puerta de atrás: construir el FeedItem a mano y poner un número."""
    culpables = []
    for p in _fuentes():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            nombre = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if nombre != "FeedItem":
                continue
            for k in node.keywords:
                if k.arg != "match_score":
                    continue
                if isinstance(k.value, ast.Constant) and isinstance(k.value.value, (int, float)):
                    culpables.append(
                        f"{p.relative_to(BACKEND)}:{node.lineno} match_score={k.value.value}"
                    )
    assert not culpables, (
        "match_score no admite literales — usa None si la fila no compara con nada:\n  "
        + "\n  ".join(culpables)
    )


def test_the_field_is_optional_in_the_schema():
    """Si vuelve a ser obligatorio, `None` deja de ser expresable y regresan los 99."""
    src = (BACKEND / "models" / "schemas.py").read_text(encoding="utf-8")
    linea = next(l for l in src.splitlines() if l.strip().startswith("match_score:"))
    assert "Optional" in linea and "= None" in linea, linea


def test_available_now_does_not_sort_by_match_score():
    """Era el único lector, y lo que ordenaba era CALIDAD con nombre de similitud."""
    src = (BACKEND / "services" / "recommendation_engine.py").read_text(encoding="utf-8")
    assert "key=lambda x: x.match_score" not in src, (
        "esa fila ordena por calidad; que la variable se llame como lo que es"
    )


if __name__ == "__main__":
    test_create_feed_item_never_receives_a_literal_score()
    test_feed_item_match_score_is_never_a_literal_number()
    test_the_field_is_optional_in_the_schema()
    test_available_now_does_not_sort_by_match_score()
    print("ok")
