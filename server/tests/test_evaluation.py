"""评估管线：mock 启发式评分、证据完备、确定性、名次。"""

import pytest

from app.core.config import BASE_DIR
from app.services.evaluation import EvalMember, Evaluator
from app.services.rubrics import load_rubrics


def _messages():
    def spk(sid, text, phase):
        return {"kind": "speak", "speaker_id": sid, "text": text, "meta": {"phase": phase}}
    return [
        {"kind": "phase", "speaker_id": None, "text": "", "meta": {}},
        spk("user", "我建议先统一评判标准：第一看可控性，第二看影响面，然后再排优先级。", "OPENING"),
        spk("c_001", "反对，回归本质看，数据说话。", "DISCUSSION"),
        spk("c_002", "我建议把问题拆成三块，先定标准再过选项。", "DISCUSSION"),
        spk("user", "同意李想的框架，我们按这个标准逐项过。时间只剩一半，要开始收敛了。", "DISCUSSION"),
        spk("c_003", "举个例子，风险项没有数据支撑。", "DISCUSSION"),
        spk("user", "那我们合并一下：A 和 B 归为一组，先表决这组？", "DISCUSSION"),
        spk("c_004", "我同意刚才的观点，补充一点。", "DISCUSSION"),
        spk("user", "最后 3 分钟，我提醒一下时间，大家把结论敲定。", "DISCUSSION"),
    ]


@pytest.fixture
def evaluator(mock_settings):
    rubrics = load_rubrics(BASE_DIR / "configs" / "rubrics.yaml")
    from app.services.llm_gateway import LLMGateway
    return Evaluator(LLMGateway(mock_settings), rubrics)


async def test_eight_dimensions_with_complete_evidence(evaluator, bank):
    q = bank.get("q_2026_003")
    user = EvalMember("user", "你", "user")
    ais = [EvalMember(i, n, "candidate") for i, n in
           [("c_001", "陈锐"), ("c_002", "李想"), ("c_003", "王雨"), ("c_004", "赵敏"), ("c_005", "韩磊")]]
    report = await evaluator.evaluate(q, user, ais, _messages())

    assert len(report["dimensions"]) == 8
    user_texts = [m["text"] for m in _messages() if m["speaker_id"] == "user"]
    for d in report["dimensions"]:
        assert 1.0 <= d["score"] <= 5.0 and d["score"] * 2 == int(d["score"] * 2)
        assert d["rationale"]
        assert d["evidence"], f"维度 {d['key']} 证据缺失"
        for e in d["evidence"]:
            assert any(e["quote"] in t for t in user_texts), "证据必须逐字摘自用户原话"
    assert set(report["radar"]) == {d["key"] for d in report["dimensions"]}
    assert 1 <= report["rank"] <= 6
    assert len(report["actions"]) == 3
    assert report["strengths"] and report["weaknesses"]


async def test_deterministic(evaluator, bank):
    q = bank.get("q_2026_003")
    user = EvalMember("user", "你", "user")
    ais = [EvalMember("c_001", "陈锐", "candidate")]
    r1 = await evaluator.evaluate(q, user, ais, _messages())
    r2 = await evaluator.evaluate(q, user, ais, _messages())
    assert r1["radar"] == r2["radar"] and r1["overall"] == r2["overall"]


async def test_silent_user_scores_low(evaluator, bank):
    q = bank.get("q_2026_003")
    user = EvalMember("user", "你", "user")
    ais = [EvalMember("c_001", "陈锐", "candidate")]
    msgs = [{"kind": "phase", "speaker_id": None, "text": "", "meta": {}}]
    report = await evaluator.evaluate(q, user, ais, msgs)
    assert report["overall"] <= 2.0
    assert all(d["score"] <= 2.5 for d in report["dimensions"])
