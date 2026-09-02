from __future__ import annotations

from dataclasses import dataclass

from joanna.core.correction import correction_bias
from joanna.core.features import extract_features, feature_evidence, feature_kinds
from joanna.core.memory import JoannaMemory
from joanna.core.schema import ContextHypothesis, ExperienceEvent, ExperienceFeature, FeatureKind


CUSTOMER_CONTEXT_ID = "context.customer_meeting_pressure"


@dataclass(frozen=True)
class SituationTemplate:
    id: str
    context_type: str
    required: frozenset[str]
    supporting: frozenset[str]
    counter: frozenset[str]
    alternatives: tuple[str, ...]
    uncertainty: str
    min_score: float = 0.42


SITUATION_TEMPLATES: tuple[SituationTemplate, ...] = (
    SituationTemplate(
        id="context.high_load_interaction",
        context_type="高负荷互动前后情境",
        required=frozenset({FeatureKind.SOCIAL_LOAD}),
        supporting=frozenset({
            FeatureKind.BODY_ACTIVATION,
            FeatureKind.RECOVERY_DEBT,
            FeatureKind.EXPRESSION_LOAD,
            FeatureKind.TASK_COMMITMENT,
            FeatureKind.TIME_PRESSURE,
        }),
        counter=frozenset({FeatureKind.SELF_DOWNPLAY}),
        alternatives=("赶路或运动", "咖啡因影响", "睡眠不足", "正常兴奋", "测量误差"),
        uncertainty="高负荷互动是由通用特征组合得到的情境假设，不绑定客户、面试或任何单一表面场景。",
    ),
    SituationTemplate(
        id="context.time_boundary_tension",
        context_type="时间边界与外部牵引情境",
        required=frozenset({FeatureKind.TIME_PRESSURE}),
        supporting=frozenset({
            FeatureKind.LOCATION_STAY,
            FeatureKind.FAMILY_PULL,
            FeatureKind.TASK_COMMITMENT,
            FeatureKind.EXPLICIT_PREFERENCE,
        }),
        counter=frozenset(),
        alternatives=("已经同步过", "原本计划晚归", "时间记录不准确", "正在收尾"),
        uncertainty="时间压力和外部牵引只能说明可能值得低打扰确认，不能说明用户忘记或必须行动。",
    ),
    SituationTemplate(
        id="context.relationship_repair_window",
        context_type="关系摩擦后的复盘窗口",
        required=frozenset({FeatureKind.RELATIONSHIP_FRICTION}),
        supporting=frozenset({
            FeatureKind.REFLECTIVE_INTENT,
            FeatureKind.EXPRESSION_LOAD,
            FeatureKind.SELF_DOWNPLAY,
        }),
        counter=frozenset(),
        alternatives=("普通意见分歧", "对方也需要时间", "当下不适合继续沟通", "信息不足"),
        uncertainty="关系摩擦后的复盘窗口只建议整理和追问，不能替用户判断关系或自动联系对方。",
    ),
)


def build_context_hypotheses(memory: JoannaMemory, events: list[ExperienceEvent]) -> list[ContextHypothesis]:
    return build_context_hypotheses_from_features(memory, extract_features(events, memory=memory))


def build_context_hypotheses_from_features(
    memory: JoannaMemory,
    features: list[ExperienceFeature],
) -> list[ContextHypothesis]:
    hypotheses = [
        hypothesis
        for template in SITUATION_TEMPLATES
        if (hypothesis := _evaluate_template(memory, template, features)) is not None
    ]
    return sorted(hypotheses, key=lambda item: item.confidence, reverse=True)


def _evaluate_template(
    memory: JoannaMemory,
    template: SituationTemplate,
    features: list[ExperienceFeature],
) -> ContextHypothesis | None:
    kinds = feature_kinds(features)
    if not template.required.issubset(kinds):
        return None

    matched = [
        feature
        for feature in features
        if feature.kind in template.required
        or feature.kind in template.supporting
        or feature.kind in template.counter
    ]
    supporting_count = len(template.supporting.intersection(kinds))
    counter_count = len(template.counter.intersection(kinds))
    score = 0.24 + 0.16 * len(template.required) + 0.1 * supporting_count - 0.04 * counter_count
    if matched:
        score += max(feature.confidence for feature in matched) * 0.12
    if score < template.min_score:
        return None

    bias = correction_bias(memory, _correction_key(template, kinds))
    score += float(bias["confidence_delta"])
    alternatives = _prefer_alternatives(list(template.alternatives), list(bias["preferred_alternatives"]))
    notes = [
        template.uncertainty,
        f"匹配到的通用特征：{', '.join(sorted(feature.kind for feature in matched))}。",
        *list(bias["notes"]),
    ]
    return ContextHypothesis(
        id=_context_id(template, kinds),
        context_type=template.context_type,
        time_range=_time_range(matched),
        evidence=feature_evidence(matched),
        confidence=_clamp(score),
        alternatives=alternatives,
        uncertainty=" ".join(note for note in notes if note),
    )


def _context_id(template: SituationTemplate, kinds: set[str]) -> str:
    if template.id == "context.high_load_interaction" and FeatureKind.BODY_ACTIVATION in kinds:
        return CUSTOMER_CONTEXT_ID
    return template.id


def _correction_key(template: SituationTemplate, kinds: set[str]) -> str:
    context_id = _context_id(template, kinds)
    if context_id == CUSTOMER_CONTEXT_ID:
        return CUSTOMER_CONTEXT_ID
    return template.id


def _time_range(features: list[ExperienceFeature]) -> str:
    ordered = sorted(features, key=lambda item: item.evidence.occurred_at)
    if not ordered:
        return "unknown"
    return f"{ordered[0].evidence.occurred_at} -> {ordered[-1].evidence.occurred_at}"


def _prefer_alternatives(alternatives: list[str], preferred: list[str]) -> list[str]:
    result = alternatives[:]
    for item in reversed(preferred):
        if item in result:
            result.remove(item)
        result.insert(0, item)
    return result


def _clamp(value: float) -> float:
    return round(max(0.05, min(0.9, value)), 2)
