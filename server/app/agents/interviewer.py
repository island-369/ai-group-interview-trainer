"""InterviewerAgent：M1 用模板话术（宣题/阶段转换/提醒/控场），零 LLM 成本、确定性可测。

真实模式下 M2 再接 LLM 润色（pressure 档位的压力面话术）。"""

from app.services.question_bank import Question


class InterviewerAgent:
    def __init__(self, name: str = "林面试官", iid: str = "interviewer"):
        self.id = iid
        self.name = name

    def briefing(self, q: Question, *, opening_sec: int, discussion_min: int) -> str:
        return (
            f"各位好，我是本场面试官{self.name}。本场无领导小组讨论的题目是《{q.title}》。"
            f"背景材料：{q.material.strip()} 讨论任务：{q.requirement.strip()} "
            f"流程与规则：先独立阅读材料，随后每人{opening_sec}秒个人陈述；"
            f"之后进入{discussion_min}分钟自由讨论，需达成一致结论；最后由一名代表做总结陈词。"
            "现在开始独立阅读，可以做个人笔记。"
        )

    def opening_start(self) -> str:
        return "下面进入个人陈述环节，按座位顺序发言，请注意控制时间。"

    def invite(self, name: str) -> str:
        return f"有请{name}。"

    def opening_timeout(self, name: str) -> str:
        return f"{name}时间到，我们听下一位的。"

    def discussion_start(self, minutes: int) -> str:
        return f"个人陈述结束，进入自由讨论环节，共{minutes}分钟，请开始。"

    def half_time(self) -> str:
        return "时间已过半，请注意推进结论的收敛。"

    def five_minutes(self) -> str:
        return "最后5分钟，请大家聚焦最终方案。"

    def discussion_end(self) -> str:
        return "自由讨论时间到。"

    def summary_solicit(self, seconds: int) -> str:
        return f"讨论环节结束。谁愿意代表小组做总结陈词？有意向的请举手示意，{seconds}秒内。"

    def summary_chosen(self, name: str) -> str:
        return f"好，有请{name}代表小组做总结陈词。"

    def closing(self) -> str:
        return "本场讨论到此结束，感谢各位的参与。复盘报告正在生成，请稍后查看。"

    # 冷场处理（docs/04§5.3）
    def nominate(self, name: str) -> str:
        return f"{name}，你还没怎么发言，说说你的看法？"

    def advance(self) -> str:
        return "刚才的讨论略有停滞，建议大家先明确评判标准，再逐项过。"
