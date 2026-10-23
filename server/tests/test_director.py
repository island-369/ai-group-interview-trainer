"""导演调度纯逻辑：意愿公式与冷场处理。"""

import random

from app.core import director
from app.core.config import get_settings
from app.services.personas import load_personas


def _cards():
    return load_personas(get_settings().personas_dir)


def test_desire_monotonic_in_aggressiveness(personas, monkeypatch):
    monkeypatch.setattr(random, "gauss", lambda mu, sigma: 0.0)
    strong = director.AgentDyn(personas["c_001"])  # 强势型 0.85
    mild = director.AgentDyn(personas["c_004"])    # 附和型 0.20
    assert director.desire(strong, 5) > director.desire(mild, 5)


def test_desire_grows_with_silence(personas, monkeypatch):
    monkeypatch.setattr(random, "gauss", lambda mu, sigma: 0.0)
    p = personas["c_002"]
    early = director.AgentDyn(p, last_spoke_turn=4)
    late = director.AgentDyn(p, last_spoke_turn=0)
    assert director.desire(late, turn=10) > director.desire(early, turn=10)


def test_desire_recency_fatigue(personas, monkeypatch):
    monkeypatch.setattr(random, "gauss", lambda mu, sigma: 0.0)
    p = personas["c_003"]
    just_spoke = director.AgentDyn(p, last_spoke_turn=10)
    rested = director.AgentDyn(p, last_spoke_turn=4)
    assert director.desire(just_spoke, turn=10) < director.desire(rested, turn=10)


def test_desire_bounds(personas):
    rng = random.Random(42)
    dyn = director.AgentDyn(personas["c_001"])
    for turn in range(200):
        assert 0.0 <= director.desire(dyn, turn, rng=rng) <= 1.0


def test_schedule_returns_none_when_all_cold(personas, monkeypatch):
    monkeypatch.setattr(random, "gauss", lambda mu, sigma: 0.0)
    # 全员刚发言完（疲劳惩罚）且人格温和 → 无人有意愿
    dyns = [director.AgentDyn(personas["c_004"], last_spoke_turn=3) for _ in range(3)]
    chosen, desires = director.schedule(dyns, turn=3)
    assert chosen is None
    assert all(v < 0.25 for v in desires.values())


def test_schedule_picks_someone_when_eager(personas, monkeypatch):
    monkeypatch.setattr(random, "gauss", lambda mu, sigma: 0.0)
    dyns = [director.AgentDyn(p) for p in personas.values()]
    chosen, _ = director.schedule(dyns, turn=1)
    assert chosen is not None


def test_cold_field_prefers_silent_nominee():
    cards = _cards()
    a = director.AgentDyn(cards["c_001"])
    b = director.AgentDyn(cards["c_002"])
    action, target = director.cold_field_action([a, b], {"c_001": 2, "c_002": 0})
    assert (action, target) == ("nominate", "c_002")
    action, target = director.cold_field_action([a, b], {"c_001": 1, "c_002": 2})
    assert action == "advance" and target == ""
