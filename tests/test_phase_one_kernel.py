from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from joanna.adapters.manual import ingest_jsonl
from joanna.core import context as context_module
from joanna.core.context import CUSTOMER_CONTEXT_ID
from joanna.core.correction import record_correction
from joanna.core.features import extract_features
from joanna.core.memory import JoannaMemory
from joanna.core.reasoning import build_daily_state, build_event_review, build_reminder
from joanna.core.schema import ExperienceEvent, FeatureKind


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "phase_one_events.jsonl"


class PhaseOneKernelTest(unittest.TestCase):
    def test_context_inference_uses_generic_situation_templates(self) -> None:
        self.assertTrue(context_module.SITUATION_TEMPLATES)
        self.assertFalse(hasattr(context_module, "CONTEXT_RULES"))
        self.assertFalse(hasattr(context_module, "_customer_meeting_context"))
        self.assertFalse(hasattr(context_module, "_fishing_context"))
        self.assertFalse(hasattr(context_module, "_conflict_context"))

    def test_ingest_query_and_governance_filters(self) -> None:
        with _memory() as memory:
            self.assertEqual(ingest_jsonl(memory, SAMPLE), 15)
            by_date = memory.query_events(date="2026-06-16")
            by_person = memory.query_events(person="客户A")
            by_scene = memory.query_events(scene="客户现场")
            self.assertEqual(len(by_date), 7)
            self.assertGreaterEqual(len(by_person), 2)
            self.assertGreaterEqual(len(by_scene), 4)

            before = build_daily_state(memory, "2026-06-16", use_llm=False)
            self.assertEqual(len(before.context_hypotheses), 1)
            memory.disable_event("evt-20260616-hr")
            after = build_daily_state(memory, "2026-06-16", use_llm=False)
            self.assertLess(after.context_hypotheses[0].confidence, before.context_hypotheses[0].confidence)
            after_evidence_ids = {item.event_id for item in after.context_hypotheses[0].evidence}
            self.assertNotIn("evt-20260616-hr", after_evidence_ids)

    def test_generic_features_and_induced_profiles_are_traceable(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            customer = build_daily_state(memory, "2026-06-16", use_llm=False)
            fishing = build_reminder(memory, "2026-06-17", use_llm=False)
            conflict = build_event_review(memory, "evt-20260618-conflict", use_llm=False)

            self.assertIn("可能", customer.body)
            self.assertGreaterEqual(len(customer.evidence), 6)
            self.assertTrue(any("social_load" in profile.id for profile in customer.profile_claims))
            profile = next(profile for profile in customer.profile_claims if "social_load" in profile.id)
            self.assertGreater(len(profile.evidence), 1)
            self.assertIn("不会自动发送消息", fishing.body)
            self.assertIn("复盘", conflict.body)

    def test_profile_induction_does_not_require_seeded_pattern_events(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            for event_id in [
                "evt-20260616-customer-pattern",
                "evt-20260617-fishing-pattern",
                "evt-20260618-conflict-pattern",
            ]:
                memory.delete_event(event_id)

            insight = build_daily_state(memory, "2026-06-16", use_llm=False)

            self.assertTrue(any("social_load" in profile.id for profile in insight.profile_claims))
            profile = next(profile for profile in insight.profile_claims if "social_load" in profile.id)
            evidence_ids = {item.event_id for item in profile.evidence}
            self.assertNotIn("evt-20260616-customer-pattern", evidence_ids)
            self.assertGreaterEqual(len(evidence_ids), 2)

    def test_profile_induction_avoids_broad_substring_matches(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            build_reminder(memory, "2026-06-17", use_llm=False)
            profile = next(profile for profile in memory.list_profiles() if "time_pressure" in profile.id)

            self.assertIsNotNone(profile)
            evidence_ids = {item.event_id for item in profile.evidence}
            self.assertIn("evt-20260617-fishing-location", evidence_ids)
            self.assertIn("evt-20260617-fishing-time", evidence_ids)
            self.assertNotIn("evt-20260616-sleep", evidence_ids)
            self.assertNotIn("evt-20260618-conflict-self", evidence_ids)

    def test_correction_records_feedback_without_overwriting_context(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            before = build_daily_state(memory, "2026-06-16", use_llm=False).context_hypotheses[0]
            record_correction(
                memory,
                target_layer="context",
                target_id=CUSTOMER_CONTEXT_ID,
                text="不是紧张，只是赶路。",
                original=before.context_type,
            )
            after = build_daily_state(memory, "2026-06-16", use_llm=False).context_hypotheses[0]
            feedback = memory.list_feedback_events(target_type="context", target_id=CUSTOMER_CONTEXT_ID)
            conflicts = memory.list_conflict_bundles(feedback_event_id=feedback[0].id)

            self.assertEqual(after.confidence, before.confidence)
            self.assertEqual(after.alternatives, before.alternatives)
            self.assertTrue(feedback)
            self.assertEqual(feedback[0].feedback_type, "deny_claim")
            self.assertTrue(conflicts)
            self.assertIn("不能直接覆盖原判断", conflicts[0].summary)

    def test_profile_revoke_removes_profile_from_insight(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            first = build_daily_state(memory, "2026-06-16", use_llm=False)
            profile_id = next(profile.id for profile in first.profile_claims if "social_load" in profile.id)

            memory.revoke_profile(profile_id)
            second = build_daily_state(memory, "2026-06-16", use_llm=False)
            self.assertFalse(any(profile.id == profile_id for profile in second.profile_claims))

    def test_deleted_event_is_not_used(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            memory.delete_event("evt-20260616-calendar")
            insight = build_daily_state(memory, "2026-06-16", use_llm=False)
            event_ids = {item.event_id for item in insight.evidence}
            self.assertNotIn("evt-20260616-calendar", event_ids)
            context_event_ids = {
                item.event_id
                for context in insight.context_hypotheses
                for item in context.evidence
            }
            self.assertNotIn("evt-20260616-calendar", context_event_ids)

    def test_no_diagnostic_language(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=False)
            combined = f"{insight.title} {insight.body}"
            self.assertNotIn("诊断为", combined)
            self.assertNotIn("社交焦虑", combined)

    def test_unseen_but_similar_events_use_same_rules(self) -> None:
        with _memory() as memory:
            for event in [
                ExperienceEvent.from_dict(
                    {
                        "id": "new-calendar",
                        "occurred_at": "2026-06-20T09:00:00+08:00",
                        "source_type": "calendar",
                        "source_id": "manual",
                        "event_type": "calendar_event",
                        "summary": "上午有 client meeting。",
                        "scenes": ["客户现场"],
                        "confidence": 0.9,
                    }
                ),
                ExperienceEvent.from_dict(
                    {
                        "id": "new-hr",
                        "occurred_at": "2026-06-20T08:40:00+08:00",
                        "source_type": "health_sample",
                        "source_id": "manual",
                        "event_type": "heart_rate",
                        "summary": "出门前心率 91。",
                        "content": {"bpm": 91},
                        "confidence": 0.82,
                    }
                ),
            ]:
                memory.upsert_event(event)

            insight = build_daily_state(memory, "2026-06-20", use_llm=False)

            self.assertEqual(len(insight.context_hypotheses), 1)
            self.assertEqual(insight.context_hypotheses[0].id, CUSTOMER_CONTEXT_ID)

    def test_same_feature_combination_across_surface_scenes(self) -> None:
        with _memory() as memory:
            for event in [
                ExperienceEvent.from_dict(
                    {
                        "id": "interview-calendar",
                        "occurred_at": "2026-06-21T09:00:00+08:00",
                        "source_type": "calendar",
                        "source_id": "manual",
                        "event_type": "calendar_event",
                        "summary": "上午有重要面试沟通。",
                        "confidence": 0.9,
                    }
                ),
                ExperienceEvent.from_dict(
                    {
                        "id": "interview-hr",
                        "occurred_at": "2026-06-21T08:40:00+08:00",
                        "source_type": "health_sample",
                        "source_id": "manual",
                        "event_type": "heart_rate",
                        "summary": "面试前心率 91。",
                        "content": {"bpm": 91},
                        "confidence": 0.82,
                    }
                ),
                ExperienceEvent.from_dict(
                    {
                        "id": "interview-speech",
                        "occurred_at": "2026-06-21T10:00:00+08:00",
                        "source_type": "manual",
                        "source_id": "manual",
                        "event_type": "speech_summary",
                        "summary": "沟通中解释较多，语速偏快。",
                        "confidence": 0.74,
                    }
                ),
            ]:
                memory.upsert_event(event)

            features = extract_features(memory.query_events(date="2026-06-21"))
            kinds = {feature.kind for feature in features}
            insight = build_daily_state(memory, "2026-06-21", use_llm=False)

            self.assertIn(FeatureKind.SOCIAL_LOAD, kinds)
            self.assertIn(FeatureKind.BODY_ACTIVATION, kinds)
            self.assertEqual(insight.context_hypotheses[0].context_type, "高负荷互动前后情境")

    def test_travel_delay_extracts_switching_and_decision_features(self) -> None:
        with _memory() as memory:
            for event in [
                ExperienceEvent.from_dict(
                    {
                        "id": "travel-delay",
                        "occurred_at": "2026-06-24T09:20:00+08:00",
                        "source_type": "manual",
                        "source_id": "travel-note",
                        "event_type": "message_summary",
                        "summary": "高铁延误 80 分钟，可能需要改约或调整到线上参会。",
                        "content": {"delay_minutes": 80, "topic": "改约或线上参会"},
                        "confidence": 0.88,
                    }
                ),
                ExperienceEvent.from_dict(
                    {
                        "id": "travel-self",
                        "occurred_at": "2026-06-24T10:20:00+08:00",
                        "source_type": "manual",
                        "source_id": "self-note",
                        "event_type": "self_report",
                        "summary": "用户自述：有点烦，但先把会议改成线上吧。",
                        "content": {"text": "有点烦，先改线上"},
                        "confidence": 0.84,
                    }
                ),
            ]:
                memory.upsert_event(event)

            kinds = {feature.kind for feature in extract_features(memory.query_events(date="2026-06-24"))}

            self.assertIn(FeatureKind.TRAVEL_DELAY, kinds)
            self.assertIn(FeatureKind.SCHEDULE_DISRUPTION, kinds)
            self.assertIn(FeatureKind.TASK_SWITCHING, kinds)
            self.assertIn(FeatureKind.DECISION_PRESSURE, kinds)


class _memory:
    def __enter__(self) -> JoannaMemory:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.memory = JoannaMemory(Path(self.tmpdir.name) / "local.db")
        return self.memory

    def __exit__(self, exc_type, exc, tb) -> None:
        self.memory.close()
        self.tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
