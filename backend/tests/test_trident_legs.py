"""Trident partition — the taste-card radar and the /you bars read these three
numbers, and a silent regression there is invisible (it still renders a triangle).

The old formula's failure was saturation: everything above a third of the library
read the same. The last case is the guard against that coming back.
"""
from routers.users import trident_legs, GEM_IMDB_VOTES

MAINSTREAM = GEM_IMDB_VOTES * 10
OBSCURE = GEM_IMDB_VOTES // 10


def test_partition_sums_to_one_and_buckets_once():
    loved = [
        (["Nolan"], MAINSTREAM, None),   # repeat director → auteur
        (["Nolan"], OBSCURE, None),      # auteur wins over obscure — one bucket only
        (["Once"], OBSCURE, None),       # gems
        (["Twice"], MAINSTREAM, None),   # vibe
    ]
    legs = trident_legs(loved, {"Nolan": 2, "Once": 1, "Twice": 1})
    assert legs == {"vibe": 0.25, "auteur": 0.5, "gems": 0.25}
    assert abs(sum(legs.values()) - 1.0) < 0.01


def test_tmdb_votes_are_scaled_not_compared_raw():
    # 3000 TMDB votes ~ 123k IMDb votes: mainstream, NOT a gem.
    legs = trident_legs([(["Solo"], None, 3000)], {})
    assert legs["gems"] == 0.0
    legs = trident_legs([(["Solo"], None, 100)], {})
    assert legs["gems"] == 1.0


def test_no_saturation_across_the_range():
    """One director repeat in 10 films must not read the same as nine."""
    def auteur(n_repeat):
        loved = [(["Rep"], MAINSTREAM, None)] * n_repeat + [([f"X{i}"], MAINSTREAM, None) for i in range(10 - n_repeat)]
        return trident_legs(loved, {"Rep": n_repeat})["auteur"]

    assert auteur(2) == 0.2
    assert auteur(5) == 0.5
    assert auteur(9) == 0.9


def test_empty_library_is_all_zero_not_a_crash():
    assert trident_legs([], {}) == {"vibe": 0.0, "auteur": 0.0, "gems": 0.0}
