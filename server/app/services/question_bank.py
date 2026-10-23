"""题库：加载与查询种子题目（docs/04§2）。"""

import random
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

QuestionType = Literal["open_debate", "ranking", "resource_allocation", "case_analysis", "debate"]


class TimeLimit(BaseModel):
    reading: int = 240
    opening: int = 60
    discussion: int = 1500
    summary: int = 180


class Question(BaseModel):
    id: str
    type: QuestionType
    title: str
    industry: str = "通用"
    difficulty: int = Field(default=2, ge=1, le=3)
    material: str = ""
    requirement: str = ""
    key_points: list[str] = []
    assessment_focus: list[str] = []
    time_limit: TimeLimit = TimeLimit()
    tags: list[str] = []
    status: str = "approved"


class QuestionBank:
    def __init__(self, questions: list[Question]):
        self._qs: dict[str, Question] = {q.id: q for q in questions}

    @classmethod
    def load(cls, dir_: Path) -> "QuestionBank":
        questions = [
            Question.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
            for f in sorted(dir_.glob("*.yaml"))
        ]
        if not questions:
            raise ValueError(f"题库目录为空: {dir_}")
        return cls(questions)

    def list(
        self,
        *,
        type_: str | None = None,
        industry: str | None = None,
        difficulty: int | None = None,
        limit: int | None = None,
    ) -> list[Question]:
        qs = [
            q
            for q in self._qs.values()
            if (type_ is None or q.type == type_)
            and (industry is None or q.industry == industry)
            and (difficulty is None or q.difficulty == difficulty)
        ]
        return qs[:limit] if limit else qs

    def get(self, question_id: str) -> Question | None:
        return self._qs.get(question_id)

    def random(
        self,
        *,
        exclude_ids: tuple[str, ...] = (),
        type_: str | None = None,
        industry: str | None = None,
        difficulty: int | None = None,
    ) -> Question:
        candidates = [
            q.id
            for q in self.list(type_=type_, industry=industry, difficulty=difficulty)
            if q.id not in exclude_ids
        ]
        if not candidates:
            raise ValueError("没有符合条件的题目")
        return self._qs[random.choice(candidates)]
