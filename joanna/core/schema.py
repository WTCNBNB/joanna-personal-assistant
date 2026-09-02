from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
import json
from typing import Any


class SourceType(StrEnum):
    MANUAL = "manual"
    CALENDAR = "calendar"
    HEALTH_SAMPLE = "health_sample"
    LOCATION_SAMPLE = "location_sample"
    AUDIO_CAPTURE = "audio_capture"
    MEMORY_SEED = "memory_seed"


class SensitivityLevel(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class ProfileType(StrEnum):
    DECLARED = "declared"
    STABLE_PATTERN = "stable_pattern"
    UNCONFIRMED_PATTERN = "unconfirmed_pattern"


class ProfileStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CORRECTED = "corrected"
    REVOKED = "revoked"
    STALE = "stale"


class MemorySummaryStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_RECOMPUTE = "needs_recompute"
    STALE = "stale"
    REVOKED = "revoked"


class SemanticRuleType(StrEnum):
    FEATURE_EXTRACTOR = "feature_extractor"
    SITUATION_TEMPLATE = "situation_template"
    DIRECT_EXPRESSION = "direct_expression"


class SemanticRuleStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    STALE = "stale"
    ROLLED_BACK = "rolled_back"


class LLMTier(StrEnum):
    SHORT = "short"
    LONG = "long"
    HUGE = "huge"


class LLMFailureType(StrEnum):
    MISSING_KEY = "missing_key"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    INVALID_JSON = "invalid_json"
    GOVERNANCE_VIOLATION = "governance_violation"
    EMPTY_RESPONSE = "empty_response"
    UNKNOWN = "unknown"


class InsightType(StrEnum):
    DAILY = "daily"
    EVENT_REVIEW = "event_review"
    PERIOD_REVIEW = "period_review"
    REMINDER = "reminder"


class FeedbackType(StrEnum):
    DENY_CLAIM = "deny_claim"
    CORRECT_EXPLANATION = "correct_explanation"
    RESIST_PROFILE = "resist_profile"
    DELETE_REQUEST = "delete_request"
    CLOSE_REQUEST = "close_request"
    DISLIKE_EXPRESSION = "dislike_expression"
    ASK_REASON = "ask_reason"
    OTHER = "other"


class InferenceClaimType(StrEnum):
    CONTEXT = "context"
    SEMANTIC_OBSERVATION = "semantic_observation"
    PROFILE = "profile"
    EXPRESSION = "expression"
    CONFLICT_ASSESSMENT = "conflict_assessment"


class ConflictBundleStatus(StrEnum):
    OPEN = "open"
    REVIEWED = "reviewed"
    STALE = "stale"


class CorrectionLayer(StrEnum):
    EVENT = "event"
    FEATURE = "feature"
    CONTEXT = "context"
    PROFILE = "profile"
    EXPRESSION = "expression"
    GOVERNANCE = "governance"


class FeatureKind(StrEnum):
    BODY_ACTIVATION = "body_activation"
    RECOVERY_DEBT = "recovery_debt"
    SOCIAL_LOAD = "social_load"
    TIME_PRESSURE = "time_pressure"
    RELATIONSHIP_FRICTION = "relationship_friction"
    FAMILY_PULL = "family_pull"
    EXPRESSION_LOAD = "expression_load"
    LOCATION_STAY = "location_stay"
    SELF_DOWNPLAY = "self_downplay"
    EXPLICIT_PREFERENCE = "explicit_preference"
    TASK_COMMITMENT = "task_commitment"
    REFLECTIVE_INTENT = "reflective_intent"
    TRAVEL_DELAY = "travel_delay"
    SCHEDULE_DISRUPTION = "schedule_disruption"
    TASK_SWITCHING = "task_switching"
    DECISION_PRESSURE = "decision_pressure"


class EvolutionRisk(StrEnum):
    LOW = "low"
    HIGH = "high"


class EvolutionStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class EvolutionProposalType(StrEnum):
    FEATURE_WEIGHT = "feature_weight"
    EXPRESSION_PREFERENCE = "expression_preference"
    PROFILE_CANDIDATE = "profile_candidate"
    GOVERNANCE_BOUNDARY = "governance_boundary"


@dataclass(frozen=True)
class AuditRecord:
    id: str
    created_at: datetime
    action: str
    target_type: str
    target_id: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_ids: list[str] = field(default_factory=list)
    profile_ids: list[str] = field(default_factory=list)
    llm_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(frozen=True)
class LLMCallRecord:
    id: str
    created_at: datetime
    task_type: str
    tier: str
    model: str
    max_tokens: int
    timeout_seconds: int
    prompt_bytes: int
    event_ids: list[str]
    profile_ids: list[str]
    sent_external: bool
    status: str = "started"
    failure_type: str | None = None
    error_message: str = ""
    attempts: int = 1
    response_bytes: int = 0
    feedback_event_ids: list[str] = field(default_factory=list)
    inference_claim_ids: list[str] = field(default_factory=list)
    conflict_bundle_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.tier not in {item.value for item in LLMTier}:
            raise ValueError("LLMCallRecord.tier is invalid")
        if self.failure_type is not None and self.failure_type not in {item.value for item in LLMFailureType}:
            raise ValueError("LLMCallRecord.failure_type is invalid")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(frozen=True)
class MemorySummary:
    id: str
    summary_type: str
    status: str
    title: str
    body: str
    time_range: str
    source_event_ids: list[str]
    context_ids: list[str] = field(default_factory=list)
    profile_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    invalidated_by_event_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {item.value for item in MemorySummaryStatus}:
            raise ValueError("MemorySummary.status is invalid")
        if not self.source_event_ids:
            raise ValueError("MemorySummary.source_event_ids cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return payload


@dataclass(frozen=True)
class FeedbackEvent:
    id: str
    created_at: datetime
    feedback_type: str
    target_type: str
    target_id: str
    text: str
    source: str = "user"
    related_event_ids: list[str] = field(default_factory=list)
    related_profile_ids: list[str] = field(default_factory=list)
    related_rule_ids: list[str] = field(default_factory=list)
    related_claim_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.feedback_type not in {item.value for item in FeedbackType}:
            raise ValueError("FeedbackEvent.feedback_type is invalid")
        if not self.target_type:
            raise ValueError("FeedbackEvent.target_type is required")
        if not self.target_id:
            raise ValueError("FeedbackEvent.target_id is required")
        if not self.text:
            raise ValueError("FeedbackEvent.text is required")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(frozen=True)
class InferenceClaim:
    id: str
    created_at: datetime
    claim_type: str
    subject_type: str
    subject_id: str
    text: str
    evidence: list[Evidence]
    confidence: float
    alternatives: list[str] = field(default_factory=list)
    source: str = "system"
    insight_id: str | None = None
    llm_call_id: str | None = None
    feedback_event_ids: list[str] = field(default_factory=list)
    conflict_bundle_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.claim_type not in {item.value for item in InferenceClaimType}:
            raise ValueError("InferenceClaim.claim_type is invalid")
        if not self.subject_type:
            raise ValueError("InferenceClaim.subject_type is required")
        if not self.subject_id:
            raise ValueError("InferenceClaim.subject_id is required")
        if not self.text:
            raise ValueError("InferenceClaim.text is required")
        if not self.evidence:
            raise ValueError("InferenceClaim.evidence cannot be empty")
        _validate_confidence(self.confidence, "InferenceClaim.confidence")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class ConflictBundle:
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    conflict_type: str
    summary: str
    claim_ids: list[str]
    feedback_event_ids: list[str]
    event_ids: list[str] = field(default_factory=list)
    profile_ids: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    resolution_hint: str = ""
    llm_call_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {item.value for item in ConflictBundleStatus}:
            raise ValueError("ConflictBundle.status is invalid")
        if not self.claim_ids:
            raise ValueError("ConflictBundle.claim_ids cannot be empty")
        if not self.feedback_event_ids:
            raise ValueError("ConflictBundle.feedback_event_ids cannot be empty")
        if not self.summary:
            raise ValueError("ConflictBundle.summary is required")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return payload


@dataclass(frozen=True)
class ProfileVersion:
    id: str
    profile_id: str
    version: int
    status: str
    claim: str
    profile_type: str
    evidence: list[Evidence]
    reason: str
    created_at: datetime
    updated_at: datetime
    supersedes_version: int | None = None

    def __post_init__(self) -> None:
        if self.status not in {item.value for item in ProfileStatus}:
            raise ValueError("ProfileVersion.status is invalid")
        if self.version < 1:
            raise ValueError("ProfileVersion.version must be positive")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class SemanticRule:
    id: str
    rule_type: str
    status: str
    version: int
    source: str
    created_by_llm_call_id: str | None
    match_spec: dict[str, Any]
    output_spec: dict[str, Any]
    evidence_event_ids: list[str]
    confidence: float
    created_at: datetime
    updated_at: datetime
    supersedes_version: int | None = None
    rollback_target: int | None = None

    def __post_init__(self) -> None:
        if self.rule_type not in {item.value for item in SemanticRuleType}:
            raise ValueError("SemanticRule.rule_type is invalid")
        if self.status not in {item.value for item in SemanticRuleStatus}:
            raise ValueError("SemanticRule.status is invalid")
        if self.version < 1:
            raise ValueError("SemanticRule.version must be positive")
        if not self.evidence_event_ids:
            raise ValueError("SemanticRule.evidence_event_ids cannot be empty")
        _validate_confidence(self.confidence, "SemanticRule.confidence")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return payload


@dataclass(frozen=True)
class RuleVersion:
    id: str
    rule_id: str
    version: int
    rule_type: str
    status: str
    source: str
    created_by_llm_call_id: str | None
    match_spec: dict[str, Any]
    output_spec: dict[str, Any]
    evidence_event_ids: list[str]
    confidence: float
    reason: str
    created_at: datetime
    updated_at: datetime
    supersedes_version: int | None = None
    rollback_target: int | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("RuleVersion.version must be positive")
        if self.rule_type not in {item.value for item in SemanticRuleType}:
            raise ValueError("RuleVersion.rule_type is invalid")
        if self.status not in {item.value for item in SemanticRuleStatus}:
            raise ValueError("RuleVersion.status is invalid")
        _validate_confidence(self.confidence, "RuleVersion.confidence")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        return payload


@dataclass(frozen=True)
class RuleApplication:
    id: str
    rule_id: str
    rule_version: int
    applied_at: datetime
    status: str
    event_ids: list[str]
    output: dict[str, Any]
    reason: str
    llm_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["applied_at"] = self.applied_at.isoformat()
        return payload


@dataclass(frozen=True)
class Evidence:
    id: str
    event_id: str
    summary: str
    occurred_at: str
    confidence: float
    source_type: str
    sensitivity: str

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "Evidence.confidence")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperienceEvent:
    id: str
    occurred_at: datetime
    source_type: str
    source_id: str
    event_type: str
    summary: str
    content: dict[str, Any] = field(default_factory=dict)
    people: list[str] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    sensitivity: str = SensitivityLevel.PRIVATE
    allow_long_term: bool = True
    allow_profile: bool = True
    confidence: float = 1.0
    evidence_refs: list[str] = field(default_factory=list)
    disabled: bool = False
    deleted: bool = False
    profile_usage_revoked: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ExperienceEvent.id is required")
        if not self.event_type:
            raise ValueError("ExperienceEvent.event_type is required")
        _validate_confidence(self.confidence, "ExperienceEvent.confidence")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExperienceEvent":
        required = [
            "id",
            "occurred_at",
            "source_type",
            "source_id",
            "event_type",
            "summary",
        ]
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"ExperienceEvent missing required fields: {', '.join(missing)}")

        return cls(
            id=str(payload["id"]),
            occurred_at=_parse_datetime(payload["occurred_at"]),
            source_type=str(payload["source_type"]),
            source_id=str(payload["source_id"]),
            event_type=str(payload["event_type"]),
            summary=str(payload["summary"]),
            content=dict(payload.get("content", {})),
            people=[str(item) for item in payload.get("people", [])],
            scenes=[str(item) for item in payload.get("scenes", [])],
            sensitivity=str(payload.get("sensitivity", SensitivityLevel.PRIVATE)),
            allow_long_term=bool(payload.get("allow_long_term", True)),
            allow_profile=bool(payload.get("allow_profile", True)),
            confidence=float(payload.get("confidence", 1.0)),
            evidence_refs=[str(item) for item in payload.get("evidence_refs", [])],
            disabled=bool(payload.get("disabled", False)),
            deleted=bool(payload.get("deleted", False)),
            profile_usage_revoked=bool(payload.get("profile_usage_revoked", False)),
        )

    @classmethod
    def from_json(cls, raw: str) -> "ExperienceEvent":
        return cls.from_dict(json.loads(raw))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["occurred_at"] = self.occurred_at.isoformat()
        return payload

    def to_evidence(self) -> Evidence:
        return Evidence(
            id=f"evd:{self.id}",
            event_id=self.id,
            summary=self.summary,
            occurred_at=self.occurred_at.isoformat(),
            confidence=self.confidence,
            source_type=self.source_type,
            sensitivity=self.sensitivity,
        )


@dataclass(frozen=True)
class ExperienceFeature:
    id: str
    event_id: str
    kind: str
    label: str
    value: str
    confidence: float
    evidence: Evidence
    polarity: str = "support"

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "ExperienceFeature.confidence")
        if self.polarity not in {"support", "counter", "preference"}:
            raise ValueError("ExperienceFeature.polarity must be support, counter, or preference")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = self.evidence.to_dict()
        return payload


@dataclass(frozen=True)
class ContextHypothesis:
    id: str
    context_type: str
    time_range: str
    evidence: list[Evidence]
    confidence: float
    alternatives: list[str]
    uncertainty: str
    allow_expression: bool = True
    allow_profile_candidate: bool = True

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "ContextHypothesis.confidence")
        if not self.evidence:
            raise ValueError("ContextHypothesis.evidence cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class EvolutionProposal:
    id: str
    proposal_type: str
    status: str
    risk: str
    title: str
    rationale: str
    payload: dict[str, Any]
    evidence: list[Evidence]
    created_at: datetime
    applied_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {item.value for item in EvolutionStatus}:
            raise ValueError("EvolutionProposal.status is invalid")
        if self.risk not in {item.value for item in EvolutionRisk}:
            raise ValueError("EvolutionProposal.risk is invalid")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["applied_at"] = self.applied_at.isoformat() if self.applied_at else None
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class ProfileClaim:
    id: str
    claim: str
    profile_type: str
    evidence: list[Evidence]
    created_at: datetime
    updated_at: datetime
    confidence: float
    user_confirmed: bool = False
    user_corrected: bool = False
    allowed_for_reasoning: bool = True
    revoked: bool = False
    deleted: bool = False

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "ProfileClaim.confidence")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["updated_at"] = self.updated_at.isoformat()
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class Correction:
    id: str
    created_at: datetime
    target_layer: str
    target_id: str
    original: str
    correction: str
    effect: str
    requires_profile_revoke: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(frozen=True)
class SemanticObservation:
    id: str
    observation_type: str
    text: str
    evidence: list[Evidence]
    confidence: float
    alternatives: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "SemanticObservation.confidence")
        if not self.evidence:
            raise ValueError("SemanticObservation.evidence cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class Insight:
    id: str
    insight_type: str
    title: str
    body: str
    evidence: list[Evidence]
    context_hypotheses: list[ContextHypothesis]
    profile_claims: list[ProfileClaim]
    confidence: float
    alternatives: list[str]
    correction_prompt: str
    governance_notes: list[str]
    created_at: datetime
    semantic_observations: list[SemanticObservation] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "Insight.confidence")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        payload["context_hypotheses"] = [item.to_dict() for item in self.context_hypotheses]
        payload["profile_claims"] = [item.to_dict() for item in self.profile_claims]
        payload["semantic_observations"] = [item.to_dict() for item in self.semantic_observations]
        return payload


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"invalid datetime value: {value!r}")


def _validate_confidence(value: float, field_name: str) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
