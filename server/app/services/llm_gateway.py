"""LLM Gateway：按角色路由模型、流式输出、mock 模式与用量统计。

自研薄层（ADR-3）：底层经 litellm 访问任意 OpenAI 兼容端点。
M0 范围：stream + mock；重试/降级/熔断在 M1 接入（role 与 usage 已预留）。
"""

import asyncio
import copy
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings

Role = Literal["strong", "light"]

Message = dict  # {"role": "system|user|assistant", "content": str}


class LLMGatewayError(RuntimeError):
    pass


def _parse_json(content: str) -> dict:
    """容错解析模型输出：剥离 ```json 围栏、截取首尾大括号。"""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMGatewayError(f"模型输出不是合法 JSON: {content[:120]}") from e
    if not isinstance(obj, dict):
        raise LLMGatewayError("模型输出应为 JSON 对象")
    return obj


@dataclass
class Usage:
    calls: int = 0
    chars_in: int = 0
    chars_out: int = 0


class LLMGateway:
    def __init__(self, settings: Settings):
        self._s = settings
        self.usage = Usage()

    def model_for(self, role: Role) -> str:
        return self._s.llm_model_strong if role == "strong" else self._s.llm_model_light

    async def stream(
        self, role: Role, messages: list[Message], *, mock_hint: str | None = None
    ) -> AsyncIterator[str]:
        """流式生成；mock 模式下若调用方给出 mock_hint 则流式输出该文本。"""
        self.usage.calls += 1
        self.usage.chars_in += sum(len(m["content"]) for m in messages)
        if self._s.resolved_provider == "mock":
            text = mock_hint or self._default_mock(messages)
            async for chunk in self._mock_stream(text):
                self.usage.chars_out += len(chunk)
                yield chunk
            return
        async for chunk in self._real_stream(role, messages):
            self.usage.chars_out += len(chunk)
            yield chunk

    async def complete_json(
        self, role: Role, messages: list[Message], *, mock_obj: dict | None = None
    ) -> dict:
        """非流式结构化输出（评估管线用）；mock 模式直接返回 mock_obj 深拷贝。"""
        self.usage.calls += 1
        self.usage.chars_in += sum(len(m["content"]) for m in messages)
        if self._s.resolved_provider == "mock":
            if mock_obj is None:
                raise LLMGatewayError("mock 模式必须提供 mock_obj")
            out_obj = copy.deepcopy(mock_obj)
            self.usage.chars_out += len(str(out_obj))
            return out_obj
        try:
            import litellm
        except ImportError as e:
            raise LLMGatewayError("litellm 未安装：pip install litellm") from e
        try:
            resp = await litellm.acompletion(
                model=f"openai/{self.model_for(role)}",
                api_base=self._s.llm_api_base,
                api_key=self._s.llm_api_key,
                messages=messages,
                temperature=0,
            )
            content = resp.choices[0].message.content or ""
            self.usage.chars_out += len(content)
            return _parse_json(content)
        except LLMGatewayError:
            raise
        except Exception as e:  # 统一包装为网关错误
            raise LLMGatewayError(f"LLM 调用失败: {e}") from e

    # ---------------------------------------------------------------- mock
    @staticmethod
    def _default_mock(messages: list[Message]) -> str:
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        return f"（mock）收到：{last_user[:30]}"

    @staticmethod
    async def _mock_stream(text: str) -> AsyncIterator[str]:
        for i in range(0, len(text), 8):
            await asyncio.sleep(0.005)
            yield text[i : i + 8]

    # ---------------------------------------------------------------- real
    async def _real_stream(self, role: Role, messages: list[Message]) -> AsyncIterator[str]:
        try:
            import litellm  # 延迟导入：mock 模式零开销
        except ImportError as e:
            raise LLMGatewayError("litellm 未安装：pip install litellm") from e
        try:
            resp = await litellm.acompletion(
                model=f"openai/{self.model_for(role)}",
                api_base=self._s.llm_api_base,
                api_key=self._s.llm_api_key,
                messages=messages,
                stream=True,
            )
            async for part in resp:
                delta = part.choices[0].delta.content or ""
                if delta:
                    yield delta
        except LLMGatewayError:
            raise
        except Exception as e:  # 统一包装为网关错误，M1 在此接重试/降级
            raise LLMGatewayError(f"LLM 调用失败: {e}") from e
