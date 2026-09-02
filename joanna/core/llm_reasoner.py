from __future__ import annotations

from datetime import datetime
import json
from uuid import uuid4

from joanna.core.features import extract_features, validate_runtime_feature_rule_spec
from joanna.core.governance import DIAGNOSTIC_TERMS, FUTURE_COMMITMENT_TERMS, usable_for_profile, validate_insight_language
from joanna.core.llm import LLMClient
from joanna.core.schema import (
    ConflictBundle,
    ContextHypothesis,
    EvolutionProposal,
    EvolutionProposalType,
    EvolutionRisk,
    EvolutionStatus,
    ExperienceEvent,
    ExperienceFeature,
    FeatureKind,
    FeedbackEvent,
    Insight,
    InsightType,
    InferenceClaim,
    ProfileClaim,
    ProfileType,
    SemanticObservation,
    SemanticRule,
    SemanticRuleStatus,
    SemanticRuleType,
)


FORBIDDEN_VISIBLE_TERMS = "、".join([*DIAGNOSTIC_TERMS, *FUTURE_COMMITMENT_TERMS])
OBSERVATION_SEEDS = (
    "进食与补给",
    "睡眠与恢复",
    "出行与等待",
    "工作与任务切换",
    "人际互动",
    "家庭与居家牵引",
    "独处与恢复",
    "身体信号变化",
    "消费与选择",
    "计划变更与延误",
)
OBSERVATION_SEEDS_TEXT = "、".join(OBSERVATION_SEEDS)
CORE_PHILOSOPHY = (
    {
        "principle": "以 LLM 推理为中心，持续解释个人证据链。",
        "implementation_meaning": "LLM 的职责是基于完整上下文重新解释证据链，而不是替代证据、治理或用户授权。",
    },
    {
        "principle": "所有可观察输入都先进入证据链。",
        "implementation_meaning": "原始事件、设备信号、用户自述、用户纠正、否认、抵触、删除请求、撤回请求和关闭规则请求都是证据事件，而不是直接改写真相的最终裁决。",
    },
    {
        "principle": "所有系统输出都只是推理声明。",
        "implementation_meaning": "情境判断、语义观察、画像候选、提醒建议和表达内容必须保留证据、置信度、不确定性和替代解释，不能写成事实、诊断或定论。",
    },
    {
        "principle": "所有状态都只是派生结果。",
        "implementation_meaning": "当前状态、长期画像、规则命中和表达偏好都是某一时点的临时结果，可以被新证据更新、降权、冲突化或重算。",
    },
    {
        "principle": "冲突不应覆盖任何一方。",
        "implementation_meaning": "当用户反馈与系统推理不一致时，必须同时保留原始经验、当时的系统推理、用户后续反馈和相邻证据，由 LLM 基于完整上下文重新解释。",
    },
    {
        "principle": "用户本人及其反应也是证据链的一部分。",
        "implementation_meaning": "乔纳不是用户编辑自己画像的系统，也不是用户拥有最终解释权的助手；反馈机制的目标是把用户反应纳入证据链。",
    },
    {
        "principle": "治理边界不能被 LLM 推理绕过。",
        "implementation_meaning": "用户的授权、隐私、外部行动和风险边界属于治理约束；治理机制保证证据链、推理链、反馈链、状态派生链和表达链可追溯。",
    },
    {
        "principle": "表达层必须呈现证据、不确定性、替代解释和冲突。",
        "implementation_meaning": "表达不能把原推理、用户反馈、画像、规则或任何一方包装成定论。",
    },
)
CONTEXT_ASSEMBLY_POLICY = (
    {
        "object": "events",
        "meaning": "原始或导入经验事件，是证据材料，不是自动事实结论。",
        "do_not_misuse_as": "原因、诊断或长期画像本身。",
    },
    {
        "object": "features",
        "meaning": "本地特征是低层信号，用来帮助组织观察域。",
        "do_not_misuse_as": "因果证明、诊断或用户本质定义。",
    },
    {
        "object": "profiles",
        "meaning": "画像是长期派生候选或状态，需要证据和治理约束。",
        "do_not_misuse_as": "事实、诊断、人格标签或不可变化的定义。",
    },
    {
        "object": "corrections",
        "meaning": "旧兼容纠正记录，只能作为历史反馈线索。",
        "do_not_misuse_as": "直接删除原推理的命令。",
    },
    {
        "object": "feedback_events",
        "meaning": "用户反应事件，包括否认、修正、抵触画像、删除请求、撤回请求、关闭规则请求、表达反感和追问原因。",
        "do_not_misuse_as": "最终裁决、未来固定偏置规则或绕过授权的理由。",
    },
    {
        "object": "inference_claims",
        "meaning": "系统或 LLM 曾经形成的推理声明，不是事实。",
        "do_not_misuse_as": "真实发生的事件或用户自我定义。",
    },
    {
        "object": "conflict_bundles",
        "meaning": "原推理、用户反馈和相关证据之间的冲突组合，后续必须解释。",
        "do_not_misuse_as": "覆盖其中一方或宣布某一方最终为真。",
    },
    {
        "object": "semantic_rules/direct_expression",
        "meaning": "运行时规则是派生工具，只能帮助表达和观察。",
        "do_not_misuse_as": "放宽治理边界、外部能力或长期事实的机制。",
    },
)
OUTPUT_CONTRACT = (
    {
        "field": "semantic_observations",
        "responsibility": "候选细节观察，只能说可能看到什么，必须引用有效事件 ID。",
    },
    {
        "field": "contexts",
        "responsibility": "候选情境解释，必须带替代解释和不确定性。",
    },
    {
        "field": "profile_candidates",
        "responsibility": "长期画像候选，只能进入待确认流程，并受画像证据边界限制。",
    },
    {
        "field": "evolution_proposals",
        "responsibility": "运行时调整建议，不得绕过风险分级；governance_boundary 永远 high risk + pending。",
    },
    {
        "field": "rule_updates",
        "responsibility": "运行时语义规则，不得扩张授权、隐私边界或外部能力。",
    },
    {
        "field": "direct_expression_rules",
        "responsibility": "窄范围固定事件直接表达规则，不能固化用户反馈为未来事实。",
    },
    {
        "field": "conflict_assessments",
        "responsibility": "解释冲突关系，不能直接裁决原推理或用户反馈某一方为真。",
    },
    {
        "field": "expression",
        "responsibility": "用户可见表达，必须呈现证据、不确定性、替代解释和冲突。",
    },
)
HARD_GOVERNANCE_BOUNDARIES = (
    {
        "boundary": "privacy_and_permission",
        "rule": "未授权数据源不得读取、假装读取或暗示已接入。",
    },
    {
        "boundary": "external_actions",
        "rule": "不得自动发消息、联系他人、创建日程、发送文件、下单或触发外部服务。",
    },
    {
        "boundary": "diagnosis",
        "rule": "不得输出医学、心理、人格诊断。",
    },
    {
        "boundary": "capability_claims",
        "rule": "只能描述当前系统真实具备的能力；当前未接入 Calendar、HealthKit、路线、通知或通信工具。",
    },
    {
        "boundary": "data_governance",
        "rule": "删除、禁用、撤回画像使用等维护命令必须与用户语义反馈入口区分；删除请求是证据，但不能替代工程层隐私治理。",
    },
    {
        "boundary": "epistemic_boundary",
        "rule": "用户反馈和 LLM 推理都不是最终事实；必须并列进入后续证据链。",
    },
    {
        "boundary": "expression_boundary",
        "rule": "表达层不能把不确定推理包装成事实、诊断、承诺或自动行动。",
    },
)


JSON_SCHEMA_PROMPT = """
JSON 格式：
{
  "semantic_observations": [
    {
      "id": "observation.some_slug",
      "observation_type": "进食与补给|睡眠与恢复|出行与等待|工作与任务切换|人际互动|家庭与居家牵引|独处与恢复|身体信号变化|消费与选择|计划变更与延误|其他",
      "text": "LLM 发现的候选细节",
      "confidence": 0.0,
      "evidence_event_ids": ["event-id"],
      "alternatives": ["替代解释"]
    }
  ],
  "contexts": [
    {
      "id": "context.open.some_slug",
      "context_type": "候选情境名称",
      "confidence": 0.0,
      "evidence_event_ids": ["event-id"],
      "alternatives": ["替代解释"],
      "uncertainty": "不确定性说明"
    }
  ],
  "profile_candidates": [
    {
      "id": "profile.candidate.some_slug",
      "claim": "待确认画像候选",
      "confidence": 0.0,
      "evidence_event_ids": ["event-id"]
    }
  ],
  "evolution_proposals": [
    {
      "proposal_type": "feature_weight|expression_preference|profile_candidate|governance_boundary",
      "risk": "low|high",
      "title": "建议标题",
      "rationale": "理由",
      "payload": {},
      "evidence_event_ids": ["event-id"]
    }
  ],
  "rule_updates": [
    {
      "id": "rule.semantic.some_slug",
      "type": "feature_extractor|situation_template|direct_expression",
      "match_spec": {},
      "output_spec": {},
      "confidence": 0.0,
      "evidence_event_ids": ["event-id"]
    }
  ],
  "direct_expression_rules": [
    {
      "id": "rule.direct.some_slug",
      "match_spec": {"event_type": "self_report", "contains": ["固定关键词"], "scenes": ["场景"]},
      "output_spec": {"title": "固定事件输出标题", "body": "固定事件直接输出正文", "alternatives": ["替代解释"]},
      "confidence": 0.0,
      "evidence_event_ids": ["event-id"]
    }
  ],
  "conflict_assessments": [
    {
      "id": "conflict_assessment.some_slug",
      "conflict_bundle_id": "conflict-id",
      "summary": "对原推理和用户反馈之间关系的候选解释",
      "confidence": 0.0,
      "evidence_event_ids": ["event-id"],
      "alternatives": ["误判", "自我感知差异", "表达防御", "场景变化"]
    }
  ],
  "expression": {
    "title": "洞察标题",
    "body": "面向用户的可信表达",
    "alternatives": ["替代解释"],
    "confidence": 0.0
  }
}
"""


def build_system_prompt(retry_instruction: str = "") -> str:
    sections = [
        "你是乔纳个人助手的受控 LLM 推理器。你只能基于 payload 提供的事件、通用特征、画像、推理声明、用户反馈事件和冲突上下文生成候选推理。",
        (
            "核心理念：以 LLM 推理为中心，持续解释个人证据链；所有可观察输入都先进入证据链；"
            "所有系统输出都只是推理声明；所有状态都只是派生结果；冲突需要基于完整上下文重新解释。"
            "payload.core_philosophy 会提供结构化版本。"
        ),
        (
            "上下文对象说明见 payload.context_assembly_policy。不能把 feedback_events 当成最终裁决，"
            "不能把 profiles 当成事实或诊断，不能把 features 当成因果证明，遇到 conflict_bundles 必须保留原推理和用户反馈。"
        ),
        (
            "输出字段职责见 payload.output_contract。semantic_observations 只能是候选细节观察；contexts 必须带替代解释和不确定性；"
            "profile_candidates 只能进入待确认流程；direct_expression_rules 不能固化用户反馈为未来事实；"
            "conflict_assessments 不能直接裁决某一方为真；expression 必须呈现证据、不确定性、替代解释和冲突。"
        ),
        (
            "硬治理边界见 payload.hard_governance_boundaries。用户反馈不是最终事实，但授权、隐私、外部行动和能力声明是硬边界，"
            "不能由推理绕过。未授权数据源不得读取、假装读取或暗示已接入；不得自动发消息、联系他人、创建日程、发送文件、下单或触发外部服务；"
            "当前未接入 Calendar、HealthKit、路线、通知或通信工具。"
        ),
        "必须输出 JSON object，不要输出 Markdown。严禁编造事件、证据或个人事实。每个候选判断必须引用 evidence_event_ids 中存在的事件 ID。",
        "不要做医学、心理、人格诊断；不要把相关性写成因果；不要自动执行任何外部行动。",
        "用户反馈、否认、撤回、删除请求和关闭请求都是新的个人证据，不是最终裁决；不能因为用户反馈就删除或覆盖原推理。",
        "不要承诺“后续类似情境会优先采用用户反馈”“系统会据此在类似情境减少展开”“未来会更加注意”，"
        "不要写“这些都不准确”“我们已经调整了理解”，也不要把用户说法变成固定偏置规则；只能说明用户反馈会作为高价值证据与原证据并列参与推理。",
        "如果 payload 中有 conflict_bundles，表达时应同时保留原推理和用户反馈，并给出候选解释。",
        f"通用观察种子是：{OBSERVATION_SEEDS_TEXT}。它们只表示值得观察的日常生活域，不是结论模板，也不能替代证据。",
        f"所有用户可见字段严禁出现这些词：{FORBIDDEN_VISIBLE_TERMS}。请用“可能”“候选”“也许”“需要确认”这类保守表达替代。",
        "所有用户可见字段必须用中文输出，包括 context_type、title、body、alternatives、uncertainty、claim、proposal title 和 rationale。",
        "如果表达正文引用了某个事件中的人物、地点、偏好、消息或状态，对应事件 ID 必须出现在相关 context 的 evidence_event_ids 中。",
        "不要生成重复的画像候选或重复的自进化提案。governance_boundary 永远是 high risk，必须 pending，不能自动生效。",
        JSON_SCHEMA_PROMPT.strip(),
    ]
    if retry_instruction:
        sections.append("重试要求：只输出更小的 JSON object，减少候选数量，严格避开所有治理违规词和无效证据 ID。")
        sections.append(f"本次重试原因：{retry_instruction}")
    return "\n".join(sections)


SYSTEM_PROMPT = build_system_prompt()


def build_llm_daily_insight(
    client: LLMClient,
    date: str,
    events: list[ExperienceEvent],
    profiles: list[ProfileClaim],
    corrections: list,
    task_type: str = "daily_insight",
    feedback_events: list[FeedbackEvent] | None = None,
    inference_claims: list[InferenceClaim] | None = None,
    conflict_bundles: list[ConflictBundle] | None = None,
    features: list[ExperienceFeature] | None = None,
    evidence_compression: dict | None = None,
    retry_instruction: str = "",
    llm_call_id: str | None = None,
) -> tuple[Insight, list[ProfileClaim], list[EvolutionProposal], list[SemanticRule], list[dict]]:
    features = features or extract_features(events)
    allowed_event_ids = {event.id for event in events}
    profile_event_ids = {event.id for event in events if usable_for_profile(event)}
    feedback_events = feedback_events or []
    inference_claims = inference_claims or []
    conflict_bundles = conflict_bundles or []
    payload = {
        "task_type": task_type,
        "date": date,
        "events": [event.to_dict() for event in events],
        "features": [feature.to_dict() for feature in features],
        "profiles": [profile.to_dict() for profile in profiles],
        "corrections": [correction.to_dict() for correction in corrections],
        "feedback_events": [feedback.to_dict() for feedback in feedback_events],
        "inference_claims": [claim.to_dict() for claim in inference_claims],
        "conflict_bundles": [bundle.to_dict() for bundle in conflict_bundles],
        "evidence_compression": evidence_compression or {"applied": False},
        "core_philosophy": list(CORE_PHILOSOPHY),
        "context_assembly_policy": list(CONTEXT_ASSEMBLY_POLICY),
        "output_contract": list(OUTPUT_CONTRACT),
        "hard_governance_boundaries": list(HARD_GOVERNANCE_BOUNDARIES),
        "governance": {
            "must_reference_event_ids": True,
            "no_diagnosis": True,
            "profile_candidates_require_confirmation": True,
            "allowed_low_risk_auto_evolution": ["expression_preference", "feature_weight"],
            "observation_seeds_are_not_conclusions": True,
            "feedback_is_evidence_not_final_verdict": True,
            "system_outputs_are_inference_claims": True,
            "states_are_derived_not_facts": True,
            "llm_must_use_complete_evidence_chain": True,
            "permission_privacy_external_action_are_hard_boundaries": True,
            "capability_claims_must_match_current_system": True,
        },
        "observation_seeds": list(OBSERVATION_SEEDS),
    }
    if retry_instruction:
        payload["retry_instruction"] = retry_instruction
    system_prompt = build_system_prompt(retry_instruction)
    raw = client.complete_json(system_prompt, payload)
    if not isinstance(raw, dict):
        raise ValueError("LLM returned invalid JSON object")
    expression = raw.get("expression", {})
    body = str(expression.get("body") or "")
    semantic_observations = _parse_semantic_observations(raw.get("semantic_observations", []), events, allowed_event_ids)
    conflict_assessments = _parse_conflict_assessments(raw.get("conflict_assessments", []), events, allowed_event_ids, conflict_bundles)
    semantic_observations.extend(_conflict_assessments_as_observations(conflict_assessments, events))
    contexts = _parse_contexts(raw.get("contexts", []), events, allowed_event_ids, body)
    profile_candidates = _parse_profile_candidates(raw.get("profile_candidates", []), events, profile_event_ids)
    proposals = _parse_evolution(raw.get("evolution_proposals", []), events, allowed_event_ids, profile_event_ids)
    semantic_rules = [
        *_parse_rule_updates(raw.get("rule_updates", []), events, allowed_event_ids, llm_call_id),
        *_parse_rule_updates(
            raw.get("direct_expression_rules", []),
            events,
            allowed_event_ids,
            llm_call_id,
            default_rule_type=SemanticRuleType.DIRECT_EXPRESSION,
        ),
    ]
    evidence = _evidence_for_ids(events, _ids_from_contexts(contexts) or sorted(allowed_event_ids))
    insight = Insight(
        id=f"insight.llm.daily.{date}",
        insight_type=InsightType.DAILY,
        title=str(expression.get("title") or f"{date} LLM 今日状态洞察"),
        body=str(expression.get("body") or _fallback_body(contexts)),
        evidence=evidence,
        context_hypotheses=contexts,
        profile_claims=profiles + profile_candidates,
        confidence=_confidence(expression.get("confidence"), contexts),
        alternatives=[str(item) for item in expression.get("alternatives", _alternatives_from_contexts(contexts))],
        correction_prompt="如果这个判断不对，请记录反馈事件；原判断和反馈会并存进入后续推理。",
        governance_notes=[
            "LLM 只能基于当前传入证据生成候选判断。",
            "通用观察种子只是观察域，不是结论模板。",
            "画像候选默认待确认，不能自动变成事实。",
            "低风险表达偏好可自动沉淀，高风险授权或画像变化必须确认。",
            "用户反馈是后续推理证据，不会直接覆盖原推理声明。",
        ],
        created_at=datetime.now(),
        semantic_observations=semantic_observations,
    )
    validate_insight_language(insight)
    return insight, profile_candidates, proposals, semantic_rules, conflict_assessments


def _parse_semantic_observations(
    raw_observations: list,
    events: list[ExperienceEvent],
    allowed_ids: set[str],
) -> list[SemanticObservation]:
    observations: list[SemanticObservation] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in raw_observations:
        ids = _valid_ids(item.get("evidence_event_ids", []), allowed_ids)
        if not ids:
            continue
        text = str(item.get("text") or "")
        if not text:
            continue
        key = (_normalize_text(text), tuple(sorted(ids)))
        if key in seen:
            continue
        seen.add(key)
        observations.append(
            SemanticObservation(
                id=str(item.get("id") or f"observation.{uuid4().hex[:8]}"),
                observation_type=str(item.get("observation_type") or "其他"),
                text=text,
                evidence=_evidence_for_ids(events, ids),
                confidence=_bounded_float(item.get("confidence"), default=0.45),
                alternatives=[str(value) for value in item.get("alternatives", [])],
            )
        )
    return observations


def _parse_conflict_assessments(
    raw_assessments: list,
    events: list[ExperienceEvent],
    allowed_ids: set[str],
    conflict_bundles: list[ConflictBundle],
) -> list[dict]:
    valid_bundle_ids = {bundle.id for bundle in conflict_bundles}
    assessments: list[dict] = []
    for item in raw_assessments:
        bundle_id = str(item.get("conflict_bundle_id") or "")
        if bundle_id not in valid_bundle_ids:
            continue
        ids = _valid_ids(item.get("evidence_event_ids", []), allowed_ids)
        if not ids:
            continue
        summary = str(item.get("summary") or "")
        if not summary:
            continue
        assessments.append(
            {
                "id": str(item.get("id") or f"conflict_assessment.{uuid4().hex[:8]}"),
                "conflict_bundle_id": bundle_id,
                "summary": summary,
                "confidence": _bounded_float(item.get("confidence"), default=0.45),
                "evidence_event_ids": ids,
                "alternatives": [str(value) for value in item.get("alternatives", [])],
            }
        )
    return assessments


def _conflict_assessments_as_observations(
    assessments: list[dict],
    events: list[ExperienceEvent],
) -> list[SemanticObservation]:
    observations: list[SemanticObservation] = []
    for item in assessments:
        observations.append(
            SemanticObservation(
                id=str(item["id"]),
                observation_type="冲突解释",
                text=str(item["summary"]),
                evidence=_evidence_for_ids(events, item["evidence_event_ids"]),
                confidence=float(item["confidence"]),
                alternatives=[str(value) for value in item.get("alternatives", [])],
            )
        )
    return observations


def _parse_contexts(
    raw_contexts: list,
    events: list[ExperienceEvent],
    allowed_ids: set[str],
    expression_body: str,
) -> list[ContextHypothesis]:
    contexts: list[ContextHypothesis] = []
    for item in raw_contexts:
        ids = _valid_ids(item.get("evidence_event_ids", []), allowed_ids)
        if not ids:
            continue
        ids = _augment_ids_from_expression(ids, expression_body, events)
        contexts.append(
            ContextHypothesis(
                id=str(item.get("id") or f"context.open.{uuid4().hex[:8]}"),
                context_type=str(item.get("context_type") or "开放候选情境"),
                time_range=_time_range(events, ids),
                evidence=_evidence_for_ids(events, ids),
                confidence=_bounded_float(item.get("confidence"), default=0.45),
                alternatives=[str(value) for value in item.get("alternatives", [])],
                uncertainty=str(item.get("uncertainty") or "LLM 候选情境，需要用户确认和后续校正。"),
            )
        )
    return _dedupe_contexts(contexts)


def _parse_profile_candidates(raw_profiles: list, events: list[ExperienceEvent], allowed_ids: set[str]) -> list[ProfileClaim]:
    profiles: list[ProfileClaim] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in raw_profiles:
        ids = _valid_ids(item.get("evidence_event_ids", []), allowed_ids)
        if not ids:
            continue
        key = (
            _normalize_text(str(item.get("claim") or "")),
            tuple(sorted(ids)),
        )
        if key in seen:
            continue
        seen.add(key)
        profiles.append(
            ProfileClaim(
                id=str(item.get("id") or f"profile.candidate.{uuid4().hex[:8]}"),
                claim=str(item.get("claim") or "LLM 生成的待确认画像候选。"),
                profile_type=ProfileType.UNCONFIRMED_PATTERN,
                evidence=_evidence_for_ids(events, ids),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                confidence=_bounded_float(item.get("confidence"), default=0.45),
                user_confirmed=False,
            )
        )
    return profiles


def _parse_evolution(
    raw_proposals: list,
    events: list[ExperienceEvent],
    allowed_ids: set[str],
    profile_allowed_ids: set[str],
) -> list[EvolutionProposal]:
    proposals: list[EvolutionProposal] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in raw_proposals:
        proposal_type = str(item.get("proposal_type") or EvolutionProposalType.PROFILE_CANDIDATE)
        ids = _valid_ids(
            item.get("evidence_event_ids", []),
            profile_allowed_ids if proposal_type in {EvolutionProposalType.PROFILE_CANDIDATE, "profile_candidate"} else allowed_ids,
        )
        if not ids:
            continue
        risk = str(item.get("risk") or EvolutionRisk.HIGH)
        if proposal_type == EvolutionProposalType.GOVERNANCE_BOUNDARY or proposal_type == "governance_boundary":
            risk = EvolutionRisk.HIGH
        status = EvolutionStatus.APPLIED if risk == EvolutionRisk.LOW and proposal_type in {
            EvolutionProposalType.EXPRESSION_PREFERENCE,
            EvolutionProposalType.FEATURE_WEIGHT,
            "expression_preference",
            "feature_weight",
        } else EvolutionStatus.PENDING
        title = str(item.get("title") or "LLM 自进化建议")
        rationale = str(item.get("rationale") or "LLM 基于当前证据生成的候选建议。")
        key = (proposal_type, _normalize_text(title + rationale), tuple(sorted(ids)))
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            EvolutionProposal(
                id=f"evo.llm.{uuid4().hex[:12]}",
                proposal_type=proposal_type,
                status=status,
                risk=risk,
                title=title,
                rationale=rationale,
                payload=dict(item.get("payload", {})),
                evidence=_evidence_for_ids(events, ids),
                created_at=datetime.now(),
                applied_at=datetime.now() if status == EvolutionStatus.APPLIED else None,
            )
        )
    return proposals


def _parse_rule_updates(
    raw_rules: list,
    events: list[ExperienceEvent],
    allowed_ids: set[str],
    llm_call_id: str | None,
    default_rule_type: str = SemanticRuleType.SITUATION_TEMPLATE,
) -> list[SemanticRule]:
    rules: list[SemanticRule] = []
    seen: set[str] = set()
    for item in raw_rules:
        rule_type = str(item.get("type") or item.get("rule_type") or default_rule_type)
        if rule_type not in {value.value for value in SemanticRuleType}:
            continue
        ids = _valid_ids(item.get("evidence_event_ids", []), allowed_ids)
        if not ids:
            continue
        match_spec = _dict_value(item.get("match_spec"))
        output_spec = _dict_value(item.get("output_spec"))
        if (
            not match_spec
            or not output_spec
            or _rule_update_violates_governance(match_spec, output_spec)
            or _feature_extractor_rule_invalid(rule_type, match_spec, output_spec)
            or _direct_expression_match_too_broad(rule_type, match_spec)
        ):
            continue
        rule_id = str(item.get("id") or f"rule.semantic.{uuid4().hex[:8]}")
        if rule_id in seen:
            continue
        seen.add(rule_id)
        now = datetime.now()
        rules.append(
            SemanticRule(
                id=rule_id,
                rule_type=rule_type,
                status=SemanticRuleStatus.ACTIVE,
                version=1,
                source=str(item.get("source") or "llm_rule_update"),
                created_by_llm_call_id=llm_call_id,
                match_spec=match_spec,
                output_spec=output_spec,
                evidence_event_ids=ids,
                confidence=_bounded_float(item.get("confidence"), default=0.45),
                created_at=now,
                updated_at=now,
            )
        )
    return rules


def _dict_value(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _rule_update_violates_governance(match_spec: dict, output_spec: dict) -> bool:
    text = json.dumps({"match_spec": match_spec, "output_spec": output_spec}, ensure_ascii=False).lower()
    forbidden = [
        "__",
        "eval",
        "exec",
        "lambda",
        "import",
        "subprocess",
        "open(",
        "read(",
        "write(",
        "os.",
        "sys.",
        "pathlib",
        "requests",
        "http://",
        "https://",
        "regex",
        "re.",
        "python",
        "allow_profile",
        "allow_long_term",
        "profile_usage_revoked",
        "governance_boundary",
        "authorization",
        "permission",
        "send_message",
        "external_action",
        "diagnosis",
        "帮你查看",
        "查看后续",
        "查看会议",
        "路程时间",
        "自动发送",
        "自动联系",
        "诊断",
        "授权",
        "后续类似",
        "而非紧张",
        "不是紧张",
        "优先考虑",
        "固定判断",
    ]
    return any(term in text for term in forbidden)


def _feature_extractor_rule_invalid(rule_type: str, match_spec: dict, output_spec: dict) -> bool:
    if rule_type != SemanticRuleType.FEATURE_EXTRACTOR:
        return False
    return not validate_runtime_feature_rule_spec(match_spec, output_spec)


def _direct_expression_match_too_broad(rule_type: str, match_spec: dict) -> bool:
    if rule_type != SemanticRuleType.DIRECT_EXPRESSION:
        return False
    executable_keys = {
        "event_type",
        "source_type",
        "contains",
        "scenes",
        "people",
        "min_confidence",
    }
    return not any(key in match_spec for key in executable_keys)


def _augment_ids_from_expression(
    ids: list[str],
    expression_body: str,
    events: list[ExperienceEvent],
) -> list[str]:
    result = list(dict.fromkeys(ids))
    for event in events:
        if event.id in result:
            continue
        markers = _event_markers(event)
        if markers and sum(1 for marker in markers if marker in expression_body) >= 1:
            result.append(event.id)
    return result


def _event_markers(event: ExperienceEvent) -> set[str]:
    markers: set[str] = set()
    for value in [*event.people, *event.scenes]:
        if len(value) >= 2:
            markers.add(value)
    for term in [
        "家人",
        "回家",
        "低打扰",
        "延误",
        "改约",
        "线上",
        "高铁",
        "航班",
        "睡眠",
        "心率",
        "语速",
        "复盘",
        "电话",
        "会议",
        "创作",
        "早餐",
        "午餐",
        "午饭",
        "用餐",
        "餐后",
        "晚饭",
        "夜宵",
        "吃饭",
        "回消息",
        "取快递",
        "顺路",
    ]:
        if term in event.summary or term in str(event.content):
            markers.add(term)
    return markers


def _dedupe_contexts(contexts: list[ContextHypothesis]) -> list[ContextHypothesis]:
    result: list[ContextHypothesis] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for context in contexts:
        key = (
            _normalize_text(context.context_type),
            tuple(sorted(item.event_id for item in context.evidence)),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(context)
    return result


def _normalize_text(value: str) -> str:
    return "".join(value.lower().split())


def _valid_ids(raw_ids: list, allowed_ids: set[str]) -> list[str]:
    return [str(item) for item in raw_ids if str(item) in allowed_ids]


def _evidence_for_ids(events: list[ExperienceEvent], ids: list[str]) -> list:
    by_id = {event.id: event for event in events}
    return [by_id[event_id].to_evidence() for event_id in ids if event_id in by_id]


def _time_range(events: list[ExperienceEvent], ids: list[str]) -> str:
    by_id = {event.id: event for event in events}
    selected = sorted([by_id[event_id] for event_id in ids if event_id in by_id], key=lambda event: event.occurred_at)
    if not selected:
        return "unknown"
    return f"{selected[0].occurred_at.isoformat()} -> {selected[-1].occurred_at.isoformat()}"


def _ids_from_contexts(contexts: list[ContextHypothesis]) -> list[str]:
    ids = []
    for context in contexts:
        ids.extend(item.event_id for item in context.evidence)
    return sorted(set(ids))


def _confidence(value, contexts: list[ContextHypothesis]) -> float:
    if value is not None:
        return _bounded_float(value, default=0.45)
    if contexts:
        return max(context.confidence for context in contexts)
    return 0.2


def _bounded_float(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 2)


def _alternatives_from_contexts(contexts: list[ContextHypothesis]) -> list[str]:
    alternatives: list[str] = []
    for context in contexts:
        alternatives.extend(context.alternatives)
    return alternatives or ["信息不足", "需要用户确认"]


def _fallback_body(contexts: list[ContextHypothesis]) -> str:
    if not contexts:
        return "LLM 没有生成可接受的证据化候选情境。"
    return "；".join(context.context_type for context in contexts)
