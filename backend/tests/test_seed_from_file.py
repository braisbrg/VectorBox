"""Hermetic tests for the from_file seed strategy helpers (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_db import (
    ascii_fold,
    deinvert_article,
    fix_mojibake,
    parse_film_list,
    pick_from_filmography,
    wanted_director_keys,
)


def test_deinvert_article():
    assert deinvert_article("Rules of the Game, The") == "The Rules of the Game"
    assert deinvert_article("Atalante, L'") == "L'Atalante"
    assert deinvert_article("Dolce vita, La") == "La Dolce vita"
    # greedy match survives commas inside the title
    assert deinvert_article("Commune (Paris, 1871), La") == "La Commune (Paris, 1871)"
    assert deinvert_article("Citizen Kane") == "Citizen Kane"
    assert deinvert_article("El") == "El"  # bare-article title untouched


def test_fix_mojibake():
    assert fix_mojibake("BuÃ±uel, Luis") == "Buñuel, Luis"  # broken input repaired
    assert fix_mojibake("Buñuel, Luis") == "Buñuel, Luis"  # clean input untouched
    assert fix_mojibake("Welles, Orson") == "Welles, Orson"  # ASCII untouched


def test_ascii_fold():
    assert ascii_fold("González Iñárritu, Alejandro") == "gonzalez inarritu, alejandro"


def test_wanted_director_keys():
    # hyphen/space variants squash equal: 'Joon-ho' matches TMDB 'Bong Joon Ho'
    assert "bongjoonho" in wanted_director_keys("Bong Joon-ho")
    # co-directors: ANY of them matching is enough (TMDB credits only Sedgwick)
    keys = wanted_director_keys("Keaton, Buster & Edward Sedgwick")
    assert "keaton" in keys and "sedgwick" in keys
    # last-token fallback: 'González Iñárritu' matches TMDB 'Alejandro G. Iñárritu'
    assert "inarritu" in wanted_director_keys("González Iñárritu, Alejandro")
    # no gate for collective credits
    assert wanted_director_keys("Various Directors") == set()


def test_pick_from_filmography():
    # Jarman 1993: exact title beats same-year sibling (Wittgenstein)
    jarman = [
        {"id": 1, "title": "Blue", "original_title": "Blue", "release_date": "1993-09-02", "job": "Director"},
        {"id": 2, "title": "Wittgenstein", "original_title": "Wittgenstein", "release_date": "1993-03-17", "job": "Director"},
    ]
    assert pick_from_filmography(jarman, "Blue", 1993) == 1
    # exact title tolerates big year drift (Partie de campagne: shot 1936, released 1946)
    renoir = [{"id": 3, "title": "A Day in the Country", "original_title": "Partie de campagne", "release_date": "1946-05-08"}]
    assert pick_from_filmography(renoir, "A Day in the Country", 1936) == 3
    # even Soviet-shelf drift (The Long Farewell: shot 1971, released 1987)
    muratova = [{"id": 10, "title": "The Long Farewell", "original_title": "Долгие проводы", "release_date": "1987-06-01"}]
    assert pick_from_filmography(muratova, "The Long Farewell", 1971) == 10
    # boundary: 22-year drift via exact ORIGINAL title (Un chant d'amour, TMDB dates 1972)
    genet = [{"id": 11, "title": "Song of Love", "original_title": "Un chant d'amour", "release_date": "1972-01-01"}]
    assert pick_from_filmography(genet, "Un Chant d'amour", 1950) == 11
    # divergent English title resolved by year alone when unique in the window
    hondo = [
        {"id": 4, "title": "Soleil Ô", "original_title": "Soleil Ô", "release_date": "1970-03-01"},
        {"id": 5, "title": "West Indies", "original_title": "West Indies", "release_date": "1979-01-01"},
    ]
    assert pick_from_filmography(hondo, "Oh, Sun", 1970) == 4
    # two same-year films with no title signal -> ambiguous -> None
    twins = [
        {"id": 6, "title": "Alpha", "original_title": "Alpha", "release_date": "1970-01-01"},
        {"id": 7, "title": "Beta", "original_title": "Beta", "release_date": "1970-06-01"},
    ]
    assert pick_from_filmography(twins, "Gamma", 1970) is None
    # short titles match via exact original_title ('El' -> 'Él'), never containment
    bunuel = [
        {"id": 8, "title": "This Strange Passion", "original_title": "Él", "release_date": "1953-07-09"},
        {"id": 9, "title": "Illusion Travels by Streetcar", "original_title": "La ilusión viaja en tranvía", "release_date": "1954-06-10"},
    ]
    assert pick_from_filmography(bunuel, "El", 1953) == 8


def test_parse_film_list(tmp_path):
    tsv = tmp_path / "list.tsv"
    tsv.write_text(
        "TSPDT - 1,000 Greatest Films (Table)\n\n"
        "Pos\t2025\tTitle\tDirector\tYear\tCountry\tMins\n"
        "1\t1\tCitizen Kane\tWelles, Orson\t1941\tUSA\t119\n"
        "5\t5\tRules of the Game, The\tRenoir, Jean\t1939\tFrance\t113\n"
        "140\t138\tHistoire(s) du cinema\tGodard, Jean-Luc\t1988-98\tFrance\t267\n"
        "335\t323\tBerlin Alexanderplatz [TV]\tFassbinder, Rainer Werner\t1980\tWest Germany\t931\n"
        "1\t1706\tBorder\tAbbasi, Ali\t2018\tSweden-Denmark\t110\ttt6817944\n",
        encoding="utf-8",
    )
    rows = parse_film_list(str(tsv))
    assert len(rows) == 5
    assert rows[0]["title"] == "Citizen Kane" and rows[0]["year"] == 1941
    assert rows[0]["imdb_id"] is None  # 7-column rows carry no IMDb id
    assert rows[1]["title"] == "The Rules of the Game"
    assert rows[2]["year"] == 1988  # first year of a '1988-98' range
    assert rows[3]["is_tv"] is True and rows[3]["title"] == "Berlin Alexanderplatz"
    assert rows[4]["imdb_id"] == "tt6817944"  # 8th column (21st-century list format)
