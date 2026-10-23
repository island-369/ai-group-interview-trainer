"""评分量表加载。"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel


class Anchor(BaseModel):
    key: str
    label: str
    definition: str
    anchors: dict[str, str]
    action_if_low: str


@lru_cache
def load_rubrics(path: Path) -> dict[str, Anchor]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rubrics = {d["key"]: Anchor.model_validate(d) for d in data["dimensions"]}
    if len(rubrics) != 8:
        raise ValueError("评分量表应为 8 个维度")
    return rubrics
