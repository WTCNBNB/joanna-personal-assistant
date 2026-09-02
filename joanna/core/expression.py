from __future__ import annotations

from datetime import datetime

from joanna.core.governance import governance_notes
from joanna.core.schema import ContextHypothesis, Evidence, Insight, InsightType, ProfileClaim


def daily_insight(
    date: str,
    contexts: list[ContextHypothesis],
    profiles: list[ProfileClaim],
    evidence: list[Evidence],
) -> Insight:
    if not contexts:
        body = "今天的可用证据还不足以形成状态洞察。保持沉默比强行判断更符合当前置信度。"
        confidence = 0.2
        alternatives = ["信息不足", "当天没有明显模式", "相关数据处于维护级隐藏状态"]
    else:
        context = contexts[0]
        body = _body_for_context(context, profiles)
        confidence = context.confidence
        alternatives = context.alternatives
    insight = Insight(
        id=f"insight.daily.{date}",
        insight_type=InsightType.DAILY,
        title=f"{date} 今日状态洞察",
        body=body,
        evidence=evidence,
        context_hypotheses=contexts,
        profile_claims=profiles,
        confidence=confidence,
        alternatives=alternatives,
        correction_prompt="如果这个判断不对，请记录反馈事件；原判断和反馈会并存进入后续推理。",
        governance_notes=governance_notes(),
        created_at=datetime.now(),
    )
    return insight


def event_review(event_id: str, context: ContextHypothesis | None, profiles: list[ProfileClaim]) -> Insight:
    evidence = context.evidence if context else []
    if context:
        body = (
            f"围绕事件 {event_id}，当前更稳妥的复盘方式是把它看成“{context.context_type}”的可能性，"
            f"置信度约 {context.confidence:.0%}。需要同时保留这些替代解释："
            f"{'、'.join(context.alternatives)}。"
        )
        confidence = context.confidence
        alternatives = context.alternatives
        contexts = [context]
    else:
        body = f"围绕事件 {event_id} 的证据不足，暂不形成复盘判断。"
        confidence = 0.2
        alternatives = ["信息不足", "事件被禁用或删除", "没有同类上下文"]
        contexts = []
    return Insight(
        id=f"insight.review.{event_id}",
        insight_type=InsightType.EVENT_REVIEW,
        title="事件复盘",
        body=body,
        evidence=evidence,
        context_hypotheses=contexts,
        profile_claims=profiles,
        confidence=confidence,
        alternatives=alternatives,
        correction_prompt="如果这个复盘方向不对，请记录针对 context 或 event 的反馈事件。",
        governance_notes=governance_notes(),
        created_at=datetime.now(),
    )


def reminder_suggestion(date: str, contexts: list[ContextHypothesis], profiles: list[ProfileClaim]) -> Insight:
    reminder_context = next(
        (
            item
            for item in contexts
            if "time_boundary" in item.id
            or "时间边界" in item.context_type
            or "提醒" in item.context_type
        ),
        None,
    )
    if reminder_context:
        body = (
            "可以考虑一条低打扰提醒：现在是否需要确认回家时间或和家人同步一下？"
            "这只是建议，不会自动发送消息。"
        )
        confidence = reminder_context.confidence
        evidence = reminder_context.evidence
        alternatives = reminder_context.alternatives
        contexts_for_insight = [reminder_context]
    else:
        body = "今天没有足够证据生成提醒建议。"
        confidence = 0.2
        evidence = []
        alternatives = ["没有提醒场景", "信息不足", "用户可能不希望被打扰"]
        contexts_for_insight = []
    return Insight(
        id=f"insight.reminder.{date}",
        insight_type=InsightType.REMINDER,
        title="温和提醒建议",
        body=body,
        evidence=evidence,
        context_hypotheses=contexts_for_insight,
        profile_claims=profiles,
        confidence=confidence,
        alternatives=alternatives,
        correction_prompt="如果这个提醒不合适，请记录针对 expression 或 context 的反馈事件。",
        governance_notes=governance_notes(),
        created_at=datetime.now(),
    )


def _body_for_context(context: ContextHypothesis, profiles: list[ProfileClaim]) -> str:
    profile_sentence = ""
    related_profiles = [profile for profile in profiles if _profile_related(profile, context)]
    if related_profiles:
        profile_sentence = f"历史画像里有 {len(related_profiles)} 条待确认模式可作背景，但不会把单日事件永久化。"
    return (
        f"今天值得关注的是多条证据共同指向“{context.context_type}”的可能性，"
        f"而不是把单个信号当成事实。{profile_sentence}"
        f"当前置信度约 {context.confidence:.0%}。"
        f"替代解释包括：{'、'.join(context.alternatives)}。"
        f"{context.uncertainty}"
    )


def _profile_related(profile: ProfileClaim, context: ContextHypothesis) -> bool:
    context_event_ids = {item.event_id for item in context.evidence}
    profile_event_ids = {item.event_id for item in profile.evidence}
    return bool(context_event_ids.intersection(profile_event_ids))
