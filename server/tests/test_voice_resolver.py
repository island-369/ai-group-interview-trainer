"""音色解析：seed 确定性、原型过滤、参数映射范围（docs/06§4）。"""

import random

from app.core.config import get_settings
from app.services.voice.resolver import VoiceBook, VoiceResolver, _fmt_pitch, _fmt_rate


def book() -> VoiceBook:
    return VoiceBook.load(get_settings().voices_file)


def test_pool_not_empty_and_tags():
    b = book()
    assert len(b.candidate_voices) >= 8 and len(b.interviewer_voices) >= 2
    assert {"强势型", "附和型", "搅局型"} <= set(b.archetype_voice_tags)


def test_deterministic_by_seed(personas):
    r = VoiceResolver(book())
    rng1, rng2 = random.Random(7), random.Random(7)
    v1 = r.resolve_candidate(personas["c_001"], rng1)
    v2 = r.resolve_candidate(personas["c_001"], rng2)
    assert v1 == v2
    assert r.resolve_interviewer(random.Random(3)) == r.resolve_interviewer(random.Random(3))


def test_archetype_filter_prefers_tagged(personas):
    r = VoiceResolver(book())
    rng = random.Random(1)
    got = {r.resolve_candidate(personas["c_001"], rng).provider_voice for _ in range(30)}
    pool_strong = {v.provider_voice for v in book().candidate_voices
                   if set(v.tags) & {"果断", "沉稳"}}
    assert got <= pool_strong, f"强势型抽到了池外音色: {got - pool_strong}"


def test_aggressive_speaks_faster_than_agreeable(personas):
    r = VoiceResolver(book())
    strong = r.resolve_candidate(personas["c_001"], random.Random(1))   # aggressiveness .85
    mild = r.resolve_candidate(personas["c_004"], random.Random(1))     # aggressiveness .20
    assert float(strong.rate.strip("%+")) > float(mild.rate.strip("%+"))


def test_params_clamped(personas, difficulty):
    r = VoiceResolver(book())
    hell = personas["c_001"].scaled_by(difficulty["hell"])
    newbie = personas["c_004"].scaled_by(difficulty["newbie"])
    for v in (r.resolve_candidate(hell, random.Random(1)), r.resolve_candidate(newbie, random.Random(1))):
        assert -30 <= float(v.rate.strip("%+")) <= 40
        assert -20 <= float(v.pitch.strip("Hz+")) <= 20


def test_fmt_helpers():
    assert _fmt_rate(12.4) == "+12%" and _fmt_rate(-3.0) == "-3%"
    assert _fmt_pitch(0) == "+0Hz" and _fmt_pitch(-2.4) == "-2Hz"


def test_split_sentences():
    from app.services.voice.tts_edge import clean_for_tts, split_sentences
    assert split_sentences("第一句。第二句！") == ["第一句。", "第二句！"]
    assert split_sentences("好，这是首句。第二句。") == ["好，", "这是首句。", "第二句。"]
    assert split_sentences("没有边界的文本") == ["没有边界的文本"]
    assert split_sentences("，开头的逗号不切") == ["，开头的逗号不切"]
    assert split_sentences("喂？喂喂。", first_soft=False) == ["喂？", "喂喂。"]
    assert clean_for_tts("**重点**：看[数据]！") == "重点：看数据！"
