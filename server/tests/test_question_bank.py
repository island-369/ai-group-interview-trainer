"""题库加载与查询。"""

import pydantic
import pytest

from app.services.question_bank import Question


def test_load_at_least_ten_seeds(bank):
    assert len(bank.list()) >= 10


def test_filter_by_type(bank):
    for t, minimum in [("ranking", 3), ("resource_allocation", 2), ("case_analysis", 3), ("open_debate", 2)]:
        assert len(bank.list(type_=t)) >= minimum, f"题型 {t} 种子不足"


def test_filter_by_industry_and_difficulty(bank):
    assert len(bank.list(industry="互联网")) >= 2
    assert all(q.difficulty == 3 for q in bank.list(difficulty=3))


def test_random_pick_and_exclude(bank):
    first = bank.random()
    second = bank.random(exclude_ids=(first.id,))
    assert second.id != first.id


def test_random_no_candidate_raises(bank):
    with pytest.raises(ValueError):
        bank.random(industry="不存在的行业")


def test_invalid_question_rejected():
    with pytest.raises(pydantic.ValidationError):
        Question.model_validate({"id": "x", "title": "t", "type": "不存在的题型"})
