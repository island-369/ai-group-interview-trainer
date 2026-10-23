"""LLM Gateway：mock 流式与角色路由。"""

from app.services.llm_gateway import LLMGateway


async def test_mock_stream_yields_hint_in_chunks(mock_settings):
    gw = LLMGateway(mock_settings)
    hint = "人格化的一句确定性回复文本"
    chunks = [c async for c in gw.stream("strong", [{"role": "user", "content": "你好"}], mock_hint=hint)]
    assert "".join(chunks) == hint
    assert len(chunks) > 1, "应切分为多个流式片段"


async def test_mock_default_reply_without_hint(mock_settings):
    gw = LLMGateway(mock_settings)
    text = "".join(
        [c async for c in gw.stream("light", [{"role": "user", "content": "今天状态如何"}])]
    )
    assert "mock" in text and "今天状态如何" in text


async def test_usage_counted(mock_settings):
    gw = LLMGateway(mock_settings)
    hint = "1234567890"
    _ = [c async for c in gw.stream("strong", [{"role": "user", "content": "hi"}], mock_hint=hint)]
    assert gw.usage.calls == 1
    assert gw.usage.chars_out == len(hint)
    assert gw.usage.chars_in == len("hi")


def test_role_routing(mock_settings):
    gw = LLMGateway(mock_settings)
    assert gw.model_for("strong") == mock_settings.llm_model_strong
    assert gw.model_for("light") == mock_settings.llm_model_light
    assert gw.model_for("strong") != gw.model_for("light")
