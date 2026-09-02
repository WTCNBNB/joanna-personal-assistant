from __future__ import annotations

from datetime import datetime
import json
from statistics import mean
from uuid import uuid4

from joanna.core.context import build_context_hypotheses
from joanna.core.expression import daily_insight, event_review, reminder_suggestion
from joanna.core.features import extract_features
from joanna.core.governance import usable_for_profile, usable_for_reasoning, validate_insight_language
from joanna.core.llm import DeepSeekClient, LLMClient
from joanna.core.llm_governance import LLMRunPlan, classify_llm_exception, estimate_llm_run, retryable_failure
from joanna.core.llm_reasoner import build_llm_daily_insight
from joanna.core.memory import JoannaMemory
from joanna.core.profile import refresh_profile_claims
from joanna.core.schema import EvolutionProposal, EvolutionProposalType, EvolutionRisk, EvolutionStatus, ExperienceEvent, Insight, InsightType, LLMCallRecord, ProfileClaim, SemanticRule, SemanticRuleType, SensitivityLevel, SourceType


def build_daily_state(memory: JoannaMemory, date: str, use_llm: bool = True, llm_client: LLMClient | None = None) -> Insight:
    events = [event for event in memory.query_events(date=date) if usable_for_reasoning(event)]
    profiles = refresh_profile_claims(memory, memory.query_events(profile_eligible_only=True))
    if use_llm:
        fast_path = _try_direct_expression_fast_path(
            memory,
            events,
            profiles,
            insight_id=f"insight.direct.daily.{date}",
            insight_type=InsightType.DAILY,
            correction_prompt="如果这个判断不对，请记录反馈事件；原判断和反馈会并存进入后续推理。",
        )
        if fast_path:
            memory.save_insight(
                fast_path.insight_type,
                fast_path.id,
                fast_path.to_dict(),
                event_ids=[item.event_id for item in fast_path.evidence],
                profile_ids=[profile.id for profile in fast_path.profile_claims],
                used_llm=False,
            )
            return fast_path
        insight, profile_candidates, proposals, semantic_rules, call_id = _run_llm_insight(
            memory=memory,
            task_type="daily_insight",
            date_label=date,
            events=events,
            profiles=profiles,
            llm_client=llm_client,
            allow_huge=False,
        )
        for proposal in [*proposals, *_profile_candidate_proposals(memory, profile_candidates)]:
            memory.upsert_evolution_proposal(proposal)
        _persist_llm_runtime_rules(memory, semantic_rules, events, call_id)
        memory.save_insight(
            insight.insight_type,
            insight.id,
            insight.to_dict(),
            event_ids=[event.id for event in events],
            profile_ids=[profile.id for profile in insight.profile_claims],
            llm_call_id=call_id,
            used_llm=True,
        )
        return insight

    contexts = build_context_hypotheses(memory, events)
    insight = daily_insight(
        date=date,
        contexts=contexts,
        profiles=profiles,
        evidence=[event.to_evidence() for event in events],
    )
    validate_insight_language(insight)
    memory.save_insight(insight.insight_type, insight.id, insight.to_dict(), used_llm=False)
    return insight


def build_event_review(
    memory: JoannaMemory,
    event_id: str,
    use_llm: bool = True,
    llm_client: LLMClient | None = None,
) -> Insight:
    event = memory.get_event(event_id)
    if not event or not usable_for_reasoning(event):
        profiles = memory.list_profiles()
        insight = event_review(event_id, None, profiles)
        validate_insight_language(insight)
        memory.save_insight(insight.insight_type, insight.id, insight.to_dict(), used_llm=False)
        return insight

    date = event.occurred_at.date().isoformat()
    events = [item for item in memory.query_events(date=date) if usable_for_reasoning(item)]
    profiles = refresh_profile_claims(memory, memory.query_events(profile_eligible_only=True))
    if use_llm:
        fast_path = _try_direct_expression_fast_path(
            memory,
            events,
            profiles,
            insight_id=f"insight.direct.review.{event_id}",
            insight_type=InsightType.EVENT_REVIEW,
            correction_prompt="如果这个复盘方向不对，请记录针对 context 或 event 的反馈事件。",
        )
        if fast_path:
            memory.save_insight(
                fast_path.insight_type,
                fast_path.id,
                fast_path.to_dict(),
                event_ids=[item.event_id for item in fast_path.evidence],
                profile_ids=[profile.id for profile in fast_path.profile_claims],
                used_llm=False,
            )
            return fast_path
        insight, profile_candidates, proposals, semantic_rules, call_id = _run_llm_insight(
            memory=memory,
            task_type="event_review",
            date_label=f"event:{event_id}",
            events=events,
            profiles=profiles,
            llm_client=llm_client,
            allow_huge=False,
        )
        review_insight = Insight(
            id=f"insight.llm.review.{event_id}",
            insight_type=InsightType.EVENT_REVIEW,
            title=insight.title,
            body=insight.body,
            evidence=insight.evidence,
            context_hypotheses=insight.context_hypotheses,
            profile_claims=insight.profile_claims,
            confidence=insight.confidence,
            alternatives=insight.alternatives,
            correction_prompt="如果这个复盘方向不对，请记录针对 context 或 event 的反馈事件。",
            governance_notes=insight.governance_notes,
            created_at=datetime.now(),
            semantic_observations=insight.semantic_observations,
        )
        for proposal in [*proposals, *_profile_candidate_proposals(memory, profile_candidates)]:
            memory.upsert_evolution_proposal(proposal)
        _persist_llm_runtime_rules(memory, semantic_rules, events, call_id)
        memory.save_insight(
            review_insight.insight_type,
            review_insight.id,
            review_insight.to_dict(),
            event_ids=[item.id for item in events],
            profile_ids=[profile.id for profile in review_insight.profile_claims],
            llm_call_id=call_id,
            used_llm=True,
        )
        return review_insight

    contexts = build_context_hypotheses(memory, events)
    context = _context_for_event(event_id, contexts)
    insight = event_review(event_id, context, profiles)
    validate_insight_language(insight)
    memory.save_insight(insight.insight_type, insight.id, insight.to_dict(), used_llm=False)
    return insight


def build_period_review(
    memory: JoannaMemory,
    start_date: str,
    end_date: str,
    use_llm: bool = True,
    llm_client: LLMClient | None = None,
    allow_huge: bool = False,
) -> Insight:
    events = [
        event
        for event in memory.query_events_range(start_date, end_date)
        if usable_for_reasoning(event)
    ]
    profiles = refresh_profile_claims(memory, memory.query_events(profile_eligible_only=True))
    if use_llm:
        insight, profile_candidates, proposals, semantic_rules, call_id = _run_llm_insight(
            memory=memory,
            task_type="period_review",
            date_label=f"{start_date}->{end_date}",
            events=events,
            profiles=profiles,
            llm_client=llm_client,
            allow_huge=allow_huge,
        )
        period_insight = Insight(
            id=f"insight.llm.period.{start_date}.{end_date}",
            insight_type=InsightType.PERIOD_REVIEW,
            title=insight.title,
            body=insight.body,
            evidence=insight.evidence,
            context_hypotheses=insight.context_hypotheses,
            profile_claims=insight.profile_claims,
            confidence=insight.confidence,
            alternatives=insight.alternatives,
            correction_prompt="如果这个多日复盘不对，请记录反馈事件；后续复盘会保留原判断和反馈。",
            governance_notes=insight.governance_notes,
            created_at=datetime.now(),
            semantic_observations=insight.semantic_observations,
        )
        for proposal in [*proposals, *_profile_candidate_proposals(memory, profile_candidates)]:
            memory.upsert_evolution_proposal(proposal)
        _persist_llm_runtime_rules(memory, semantic_rules, events, call_id)
        memory.save_insight(
            period_insight.insight_type,
            period_insight.id,
            period_insight.to_dict(),
            event_ids=[event.id for event in events],
            profile_ids=[profile.id for profile in period_insight.profile_claims],
            llm_call_id=call_id,
            used_llm=True,
        )
        return period_insight

    contexts = build_context_hypotheses(memory, events)
    evidence = [event.to_evidence() for event in events]
    if contexts:
        context = contexts[0]
        body = (
            f"{start_date} 至 {end_date} 的多日复盘里，当前最明显的是“{context.context_type}”候选摘要，"
            f"置信度约 {context.confidence:.0%}。这仍然需要保留替代解释：{'、'.join(context.alternatives)}。"
        )
        confidence = context.confidence
        alternatives = context.alternatives
    else:
        body = f"{start_date} 至 {end_date} 的证据不足，暂不形成多日复盘判断。"
        confidence = 0.2
        alternatives = ["信息不足", "事件被禁用或删除", "没有跨日重复线索"]
    insight = Insight(
        id=f"insight.period.{start_date}.{end_date}",
        insight_type=InsightType.PERIOD_REVIEW,
        title=f"{start_date} 至 {end_date} 多日复盘",
        body=body,
        evidence=evidence,
        context_hypotheses=contexts,
        profile_claims=profiles,
        confidence=confidence,
        alternatives=alternatives,
        correction_prompt="如果这个多日复盘不对，请记录反馈事件；后续复盘会保留原判断和反馈。",
        governance_notes=[
            "多日复盘只能基于当前可用本地证据。",
            "用户反馈、删除请求和关闭请求会作为证据进入后续复盘。",
            "多日相关性不能被包装成因果。",
        ],
        created_at=datetime.now(),
    )
    validate_insight_language(insight)
    memory.save_insight(insight.insight_type, insight.id, insight.to_dict(), used_llm=False)
    return insight


def build_reminder(
    memory: JoannaMemory,
    date: str,
    use_llm: bool = True,
    llm_client: LLMClient | None = None,
) -> Insight:
    events = [event for event in memory.query_events(date=date) if usable_for_reasoning(event)]
    profiles = refresh_profile_claims(memory, memory.query_events(profile_eligible_only=True))
    if use_llm:
        fast_path = _try_direct_expression_fast_path(
            memory,
            events,
            profiles,
            insight_id=f"insight.direct.reminder.{date}",
            insight_type=InsightType.REMINDER,
            correction_prompt="如果这个提醒不合适，请记录针对 expression 或 context 的反馈事件。",
        )
        if fast_path:
            memory.save_insight(
                fast_path.insight_type,
                fast_path.id,
                fast_path.to_dict(),
                event_ids=[item.event_id for item in fast_path.evidence],
                profile_ids=[profile.id for profile in fast_path.profile_claims],
                used_llm=False,
            )
            return fast_path
        insight, profile_candidates, proposals, semantic_rules, call_id = _run_llm_insight(
            memory=memory,
            task_type="reminder",
            date_label=f"reminder:{date}",
            events=events,
            profiles=profiles,
            llm_client=llm_client,
            allow_huge=False,
        )
        reminder_insight = Insight(
            id=f"insight.llm.reminder.{date}",
            insight_type=InsightType.REMINDER,
            title=insight.title,
            body=insight.body,
            evidence=insight.evidence,
            context_hypotheses=insight.context_hypotheses,
            profile_claims=insight.profile_claims,
            confidence=insight.confidence,
            alternatives=insight.alternatives,
            correction_prompt="如果这个提醒不合适，请记录针对 expression 或 context 的反馈事件。",
            governance_notes=insight.governance_notes,
            created_at=datetime.now(),
            semantic_observations=insight.semantic_observations,
        )
        for proposal in [*proposals, *_profile_candidate_proposals(memory, profile_candidates)]:
            memory.upsert_evolution_proposal(proposal)
        _persist_llm_runtime_rules(memory, semantic_rules, events, call_id)
        memory.save_insight(
            reminder_insight.insight_type,
            reminder_insight.id,
            reminder_insight.to_dict(),
            event_ids=[event.id for event in events],
            profile_ids=[profile.id for profile in reminder_insight.profile_claims],
            llm_call_id=call_id,
            used_llm=True,
        )
        return reminder_insight

    contexts = build_context_hypotheses(memory, events)
    insight = reminder_suggestion(date, contexts, profiles)
    validate_insight_language(insight)
    memory.save_insight(insight.insight_type, insight.id, insight.to_dict(), used_llm=False)
    return insight


def _context_for_event(event_id: str, contexts):
    for context in contexts:
        if any(item.event_id == event_id for item in context.evidence):
            return context
    return None


def _profile_candidate_proposals(memory: JoannaMemory, profile_candidates: list[ProfileClaim]) -> list[EvolutionProposal]:
    proposals: list[EvolutionProposal] = []
    for profile in profile_candidates:
        valid_evidence = [
            evidence
            for evidence in profile.evidence
            if (event := memory.get_event(evidence.event_id)) is not None and usable_for_profile(event)
        ]
        if not valid_evidence:
            continue
        profile = ProfileClaim(
            id=profile.id,
            claim=profile.claim,
            profile_type=profile.profile_type,
            evidence=valid_evidence,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            confidence=profile.confidence,
            user_confirmed=profile.user_confirmed,
            user_corrected=profile.user_corrected,
            allowed_for_reasoning=profile.allowed_for_reasoning,
            revoked=profile.revoked,
            deleted=profile.deleted,
        )
        proposals.append(
            EvolutionProposal(
                id=f"evo.profile_candidate.{profile.id.replace('profile.', '').replace('.', '_')}",
                proposal_type=EvolutionProposalType.PROFILE_CANDIDATE,
                status=EvolutionStatus.PENDING,
                risk=EvolutionRisk.HIGH,
                title="确认 LLM 画像候选",
                rationale="LLM 生成的画像候选需要用户确认后才能进入长期推理。",
                payload=profile.to_dict(),
                evidence=profile.evidence,
                created_at=datetime.now(),
            )
        )
    return proposals


def _persist_llm_runtime_rules(
    memory: JoannaMemory,
    semantic_rules: list[SemanticRule],
    events: list[ExperienceEvent],
    call_id: str,
) -> None:
    event_ids = {event.id for event in events}
    for rule in semantic_rules:
        saved = memory.upsert_semantic_rule(rule, reason="llm_rule_update")
        application_event_ids = [event_id for event_id in saved.evidence_event_ids if event_id in event_ids]
        memory.record_rule_application(
            saved.id,
            application_event_ids or saved.evidence_event_ids,
            {"rule_type": saved.rule_type, "output_spec": saved.output_spec},
            status="applied",
            reason="llm_rule_update_applied_to_source_events",
            llm_call_id=call_id,
        )


def _try_direct_expression_fast_path(
    memory: JoannaMemory,
    events: list[ExperienceEvent],
    profiles: list[ProfileClaim],
    *,
    insight_id: str,
    insight_type: str,
    correction_prompt: str,
) -> Insight | None:
    rules = [
        rule
        for rule in memory.list_semantic_rules()
        if rule.rule_type == SemanticRuleType.DIRECT_EXPRESSION
    ]
    for rule in rules:
        matched_events = _events_matching_direct_rule(rule, events)
        if not matched_events:
            continue
        skip_reason = _direct_rule_skip_reason(memory, rule, matched_events)
        if skip_reason:
            memory.record_rule_application(
                rule.id,
                [event.id for event in matched_events],
                {"reason": skip_reason},
                status="skipped",
                reason=skip_reason,
            )
            continue
        output = rule.output_spec
        evidence = [event.to_evidence() for event in matched_events]
        insight = Insight(
            id=insight_id,
            insight_type=insight_type,
            title=str(output.get("title") or "固定事件直接观察"),
            body=str(output.get("body") or "命中固定事件直接表达规则，但仍保留用户反馈入口。"),
            evidence=evidence,
            context_hypotheses=[],
            profile_claims=profiles,
            confidence=rule.confidence,
            alternatives=[str(item) for item in output.get("alternatives", ["需要继续确认"])],
            correction_prompt=correction_prompt,
            governance_notes=[
                f"命中运行时 direct_expression 规则：{rule.id} v{rule.version}。",
                "本次没有产生新的 LLM 调用。",
                "如果事件、授权、画像或用户反馈出现偏离，将接回 LLM。",
            ],
            created_at=datetime.now(),
        )
        validate_insight_language(insight)
        memory.record_rule_application(
            rule.id,
            [event.id for event in matched_events],
            insight.to_dict(),
            status="applied",
            reason="direct_expression_fast_path_hit",
        )
        return insight
    return None


def _events_matching_direct_rule(rule: SemanticRule, events: list[ExperienceEvent]) -> list[ExperienceEvent]:
    match_spec = rule.match_spec
    if not _direct_rule_has_executable_match(match_spec):
        return []
    matched: list[ExperienceEvent] = []
    contains = [str(item) for item in match_spec.get("contains", [])]
    for event in events:
        if match_spec.get("event_type") and event.event_type != str(match_spec["event_type"]):
            continue
        if match_spec.get("source_type") and event.source_type != str(match_spec["source_type"]):
            continue
        if match_spec.get("min_confidence") is not None and event.confidence < float(match_spec["min_confidence"]):
            continue
        text = _event_text(event)
        if contains and not all(term in text for term in contains):
            continue
        if match_spec.get("scenes") and not set(str(item) for item in match_spec["scenes"]).issubset(set(event.scenes)):
            continue
        if match_spec.get("people") and not set(str(item) for item in match_spec["people"]).issubset(set(event.people)):
            continue
        matched.append(event)
    return matched


def _direct_rule_has_executable_match(match_spec: dict) -> bool:
    executable_keys = {
        "event_type",
        "source_type",
        "contains",
        "scenes",
        "people",
        "min_confidence",
    }
    return any(key in match_spec for key in executable_keys)


def _direct_rule_skip_reason(memory: JoannaMemory, rule: SemanticRule, matched_events: list[ExperienceEvent]) -> str | None:
    for event_id in rule.evidence_event_ids:
        source_event = memory.get_event(event_id)
        if source_event is None or not usable_for_reasoning(source_event):
            return "source_event_governance_changed"
    if memory.list_feedback_events(target_type="rule", target_id=rule.id) or memory.list_feedback_events(target_type="semantic_rule", target_id=rule.id):
        return "user_feedback_for_rule"
    matched_event_ids = {event.id for event in matched_events}
    for feedback in memory.list_feedback_events(limit=100):
        if matched_event_ids.intersection(feedback.related_event_ids):
            return "user_feedback_for_matched_events"
        if feedback.target_type == "event" and feedback.target_id in matched_event_ids:
            return "user_feedback_for_matched_events"
    for bundle in memory.list_conflict_bundles(limit=100):
        if matched_event_ids.intersection(bundle.event_ids):
            return "conflict_context_for_matched_events"
    expected_features = set(str(item) for item in rule.match_spec.get("feature_kinds", []))
    if "feature_kinds" in rule.match_spec:
        current_features = {feature.kind for feature in extract_features(matched_events, memory=memory)}
        unexpected = current_features - expected_features
        if unexpected:
            return "new_uncovered_features"
    return None


def _event_text(event: ExperienceEvent) -> str:
    return " ".join(
        [
            event.summary,
            str(event.content),
            " ".join(event.people),
            " ".join(event.scenes),
            event.event_type,
        ]
    )


def _run_llm_insight(
    *,
    memory: JoannaMemory,
    task_type: str,
    date_label: str,
    events,
    profiles: list[ProfileClaim],
    llm_client: LLMClient | None,
    allow_huge: bool,
) -> tuple[Insight, list[ProfileClaim], list[EvolutionProposal], list[SemanticRule], str]:
    events_for_llm, compression = _compress_events_for_llm(memory, events)
    plan = estimate_llm_run(task_type, events_for_llm, profiles, allow_huge=allow_huge)
    client = llm_client or DeepSeekClient(max_tokens=plan.max_tokens, timeout=plan.timeout_seconds)
    call_id = f"llm-{uuid4().hex[:12]}"
    started_at = datetime.now()
    event_ids = [event.id for event in events]
    profile_ids = [profile.id for profile in profiles]
    feedback_events = _relevant_feedback_events(memory, events, profiles)
    inference_claims = _relevant_inference_claims(memory, events, profiles)
    conflict_bundles = _relevant_conflict_bundles(memory, feedback_events, inference_claims)
    features = extract_features(events_for_llm, memory=memory)
    attempts = 0
    retry_instruction = ""
    last_error: Exception | None = None
    while attempts < 2:
        attempts += 1
        try:
            insight, profile_candidates, proposals, semantic_rules, conflict_assessments = build_llm_daily_insight(
                client=client,
                date=date_label,
                events=events_for_llm,
                profiles=profiles,
                corrections=memory.list_corrections(),
                task_type=task_type,
                feedback_events=feedback_events,
                inference_claims=inference_claims,
                conflict_bundles=conflict_bundles,
                features=features,
                evidence_compression=compression,
                retry_instruction=retry_instruction,
                llm_call_id=call_id,
            )
            memory.save_llm_call(
                LLMCallRecord(
                    id=call_id,
                    created_at=started_at,
                    task_type=task_type,
                    tier=plan.tier,
                    model=_client_model(client),
                    max_tokens=_client_max_tokens(client) or plan.max_tokens,
                    timeout_seconds=_client_timeout(client) or plan.timeout_seconds,
                    prompt_bytes=plan.prompt_bytes,
                    event_ids=event_ids,
                    profile_ids=profile_ids,
                    sent_external=_sent_external(client),
                    status="success",
                    attempts=attempts,
                    response_bytes=len(json.dumps(insight.to_dict(), ensure_ascii=False).encode("utf-8")),
                    feedback_event_ids=[item.id for item in feedback_events],
                    conflict_bundle_ids=[item.id for item in conflict_bundles],
                )
            )
            for assessment in conflict_assessments:
                memory.update_conflict_bundle_resolution(
                    str(assessment["conflict_bundle_id"]),
                    str(assessment["summary"]),
                    call_id,
                )
            return insight, profile_candidates, proposals, semantic_rules, call_id
        except Exception as exc:
            last_error = exc
            failure_type = classify_llm_exception(exc)
            if attempts < 2 and retryable_failure(failure_type):
                retry_instruction = f"上一次失败类型：{failure_type}。请缩小输出并严格返回合法 JSON。"
                continue
            memory.save_llm_call(
                LLMCallRecord(
                    id=call_id,
                    created_at=started_at,
                    task_type=task_type,
                    tier=plan.tier,
                    model=_client_model(client),
                    max_tokens=_client_max_tokens(client) or plan.max_tokens,
                    timeout_seconds=_client_timeout(client) or plan.timeout_seconds,
                    prompt_bytes=plan.prompt_bytes,
                    event_ids=event_ids,
                    profile_ids=profile_ids,
                    sent_external=_sent_external(client),
                    status="failed",
                    failure_type=failure_type,
                    error_message=str(exc),
                    attempts=attempts,
                    feedback_event_ids=[item.id for item in feedback_events],
                    conflict_bundle_ids=[item.id for item in conflict_bundles],
                )
            )
            raise
    if last_error:
        raise last_error
    raise RuntimeError("LLM call failed without an exception")


def _compress_events_for_llm(
    memory: JoannaMemory,
    events: list[ExperienceEvent],
    *,
    health_threshold: int = 200,
) -> tuple[list[ExperienceEvent], dict]:
    health_events = [
        event
        for event in events
        if event.event_type.startswith("apple_health_")
        and event.event_type != "phase5_health_summary"
    ]
    if len(health_events) < health_threshold:
        return events, {"applied": False, "reason": "below_threshold", "health_event_count": len(health_events)}

    compressed = _phase5_health_summary_events(health_events)
    for event in compressed:
        memory.upsert_event(event)

    non_health = [
        event
        for event in events
        if not event.event_type.startswith("apple_health_")
        and event.event_type != "phase5_health_summary"
    ]
    events_for_llm = sorted([*non_health, *compressed], key=lambda item: (item.occurred_at, item.id))
    return events_for_llm, {
        "applied": True,
        "strategy": "phase5_apple_health_group_by_date_type_audio_overlap",
        "original_event_count": len(events),
        "original_apple_health_event_count": len(health_events),
        "compressed_event_count": len(compressed),
        "llm_event_count": len(events_for_llm),
        "summary_event_ids": [event.id for event in compressed],
        "governance": [
            "Apple Health 原始样本仍保留在 SQLite；LLM 收到的是按日期、类型和音频重叠状态压缩后的摘要证据。",
            "摘要事件只是证据索引，不是医学判断，也不允许直接沉淀画像。",
            "需要核查细节时，应回到原始 apple_health_* 事件和 overlap_audio_segment_ids。",
        ],
    }


def _phase5_health_summary_events(events: list[ExperienceEvent]) -> list[ExperienceEvent]:
    groups: dict[tuple[str, str, str], list[ExperienceEvent]] = {}
    for event in events:
        overlap = bool(event.content.get("audio_overlap"))
        key = (
            event.occurred_at.date().isoformat(),
            event.event_type,
            "audio_overlap" if overlap else "no_audio_overlap",
        )
        groups.setdefault(key, []).append(event)

    summaries: list[ExperienceEvent] = []
    for (date, event_type, overlap_key), grouped in sorted(groups.items()):
        grouped = sorted(grouped, key=lambda item: (item.occurred_at, item.id))
        values = [_float_value(item.content.get("value")) for item in grouped]
        numeric_values = [value for value in values if value is not None]
        source_names = sorted({str(item.content.get("source_name") or "") for item in grouped if item.content.get("source_name")})
        units = sorted({str(item.content.get("unit") or "") for item in grouped if item.content.get("unit")})
        overlap_segment_ids = sorted(
            {
                str(segment_id)
                for item in grouped
                for segment_id in item.content.get("overlap_audio_segment_ids", [])
            }
        )
        sample_event_ids = [item.id for item in [*grouped[:5], *grouped[-5:]]]
        sample_event_ids = list(dict.fromkeys(sample_event_ids))
        content = {
            "compression": "phase5_apple_health_summary",
            "source_event_type": event_type,
            "source_event_count": len(grouped),
            "sample_source_event_ids": sample_event_ids,
            "date": date,
            "audio_overlap": overlap_key == "audio_overlap",
            "overlap_audio_segment_ids": overlap_segment_ids[:20],
            "overlap_audio_segment_count": len(overlap_segment_ids),
            "source_names": source_names[:10],
            "units": units[:5],
            "first_occurred_at": grouped[0].occurred_at.isoformat(),
            "last_occurred_at": grouped[-1].occurred_at.isoformat(),
            "allow_medical_judgment": False,
            "allow_profile": False,
        }
        if numeric_values:
            content["numeric"] = {
                "count": len(numeric_values),
                "min": round(min(numeric_values), 4),
                "max": round(max(numeric_values), 4),
                "avg": round(mean(numeric_values), 4),
            }
        summary = _health_summary_text(event_type, grouped, overlap_key, units, numeric_values)
        summaries.append(
            ExperienceEvent(
                id=f"evt.phase5.health_summary.{date}.{_slug(event_type)}.{overlap_key}",
                occurred_at=grouped[0].occurred_at,
                source_type=SourceType.HEALTH_SAMPLE,
                source_id="phase5-health-compression",
                event_type="phase5_health_summary",
                summary=summary,
                content=content,
                people=[],
                scenes=["五期真实测试", "健康数据摘要"],
                sensitivity=SensitivityLevel.SENSITIVE,
                allow_long_term=True,
                allow_profile=False,
                confidence=0.78 if overlap_key == "audio_overlap" else 0.7,
                evidence_refs=sample_event_ids + overlap_segment_ids[:10],
            )
        )
    return summaries


def _health_summary_text(
    event_type: str,
    grouped: list[ExperienceEvent],
    overlap_key: str,
    units: list[str],
    numeric_values: list[float],
) -> str:
    label = event_type.removeprefix("apple_health_")
    overlap_text = "与 DJI 音频片段有时间重叠" if overlap_key == "audio_overlap" else "处在测试总窗内但不与 DJI 音频片段重叠"
    time_range = f"{grouped[0].occurred_at.isoformat()} 至 {grouped[-1].occurred_at.isoformat()}"
    if numeric_values:
        unit = units[0] if units else ""
        stat = f"，数值范围 {round(min(numeric_values), 4)}-{round(max(numeric_values), 4)} {unit}".rstrip()
    else:
        stat = ""
    return (
        f"Apple Health {label} 摘要：{len(grouped)} 条样本，{time_range}，{overlap_text}{stat}。"
        "这是发送给 LLM 的压缩证据，不是医学判断；原始样本仍保留在本地库。"
    )


def _float_value(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")


def _client_model(client: LLMClient) -> str:
    return str(getattr(client, "model", client.__class__.__name__))


def _client_max_tokens(client: LLMClient) -> int:
    return int(getattr(client, "max_tokens", 0) or 0)


def _client_timeout(client: LLMClient) -> int:
    return int(getattr(client, "timeout", 0) or 0)


def _sent_external(client: LLMClient) -> bool:
    return isinstance(client, DeepSeekClient)


def _relevant_feedback_events(
    memory: JoannaMemory,
    events: list[ExperienceEvent],
    profiles: list[ProfileClaim],
) -> list:
    event_ids = {event.id for event in events}
    profile_ids = {profile.id for profile in profiles}
    feedback_events = memory.list_feedback_events(limit=100)
    relevant = []
    for feedback in feedback_events:
        if event_ids.intersection(feedback.related_event_ids) or profile_ids.intersection(feedback.related_profile_ids):
            relevant.append(feedback)
            continue
        if feedback.target_type == "event" and feedback.target_id in event_ids:
            relevant.append(feedback)
            continue
        if feedback.target_type == "profile" and feedback.target_id in profile_ids:
            relevant.append(feedback)
    return relevant[:50]


def _relevant_inference_claims(
    memory: JoannaMemory,
    events: list[ExperienceEvent],
    profiles: list[ProfileClaim],
) -> list:
    event_ids = {event.id for event in events}
    profile_ids = {profile.id for profile in profiles}
    claims = memory.list_inference_claims(limit=100)
    relevant = []
    for claim in claims:
        claim_event_ids = {item.event_id for item in claim.evidence}
        if event_ids.intersection(claim_event_ids) or claim.subject_id in profile_ids:
            relevant.append(claim)
    return relevant[:50]


def _relevant_conflict_bundles(
    memory: JoannaMemory,
    feedback_events,
    inference_claims,
) -> list:
    feedback_ids = {item.id for item in feedback_events}
    claim_ids = {item.id for item in inference_claims}
    bundles = []
    for bundle in memory.list_conflict_bundles(limit=100):
        if feedback_ids.intersection(bundle.feedback_event_ids) or claim_ids.intersection(bundle.claim_ids):
            bundles.append(bundle)
    return bundles[:30]
