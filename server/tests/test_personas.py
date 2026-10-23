"""人格卡加载与难度缩放。"""


def test_load_five_personas(personas):
    assert len(personas) == 5
    archetypes = {p.archetype for p in personas.values()}
    assert {"强势型", "框架型", "数据型", "附和型", "搅局型"} <= archetypes


def test_params_in_range(personas):
    for p in personas.values():
        for name, v in p.params.model_dump().items():
            assert 0.0 <= v <= 1.0, f"{p.id}.{name}={v} 越界"


def test_difficulty_presets(difficulty):
    assert set(difficulty) == {"newbie", "standard", "realistic", "hell"}
    scales = [difficulty[k].aggressiveness_scale for k in ("newbie", "standard", "realistic", "hell")]
    assert scales == sorted(scales), "难度档激进度应递增"


def test_scaling_ordering_and_clamp(personas, difficulty):
    card = personas["c_001"]  # 强势型，基础激进度 0.85
    newbie = card.scaled_by(difficulty["newbie"])
    hell = card.scaled_by(difficulty["hell"])
    assert newbie.params.aggressiveness < hell.params.aggressiveness
    assert hell.params.aggressiveness <= 1.0


def test_scaling_does_not_mutate_original(personas, difficulty):
    card = personas["c_001"]
    before = card.params.aggressiveness
    card.scaled_by(difficulty["hell"])
    assert card.params.aggressiveness == before
