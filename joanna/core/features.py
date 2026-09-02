from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable

from joanna.core.schema import ExperienceEvent, ExperienceFeature, FeatureKind, SemanticRule, SemanticRuleType


@dataclass(frozen=True)
class FeatureExtractor:
    kind: str
    label: str
    polarity: str
    match: Callable[[ExperienceEvent], bool]
    value: Callable[[ExperienceEvent], str]
    confidence_scale: float = 1.0


FEATURE_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ALLOWED_POLARITIES = {"support", "counter", "preference"}
ALLOWED_FIELDS = {
    "event_type",
    "source_type",
    "summary",
    "text",
    "people",
    "scenes",
    "confidence",
    "sensitivity",
}
ALLOWED_OPERATORS = {
    "eq",
    "ne",
    "in",
    "contains",
    "contains_any",
    "contains_all",
    "exists",
    "gt",
    "gte",
    "lt",
    "lte",
}
LEGACY_MATCH_KEYS = {"event_type", "source_type", "contains", "scenes", "people", "min_confidence"}


def extract_features(events: list[ExperienceEvent], memory: Any | None = None) -> list[ExperienceFeature]:
    features: list[ExperienceFeature] = []
    for event in events:
        for extractor in EXTRACTORS:
            if not extractor.match(event):
                continue
            features.append(
                ExperienceFeature(
                    id=f"feature:{event.id}:{extractor.kind}",
                    event_id=event.id,
                    kind=extractor.kind,
                    label=extractor.label,
                    value=extractor.value(event),
                    confidence=round(min(1.0, event.confidence * extractor.confidence_scale), 2),
                    evidence=event.to_evidence(),
                    polarity=extractor.polarity,
                )
            )
    features.extend(_runtime_features(events, memory))
    return _dedupe_features(features)


def validate_runtime_feature_rule_spec(match_spec: dict[str, Any], output_spec: dict[str, Any]) -> bool:
    return _runtime_feature_rule_error(match_spec, output_spec) is None


def runtime_feature_rule_error(match_spec: dict[str, Any], output_spec: dict[str, Any]) -> str | None:
    return _runtime_feature_rule_error(match_spec, output_spec)


def feature_kinds(features: list[ExperienceFeature]) -> set[str]:
    return {feature.kind for feature in features}


def features_by_kind(features: list[ExperienceFeature]) -> dict[str, list[ExperienceFeature]]:
    grouped: dict[str, list[ExperienceFeature]] = {}
    for feature in features:
        grouped.setdefault(feature.kind, []).append(feature)
    return grouped


def feature_evidence(features: list[ExperienceFeature]):
    by_id = {}
    for feature in features:
        by_id[feature.event_id] = feature.evidence
    return [by_id[event_id] for event_id in sorted(by_id)]


def _runtime_features(events: list[ExperienceEvent], memory: Any | None) -> list[ExperienceFeature]:
    if memory is None:
        return []
    rules = [
        rule
        for rule in memory.list_semantic_rules()
        if rule.rule_type == SemanticRuleType.FEATURE_EXTRACTOR
    ]
    features: list[ExperienceFeature] = []
    for rule in rules:
        error = _runtime_feature_rule_error(rule.match_spec, rule.output_spec)
        if error:
            _record_rule_application(memory, rule, [], {"reason": error}, status="skipped", reason="invalid_feature_extractor_rule")
            continue
        matched_events: list[ExperienceEvent] = []
        for event in events:
            if not _matches_runtime_rule(event, rule.match_spec):
                continue
            matched_events.append(event)
            features.append(_feature_from_runtime_rule(event, rule))
        if matched_events:
            _record_rule_application(
                memory,
                rule,
                [event.id for event in matched_events],
                {"rule_type": rule.rule_type, "feature_kind": rule.output_spec["feature_kind"]},
                status="applied",
                reason="runtime_feature_extractor_hit",
            )
        else:
            _record_rule_application(
                memory,
                rule,
                [],
                {"rule_type": rule.rule_type, "feature_kind": rule.output_spec["feature_kind"]},
                status="skipped",
                reason="runtime_feature_extractor_no_match",
            )
    return features


def _record_rule_application(
    memory: Any,
    rule: SemanticRule,
    event_ids: list[str],
    output: dict[str, Any],
    *,
    status: str,
    reason: str,
) -> None:
    try:
        memory.record_rule_application(rule.id, event_ids, output, status=status, reason=reason)
    except Exception:
        return


def _runtime_feature_rule_error(match_spec: dict[str, Any], output_spec: dict[str, Any]) -> str | None:
    if not isinstance(match_spec, dict) or not match_spec:
        return "feature_extractor match_spec must be a non-empty object"
    if not _valid_match_spec(match_spec):
        return "feature_extractor match_spec contains unsupported JSON DSL"
    if not isinstance(output_spec, dict) or not output_spec:
        return "feature_extractor output_spec must be a non-empty object"
    feature_kind = output_spec.get("feature_kind")
    if not isinstance(feature_kind, str) or not FEATURE_KIND_RE.fullmatch(feature_kind):
        return "feature_extractor feature_kind must be snake_case"
    label = output_spec.get("label")
    if not isinstance(label, str) or not label.strip():
        return "feature_extractor label is required"
    if output_spec.get("polarity") not in ALLOWED_POLARITIES:
        return "feature_extractor polarity is invalid"
    scale = output_spec.get("confidence_scale", 1.0)
    if not isinstance(scale, int | float) or scale <= 0 or scale > 1:
        return "feature_extractor confidence_scale must be between 0 and 1"
    value = output_spec.get("value")
    value_template = output_spec.get("value_template")
    if not isinstance(value, str) and not isinstance(value_template, str):
        return "feature_extractor output_spec requires value or value_template"
    return None


def _valid_match_spec(match_spec: dict[str, Any]) -> bool:
    if _looks_like_legacy_match(match_spec):
        return _valid_legacy_match(match_spec)
    return _valid_json_expr(match_spec)


def _looks_like_legacy_match(match_spec: dict[str, Any]) -> bool:
    return bool(LEGACY_MATCH_KEYS.intersection(match_spec)) and not any(key in match_spec for key in {"and", "or", "not", "field", "op"})


def _valid_legacy_match(match_spec: dict[str, Any]) -> bool:
    if not set(match_spec).issubset(LEGACY_MATCH_KEYS):
        return False
    if "contains" in match_spec and not _string_or_string_list(match_spec["contains"]):
        return False
    for key in ["event_type", "source_type"]:
        if key in match_spec and not _string_or_string_list(match_spec[key]):
            return False
    for key in ["scenes", "people"]:
        if key in match_spec and not _string_list(match_spec[key]):
            return False
    if "min_confidence" in match_spec and not isinstance(match_spec["min_confidence"], int | float):
        return False
    return True


def _valid_json_expr(expr: Any, depth: int = 0) -> bool:
    if depth > 8 or not isinstance(expr, dict) or not expr:
        return False
    logical_keys = [key for key in ("and", "or", "not") if key in expr]
    if logical_keys:
        if len(logical_keys) != 1 or len(expr) != 1:
            return False
        key = logical_keys[0]
        value = expr[key]
        if key in {"and", "or"}:
            return isinstance(value, list) and bool(value) and all(_valid_json_expr(item, depth + 1) for item in value)
        return _valid_json_expr(value, depth + 1)

    field = expr.get("field")
    op = expr.get("op")
    if not _valid_field(field) or op not in ALLOWED_OPERATORS:
        return False
    if op == "exists":
        return "value" not in expr or isinstance(expr["value"], bool)
    if "value" not in expr:
        return False
    value = expr["value"]
    if op in {"gt", "gte", "lt", "lte"}:
        return isinstance(value, int | float)
    if op in {"in", "contains_any", "contains_all"}:
        return _string_list(value)
    if op == "contains":
        return isinstance(value, str)
    return isinstance(value, str | int | float | bool)


def _valid_field(field: Any) -> bool:
    if not isinstance(field, str):
        return False
    if field in ALLOWED_FIELDS:
        return True
    return bool(re.fullmatch(r"content\.[A-Za-z0-9_]+", field))


def _string_or_string_list(value: Any) -> bool:
    return isinstance(value, str) or _string_list(value)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _matches_runtime_rule(event: ExperienceEvent, match_spec: dict[str, Any]) -> bool:
    if _looks_like_legacy_match(match_spec):
        return _matches_legacy_rule(event, match_spec)
    return _eval_json_expr(event, match_spec)


def _matches_legacy_rule(event: ExperienceEvent, match_spec: dict[str, Any]) -> bool:
    if match_spec.get("event_type") and not _matches_string_or_list(event.event_type, match_spec["event_type"]):
        return False
    if match_spec.get("source_type") and not _matches_string_or_list(event.source_type, match_spec["source_type"]):
        return False
    if match_spec.get("min_confidence") is not None and event.confidence < float(match_spec["min_confidence"]):
        return False
    text = _text(event)
    contains = _as_list(match_spec.get("contains", []))
    if contains and not all(term.lower() in text for term in contains):
        return False
    if match_spec.get("scenes") and not set(match_spec["scenes"]).issubset(set(event.scenes)):
        return False
    if match_spec.get("people") and not set(match_spec["people"]).issubset(set(event.people)):
        return False
    return True


def _matches_string_or_list(actual: str, expected: Any) -> bool:
    values = _as_list(expected)
    return actual in values


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _eval_json_expr(event: ExperienceEvent, expr: dict[str, Any]) -> bool:
    if "and" in expr:
        return all(_eval_json_expr(event, item) for item in expr["and"])
    if "or" in expr:
        return any(_eval_json_expr(event, item) for item in expr["or"])
    if "not" in expr:
        return not _eval_json_expr(event, expr["not"])

    actual = _field_value(event, str(expr["field"]))
    op = str(expr["op"])
    expected = expr.get("value")
    if op == "exists":
        exists = actual is not None and actual != "" and actual != []
        return exists if expr.get("value", True) else not exists
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return _actual_in_expected(actual, expected)
    if op == "contains":
        return _contains_value(actual, str(expected))
    if op == "contains_any":
        return any(_contains_value(actual, term) for term in expected)
    if op == "contains_all":
        return all(_contains_value(actual, term) for term in expected)
    if op in {"gt", "gte", "lt", "lte"}:
        return _compare_numeric(actual, float(expected), op)
    return False


def _actual_in_expected(actual: Any, expected: Any) -> bool:
    values = set(expected if isinstance(expected, list) else [expected])
    if isinstance(actual, list):
        return bool(values.intersection(actual))
    return actual in values


def _contains_value(actual: Any, term: str) -> bool:
    lowered = term.lower()
    if isinstance(actual, str):
        return lowered in actual.lower()
    if isinstance(actual, list):
        return any(lowered in str(item).lower() for item in actual)
    return False


def _compare_numeric(actual: Any, expected: float, op: str) -> bool:
    if not isinstance(actual, int | float):
        return False
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "lt":
        return actual < expected
    return actual <= expected


def _field_value(event: ExperienceEvent, field: str) -> Any:
    if field == "event_type":
        return event.event_type
    if field == "source_type":
        return event.source_type
    if field == "summary":
        return event.summary
    if field == "text":
        return _text(event)
    if field == "people":
        return event.people
    if field == "scenes":
        return event.scenes
    if field == "confidence":
        return event.confidence
    if field == "sensitivity":
        return event.sensitivity
    if field.startswith("content."):
        return event.content.get(field.removeprefix("content."))
    return None


def _feature_from_runtime_rule(event: ExperienceEvent, rule: SemanticRule) -> ExperienceFeature:
    output_spec = rule.output_spec
    feature_kind = str(output_spec["feature_kind"])
    scale = float(output_spec.get("confidence_scale", 1.0))
    confidence = round(min(1.0, event.confidence * rule.confidence * scale), 2)
    return ExperienceFeature(
        id=f"feature:{event.id}:runtime:{rule.id}:{feature_kind}",
        event_id=event.id,
        kind=feature_kind,
        label=str(output_spec["label"]),
        value=_runtime_feature_value(event, output_spec),
        confidence=confidence,
        evidence=event.to_evidence(),
        polarity=str(output_spec["polarity"]),
    )


def _runtime_feature_value(event: ExperienceEvent, output_spec: dict[str, Any]) -> str:
    if isinstance(output_spec.get("value_template"), str):
        return _render_value_template(event, output_spec["value_template"])
    value = str(output_spec.get("value", "summary"))
    if _valid_field(value):
        resolved = _field_value(event, value)
        if isinstance(resolved, list):
            return "、".join(str(item) for item in resolved)
        if isinstance(resolved, dict):
            return json.dumps(resolved, ensure_ascii=False, sort_keys=True)
        return str(resolved)
    return value


def _render_value_template(event: ExperienceEvent, template: str) -> str:
    def replace(match: re.Match[str]) -> str:
        field = match.group(1)
        if not _valid_field(field):
            return ""
        value = _field_value(event, field)
        if isinstance(value, list):
            return "、".join(str(item) for item in value)
        return "" if value is None else str(value)

    return re.sub(r"\{([A-Za-z0-9_.]+)\}", replace, template)


def _dedupe_features(features: list[ExperienceFeature]) -> list[ExperienceFeature]:
    by_id: dict[str, ExperienceFeature] = {}
    for feature in features:
        by_id[feature.id] = feature
    return sorted(by_id.values(), key=lambda item: (item.evidence.occurred_at, item.kind))


def _text(event: ExperienceEvent) -> str:
    return " ".join(
        [
            event.summary,
            str(event.content),
            " ".join(event.people),
            " ".join(event.scenes),
            event.event_type,
        ]
    ).lower()


def _contains(*terms: str) -> Callable[[ExperienceEvent], bool]:
    return lambda event: any(term.lower() in _text(event) for term in terms)


def _event_type(*event_types: str) -> Callable[[ExperienceEvent], bool]:
    return lambda event: event.event_type in event_types


def _and(*checks: Callable[[ExperienceEvent], bool]) -> Callable[[ExperienceEvent], bool]:
    return lambda event: all(check(event) for check in checks)


def _or(*checks: Callable[[ExperienceEvent], bool]) -> Callable[[ExperienceEvent], bool]:
    return lambda event: any(check(event) for check in checks)


def _numeric(field: str, minimum: float | None = None, maximum: float | None = None) -> Callable[[ExperienceEvent], bool]:
    def check(event: ExperienceEvent) -> bool:
        value = event.content.get(field)
        if not isinstance(value, int | float):
            return False
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
        return True

    return check


def _numeric_value(field: str, suffix: str = "") -> Callable[[ExperienceEvent], str]:
    def value(event: ExperienceEvent) -> str:
        raw = event.content.get(field)
        return f"{raw}{suffix}" if raw is not None else event.summary

    return value


def _summary(event: ExperienceEvent) -> str:
    return event.summary


EXTRACTORS: tuple[FeatureExtractor, ...] = (
    FeatureExtractor(
        kind=FeatureKind.BODY_ACTIVATION,
        label="身体激活",
        polarity="support",
        match=_and(_event_type("heart_rate"), _numeric("bpm", minimum=88)),
        value=_numeric_value("bpm", " bpm"),
    ),
    FeatureExtractor(
        kind=FeatureKind.RECOVERY_DEBT,
        label="恢复不足",
        polarity="support",
        match=_and(_event_type("sleep_summary"), _numeric("hours", maximum=6.5)),
        value=_numeric_value("hours", " hours"),
    ),
    FeatureExtractor(
        kind=FeatureKind.SOCIAL_LOAD,
        label="社交/互动负荷",
        polarity="support",
        match=_or(
            _and(_event_type("calendar_event", "relationship_event", "message_summary"), _contains("客户", "会面", "meeting", "client", "面试", "电话", "沟通")),
            _and(_event_type("speech_summary"), _contains("解释", "语速", "停顿")),
        ),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.TIME_PRESSURE,
        label="时间压力",
        polarity="support",
        match=_or(
            _and(_event_type("time_marker"), _numeric("hour", minimum=20)),
            _contains("赶路", "来不及", "晚点", "时间已经", "马上", "临近"),
        ),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.RELATIONSHIP_FRICTION,
        label="关系摩擦",
        polarity="support",
        match=_and(_event_type("relationship_event", "message_summary", "self_report"), _contains("争执", "冲突", "分歧", "吵")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.FAMILY_PULL,
        label="家庭牵引",
        polarity="support",
        match=_and(_event_type("message_summary", "self_report"), _contains("家里", "家人", "回家", "什么时候回")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.EXPRESSION_LOAD,
        label="表达负荷",
        polarity="support",
        match=_and(_event_type("speech_summary", "self_report"), _contains("语速", "停顿", "解释", "说得快", "复盘")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.LOCATION_STAY,
        label="地点滞留",
        polarity="support",
        match=_and(_event_type("location_scene"), _contains("仍在", "湖边", "河边", "地点", "现场")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.SELF_DOWNPLAY,
        label="用户弱化/否认",
        polarity="counter",
        match=_and(_event_type("self_report"), _contains("没事", "还好", "正常")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.EXPLICIT_PREFERENCE,
        label="明确偏好",
        polarity="preference",
        match=_event_type("preference_statement"),
        value=lambda event: str(event.content.get("claim") or event.summary),
    ),
    FeatureExtractor(
        kind=FeatureKind.TASK_COMMITMENT,
        label="承诺/任务临近",
        polarity="support",
        match=_and(_event_type("calendar_event", "message_summary"), _contains("会面", "meeting", "面试", "电话", "提醒", "行程", "会议", "参会")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.REFLECTIVE_INTENT,
        label="反思/复盘意图",
        polarity="support",
        match=_and(_event_type("self_report", "message_summary"), _contains("复盘", "后悔", "冷静", "晚点")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.TRAVEL_DELAY,
        label="出行延误",
        polarity="support",
        match=_and(_event_type("message_summary", "calendar_event", "self_report"), _contains("延误", "晚点", "高铁", "航班", "改签", "车站", "机场")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.SCHEDULE_DISRUPTION,
        label="日程扰动",
        polarity="support",
        match=_and(_event_type("message_summary", "calendar_event", "time_marker", "self_report"), _contains("延误", "重排", "改约", "调整", "改成线上", "线上参会", "只剩", "来不及")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.TASK_SWITCHING,
        label="任务切换",
        polarity="support",
        match=_and(_event_type("message_summary", "self_report", "calendar_event"), _contains("改约", "改成线上", "线上", "重排", "调整", "选择", "方案")),
        value=_summary,
    ),
    FeatureExtractor(
        kind=FeatureKind.DECISION_PRESSURE,
        label="决策压力",
        polarity="support",
        match=_and(_event_type("message_summary", "self_report", "time_marker"), _contains("先", "需要", "选择", "方案", "只剩", "改约", "调整", "有点烦")),
        value=_summary,
    ),
)
