from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from joanna.adapters.manual import ingest_jsonl
from joanna.app.cli import _friendly_error
from joanna.app.web import _view
from joanna.core.correction import record_correction
from joanna.core.memory import JoannaMemory
from joanna.core.feedback import record_feedback
from joanna.core.features import extract_features
from joanna.core.reasoning import build_daily_state, build_event_review, build_period_review, build_reminder
from joanna.core.schema import SemanticRule, SemanticRuleStatus, SemanticRuleType
from joanna.core.summaries import build_memory_summaries


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "phase_one_events.jsonl"
PHASE_TWO_SAMPLE = ROOT / "samples" / "phase_two_events.jsonl"


class FakeLLMClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.payloads: list[dict] = []
        self.model = "fake-governed"
        self.max_tokens = 8192
        self.timeout = 30

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        self.payloads.append(user_payload)
        return self.response


class FlakyLLMClient(FakeLLMClient):
    def __init__(self, responses: list[dict | Exception]) -> None:
        super().__init__({})
        self.responses = responses

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        self.payloads.append(user_payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class PhaseTwoGovernanceTest(unittest.TestCase):
    def test_insight_save_records_audit_evidence_and_profiles(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=False)
            audits = memory.list_audit_records(action="insight_saved")

        self.assertEqual(audits[0].target_id, insight.id)
        self.assertIn("evt-20260616-calendar", audits[0].event_ids)
        self.assertTrue(any(profile_id.startswith("profile.") for profile_id in audits[0].profile_ids))
        self.assertFalse(audits[0].payload["used_llm"])

    def test_llm_daily_insight_records_call_budget_and_data_boundary(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "高负荷互动候选",
                        "confidence": 0.6,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "候选洞察",
                    "body": "这是一个需要确认的候选判断。",
                    "confidence": 0.6,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            calls = memory.list_llm_calls()
            audits = memory.list_audit_records(action="insight_saved")

        self.assertEqual(calls[0].status, "success")
        self.assertEqual(calls[0].task_type, "daily_insight")
        self.assertEqual(calls[0].tier, "short")
        self.assertEqual(calls[0].model, "fake-governed")
        self.assertFalse(calls[0].sent_external)
        self.assertIn("evt-20260616-calendar", calls[0].event_ids)
        self.assertEqual(audits[0].target_id, insight.id)
        self.assertEqual(audits[0].llm_call_id, calls[0].id)
        self.assertTrue(audits[0].payload["used_llm"])

    def test_inference_claim_feedback_and_conflict_are_persisted(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=False)
            claims = memory.list_inference_claims(insight_id=insight.id)
            feedback = record_feedback(
                memory,
                target_type="claim",
                target_id=claims[0].id,
                text="不是紧张，只是赶路。",
            )
            conflicts = memory.list_conflict_bundles(feedback_event_id=feedback.id)

        self.assertTrue(claims)
        self.assertEqual(feedback.related_claim_ids, [claims[0].id])
        self.assertTrue(conflicts)
        self.assertIn(claims[0].id, conflicts[0].claim_ids)
        self.assertIn("不能直接覆盖原判断", conflicts[0].summary)

    def test_llm_payload_includes_feedback_claims_and_conflicts(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "冲突复盘候选",
                        "confidence": 0.55,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "conflict_assessments": [
                    {
                        "conflict_bundle_id": "will-be-replaced",
                        "summary": "无效冲突 ID 应被丢弃。",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "反馈后洞察",
                    "body": "原判断和用户反馈需要并存复盘。",
                    "confidence": 0.55,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            first = build_daily_state(memory, "2026-06-16", use_llm=False)
            claim = memory.list_inference_claims(insight_id=first.id)[0]
            feedback = record_feedback(
                memory,
                target_type="claim",
                target_id=claim.id,
                text="不是紧张，只是赶路。",
            )
            build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            payload = client.payloads[0]

        self.assertTrue(payload["feedback_events"])
        self.assertTrue(payload["inference_claims"])
        self.assertTrue(payload["conflict_bundles"])
        self.assertEqual(payload["feedback_events"][0]["id"], feedback.id)
        self.assertTrue(payload["governance"]["feedback_is_evidence_not_final_verdict"])

    def test_cli_bad_input_hides_traceback_unless_debug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = tmp_path / "local.db"
            bad_jsonl = tmp_path / "bad.jsonl"
            bad_jsonl.write_text('{"id": "bad"}\n', encoding="utf-8")

            normal = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "joanna.app.cli",
                    "--db",
                    str(db),
                    "ingest",
                    str(bad_jsonl),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            debug = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "joanna.app.cli",
                    "--debug",
                    "--db",
                    str(db),
                    "ingest",
                    str(bad_jsonl),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(normal.returncode, 0)
        self.assertIn("错误：", normal.stderr)
        self.assertNotIn("Traceback", normal.stderr)
        self.assertNotEqual(debug.returncode, 0)
        self.assertIn("Traceback", debug.stderr)

    def test_cli_no_llm_preserves_offline_rule_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "local.db"
            ingest = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "joanna.app.cli",
                    "--db",
                    str(db),
                    "ingest",
                    str(SAMPLE),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            offline = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "joanna.app.cli",
                    "--db",
                    str(db),
                    "insight",
                    "today",
                    "--date",
                    "2026-06-16",
                    "--no-llm",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(ingest.returncode, 0)
        self.assertEqual(offline.returncode, 0)
        self.assertIn("2026-06-16 今日状态洞察", offline.stdout)

    def test_feedback_claims_and_conflicts_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "local.db"
            as_memory = JoannaMemory(db)
            try:
                ingest_jsonl(as_memory, SAMPLE)
                insight = build_daily_state(as_memory, "2026-06-16", use_llm=False)
                claim_id = as_memory.list_inference_claims(insight_id=insight.id)[0].id
            finally:
                as_memory.close()

            feedback = _run_cli(
                db,
                "feedback",
                "record",
                "--target-type",
                "claim",
                "--target-id",
                claim_id,
                "--text",
                "不是紧张，只是赶路。",
            )
            claims = _run_cli(db, "claims", "explain", claim_id)
            conflicts = _run_cli(db, "conflicts", "list")

        self.assertEqual(feedback.returncode, 0)
        self.assertIn("已记录反馈事件", feedback.stdout)
        self.assertEqual(claims.returncode, 0)
        self.assertIn("conflict_bundles", claims.stdout)
        self.assertEqual(conflicts.returncode, 0)
        self.assertIn("不能直接覆盖原判断", conflicts.stdout)

    def test_missing_api_key_error_points_to_no_llm(self) -> None:
        message = _friendly_error(RuntimeError("DeepSeek API key not found."))

        self.assertIn("--no-llm", message)

    def test_cross_day_memory_summary_traces_sources_and_invalidates(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            summaries = build_memory_summaries(memory, "2026-06-24", "2026-06-28")
            stored = memory.list_memory_summaries()
            summary = next(item for item in stored if item.source_event_ids)

            self.assertTrue(summaries)
            self.assertTrue(any(item.summary_type == "context_summary" for item in stored))
            self.assertTrue(any(item.summary_type == "long_term_clue" for item in stored))
            self.assertIn(summary.source_event_ids[0], {event.id for event in memory.query_events_range("2026-06-24", "2026-06-28")})

            memory.disable_event(summary.source_event_ids[0])
            invalidated = memory.get_memory_summary(summary.id)
            audits = memory.list_audit_records(action="memory_summary_invalidated")

        self.assertIsNotNone(invalidated)
        self.assertEqual(invalidated.status, "needs_recompute")
        self.assertEqual(invalidated.invalidated_by_event_id, summary.source_event_ids[0])
        self.assertTrue(audits)

    def test_profile_versions_track_new_evidence_revoke_and_feedback(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            first = build_daily_state(memory, "2026-06-16", use_llm=False)
            profile_id = next(profile.id for profile in first.profile_claims if "social_load" in profile.id)
            initial_versions = memory.list_profile_versions(profile_id)

            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-28", use_llm=False)
            expanded_versions = memory.list_profile_versions(profile_id)

            record_correction(
                memory,
                target_layer="profile",
                target_id=profile_id,
                text="这个画像太宽了，需要缩小适用范围。",
                original=first.profile_claims[0].claim,
            )
            feedback = memory.list_feedback_events(target_type="profile", target_id=profile_id)
            unchanged_versions = memory.list_profile_versions(profile_id)
            after_correction = build_daily_state(memory, "2026-06-16", use_llm=False)

            memory.revoke_profile(profile_id)
            revoked_versions = memory.list_profile_versions(profile_id)

        self.assertEqual(initial_versions[0].status, "candidate")
        self.assertGreater(len(expanded_versions), len(initial_versions))
        self.assertEqual(unchanged_versions[-1].status, "candidate")
        self.assertTrue(feedback)
        self.assertEqual(feedback[0].target_id, profile_id)
        self.assertTrue(any(profile.id == profile_id for profile in after_correction.profile_claims))
        self.assertEqual(revoked_versions[-1].status, "revoked")

    def test_llm_invalid_json_is_retried_once_and_audited(self) -> None:
        client = FlakyLLMClient(
            [
                ValueError("invalid json from model"),
                {
                    "contexts": [
                        {
                            "context_type": "高负荷互动候选",
                            "confidence": 0.6,
                            "evidence_event_ids": ["evt-20260616-calendar"],
                        }
                    ],
                    "expression": {
                        "title": "重试后洞察",
                        "body": "这是重试后得到的候选判断。",
                        "confidence": 0.6,
                    },
                },
            ]
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            call = memory.list_llm_calls()[0]

        self.assertEqual(insight.title, "重试后洞察")
        self.assertEqual(len(client.payloads), 2)
        self.assertEqual(call.status, "success")
        self.assertEqual(call.attempts, 2)

    def test_reasoning_builders_default_to_llm_with_fake_client(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            daily_client = FakeLLMClient(_llm_response("evt-20260616-calendar", "默认今日洞察"))
            daily = build_daily_state(memory, "2026-06-16", llm_client=daily_client)
            daily_call = memory.list_llm_calls()[0]

        self.assertEqual(daily.title, "默认今日洞察")
        self.assertEqual(daily_call.task_type, "daily_insight")

        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            event_client = FakeLLMClient(_llm_response("evt-20260618-conflict", "默认事件复盘"))
            review = build_event_review(memory, "evt-20260618-conflict", llm_client=event_client)
            event_call = memory.list_llm_calls()[0]

        self.assertEqual(review.insight_type, "event_review")
        self.assertEqual(event_call.task_type, "event_review")

        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            reminder_client = FakeLLMClient(_llm_response("evt-20260617-family-message", "默认提醒建议"))
            reminder = build_reminder(memory, "2026-06-17", llm_client=reminder_client)
            reminder_call = memory.list_llm_calls()[0]

        self.assertEqual(reminder.insight_type, "reminder")
        self.assertEqual(reminder_call.task_type, "reminder")

    def test_llm_rule_updates_create_active_runtime_rule_and_audit(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "普通午饭散步候选",
                        "confidence": 0.42,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "rule_updates": [
                    {
                        "id": "rule.semantic.meal_walk_observation",
                        "type": "situation_template",
                        "match_spec": {"event_type": "self_report", "contains": ["午饭", "散步"]},
                        "output_spec": {"context_type": "进食与补给后的轻恢复观察"},
                        "confidence": 0.58,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "expression": {
                    "title": "规则更新候选",
                    "body": "普通午饭和散步可以作为轻恢复候选观察。",
                    "confidence": 0.42,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=client)
            call = memory.list_llm_calls()[0]
            rule = memory.get_semantic_rule("rule.semantic.meal_walk_observation")
            versions = memory.list_rule_versions("rule.semantic.meal_walk_observation")
            applications = memory.list_rule_applications("rule.semantic.meal_walk_observation")
            audits = memory.list_audit_records(target_type="semantic_rule", target_id="rule.semantic.meal_walk_observation")

        self.assertIsNotNone(rule)
        self.assertEqual(rule.status, "active")
        self.assertEqual(rule.created_by_llm_call_id, call.id)
        self.assertEqual(len(versions), 1)
        self.assertEqual(len(applications), 1)
        self.assertTrue(any(audit.action == "semantic_rule_upserted" for audit in audits))
        self.assertTrue(any(audit.action == "semantic_rule_application_recorded" for audit in audits))

    def test_runtime_feature_extractor_rule_adds_feature_and_application(self) -> None:
        with _memory() as memory:
            event = _rule_source_event()
            memory.upsert_event(event)
            now = datetime.now()
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.feature.meal_walk_recovery",
                    rule_type=SemanticRuleType.FEATURE_EXTRACTOR,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={
                        "and": [
                            {"field": "event_type", "op": "eq", "value": "self_report"},
                            {"field": "text", "op": "contains_any", "value": ["午饭", "散步"]},
                        ]
                    },
                    output_spec={
                        "feature_kind": "meal_walk_recovery",
                        "label": "餐后轻恢复",
                        "polarity": "support",
                        "value": "summary",
                        "confidence_scale": 0.8,
                    },
                    evidence_event_ids=[event.id],
                    confidence=0.7,
                    created_at=now,
                    updated_at=now,
                )
            )

            features = extract_features([event], memory=memory)
            applications = memory.list_rule_applications("rule.feature.meal_walk_recovery")

        runtime = [feature for feature in features if feature.kind == "meal_walk_recovery"]
        self.assertEqual(len(runtime), 1)
        self.assertEqual(runtime[0].id, "feature:rule-source-event:runtime:rule.feature.meal_walk_recovery:meal_walk_recovery")
        self.assertTrue(any(item.status == "applied" and item.reason == "runtime_feature_extractor_hit" for item in applications))

    def test_runtime_feature_extractor_disable_and_rollback_affect_extraction(self) -> None:
        with _memory() as memory:
            event = _rule_source_event()
            memory.upsert_event(event)
            now = datetime.now()
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.feature.rollback_test",
                    rule_type=SemanticRuleType.FEATURE_EXTRACTOR,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"field": "text", "op": "contains", "value": "午饭"},
                    output_spec={
                        "feature_kind": "meal_walk_recovery",
                        "label": "餐后轻恢复",
                        "polarity": "support",
                        "value": "summary",
                    },
                    evidence_event_ids=[event.id],
                    confidence=0.7,
                    created_at=now,
                    updated_at=now,
                )
            )
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.feature.rollback_test",
                    rule_type=SemanticRuleType.FEATURE_EXTRACTOR,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"field": "text", "op": "contains", "value": "航班"},
                    output_spec={
                        "feature_kind": "travel_timing_runtime",
                        "label": "出行时间信号",
                        "polarity": "support",
                        "value": "summary",
                    },
                    evidence_event_ids=[event.id],
                    confidence=0.7,
                    created_at=now,
                    updated_at=now,
                )
            )
            self.assertNotIn("meal_walk_recovery", {feature.kind for feature in extract_features([event], memory=memory)})

            memory.disable_semantic_rule("rule.feature.rollback_test")
            self.assertNotIn("meal_walk_recovery", {feature.kind for feature in extract_features([event], memory=memory)})

            memory.rollback_semantic_rule("rule.feature.rollback_test", 1)
            kinds = {feature.kind for feature in extract_features([event], memory=memory)}

        self.assertIn("meal_walk_recovery", kinds)

    def test_llm_feature_extractor_update_enters_next_payload(self) -> None:
        first_client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "午饭散步候选",
                        "confidence": 0.42,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "rule_updates": [
                    {
                        "id": "rule.feature.meal_walk_recovery",
                        "type": "feature_extractor",
                        "match_spec": {
                            "and": [
                                {"field": "event_type", "op": "eq", "value": "self_report"},
                                {"field": "text", "op": "contains_any", "value": ["午饭", "散步"]},
                            ]
                        },
                        "output_spec": {
                            "feature_kind": "meal_walk_recovery",
                            "label": "餐后轻恢复",
                            "polarity": "support",
                            "value": "summary",
                            "confidence_scale": 0.8,
                        },
                        "confidence": 0.62,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "expression": {
                    "title": "运行时特征规则生成",
                    "body": "午饭和散步可以进入运行时特征规则。",
                    "confidence": 0.42,
                },
            }
        )
        second_client = FakeLLMClient(_llm_response("evt-20260626-low-evidence", "运行时特征已进入 payload"))
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=first_client)
            build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=second_client)
            feature_kinds = {feature["kind"] for feature in second_client.payloads[0]["features"]}
            applications = memory.list_rule_applications("rule.feature.meal_walk_recovery")

        self.assertIn("meal_walk_recovery", feature_kinds)
        self.assertTrue(any(item.reason == "runtime_feature_extractor_hit" for item in applications))

    def test_invalid_feature_extractor_rules_are_rejected(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "午饭散步候选",
                        "confidence": 0.42,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "rule_updates": [
                    {
                        "id": "rule.feature.invalid_dsl",
                        "type": "feature_extractor",
                        "match_spec": {"field": "text", "op": "regex", "value": "午饭.*散步"},
                        "output_spec": {
                            "feature_kind": "meal_walk_recovery",
                            "label": "餐后轻恢复",
                            "polarity": "support",
                            "value": "summary",
                        },
                        "confidence": 0.62,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    },
                    {
                        "id": "rule.feature.invalid_output",
                        "type": "feature_extractor",
                        "match_spec": {"field": "text", "op": "contains", "value": "午饭"},
                        "output_spec": {
                            "feature_kind": "not-valid-kind",
                            "label": "餐后轻恢复",
                            "polarity": "support",
                            "value": "summary",
                        },
                        "confidence": 0.62,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    },
                    {
                        "id": "rule.feature.python_expression",
                        "type": "feature_extractor",
                        "match_spec": {"field": "text", "op": "contains", "value": "eval('午饭')"},
                        "output_spec": {
                            "feature_kind": "meal_walk_recovery",
                            "label": "餐后轻恢复",
                            "polarity": "support",
                            "value": "summary",
                        },
                        "confidence": 0.62,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    },
                ],
                "expression": {
                    "title": "无效规则拒绝",
                    "body": "无效运行时特征规则不应入库。",
                    "confidence": 0.42,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=client)

            invalid_dsl = memory.get_semantic_rule("rule.feature.invalid_dsl")
            invalid_output = memory.get_semantic_rule("rule.feature.invalid_output")
            python_expression = memory.get_semantic_rule("rule.feature.python_expression")

        self.assertIsNone(invalid_dsl)
        self.assertIsNone(invalid_output)
        self.assertIsNone(python_expression)

    def test_runtime_feature_deviation_skips_direct_expression_fast_path(self) -> None:
        client = FakeLLMClient(_llm_response("rule-source-event", "运行时新特征接回 LLM"))
        with _memory() as memory:
            event = _rule_source_event()
            memory.upsert_event(event)
            now = datetime.now()
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.feature.meal_walk_recovery",
                    rule_type=SemanticRuleType.FEATURE_EXTRACTOR,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"field": "text", "op": "contains", "value": "散步"},
                    output_spec={
                        "feature_kind": "meal_walk_recovery",
                        "label": "餐后轻恢复",
                        "polarity": "support",
                        "value": "summary",
                    },
                    evidence_event_ids=[event.id],
                    confidence=0.7,
                    created_at=now,
                    updated_at=now,
                )
            )
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.direct.runtime_deviation",
                    rule_type=SemanticRuleType.DIRECT_EXPRESSION,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"event_type": "self_report", "contains": ["午饭"], "feature_kinds": []},
                    output_spec={"title": "午饭固定观察", "body": "普通午饭。"},
                    evidence_event_ids=[event.id],
                    confidence=0.55,
                    created_at=now,
                    updated_at=now,
                )
            )

            insight = build_daily_state(memory, "2026-06-26", llm_client=client)
            applications = memory.list_rule_applications("rule.direct.runtime_deviation")

        self.assertEqual(insight.title, "运行时新特征接回 LLM")
        self.assertTrue(any(item.status == "skipped" and item.reason == "new_uncovered_features" for item in applications))

    def test_direct_expression_rule_hits_fast_path_without_new_llm_call(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "普通午饭散步候选",
                        "confidence": 0.42,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "direct_expression_rules": [
                    {
                        "id": "rule.direct.lunch_walk",
                        "match_spec": {
                            "event_type": "self_report",
                            "contains": ["午饭"],
                            "feature_kinds": [],
                        },
                        "output_spec": {
                            "title": "午饭散步固定观察",
                            "body": "今天只是普通午饭和散步，先不扩大解读。",
                            "alternatives": ["也可能有未记录信息"],
                        },
                        "confidence": 0.57,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "expression": {
                    "title": "规则生成洞察",
                    "body": "先生成一条固定事件直接表达规则。",
                    "confidence": 0.42,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=client)
            calls_before = len(memory.list_llm_calls())

            fast = build_daily_state(memory, "2026-06-26")
            calls_after = len(memory.list_llm_calls())
            applications = memory.list_rule_applications("rule.direct.lunch_walk")

        self.assertEqual(fast.title, "午饭散步固定观察")
        self.assertEqual(calls_after, calls_before)
        self.assertTrue(any(item.status == "applied" and item.reason == "direct_expression_fast_path_hit" for item in applications))

    def test_direct_expression_rule_requires_executable_match_spec(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "普通午饭散步候选",
                        "confidence": 0.42,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "direct_expression_rules": [
                    {
                        "id": "rule.direct.too_broad",
                        "match_spec": {"context_id": "context.open.meal_walk"},
                        "output_spec": {
                            "title": "过宽固定观察",
                            "body": "这条规则不能只靠 context_id 命中。",
                        },
                        "confidence": 0.57,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "expression": {
                    "title": "规则生成洞察",
                    "body": "模型尝试生成一条过宽 direct rule。",
                    "confidence": 0.42,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=client)
            rule = memory.get_semantic_rule("rule.direct.too_broad")

        self.assertIsNone(rule)

    def test_direct_expression_rule_rejects_unavailable_capability_claim(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "出行延误候选",
                        "confidence": 0.42,
                        "evidence_event_ids": ["evt-20260624-travel-delay"],
                    }
                ],
                "direct_expression_rules": [
                    {
                        "id": "rule.direct.unavailable_calendar_route",
                        "match_spec": {"event_type": "self_report", "contains": ["延误"]},
                        "output_spec": {
                            "title": "延误提醒",
                            "body": "当前行程延误，请确认下一场安排是否需要调整。",
                            "alternatives": ["如果需要，我可以帮你查看后续会议和路程时间。"],
                        },
                        "confidence": 0.57,
                        "evidence_event_ids": ["evt-20260624-travel-delay"],
                    }
                ],
                "expression": {
                    "title": "规则生成洞察",
                    "body": "模型尝试生成一条暗示未接入能力的 direct rule。",
                    "confidence": 0.42,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            build_daily_state(memory, "2026-06-24", use_llm=True, llm_client=client)
            rule = memory.get_semantic_rule("rule.direct.unavailable_calendar_route")

        self.assertIsNone(rule)

    def test_direct_expression_rule_rejects_feedback_as_fixed_future_rule(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "赶路解释候选",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-hr", "evt-20260616-self"],
                    }
                ],
                "direct_expression_rules": [
                    {
                        "id": "rule.direct.feedback_as_rule",
                        "match_spec": {"event_type": "heart_rate", "contains": ["心率"]},
                        "output_spec": {
                            "title": "心率升高由赶路引起",
                            "body": "心率变化更可能与赶路有关，而非紧张。后续类似情况优先考虑赶路。",
                        },
                        "confidence": 0.7,
                        "evidence_event_ids": ["evt-20260616-hr", "evt-20260616-self"],
                    }
                ],
                "expression": {
                    "title": "候选洞察",
                    "body": "原推理和用户反馈需要并存复盘。",
                    "confidence": 0.5,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            rule = memory.get_semantic_rule("rule.direct.feedback_as_rule")

        self.assertIsNone(rule)

    def test_legacy_broad_direct_expression_rule_does_not_match_everything(self) -> None:
        client = FakeLLMClient(_llm_response("evt-20260626-low-evidence", "过宽规则回退 LLM"))
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            now = datetime.now()
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.direct.legacy_broad",
                    rule_type=SemanticRuleType.DIRECT_EXPRESSION,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"context_id": "context.open.meal_walk"},
                    output_spec={"title": "错误固定观察", "body": "这条规则过宽。"},
                    evidence_event_ids=["evt-20260626-low-evidence"],
                    confidence=0.55,
                    created_at=now,
                    updated_at=now,
                )
            )

            insight = build_daily_state(memory, "2026-06-26", llm_client=client)
            calls = memory.list_llm_calls()
            applications = memory.list_rule_applications("rule.direct.legacy_broad")

        self.assertEqual(insight.title, "过宽规则回退 LLM")
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(item.reason == "direct_expression_fast_path_hit" for item in applications))

    def test_direct_expression_rule_deviation_skips_and_calls_llm(self) -> None:
        client = FakeLLMClient(_llm_response("deviation-event", "偏离后 LLM 洞察"))
        with _memory() as memory:
            event = _deviation_event()
            memory.upsert_event(event)
            now = datetime.now()
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.direct.deviation",
                    rule_type=SemanticRuleType.DIRECT_EXPRESSION,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"event_type": "self_report", "contains": ["午饭"], "feature_kinds": []},
                    output_spec={"title": "午饭固定观察", "body": "普通午饭。"},
                    evidence_event_ids=[event.id],
                    confidence=0.55,
                    created_at=now,
                    updated_at=now,
                )
            )

            insight = build_daily_state(memory, "2026-07-01", llm_client=client)
            applications = memory.list_rule_applications("rule.direct.deviation")
            call = memory.list_llm_calls()[0]

        self.assertEqual(insight.title, "偏离后 LLM 洞察")
        self.assertEqual(call.task_type, "daily_insight")
        self.assertTrue(any(item.status == "skipped" and item.reason == "new_uncovered_features" for item in applications))

    def test_direct_expression_rule_skips_when_matched_event_has_feedback(self) -> None:
        client = FakeLLMClient(_llm_response("deviation-event", "反馈后 LLM 洞察"))
        with _memory() as memory:
            event = _deviation_event()
            memory.upsert_event(event)
            now = datetime.now()
            memory.upsert_semantic_rule(
                SemanticRule(
                    id="rule.direct.feedback",
                    rule_type=SemanticRuleType.DIRECT_EXPRESSION,
                    status=SemanticRuleStatus.ACTIVE,
                    version=1,
                    source="test",
                    created_by_llm_call_id=None,
                    match_spec={"event_type": "self_report", "contains": ["午饭"]},
                    output_spec={"title": "午饭固定观察", "body": "普通午饭。"},
                    evidence_event_ids=[event.id],
                    confidence=0.55,
                    created_at=now,
                    updated_at=now,
                )
            )
            record_feedback(
                memory,
                target_type="event",
                target_id=event.id,
                text="不要把普通午饭扩大解读。",
            )

            insight = build_daily_state(memory, "2026-07-01", llm_client=client)
            applications = memory.list_rule_applications("rule.direct.feedback")
            call = memory.list_llm_calls()[0]

        self.assertEqual(insight.title, "反馈后 LLM 洞察")
        self.assertEqual(call.task_type, "daily_insight")
        self.assertTrue(any(item.status == "skipped" and item.reason == "user_feedback_for_matched_events" for item in applications))

    def test_rules_cli_list_explain_history_disable_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "local.db"
            as_memory = JoannaMemory(db)
            try:
                now = datetime.now()
                as_memory.upsert_event(_rule_source_event())
                as_memory.upsert_semantic_rule(
                    SemanticRule(
                        id="rule.semantic.cli_test",
                        rule_type=SemanticRuleType.SITUATION_TEMPLATE,
                        status=SemanticRuleStatus.ACTIVE,
                        version=1,
                        source="test",
                        created_by_llm_call_id=None,
                        match_spec={"contains": ["午饭"]},
                        output_spec={"context_type": "午饭观察"},
                        evidence_event_ids=["rule-source-event"],
                        confidence=0.5,
                        created_at=now,
                        updated_at=now,
                    )
                )
                as_memory.upsert_semantic_rule(
                    SemanticRule(
                        id="rule.semantic.cli_test",
                        rule_type=SemanticRuleType.SITUATION_TEMPLATE,
                        status=SemanticRuleStatus.ACTIVE,
                        version=1,
                        source="test",
                        created_by_llm_call_id=None,
                        match_spec={"contains": ["午饭", "散步"]},
                        output_spec={"context_type": "午饭散步观察"},
                        evidence_event_ids=["rule-source-event"],
                        confidence=0.6,
                        created_at=now,
                        updated_at=now,
                    )
                )
            finally:
                as_memory.close()

            listed = _run_cli(db, "rules", "list")
            explained = _run_cli(db, "rules", "explain", "rule.semantic.cli_test")
            history = _run_cli(db, "rules", "history", "rule.semantic.cli_test")
            disabled = _run_cli(db, "rules", "disable", "rule.semantic.cli_test")
            rolled_back = _run_cli(db, "rules", "rollback", "rule.semantic.cli_test", "--to-version", "1")

        self.assertEqual(listed.returncode, 0)
        self.assertIn("rule.semantic.cli_test", listed.stdout)
        self.assertEqual(explained.returncode, 0)
        self.assertIn("applications", explained.stdout)
        self.assertEqual(history.returncode, 0)
        self.assertIn('"version": 2', history.stdout)
        self.assertEqual(disabled.returncode, 0)
        self.assertIn('"status": "disabled"', disabled.stdout)
        self.assertEqual(rolled_back.returncode, 0)
        self.assertIn('"rollback_target": 1', rolled_back.stdout)

    def test_web_observer_shows_feedback_language_without_final_verdict_controls(self) -> None:
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            html = _view(memory, "today", "2026-06-16")

        self.assertIn("记录反馈事件", html)
        self.assertIn("反馈会进入证据流", html)
        self.assertNotIn("从推理中移除", html)
        self.assertNotIn("最终裁决", html)

    def test_period_review_llm_uses_long_tier(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "多日任务切换候选",
                        "confidence": 0.58,
                        "evidence_event_ids": [
                            "evt-20260624-travel-delay",
                            "evt-20260628-call-calendar",
                        ],
                    }
                ],
                "expression": {
                    "title": "多日复盘",
                    "body": "这几天更像是日程扰动和任务切换的候选线索。",
                    "confidence": 0.58,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            insight = build_period_review(
                memory,
                "2026-06-24",
                "2026-06-28",
                llm_client=client,
            )
            call = memory.list_llm_calls()[0]

        self.assertEqual(insight.insight_type, "period_review")
        self.assertEqual(call.task_type, "period_review")
        self.assertEqual(call.tier, "long")
        self.assertIn("evt-20260624-travel-delay", call.event_ids)


class _memory:
    def __enter__(self) -> JoannaMemory:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.memory = JoannaMemory(Path(self.tmpdir.name) / "local.db")
        return self.memory

    def __exit__(self, exc_type, exc, tb) -> None:
        self.memory.close()
        self.tmpdir.cleanup()


def _llm_response(event_id: str, title: str) -> dict:
    return {
        "contexts": [
            {
                "context_type": "默认 LLM 候选",
                "confidence": 0.6,
                "evidence_event_ids": [event_id],
            }
        ],
        "expression": {
            "title": title,
            "body": "这是默认 LLM 路径生成的候选判断。",
            "confidence": 0.6,
        },
    }


def _run_cli(db: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "joanna.app.cli", "--db", str(db), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _rule_source_event():
    from joanna.core.schema import ExperienceEvent

    return ExperienceEvent.from_dict(
        {
            "id": "rule-source-event",
            "occurred_at": "2026-06-26T20:10:00+08:00",
            "source_type": "manual",
            "source_id": "test",
            "event_type": "self_report",
            "summary": "普通午饭和散步。",
            "confidence": 0.8,
        }
    )


def _deviation_event():
    from joanna.core.schema import ExperienceEvent

    return ExperienceEvent.from_dict(
        {
            "id": "deviation-event",
            "occurred_at": "2026-07-01T12:30:00+08:00",
            "source_type": "manual",
            "source_id": "test",
            "event_type": "self_report",
            "summary": "午饭后和家人有一次争执。",
            "people": ["家人"],
            "confidence": 0.82,
        }
    )


if __name__ == "__main__":
    unittest.main()
