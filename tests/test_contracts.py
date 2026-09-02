from __future__ import annotations

from datetime import datetime
import unittest

from joanna.core.schema import (
    ConflictBundle,
    ConflictBundleStatus,
    ContextHypothesis,
    Evidence,
    ExperienceEvent,
    FeedbackEvent,
    FeedbackType,
    InferenceClaim,
    InferenceClaimType,
    Insight,
    InsightType,
)


class ContractTest(unittest.TestCase):
    def test_event_roundtrip_and_defaults(self) -> None:
        event = ExperienceEvent.from_dict(
            {
                "id": "evt-1",
                "occurred_at": "2026-06-16T10:00:00+08:00",
                "source_type": "manual",
                "source_id": "note",
                "event_type": "self_report",
                "summary": "今天状态还可以。",
            }
        )

        self.assertIsInstance(event.occurred_at, datetime)
        self.assertTrue(event.allow_long_term)
        self.assertTrue(event.allow_profile)
        self.assertFalse(event.disabled)
        self.assertEqual(event.to_dict()["occurred_at"], "2026-06-16T10:00:00+08:00")

    def test_confidence_validation(self) -> None:
        with self.assertRaises(ValueError):
            ExperienceEvent.from_dict(
                {
                    "id": "evt-bad",
                    "occurred_at": "2026-06-16T10:00:00+08:00",
                    "source_type": "manual",
                    "source_id": "note",
                    "event_type": "self_report",
                    "summary": "bad",
                    "confidence": 1.5,
                }
            )

    def test_context_requires_evidence(self) -> None:
        with self.assertRaises(ValueError):
            ContextHypothesis(
                id="context.empty",
                context_type="empty",
                time_range="unknown",
                evidence=[],
                confidence=0.5,
                alternatives=["信息不足"],
                uncertainty="no evidence",
            )

    def test_insight_serializes_empty_semantic_observations(self) -> None:
        insight = Insight(
            id="insight.empty",
            insight_type=InsightType.DAILY,
            title="空观察",
            body="没有观察。",
            evidence=[],
            context_hypotheses=[],
            profile_claims=[],
            confidence=0.2,
            alternatives=["信息不足"],
            correction_prompt="可纠正。",
            governance_notes=[],
            created_at=datetime.now(),
        )

        self.assertEqual(insight.to_dict()["semantic_observations"], [])

    def test_feedback_claim_and_conflict_contracts_roundtrip(self) -> None:
        now = datetime.now()
        evidence = Evidence(
            id="evd:evt-1",
            event_id="evt-1",
            summary="系统曾判断可能紧绷。",
            occurred_at="2026-06-16T10:00:00+08:00",
            confidence=0.7,
            source_type="manual",
            sensitivity="private",
        )
        feedback = FeedbackEvent(
            id="feedback-1",
            created_at=now,
            feedback_type=FeedbackType.DENY_CLAIM,
            target_type="claim",
            target_id="claim-1",
            text="不是紧张，只是赶路。",
            related_event_ids=["evt-1"],
            related_claim_ids=["claim-1"],
        )
        claim = InferenceClaim(
            id="claim-1",
            created_at=now,
            claim_type=InferenceClaimType.CONTEXT,
            subject_type="context",
            subject_id="context.customer_meeting_pressure",
            text="可能处于高负荷互动前后情境。",
            evidence=[evidence],
            confidence=0.7,
            alternatives=["赶路或运动"],
        )
        bundle = ConflictBundle(
            id="conflict-1",
            created_at=now,
            updated_at=now,
            status=ConflictBundleStatus.OPEN,
            conflict_type="deny_claim_vs_inference_claim",
            summary="原推理和用户反馈并存。",
            claim_ids=[claim.id],
            feedback_event_ids=[feedback.id],
            event_ids=["evt-1"],
        )

        self.assertEqual(feedback.to_dict()["feedback_type"], "deny_claim")
        self.assertEqual(claim.to_dict()["evidence"][0]["event_id"], "evt-1")
        self.assertEqual(bundle.to_dict()["claim_ids"], ["claim-1"])


if __name__ == "__main__":
    unittest.main()
