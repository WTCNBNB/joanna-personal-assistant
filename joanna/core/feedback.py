from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from joanna.core.memory import JoannaMemory
from joanna.core.schema import ConflictBundle, ConflictBundleStatus, FeedbackEvent, FeedbackType, InferenceClaim


def record_feedback(
    memory: JoannaMemory,
    *,
    target_type: str,
    target_id: str,
    text: str,
    feedback_type: str | None = None,
    source: str = "user",
    metadata: dict | None = None,
) -> FeedbackEvent:
    normalized_type = feedback_type or infer_feedback_type(text, target_type)
    related_claims = _related_claims(memory, target_type, target_id)
    related_profile_ids = _related_profile_ids(target_type, target_id, related_claims)
    related_rule_ids = [target_id] if target_type in {"rule", "semantic_rule"} else []
    feedback = FeedbackEvent(
        id=f"feedback-{uuid4().hex[:12]}",
        created_at=datetime.now(),
        feedback_type=normalized_type,
        target_type=target_type,
        target_id=target_id,
        text=text,
        source=source,
        related_event_ids=_related_event_ids(memory, target_type, target_id, related_claims),
        related_profile_ids=related_profile_ids,
        related_rule_ids=related_rule_ids,
        related_claim_ids=[claim.id for claim in related_claims],
        metadata=metadata or {},
    )
    memory.add_feedback_event(feedback)
    if related_claims:
        memory.upsert_conflict_bundle(_conflict_bundle_for_feedback(feedback, related_claims))
    return feedback


def infer_feedback_type(text: str, target_type: str) -> str:
    if any(term in text for term in ["为什么", "原因", "怎么判断"]):
        return FeedbackType.ASK_REASON
    if any(term in text for term in ["删除", "删掉", "移除"]):
        return FeedbackType.DELETE_REQUEST
    if any(term in text for term in ["关闭", "关掉", "停用"]):
        return FeedbackType.CLOSE_REQUEST
    if any(term in text for term in ["不要用", "不要给我贴", "贴标签", "撤回"]):
        return FeedbackType.RESIST_PROFILE if target_type == "profile" or "画像" in text or "标签" in text else FeedbackType.CORRECT_EXPLANATION
    if any(term in text for term in ["不喜欢", "反感", "别这样"]):
        return FeedbackType.DISLIKE_EXPRESSION
    if any(term in text for term in ["不是", "没有", "并非", "不是紧张", "不是生气"]):
        return FeedbackType.DENY_CLAIM
    if any(term in text for term in ["只是", "其实", "更像"]):
        return FeedbackType.CORRECT_EXPLANATION
    return FeedbackType.OTHER


def _related_claims(memory: JoannaMemory, target_type: str, target_id: str) -> list[InferenceClaim]:
    if target_type == "claim":
        claim = memory.get_inference_claim(target_id)
        return [claim] if claim else []
    direct = memory.list_inference_claims(subject_type=target_type, subject_id=target_id, limit=50)
    if direct:
        return direct
    if target_type == "audio_segment":
        derived_event_ids = set(memory.derived_event_ids_for_audio_segment(target_id))
        return [
            claim
            for claim in memory.list_inference_claims(limit=100)
            if derived_event_ids.intersection({item.event_id for item in claim.evidence})
        ]
    if target_type == "event":
        return [
            claim
            for claim in memory.list_inference_claims(limit=100)
            if target_id in {item.event_id for item in claim.evidence}
        ]
    return []


def _related_event_ids(
    memory: JoannaMemory,
    target_type: str,
    target_id: str,
    claims: list[InferenceClaim],
) -> list[str]:
    ids = {item.event_id for claim in claims for item in claim.evidence}
    if target_type == "event":
        ids.add(target_id)
    if target_type == "audio_segment":
        ids.update(memory.derived_event_ids_for_audio_segment(target_id))
    return sorted(ids)


def _related_profile_ids(target_type: str, target_id: str, claims: list[InferenceClaim]) -> list[str]:
    ids = {claim.subject_id for claim in claims if claim.subject_type == "profile"}
    if target_type == "profile":
        ids.add(target_id)
    return sorted(ids)


def _conflict_bundle_for_feedback(feedback: FeedbackEvent, claims: list[InferenceClaim]) -> ConflictBundle:
    claim_ids = [claim.id for claim in claims]
    event_ids = sorted({event_id for claim in claims for event_id in [item.event_id for item in claim.evidence]})
    profile_ids = sorted({claim.subject_id for claim in claims if claim.subject_type == "profile"})
    summary = (
        f"用户反馈“{feedback.text}”需要与 {len(claims)} 条原推理声明并存复盘，"
        "不能直接覆盖原判断。"
    )
    return ConflictBundle(
        id=f"conflict.{feedback.id}",
        created_at=feedback.created_at,
        updated_at=feedback.created_at,
        status=ConflictBundleStatus.OPEN,
        conflict_type=f"{feedback.feedback_type}_vs_inference_claim",
        summary=summary,
        claim_ids=claim_ids,
        feedback_event_ids=[feedback.id],
        event_ids=sorted(set(event_ids).union(feedback.related_event_ids)),
        profile_ids=sorted(set(profile_ids).union(feedback.related_profile_ids)),
        rule_ids=feedback.related_rule_ids,
        resolution_hint="",
        llm_call_id=None,
        metadata={"target_type": feedback.target_type, "target_id": feedback.target_id},
    )
