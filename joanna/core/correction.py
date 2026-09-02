from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from joanna.core.feedback import record_feedback
from joanna.core.memory import JoannaMemory
from joanna.core.schema import Correction


def record_correction(
    memory: JoannaMemory,
    target_layer: str,
    target_id: str,
    text: str,
    original: str = "",
) -> Correction:
    lowered = text.lower()
    requires_profile_revoke = "不要用" in text or "撤回" in text or "不要长期保存" in text
    effect = _infer_effect(target_layer, lowered, text)
    correction = Correction(
        id=f"corr-{uuid4().hex[:12]}",
        created_at=datetime.now(),
        target_layer=target_layer,
        target_id=target_id,
        original=original,
        correction=text,
        effect=effect,
        requires_profile_revoke=requires_profile_revoke,
    )
    memory.add_correction(correction)
    record_feedback(
        memory,
        target_type=target_layer,
        target_id=target_id,
        text=text,
        metadata={
            "source_correction_id": correction.id,
            "original": original,
            "legacy_effect": effect,
        },
    )
    return correction


def correction_bias(memory: JoannaMemory, context_id: str) -> dict[str, object]:
    return {
        "confidence_delta": 0.0,
        "preferred_alternatives": [],
        "notes": [],
    }


def _infer_effect(target_layer: str, lowered: str, text: str) -> str:
    if "不是紧张" in text or "只是赶路" in text:
        return "记录为用户反馈事件；后续推理应同时查看原判断和用户解释。"
    if "不要用" in text or "撤回" in text:
        return "记录为抵触、撤回或数据使用反馈事件；不直接覆盖原推理。"
    if "不喜欢" in text:
        return "记录为表达反感反馈事件；后续表达策略应结合冲突上下文推理。"
    if target_layer == "event":
        return "记录事件解释反馈；原事件和用户反馈并存。"
    return "记录用户反馈；不作为最终裁决直接改写推理。"
