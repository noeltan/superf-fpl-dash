"""Spec §12 — projection arithmetic, the number guard, and the scorecard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import sys
import types

from predict import actual_order, roll_record, score_prediction, window_for
from superf.claude_call import (
    MAX_TOKENS,
    allowed_numbers,
    build_prompt,
    find_violations,
    request_call,
    validate,
)
from superf.projection import (
    clean_sheet_probability,
    fixture_adjustment,
    fixtures_by_team,
    minutes_factor,
    p_plays,
    project_manager,
    project_player,
    swing_candidates,
)
from superf.fplcal import parse_utc


def element(**kwargs):
    base = {
        "id": 1, "web_name": "Player", "team": 1, "element_type": 3, "status": "a",
        "chance_of_playing_next_round": None, "form": "0.0", "points_per_game": "0.0",
        "ep_next": "0.0", "expected_goals_per_90": 0.0, "expected_assists_per_90": 0.0,
        "expected_goals_conceded_per_90": 0.0, "saves_per_90": 0.0,
        "minutes": 2700, "starts": 30,
    }
    base.update(kwargs)
    return base


# --- availability ------------------------------------------------------------

@pytest.mark.parametrize("status", ["i", "s", "u", "n"])
def test_unavailable_players_project_zero(status):
    assert p_plays(element(status=status)) == 0.0
    assert project_player(element(status=status), 2, True) == 0.0


def test_chance_of_playing_is_used_when_present():
    assert p_plays(element(status="d", chance_of_playing_next_round=25)) == 0.25


def test_a_doubt_without_a_percentage_is_a_coin_flip():
    assert p_plays(element(status="d")) == 0.5


def test_minutes_factor_reflects_the_record():
    assert minutes_factor(element(minutes=2700, starts=30)) == 1.0
    assert minutes_factor(element(minutes=1350, starts=30)) == 0.5
    # No record at all is a rotation risk, not a certainty.
    assert minutes_factor(element(minutes=0, starts=0)) == 0.6


# --- fixture and clean sheet -------------------------------------------------

def test_easier_fixtures_raise_expected_return():
    assert fixture_adjustment(2, True) > fixture_adjustment(3, True) > fixture_adjustment(5, True)


def test_home_advantage_beats_the_same_fixture_away():
    assert fixture_adjustment(3, True) > fixture_adjustment(3, False)


def test_clean_sheet_probability_falls_as_the_opponent_gets_harder():
    easy = clean_sheet_probability(0.8, 2, True)
    hard = clean_sheet_probability(0.8, 5, False)
    assert 0 < hard < easy < 1


def test_a_defender_with_a_good_fixture_outprojects_the_same_defender_away_at_a_top_side():
    defender = element(element_type=2, expected_goals_conceded_per_90=0.9)
    assert project_player(defender, 2, True) > project_player(defender, 5, False)


def test_a_striker_who_scores_outprojects_one_who_does_not():
    scorer = element(element_type=4, expected_goals_per_90=0.8)
    blank = element(element_type=4, expected_goals_per_90=0.0, ep_next="0.0")
    assert project_player(scorer, 3, True) > project_player(blank, 3, True)


def test_thin_records_fall_back_to_the_api_expectation():
    """A promoted club's signing has no per-90s; do not flatten them to appearance points."""
    newcomer = element(element_type=4, minutes=0, starts=0, ep_next="5.0")
    assert project_player(newcomer, 3, True) > 2.5


# --- manager totals ----------------------------------------------------------

ELEMENTS = {
    1: element(id=1, web_name="Keeper", element_type=1, team=1, saves_per_90=3.0,
               expected_goals_conceded_per_90=1.0),
    2: element(id=2, web_name="Star", element_type=4, team=2, expected_goals_per_90=0.9),
    3: element(id=3, web_name="Mid", element_type=3, team=2, expected_assists_per_90=0.4),
    4: element(id=4, web_name="Bench", element_type=3, team=3, expected_goals_per_90=0.5),
}
TEAMS = {1: {"short_name": "ARS"}, 2: {"short_name": "MCI"}, 3: {"short_name": "CHE"}}
BY_TEAM = {1: [(3, True)], 2: [(2, True)], 3: [(4, False)]}


def picks_payload(hits=0, chip=None):
    return {
        "active_chip": chip,
        "entry_history": {"event_transfers_cost": hits},
        "picks": [
            {"element": 1, "multiplier": 1},
            {"element": 2, "multiplier": 2},  # captain
            {"element": 3, "multiplier": 1},
            {"element": 4, "multiplier": 0},  # bench
        ],
    }


def test_captain_counts_twice_and_the_bench_not_at_all():
    projection = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    solo = project_player(ELEMENTS[2], 2, True)
    assert projection.captain == "Star"
    assert projection.captain_xp == pytest.approx(solo)
    assert all(p.name != "Bench" for p in projection.players)


def test_transfer_hits_come_straight_off_the_total():
    clean = project_manager("noel", picks_payload(hits=0), ELEMENTS, BY_TEAM, TEAMS)
    hit = project_manager("noel", picks_payload(hits=4), ELEMENTS, BY_TEAM, TEAMS)
    assert clean.xp - hit.xp == pytest.approx(4.0)
    assert hit.hits == 4


def test_concentration_names_the_club_with_the_most_players():
    projection = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    assert projection.concentration == {"club": "MCI", "players": 2}


def test_a_double_gameweek_counts_both_fixtures():
    single = project_manager("noel", picks_payload(), ELEMENTS, {2: [(2, True)]}, TEAMS)
    double = project_manager(
        "noel", picks_payload(), ELEMENTS, {2: [(2, True), (2, False)]}, TEAMS
    )
    assert double.xp > single.xp


def test_fixtures_by_team_maps_both_sides_with_their_own_difficulty():
    mapping = fixtures_by_team([
        {"team_h": 5, "team_a": 9, "team_h_difficulty": 2, "team_a_difficulty": 4}
    ])
    assert mapping[5] == [(2, True)]
    assert mapping[9] == [(4, False)]


def test_swing_shortlist_only_contains_shared_players():
    a = project_manager("a", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    b = project_manager("b", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    shortlist = swing_candidates([a, b])
    assert shortlist
    for candidate in shortlist:
        assert len(candidate["owned_by"]) > 1
    assert "Star" in {c["name"] for c in shortlist}


def test_contract_shape_matches_section_12_4():
    projection = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS).to_contract()
    assert set(projection) == {"manager", "xp", "captain", "captain_xp", "hits", "concentration"}
    assert set(projection["concentration"]) == {"club", "players"}


# --- the number guard --------------------------------------------------------

PROJECTIONS = [
    {"manager": "noel", "xp": 58.4, "captain": "Haaland", "captain_xp": 11.2,
     "hits": 0, "concentration": {"club": "MCI", "players": 4}},
    {"manager": "jack", "xp": 56.1, "captain": "Saka", "captain_xp": 10.4,
     "hits": 4, "concentration": {"club": "ARS", "players": 3}},
]
FIXTURES = [{"team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 5,
             "kickoff_time": "2026-09-05T14:00:00Z"}]
ALLOWED = allowed_numbers(PROJECTIONS, 3, FIXTURES, 8)


def test_projection_figures_are_quotable():
    assert not find_violations("Noel projects 58.4 with Haaland at 11.2.", ALLOWED)


def test_invented_figures_are_caught():
    assert find_violations("Noel should score about 71 points.", ALLOWED) == ["71"]


def test_a_percentage_the_projection_never_produced_is_caught():
    assert "83" in find_violations("He has an 83% chance of hauling.", ALLOWED)


def test_kickoff_times_are_quotable_in_both_zones():
    assert not find_violations("They kick off at 14:00 UTC, 22:00 our time.", ALLOWED)


def test_validate_rejects_an_unknown_manager():
    call = {
        "first": {"manager": "ghost", "confidence": "low"},
        "second": {"manager": "jack", "confidence": "low"},
        "swing_player": {"name": "Saka", "owned_by": ["jack"], "why": "-"},
        "reasoning": "Short.",
    }
    problems = validate(call, ALLOWED, {"noel", "jack"}, {"Saka"})
    assert any("unknown manager" in p for p in problems)


def test_validate_rejects_a_swing_player_off_the_shortlist():
    call = {
        "first": {"manager": "noel", "confidence": "low"},
        "second": {"manager": "jack", "confidence": "low"},
        "swing_player": {"name": "Invented", "owned_by": ["jack"], "why": "-"},
        "reasoning": "Short.",
    }
    problems = validate(call, ALLOWED, {"noel", "jack"}, {"Saka"})
    assert any("shortlist" in p for p in problems)


def test_validate_rejects_reasoning_over_the_word_limit():
    call = {
        "first": {"manager": "noel", "confidence": "low"},
        "second": {"manager": "jack", "confidence": "low"},
        "swing_player": {"name": "Saka", "owned_by": ["jack"], "why": "-"},
        "reasoning": "word " * 150,
    }
    problems = validate(call, ALLOWED, {"noel", "jack"}, {"Saka"})
    assert any("limit is 120" in p for p in problems)


def test_validate_rejects_calling_the_same_manager_twice():
    call = {
        "first": {"manager": "noel", "confidence": "low"},
        "second": {"manager": "noel", "confidence": "low"},
        "swing_player": {"name": "Saka", "owned_by": ["noel"], "why": "-"},
        "reasoning": "Short.",
    }
    assert any("same manager" in p for p in validate(call, ALLOWED, {"noel"}, {"Saka"}))


# --- the request loop --------------------------------------------------------
# The call is one API request a week, made unattended by a cron job, and every
# failure path in it is silent by design: whatever goes wrong, the projection
# ranking publishes and the week carries on. That is the right behaviour and it
# is also why none of this can be left untested — a loop that never once
# succeeds looks exactly like a loop that works.

def good_call(reasoning="Noel projects 58.4 against Jack on 56.1."):
    return {
        "first": {"manager": "noel", "confidence": "medium"},
        "second": {"manager": "jack", "confidence": "low"},
        "agrees_with_projection": True,
        "swing_player": {"name": "Saka", "owned_by": ["jack"], "why": "Owned by both."},
        "reasoning": reasoning,
    }


class FakeToolUse:
    type = "tool_use"

    def __init__(self, payload, block_id="toolu_01"):
        self.id = block_id
        self.name = "publish_call"
        self.input = payload


class FakeResponse:
    def __init__(self, content, stop_reason="tool_use", model="fake-model-1"):
        self.content = content
        self.stop_reason = stop_reason
        self.model = model


def fake_anthropic(monkeypatch, responses):
    """Stand in for the SDK, and record what was sent."""
    sent = []

    class Messages:
        def create(self, **kwargs):
            sent.append(kwargs)
            return responses[len(sent) - 1]

    client = types.SimpleNamespace(messages=Messages())
    module = types.SimpleNamespace(Anthropic=lambda api_key=None: client)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "configured-model")
    return sent


def call_it():
    return request_call(
        prompt="Gameweek 2.",
        allowed=ALLOWED,
        manager_ids={"noel", "jack"},
        swing_names={"Saka"},
        projections=PROJECTIONS,
        names={"noel": "Noel", "jack": "Jack"},
    )


def test_the_token_budget_leaves_room_for_thinking(monkeypatch):
    """max_tokens caps thinking plus text, so sizing it around a 120-word answer
    truncates the tool call — and a truncated call is a silent fallback."""
    sent = fake_anthropic(monkeypatch, [FakeResponse([FakeToolUse(good_call())])])
    call, model = call_it()
    assert not call.get("_fallback")
    assert model == "fake-model-1"
    assert sent[0]["max_tokens"] == MAX_TOKENS >= 4000


def test_a_rejected_call_is_retried_as_a_tool_result(monkeypatch):
    """A tool_use block must be answered by a tool_result in the next user turn.
    Sent as loose text, the retry 400s and the retry path never runs at all."""
    sent = fake_anthropic(monkeypatch, [
        FakeResponse([FakeToolUse(good_call("Noel scores about 71."), "toolu_A")]),
        FakeResponse([FakeToolUse(good_call(), "toolu_B")]),
    ])
    call, _ = call_it()
    assert not call.get("_fallback")
    assert len(sent) == 2

    replay, rejection = sent[1]["messages"][-2:]
    assert replay["role"] == "assistant"
    assert rejection["role"] == "user"
    result = rejection["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "toolu_A"
    assert result["is_error"] is True
    assert "71" in result["content"]


def test_two_bad_calls_publish_the_projection_ranking(monkeypatch):
    sent = fake_anthropic(monkeypatch, [
        FakeResponse([FakeToolUse(good_call("Noel scores about 71."), "toolu_A")]),
        FakeResponse([FakeToolUse(good_call("Jack scores about 84."), "toolu_B")]),
    ])
    call, model = call_it()
    assert call["_fallback"] is True
    assert model is None
    assert len(sent) == 2


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_a_response_with_no_tool_call_falls_back(monkeypatch, stop_reason):
    """Both arrive as HTTP 200 with content that has no tool_use block in it."""
    fake_anthropic(monkeypatch, [FakeResponse([], stop_reason=stop_reason)])
    call, model = call_it()
    assert call["_fallback"] is True
    assert model is None


def test_an_api_failure_never_fails_the_run(monkeypatch):
    class Boom:
        def create(self, **kwargs):
            raise RuntimeError("overloaded")

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(
        Anthropic=lambda api_key=None: types.SimpleNamespace(messages=Boom())))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "configured-model")
    call, model = call_it()
    assert call["_fallback"] is True and model is None


@pytest.mark.parametrize("missing", ["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"])
def test_missing_configuration_falls_back_rather_than_guessing_a_model(monkeypatch, missing):
    fake_anthropic(monkeypatch, [FakeResponse([FakeToolUse(good_call())])])
    monkeypatch.delenv(missing)
    call, model = call_it()
    assert call["_fallback"] is True and model is None


# --- §12.1 the window --------------------------------------------------------

DEADLINE = parse_utc("2026-08-21T17:30:00Z")


def test_window_closes_before_the_first_kickoff():
    opens, closes = window_for(DEADLINE, [{"kickoff_time": "2026-08-21T19:00:00Z"}])
    assert opens == DEADLINE + timedelta(minutes=3)
    assert closes == parse_utc("2026-08-21T18:55:00Z")


def test_gw1_gets_its_full_ninety_minute_window():
    """§12.1's worked case: deadline 17:30, first kickoff 19:00."""
    opens, closes = window_for(DEADLINE, [
        {"kickoff_time": "2026-08-22T14:00:00Z"},
        {"kickoff_time": "2026-08-21T19:00:00Z"},
    ])
    assert (closes - opens) > timedelta(minutes=75)


def test_a_tight_gameweek_gets_a_correspondingly_short_window():
    _, closes = window_for(DEADLINE, [{"kickoff_time": "2026-08-21T17:45:00Z"}])
    assert closes == parse_utc("2026-08-21T17:40:00Z")


def test_window_falls_back_to_the_hard_limit_without_kickoff_times():
    opens, closes = window_for(DEADLINE, [])
    assert closes == DEADLINE + timedelta(minutes=50)
    assert closes > opens


# --- §12.3 the scorecard -----------------------------------------------------

GAMEWEEK_ROW = {
    "gw": 2,
    "winners": ["soonlee"],
    "scores": {
        "soonlee": {"points": 69, "active": True},
        "noel": {"points": 68, "active": True},
        "jack": {"points": 66, "active": True},
        "chris": {"points": 0, "active": True},
    },
}


def test_actual_order_is_by_points_with_the_winner_pinned():
    assert actual_order(GAMEWEEK_ROW) == ["soonlee", "noel", "jack", "chris"]


def test_inactive_managers_are_left_out_of_the_order():
    row = {"gw": 2, "winners": ["soonlee"], "scores": dict(
        GAMEWEEK_ROW["scores"], newguy={"points": None, "active": False})}
    assert "newguy" not in actual_order(row)


def make_call(first, second):
    return {
        "gw": 2,
        "call": {"first": {"manager": first}, "second": {"manager": second}},
        "record": {"played": 1, "exact": 0, "podium": 0, "pair": 0},
    }


def test_exact_hit():
    result = score_prediction(make_call("soonlee", "jack"), GAMEWEEK_ROW)["result"]
    assert result["exact_hit"] and result["podium_hit"]
    assert not result["pair_hit"]  # jack finished third


def test_podium_hit_without_an_exact_hit():
    result = score_prediction(make_call("noel", "chris"), GAMEWEEK_ROW)["result"]
    assert not result["exact_hit"]
    assert result["podium_hit"]


def test_pair_hit_in_either_order():
    result = score_prediction(make_call("noel", "soonlee"), GAMEWEEK_ROW)["result"]
    assert result["pair_hit"]
    assert result["podium_hit"]
    assert not result["exact_hit"]


def test_a_complete_miss_is_recorded_as_one():
    result = score_prediction(make_call("chris", "jack"), GAMEWEEK_ROW)["result"]
    assert not any([result["exact_hit"], result["podium_hit"], result["pair_hit"]])


def test_mean_rank_error_matches_the_mock():
    """The mock's settled prediction reports 1.5 for exactly this shape."""
    result = score_prediction(make_call("jack", "soonlee"), GAMEWEEK_ROW)["result"]
    assert result["mean_rank_error"] == 1.5


def test_record_rolls_forward_only_on_hits():
    base = {"played": 4, "exact": 1, "podium": 2, "pair": 1}
    hit = roll_record(base, {"exact_hit": True, "podium_hit": True, "pair_hit": False})
    assert hit == {"played": 5, "exact": 2, "podium": 3, "pair": 1}
    miss = roll_record(base, {"exact_hit": False, "podium_hit": False, "pair_hit": False})
    assert miss == {"played": 5, "exact": 1, "podium": 2, "pair": 1}


# --- the run itself ----------------------------------------------------------
# Everything above tests a function. The GW1 call failed on none of it: it died
# on `Fetcher.entry_picks() got an unexpected keyword argument 'final'`, a call
# main() makes once per manager and no test had ever executed. The window is
# ~50 minutes wide and does not come back, so the run has to be exercised end
# to end, with the API and the model both stubbed out.

import inspect  # noqa: E402
import json  # noqa: E402

import predict  # noqa: E402
from superf.fpl import Fetcher  # noqa: E402

RUN_DEADLINE = "2026-08-21T17:30:00Z"
RUN_KICKOFF = "2026-08-21T19:00:00Z"
IN_WINDOW = parse_utc("2026-08-21T17:45:00Z")

RUN_MANAGERS = [
    {"id": "noel", "entry_id": 1652821, "display_name": "Noel"},
    {"id": "jack", "entry_id": 1427521, "display_name": "Jack"},
]


class StubFetcher:
    """The FPL API, reduced to what main() asks it for.

    ``fixture_states`` describes the round: one entry per fixture, each either
    "todo", "live" or "done". The default is a single fixture nobody has
    kicked off, which is the §12.1 case.
    """

    def __init__(self, *, offline=False, picks=None, fixture_states=("todo",),
                 live_points=None):
        self.offline = offline
        self.picks_calls = []
        self.live_calls = []
        self._picks = picks_payload() if picks is None else picks
        self._states = list(fixture_states)
        self._live_points = live_points or {}

    def bootstrap(self):
        return {
            "events": [{"id": 1, "deadline_time": RUN_DEADLINE}],
            "elements": list(ELEMENTS.values()),
            "teams": [{"id": i, "short_name": t["short_name"]} for i, t in TEAMS.items()],
        }

    def fixtures(self):
        # Team 1 plays team 2 in the first fixture, team 3 in any later one, so
        # a part-played round leaves some of the squad still to come.
        out = []
        for index, state in enumerate(self._states):
            out.append({
                "id": index + 1, "event": 1,
                "started": state in ("live", "done"),
                "finished": state == "done",
                "kickoff_time": RUN_KICKOFF,
                "team_h": 1 if index == 0 else 3,
                "team_a": 2 if index == 0 else 1,
                "team_h_difficulty": 3, "team_a_difficulty": 3,
            })
        return out

    def entry_picks(self, entry_id, gw, **kwargs):
        self.picks_calls.append((entry_id, gw, kwargs))
        return self._picks

    def event_live(self, gw, **kwargs):
        self.live_calls.append((gw, kwargs))
        return {"elements": [
            {"id": element, "stats": {"total_points": points}}
            for element, points in self._live_points.items()
        ]}

    def summary(self):
        return "stubbed"


@pytest.fixture
def run(monkeypatch, tmp_path):
    """main() pointed at a throwaway docs/ with two managers and one fixture."""
    monkeypatch.setattr(predict, "DATA_JSON", tmp_path / "data.json")
    monkeypatch.setattr(predict, "PREDICTION_JSON", tmp_path / "prediction.json")
    monkeypatch.setattr(predict, "PREDICTIONS", tmp_path / "predictions")
    (tmp_path / "data.json").write_text(json.dumps({
        "managers": RUN_MANAGERS, "gameweeks": [],
    }))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(predict, "datetime", _FrozenClock)
    monkeypatch.setattr(sys, "argv", ["predict.py"])

    made = {}

    def build(**states):
        stub = StubFetcher(**states)
        monkeypatch.setattr(predict, "Fetcher", lambda **kwargs: stub)
        made["stub"] = stub
        return stub

    stub = build()
    return _Run(stub, tmp_path, build, monkeypatch)


class _Run:
    def __init__(self, stub, tmp_path, build, monkeypatch):
        self.stub, self.tmp_path, self.build = stub, tmp_path, build
        self._monkeypatch = monkeypatch

    def __iter__(self):  # so `stub, tmp_path = run` keeps working
        return iter((self.stub, self.tmp_path))

    def argv(self, *flags):
        self._monkeypatch.setattr(sys, "argv", ["predict.py", *flags])

    def published(self):
        return json.loads((self.tmp_path / "prediction.json").read_text())


class _FrozenClock(datetime):
    """`datetime.now(tz)` inside the window; everything else unchanged."""

    @classmethod
    def now(cls, tz=None):
        return IN_WINDOW


def test_the_run_publishes_a_call_inside_the_window(run):
    stub, tmp_path = run
    assert predict.main() == 0

    published = json.loads((tmp_path / "prediction.json").read_text())
    assert published["gw"] == 1
    assert {published["call"]["first"]["manager"],
            published["call"]["second"]["manager"]} == {"noel", "jack"}
    # Archived under docs/predictions/ as well, so a rerun cannot erase history.
    assert json.loads((tmp_path / "predictions" / "gw01.json").read_text()) == published


def test_the_run_asks_for_picks_the_fetcher_can_actually_serve(run):
    """The kwargs main() passes must exist on the real Fetcher, not just the stub."""
    stub, _ = run
    predict.main()

    assert [entry_id for entry_id, _, _ in stub.picks_calls] == [
        m["entry_id"] for m in RUN_MANAGERS
    ]
    for _, gw, kwargs in stub.picks_calls:
        assert gw == 1
        inspect.signature(Fetcher.entry_picks).bind(Fetcher, 1652821, gw, **kwargs)


def test_in_window_picks_are_never_written_to_the_cache(tmp_path):
    """They are the submitted squad, not the one that scored: cached, they can
    be revalidated with a 304 and frozen into an immutable snapshot (§4.2)."""
    fetcher = Fetcher(cache_dir=tmp_path / "cache", raw_dir=tmp_path / "raw")
    body, meta = fetcher._cache_paths("x")
    calls = []

    class Response:
        status_code = 200
        headers = {"ETag": "W/\"abc\""}

        def raise_for_status(self):
            pass

        def json(self):
            return {"picks": []}

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    fetcher._session.get = get

    fetcher.entry_picks(1652821, 1, final=False)
    assert not any(Path(p).exists() for p in (tmp_path / "cache").glob("*"))

    fetcher.entry_picks(1652821, 1, final=True)
    assert list((tmp_path / "cache").glob("*.json"))


# --- calling a gameweek already in progress ----------------------------------
# A mid-round call is a different bet from a §12.1 one: half the information is
# already settled. The three things that have to hold are that banked and
# projected points are never confused, that it cannot bury a pre-kickoff call,
# and that it never counts towards the record §12.3 advertises.

RUN_LIVE = {1: 6.0, 2: 9.0, 3: 4.0, 4: 2.0}


def test_banked_and_projected_are_carried_separately():
    """`xp` is two kinds of number added together, so both halves are published."""
    # Team 1 has played (no fixture left); teams 2 and 3 are still to come.
    remaining = {2: [(3, True)], 3: [(4, False)]}
    projection = project_manager("noel", picks_payload(), ELEMENTS, remaining, TEAMS,
                                 RUN_LIVE)
    contract = projection.to_contract()

    assert contract["banked"] == pytest.approx(
        RUN_LIVE[1] + RUN_LIVE[2] * 2 + RUN_LIVE[3], abs=0.05
    )  # element 4 is benched, element 2 is captain
    assert contract["remaining"] > 0
    assert contract["xp"] == pytest.approx(
        contract["banked"] + contract["remaining"], abs=0.11
    )


def test_a_played_fixture_is_banked_once_and_not_projected_again():
    """The caller must restrict fixtures_by_team in step with scored_so_far —
    the whole point is that no player is counted as scored and projected."""
    everything_left = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM,
                                      TEAMS, RUN_LIVE)
    nothing_left = project_manager("noel", picks_payload(), ELEMENTS, {}, TEAMS,
                                   RUN_LIVE)
    assert nothing_left.remaining == 0
    assert nothing_left.xp == pytest.approx(nothing_left.banked)
    assert everything_left.banked == pytest.approx(nothing_left.banked)


def test_the_swing_shortlist_drops_players_with_no_fixture_left():
    """A player whose match is over cannot swing the pot."""
    a = project_manager("a", picks_payload(), ELEMENTS, {2: [(3, True)]}, TEAMS, RUN_LIVE)
    b = project_manager("b", picks_payload(), ELEMENTS, {2: [(3, True)]}, TEAMS, RUN_LIVE)
    names = {c["name"] for c in swing_candidates([a, b])}
    assert {"Star", "Mid"} <= names  # both team 2, still to play
    assert "Keeper" not in names     # team 1, already played


def test_a_mid_round_call_publishes_and_says_so(run):
    run.build(fixture_states=("done", "todo"), live_points=RUN_LIVE)
    run.argv("--mid-round")
    assert predict.main() == 0

    published = run.published()
    assert published["mode"] == "mid_round"
    assert published["mid_round"] == {
        "as_of": published["generated_at"], "played": 1, "remaining": 1, "total": 2
    }
    for projection in published["projections"]:
        assert "banked" in projection and "remaining" in projection


def test_the_live_feed_is_read_but_never_cached(run):
    """Same hazard as in-window picks: build.py freezes this URL into raw/."""
    stub = run.build(fixture_states=("done", "todo"), live_points=RUN_LIVE)
    run.argv("--mid-round")
    predict.main()

    assert stub.live_calls == [(1, {"final": False})]
    inspect.signature(Fetcher.event_live).bind(Fetcher, 1, final=False)


def test_nothing_left_to_call_is_a_refusal_not_a_commentary(run):
    run.build(fixture_states=("live", "done"), live_points=RUN_LIVE)
    run.argv("--mid-round")
    assert predict.main() == 1
    assert not (run.tmp_path / "prediction.json").exists()


def test_a_finished_gameweek_is_not_called_at_all(run):
    """target_gameweek() gets there first: a round that is over is not awaiting
    a call, so this is a deliberate no-op rather than a refusal."""
    run.build(fixture_states=("done", "done"), live_points=RUN_LIVE)
    run.argv("--mid-round")
    assert predict.main() == 0
    assert not (run.tmp_path / "prediction.json").exists()


def test_a_finished_gameweek_named_explicitly_is_refused(run):
    """--gw walks past that check, so the mid-round guard has to hold on its own."""
    run.build(fixture_states=("done", "done"), live_points=RUN_LIVE)
    run.argv("--mid-round", "--gw", "1")
    assert predict.main() == 1


def test_a_mid_round_call_will_not_overwrite_a_pre_kickoff_one(run):
    run.build(fixture_states=("todo", "todo"))
    run.argv()
    assert predict.main() == 0
    blind = run.published()
    assert "mode" not in blind

    run.build(fixture_states=("done", "todo"), live_points=RUN_LIVE)
    run.argv("--mid-round")
    assert predict.main() == 1
    assert run.published() == blind


def test_a_mid_round_call_is_scored_but_never_counted(run, monkeypatch):
    """§12.3's record is about calls made blind. One made with six results in
    hand would inflate it and mean nothing."""
    run.build(fixture_states=("done", "todo"), live_points=RUN_LIVE)
    run.argv("--mid-round")
    predict.main()

    settled = {"gameweeks": [{
        "gw": 1, "winners": ["jack"],
        "scores": {"noel": {"points": 40, "active": True},
                   "jack": {"points": 90, "active": True}},
    }]}
    record = predict.settle_outstanding(settled)

    scored = run.published()
    assert scored["result"]["actual_first"] == "jack"
    assert record == EMPTY_RECORD_FOR_TEST
    assert scored["record"] == EMPTY_RECORD_FOR_TEST


EMPTY_RECORD_FOR_TEST = {"played": 0, "exact": 0, "podium": 0, "pair": 0}


# --- chips -------------------------------------------------------------------
# Three of thirteen played a bench boost in GW1. The arithmetic was already
# right — `multiplier > 0` counts all fifteen — but nothing downstream said so,
# so the model called the gameweek without knowing, and the live table divided
# by eleven.

def bboost_payload(hits=0):
    """The same four picks, with the bench counting too."""
    payload = picks_payload(hits=hits, chip="bboost")
    for pick in payload["picks"]:
        if pick["multiplier"] == 0:
            pick["multiplier"] = 1
    return payload


def test_a_bench_boost_scores_the_whole_squad():
    """The claim project_manager's docstring makes, pinned down."""
    normal = project_manager("noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS)
    boosted = project_manager("noel", bboost_payload(), ELEMENTS, BY_TEAM, TEAMS)

    assert len(normal.players) == 3          # element 4 is benched
    assert len(boosted.players) == 4         # and now it is not
    assert boosted.xp > normal.xp
    assert boosted.xp - normal.xp == pytest.approx(
        project_player(ELEMENTS[4], 4, False), abs=0.05
    )


def test_a_bench_boost_banks_the_bench_too():
    scored = {1: 6.0, 2: 9.0, 3: 4.0, 4: 5.0}
    normal = project_manager("noel", picks_payload(), ELEMENTS, {}, TEAMS, scored)
    boosted = project_manager("noel", bboost_payload(), ELEMENTS, {}, TEAMS, scored)
    assert boosted.banked - normal.banked == pytest.approx(scored[4], abs=0.05)


def test_the_contract_carries_the_chip_and_the_squad_it_implies():
    contract = project_manager(
        "noel", bboost_payload(), ELEMENTS, BY_TEAM, TEAMS
    ).to_contract()
    assert contract["chip"] == "bboost"
    assert contract["squad"] == 4  # every counting pick in this fixture
    # …and an ordinary week is untouched, which §12.4 pins separately.
    assert "chip" not in project_manager(
        "noel", picks_payload(), ELEMENTS, BY_TEAM, TEAMS
    ).to_contract()


def test_the_prompt_says_who_played_what_and_how_big_their_squad_is():
    boosted = dict(PROJECTIONS[0], chip="bboost", squad=15)
    prompt = build_prompt(
        gw=1, deadline="2026-08-21T17:30:00Z",
        projections=[boosted, PROJECTIONS[1]], squads={}, fixtures=FIXTURES,
        teams={1: {"short_name": "ARS"}, 2: {"short_name": "MCI"}},
        managers=[{"id": "noel", "display_name": "Noel"},
                  {"id": "jack", "display_name": "Jack"}],
        swing_shortlist=[], record={},
    )
    assert "CHIPS PLAYED THIS GAMEWEEK" in prompt
    assert "bench boost" in prompt
    assert "15 players counting" in prompt
    # Jack played nothing and must not be described as though he had.
    jack_line = next(l for l in prompt.splitlines() if "id: jack" in l)
    assert "CHIP" not in jack_line


def test_fifteen_is_quotable_when_somebody_is_boosted():
    boosted = dict(PROJECTIONS[0], chip="bboost", squad=15)
    allowed = allowed_numbers([boosted, PROJECTIONS[1]], 1, FIXTURES, 8)
    assert not find_violations("Noel has 15 players scoring.", allowed)
    # Without a boost in play it is not a number the projection produced.
    assert find_violations("Noel has 15 players scoring.", ALLOWED) == ["15"]


def test_a_boosted_squad_is_sent_to_the_model_whole(run):
    """It used to be sliced to eleven, which hid four scoring players from the
    model for exactly the managers whose squad was the story."""
    run.build(fixture_states=("todo", "todo"), picks=bboost_payload())
    run.argv()
    assert predict.main() == 0
    assert run.published()["projections"][0]["squad"] == 4
