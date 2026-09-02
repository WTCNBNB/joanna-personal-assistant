from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from joanna.core.schema import ExperienceEvent, LLMFailureType, LLMTier, ProfileClaim


@dataclass(frozen=True)
class LLMRunPlan:
    task_type: str
    tier: str
    max_tokens: int
    timeout_seconds: int
    prompt_bytes: int
    event_count: int
    day_span: int


def estimate_llm_run(
    task_type: str,
    events: list[ExperienceEvent],
    profiles: list[ProfileClaim],
    *,
    allow_huge: bool = False,
) -> LLMRunPlan:
    prompt_bytes = len(
        json.dumps(
            {
                "task_type": task_type,
                "events": [event.to_dict() for event in events],
                "profiles": [profile.to_dict() for profile in profiles],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    day_span = _day_span(events)
    if len(events) >= 100 or prompt_bytes >= 80000:
        if not allow_huge:
            tier = LLMTier.LONG
            max_tokens = 32768
            timeout_seconds = 240
        else:
            tier = LLMTier.HUGE
            max_tokens = 65536
            timeout_seconds = 360
    elif task_type == "period_review" or day_span > 1 or len(events) > 10 or prompt_bytes > 15000:
        tier = LLMTier.LONG
        max_tokens = 32768
        timeout_seconds = 240
    else:
        tier = LLMTier.SHORT
        max_tokens = 8192
        timeout_seconds = 180
    return LLMRunPlan(
        task_type=task_type,
        tier=tier,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        prompt_bytes=prompt_bytes,
        event_count=len(events),
        day_span=day_span,
    )


def classify_llm_exception(exc: Exception) -> str:
    message = str(exc).lower()
    if "api key" in message:
        return LLMFailureType.MISSING_KEY
    if any(term in message for term in ["dns", "name resolution", "nodename", "servname", "temporary failure in name resolution"]):
        return LLMFailureType.NETWORK_ERROR
    if "timed out" in message or "timeout" in message:
        return LLMFailureType.TIMEOUT
    if "http" in message:
        return LLMFailureType.HTTP_ERROR
    if "json" in message or isinstance(exc, json.JSONDecodeError):
        return LLMFailureType.INVALID_JSON
    if "forbidden diagnostic terms" in message or "governance" in message:
        return LLMFailureType.GOVERNANCE_VIOLATION
    if "empty" in message:
        return LLMFailureType.EMPTY_RESPONSE
    return LLMFailureType.UNKNOWN


def retryable_failure(failure_type: str) -> bool:
    return failure_type in {
        LLMFailureType.INVALID_JSON,
        LLMFailureType.GOVERNANCE_VIOLATION,
    }


def _day_span(events: list[ExperienceEvent]) -> int:
    if not events:
        return 0
    dates = sorted({event.occurred_at.date() for event in events})
    return (dates[-1] - dates[0]).days + 1
