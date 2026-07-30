"""The gate that lets a watchlist RSS sync STOP at the already-synced boundary
(cheap incremental) vs keep scraping for the FULL removal reconcile.

Signal = Letterboxd's own watchlist total (`data-num-entries`) vs our stored
baseline PLUS the additions we counted this run. Short of that → a film was
removed (even one masked by an equal addition keeping the total flat). We only
ever compare Letterboxd-total vs Letterboxd-total, so the resolvable/irresolvable
offset never enters."""
from routers.rss import _watchlist_settled


def test_no_change_stops():
    # nothing added or removed → total == baseline → safe to stop
    assert _watchlist_settled(601, 601, 0) is True


def test_pure_additions_stop():
    # 2 added, none removed → total grew by exactly the additions → stop
    assert _watchlist_settled(601, 603, 2) is True


def test_pure_removal_keeps_scraping():
    # 1 removed, 0 added → total dropped below baseline → full scrape
    assert _watchlist_settled(601, 600, 0) is False


def test_masked_removal_keeps_scraping():
    # add 1 + remove 1 → total FLAT but we saw the addition → total < baseline+adds
    # → the removal is caught this sync, not left to the weekly net
    assert _watchlist_settled(601, 601, 1) is False


def test_more_added_than_total_grew_keeps_scraping():
    # saw 3 additions but total only rose by 1 → 2 removals hidden → full scrape
    assert _watchlist_settled(601, 602, 3) is False


def test_no_baseline_keeps_scraping():
    # first sync / Redis-evicted → full scrape to reconcile + re-establish baseline
    assert _watchlist_settled(None, 601, 0) is False


def test_no_total_stops_gracefully():
    # page-1 parse failed → no signal → plain incremental stop, weekly net covers
    assert _watchlist_settled(601, None, 0) is True
    assert _watchlist_settled(None, None, 5) is True


if __name__ == "__main__":
    for _n, _f in list(globals().items()):
        if _n.startswith("test_"):
            _f()
    print("ok")
