"""全局配置：环境变量 / .env 加载（pydantic-settings）。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # server/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = ""  # mock | openai-compatible；留空则按 api key 推断
    llm_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_model_strong: str = "glm-4-plus"
    llm_model_light: str = "glm-4-flash"
    session_token_budget: int = 400_000  # 单场 token 软预算，M1 接入熔断

    db_path: str = str(BASE_DIR / "data" / "app.db")

    @property
    def resolved_provider(self) -> str:
        if self.llm_provider:
            return self.llm_provider
        return "openai-compatible" if self.llm_api_key else "mock"

    @property
    def personas_dir(self) -> Path:
        return BASE_DIR / "configs" / "personas"

    @property
    def difficulty_file(self) -> Path:
        return BASE_DIR / "configs" / "difficulty.yaml"

    @property
    def questions_dir(self) -> Path:
        return BASE_DIR / "seeds" / "questions"


@lru_cache
def get_settings() -> Settings:
    return Settings()
