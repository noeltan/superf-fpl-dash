"""managers.json's ``excluded`` flag — a member of the FPL league who is not in
the money league is dropped from the roster on both build paths, so no pot is
ever computed with them in it."""

from __future__ import annotations

import build


class _Fetcher:
    def __init__(self):
        self.asked: list[int] = []

    def entry(self, entry_id: int) -> dict:
        self.asked.append(entry_id)
        return {"player_first_name": "P", "player_last_name": str(entry_id),
                "name": f"Team {entry_id}", "started_event": 1}


STANDINGS = {
    "standings": {"results": [
        {"entry": 1, "entry_name": "A", "player_name": "Ann A"},
        {"entry": 2, "entry_name": "B", "player_name": "Bob B"},
        {"entry": 3, "entry_name": "C", "player_name": "Cat C"},
    ]},
    "new_entries": {"results": []},
}


def test_an_excluded_entry_never_reaches_the_roster(monkeypatch):
    monkeypatch.setattr(build, "load_manager_overrides", lambda: {
        1: {"id": "ann"},
        2: {"id": "bob", "excluded": True},
    })
    fetcher = _Fetcher()
    roster = build.collect_managers(STANDINGS, fetcher)
    assert [m["id"] for m in roster] == ["ann", "catc"]
    # Nothing is fetched for them either: they are not part of the league.
    assert 2 not in fetcher.asked


def test_the_offline_mirror_is_filtered_the_same_way(monkeypatch):
    monkeypatch.setattr(build, "load_manager_overrides", lambda: {
        2: {"id": "bob", "excluded": True},
    })
    mirror = [
        {"entry_id": 1, "id": "ann", "started_event": 1},
        {"entry_id": 2, "id": "bob", "started_event": 1},
        {"entry_id": 3, "id": "cat", "started_event": 1},
    ]
    assert [m["id"] for m in build.drop_excluded(mirror)] == ["ann", "cat"]


def test_without_the_flag_nobody_is_dropped(monkeypatch):
    monkeypatch.setattr(build, "load_manager_overrides", lambda: {1: {"id": "ann"}})
    mirror = [{"entry_id": 1, "id": "ann"}, {"entry_id": 2, "id": "bob"}]
    assert build.drop_excluded(mirror) == mirror
