"""共享测试夹具。"""

import pytest

from app.core.config import Settings
from app.services.personas import load_difficulty, load_personas
from app.services.question_bank import QuestionBank


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(_env_file=None, llm_provider="mock")


@pytest.fixture
def bank(mock_settings: Settings) -> QuestionBank:
    return QuestionBank.load(mock_settings.questions_dir)


@pytest.fixture
def personas(mock_settings: Settings):
    return load_personas(mock_settings.personas_dir)


@pytest.fixture
def difficulty(mock_settings: Settings):
    return load_difficulty(mock_settings.difficulty_file)
