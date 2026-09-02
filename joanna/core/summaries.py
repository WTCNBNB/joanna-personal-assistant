from __future__ import annotations

from datetime import datetime

from joanna.core.context import build_context_hypotheses_from_features
from joanna.core.features import extract_features
from joanna.core.governance import usable_for_reasoning
from joanna.core.memory import JoannaMemory
from joanna.core.schema import ExperienceFeature, MemorySummary, MemorySummaryStatus


FEATURE_LABELS = {
    "body_activation": "身体激活",
    "recovery_debt": "恢复不足",
    "social_load": "互动负荷",
    "time_pressure": "时间压力",
    "relationship_friction": "关系摩擦",
    "family_pull": "家庭牵引",
    "expression_load": "表达负荷",
    "location_stay": "地点滞留",
    "self_downplay": "用户弱化/否认",
    "explicit_preference": "明确偏好",
    "task_commitment": "任务承诺",
    "reflective_intent": "复盘意图",
    "travel_delay": "出行延误",
    "schedule_disruption": "日程扰动",
    "task_switching": "任务切换",
    "decision_pressure": "决策压力",
}


def build_memory_summaries(memory: JoannaMemory, start_date: str, end_date: str) -> list[MemorySummary]:
    events = [
        event
        for event in memory.query_events_range(start_date, end_date)
        if usable_for_reasoning(event)
    ]
    features = extract_features(events, memory=memory)
    summaries: list[MemorySummary] = []
    summaries.extend(_context_summaries(memory, start_date, end_date, features))
    summaries.extend(_long_term_clues(start_date, end_date, features))
    for summary in summaries:
        memory.upsert_memory_summary(summary)
    return summaries


def _context_summaries(
    memory: JoannaMemory,
    start_date: str,
    end_date: str,
    features: list[ExperienceFeature],
) -> list[MemorySummary]:
    summaries: list[MemorySummary] = []
    contexts = build_context_hypotheses_from_features(memory, features)
    for context in contexts:
        event_ids = sorted({item.event_id for item in context.evidence})
        summaries.append(
            MemorySummary(
                id=f"summary.context.{start_date}.{end_date}.{_slug(context.id)}",
                summary_type="context_summary",
                status=MemorySummaryStatus.ACTIVE,
                title=f"{start_date} 至 {end_date}：{context.context_type}",
                body=(
                    f"跨日证据形成“{context.context_type}”候选摘要，"
                    f"置信度约 {context.confidence:.0%}。替代解释：{'、'.join(context.alternatives)}。"
                ),
                time_range=context.time_range,
                source_event_ids=event_ids,
                context_ids=[context.id],
                profile_ids=[],
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    return summaries


def _long_term_clues(start_date: str, end_date: str, features: list[ExperienceFeature]) -> list[MemorySummary]:
    by_kind: dict[str, list[ExperienceFeature]] = {}
    for feature in features:
        if feature.polarity == "preference":
            continue
        by_kind.setdefault(feature.kind, []).append(feature)

    summaries: list[MemorySummary] = []
    for kind, matched in sorted(by_kind.items()):
        dates = sorted({feature.evidence.occurred_at[:10] for feature in matched})
        event_ids = sorted({feature.event_id for feature in matched})
        if len(dates) < 2 or len(event_ids) < 2:
            continue
        label = FEATURE_LABELS.get(kind, kind)
        summaries.append(
            MemorySummary(
                id=f"summary.clue.{start_date}.{end_date}.{_slug(kind)}",
                summary_type="long_term_clue",
                status=MemorySummaryStatus.ACTIVE,
                title=f"{start_date} 至 {end_date}：{label}重复线索",
                body=(
                    f"{label}在 {len(dates)} 个日期、{len(event_ids)} 条事件中重复出现。"
                    "这是长期线索，不是画像事实；后续仍需结合原始证据、纠正和授权边界复核。"
                ),
                time_range=f"{start_date} -> {end_date}",
                source_event_ids=event_ids,
                context_ids=[],
                profile_ids=[],
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
    return summaries


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")
