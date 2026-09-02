from __future__ import annotations

from joanna.core.schema import ExperienceEvent, Insight


DIAGNOSTIC_TERMS = ("诊断为", "社交焦虑", "抑郁症", "人格障碍", "一定是", "必然")
FUTURE_COMMITMENT_TERMS = (
    "系统会据此",
    "后续类似",
    "未来类似",
    "优先采用用户反馈",
    "优先考虑赶路",
    "类似安静的日子里减少",
    "这些都不准确",
    "我们已经调整了理解",
    "未来会更加注意",
)


def usable_for_reasoning(event: ExperienceEvent) -> bool:
    return (
        not event.deleted
        and not event.disabled
        and event.allow_long_term
    )


def usable_for_profile(event: ExperienceEvent) -> bool:
    return (
        usable_for_reasoning(event)
        and event.allow_profile
        and not event.profile_usage_revoked
    )


def governance_notes() -> list[str]:
    return [
        "所有判断只基于当前本地证据，不上传数据。",
        "输出是情境假设或温和建议，不是医学、心理或人格诊断。",
        "相关性不能被包装成因果，必须保留替代解释。",
        "高风险行动只建议，不自动替用户联系他人或执行操作。",
        "用户反馈、删除请求和关闭请求会作为证据进入后续推理，不直接覆盖原推理。",
    ]


def validate_insight_language(insight: Insight) -> None:
    text = " ".join(
        [
            insight.title,
            insight.body,
            " ".join(insight.alternatives),
            " ".join(insight.governance_notes),
        ]
    )
    forbidden = [term for term in [*DIAGNOSTIC_TERMS, *FUTURE_COMMITMENT_TERMS] if term in text]
    if forbidden:
        raise ValueError(f"insight contains forbidden governance terms: {', '.join(forbidden)}")
