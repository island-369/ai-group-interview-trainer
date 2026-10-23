"""评估管线：8 维度评分（带证据）→ 名次 → 报告（docs/02§7、04§6）。

mock 模式：启发式评分（基于行为统计），保证无密钥全流程可测；
真实模式：逐维度 LLM 调用（temp=0），证据缺失或解析失败回落启发式并标记 fallback。
AI 成员恒用启发式简化评分（01§7 Q2 决策：AI 参与排名、简化采样）。
"""

import re
from dataclasses import dataclass

from app.services.llm_gateway import LLMGateway
from app.services.question_bank import Question
from app.services.rubrics import Anchor

POS = "positive"
NEG = "negative"
NEU = "neutral"

_FRAMEWORK_WORDS = ("标准", "框架", "第一", "其次", "建议", "推进", "优先级", "维度", "拆")
_TIME_WORDS = ("时间", "分钟", "收敛", "剩", "总结", "进度")
_AGREE_WORDS = ("同意", "我们", "补充", "支持", "合并", "一起")
_NAME_RE = None  # 由调用方按成员名构造


@dataclass
class EvalMember:
    member_id: str
    name: str
    role: str  # user / candidate


def member_stats(
    messages: list[dict], member_id: str, all_names: list[str]
) -> dict:
    """行为统计：评估的证据基础（规则可判的部分，docs/04§6.2 步骤①）。"""
    others = [n for n in all_names if n]
    speaks, discussion_turns, own = [], 0, 0
    for m in messages:
        if m["kind"] != "speak":
            continue
        if m["speaker_id"] == member_id:
            speaks.append(m["text"] or "")
            if m.get("meta", {}).get("phase") == "DISCUSSION":
                own += 1
        elif m.get("meta", {}).get("phase") == "DISCUSSION":
            discussion_turns += 1
    text_all = "".join(speaks)
    names_re = re.compile("|".join(map(re.escape, others))) if others else None
    return {
        "turns": len(speaks),
        "chars": len(text_all),
        "avg_len": len(text_all) / len(speaks) if speaks else 0.0,
        "discussion_share": own / max(own + discussion_turns, 1),
        "questions": text_all.count("？") + text_all.count("?"),
        "mentions": len(names_re.findall(text_all)) if names_re else 0,
        "framework": sum(text_all.count(w) for w in _FRAMEWORK_WORDS),
        "time_mgmt": sum(text_all.count(w) for w in _TIME_WORDS),
        "collab": sum(text_all.count(w) for w in _AGREE_WORDS),
        "top_speaks": sorted(speaks, key=len, reverse=True)[:2],
    }


def _score(v: float) -> float:
    return max(1.0, min(5.0, round(v * 2) / 2))


def heuristic_scores(s: dict) -> dict[str, float]:
    """行为统计 → 8 维度分。对用户（mock/回退）与 AI（恒定）复用。"""
    engaged = min(s["chars"] / 900.0, 1.0)  # 参与度底座
    return {
        "content": _score(2.0 + engaged * 2.0 + min(s["framework"], 4) * 0.2),
        "logic": _score(2.0 + engaged + min(s["framework"], 5) * 0.25),
        "communication": _score(2.0 + engaged + (0.5 if 30 <= s["avg_len"] <= 160 else 0.0)),
        "listening": _score(1.5 + engaged + min(s["mentions"], 4) * 0.4 + min(s["questions"], 3) * 0.2),
        "collaboration": _score(1.5 + engaged + min(s["collab"], 4) * 0.35),
        "leadership": _score(1.5 + engaged + min(s["framework"] + s["time_mgmt"], 5) * 0.3),
        "composure": _score(1.5 + engaged * 2.5 + (0.3 if s["turns"] >= 3 else 0.0)),  # M1 无打断事件
        "role_time": _score(1.5 + engaged + min(s["time_mgmt"], 4) * 0.4),
    }


def _transcript_lines(messages: list[dict], name_of: dict[str, str]) -> str:
    lines = []
    for m in messages:
        if m["kind"] == "speak" and m["text"]:
            lines.append(f"【{name_of.get(m['speaker_id'], m['speaker_id'])}】{m['text']}")
    return "\n".join(lines)


class Evaluator:
    def __init__(self, gateway: LLMGateway, rubrics: dict[str, Anchor]):
        self.gateway = gateway
        self.rubrics = rubrics

    async def evaluate(
        self,
        question: Question,
        user: EvalMember,
        ais: list[EvalMember],
        messages: list[dict],
    ) -> dict:
        names = [user.name] + [a.name for a in ais]
        name_of = {user.member_id: user.name, **{a.member_id: a.name for a in ais}}
        user_stats = member_stats(messages, user.member_id, names)
        user_hs = heuristic_scores(user_stats)

        dimensions, used_llm = [], False
        for key, rubric in self.rubrics.items():
            if self.gateway._s.resolved_provider != "mock":  # 真实路径：LLM 逐维度评分
                dim, llm_ok = await self._llm_dimension(
                    rubric, question, messages, name_of, user, fallback=user_hs[key]
                )
                used_llm = used_llm or llm_ok
            else:
                dim = self._dimension_from(rubric, user_hs[key], user_stats)
            dimensions.append(dim)

        overall = round(sum(d["score"] for d in dimensions) / len(dimensions), 2)
        # AI 简化评分与名次
        ai_overalls = []
        for a in ais:
            ai_hs = heuristic_scores(member_stats(messages, a.member_id, names))
            ai_overalls.append(sum(ai_hs.values()) / len(ai_hs))
        rank = 1 + sum(1 for v in ai_overalls if v > overall)

        dims_sorted = sorted(dimensions, key=lambda d: d["score"], reverse=True)
        actions = [self.rubrics[d["key"]].action_if_low for d in dims_sorted[-3:]]
        return {
            "question_id": question.id,
            "overall": overall,
            "rank": rank,
            "total_members": 1 + len(ais),
            "note": "llm" if used_llm else "mock-heuristic",
            "radar": {d["key"]: d["score"] for d in dimensions},
            "dimensions": dimensions,
            "strengths": [d["label"] for d in dims_sorted[:2]],
            "weaknesses": [d["label"] for d in dims_sorted[-2:]],
            "actions": actions,
            "sentence_marks": self._marks(dimensions),
        }

    # ------------------------------------------------------------ 维度评分
    def _dimension_from(self, rubric: Anchor, score: float, stats: dict) -> dict:
        sentiment = POS if score >= 4.0 else (NEG if score <= 2.5 else NEU)
        evidence = [
            {"quote": sp[:80], "sentiment": sentiment}
            for sp in stats["top_speaks"][:2]
        ]
        return {
            "key": rubric.key,
            "label": rubric.label,
            "score": score,
            "rationale": f"基于行为统计：发言{stats['turns']}次/{stats['chars']}字，"
                         f"框架词{stats['framework']}、协作词{stats['collab']}、点名他人{stats['mentions']}次。",
            "evidence": evidence,
            "fallback": True,
        }

    async def _llm_dimension(
        self, rubric, question, messages, name_of, user, *, fallback: float
    ) -> tuple[dict, bool]:
        anchors = "\n".join(f"{k}分：{v}" for k, v in rubric.anchors.items())
        mock_obj = {"score": fallback, "rationale": "", "evidence": []}
        prompt = (
            f"你是校招群面面试官，评估候选人「{user.name}」的「{rubric.label}」维度。\n"
            f"定义：{rubric.definition}\n评分锚点：\n{anchors}\n\n"
            f"题目：《{question.title}》任务：{question.requirement}\n\n"
            f"讨论转录：\n{_transcript_lines(messages, name_of)}\n\n"
            f"只评估「{user.name}」的发言。输出 JSON："
            '{{"score": 1-5(0.5步进), "rationale": "一句话评语", '
            '"evidence": [{{"quote": "转录中其原话（必须逐字摘自转录）", "sentiment": "positive|negative"}}]}}'
        )
        try:
            obj = await self.gateway.complete_json(
                "strong",
                [{"role": "user", "content": prompt}],
                mock_obj=mock_obj,
            )
            score = max(1.0, min(5.0, round(float(obj["score"]) * 2) / 2))
            evidence = [
                e for e in obj.get("evidence", [])
                if isinstance(e, dict) and e.get("quote")
            ][:3]
            if not evidence:
                raise ValueError("无有效证据")
            return {
                "key": rubric.key, "label": rubric.label, "score": score,
                "rationale": str(obj.get("rationale", ""))[:200],
                "evidence": evidence, "fallback": False,
            }, True
        except Exception:  # noqa: BLE001 —— 证据缺失/解析失败统一回退，保证证据完备率
            stats = member_stats(messages, user.member_id, list(name_of.values()))
            return self._dimension_from(rubric, fallback, stats), False

    @staticmethod
    def _marks(dimensions: list[dict]) -> list[dict]:
        marks = []
        for d in dimensions:
            for e in d["evidence"]:
                marks.append({"dimension": d["label"], **e})
        return marks
