"""Group fusion — the part that decides what a group sees.

The failure this guards against is invisible: the old centroid path returned the
same ~400 hub films to every group and nothing errored. So the tests assert the
properties that failure violated — every member gets represented, the floors
actually cut, and no single director can flood the list.
"""
from services.group_fusion import (
    MAX_PER_DIRECTOR, MIN_RUNTIME, MIN_VBS, fuse, member_lists,
)

GOOD = {"quality": 80.0, "runtime": 100.0}


def _world(ids, vbs=80.0, runtime=100.0, director="D"):
    """quality/runtime/directors maps where every id is acceptable by default."""
    return ({i: vbs for i in ids}, {i: runtime for i in ids},
            {i: [f"{director}{i}"] for i in ids})


def test_a_film_reached_by_several_of_your_films_beats_one_reached_once():
    lists = member_lists(
        neighbours={1: [100, 200], 2: [100, 300], 3: [100, 400]},
        loved=[1, 2, 3],
    )
    assert lists["knn"][0] == 100


def test_every_member_is_represented_not_just_the_biggest_library():
    big = member_lists(neighbours={i: [1000 + i] for i in range(30)}, loved=range(30))
    small = member_lists(neighbours={99: [7777, 7778]}, loved=[99])
    ids = list(range(1000, 1030)) + [7777, 7778]
    q, rt, d = _world(ids)
    out = fuse([big, small], q, rt, d, limit=20)
    assert 7777 in out, "el miembro con biblioteca pequena desaparecio de la lista"


def test_floors_drop_junk_and_shorts():
    lists = member_lists(neighbours={1: [10, 11, 12]}, loved=[1])
    q, rt, d = _world([10, 11, 12])
    q[11] = MIN_VBS - 1          # basura
    rt[12] = MIN_RUNTIME - 1     # corto
    out = fuse([lists], q, rt, d)
    assert out == [10]


def test_one_director_cannot_flood_the_list():
    ids = list(range(50, 60))
    lists = member_lists(neighbours={1: ids}, loved=[1])
    q, rt, d = _world(ids)
    for i in ids:
        d[i] = ["Miyazaki"]
    out = fuse([lists], q, rt, d, limit=20)
    assert len(out) == MAX_PER_DIRECTOR


def test_watched_films_never_come_back():
    lists = member_lists(neighbours={1: [10, 11]}, loved=[1])
    q, rt, d = _world([10, 11])
    assert fuse([lists], q, rt, d, exclude={10}) == [11]


def test_empty_group_does_not_explode():
    assert fuse([], {}, {}, {}) == []
    assert member_lists(neighbours={}, loved=[])["knn"] == []
