from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from joanna.adapters.manual import ingest_jsonl
from joanna.core.governance import DIAGNOSTIC_TERMS
from joanna.core.llm import load_deepseek_api_key
from joanna.core.llm_governance import classify_llm_exception
from joanna.core.llm_reasoner import (
    CONTEXT_ASSEMBLY_POLICY,
    CORE_PHILOSOPHY,
    HARD_GOVERNANCE_BOUNDARIES,
    OUTPUT_CONTRACT,
    SYSTEM_PROMPT,
)
from joanna.core.memory import JoannaMemory
from joanna.core.feedback import record_feedback
from joanna.core.reasoning import build_daily_state, build_reminder
from joanna.core.schema import LLMFailureType


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "samples" / "phase_one_events.jsonl"
PHASE_TWO_SAMPLE = ROOT / "samples" / "phase_two_events.jsonl"


class FakeLLMClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.payloads: list[dict] = []
        self.system_prompts: list[str] = []

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        self.system_prompts.append(system_prompt)
        self.payloads.append(user_payload)
        return self.response


class LLMReasonerTest(unittest.TestCase):
    def test_deepseek_key_loader_prefers_environment_then_configured_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "deepseek.txt"
            key_file.write_text("file-key\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key", "DEEPSEEK_API_KEY_FILE": str(key_file)}, clear=True):
                self.assertEqual(load_deepseek_api_key(), "env-key")
            with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY_FILE": str(key_file)}, clear=True):
                self.assertEqual(load_deepseek_api_key(), "file-key")

    def test_system_prompt_lists_forbidden_visible_terms(self) -> None:
        for term in DIAGNOSTIC_TERMS:
            self.assertIn(term, SYSTEM_PROMPT)
        self.assertIn("进食与补给", SYSTEM_PROMPT)
        self.assertIn("不是结论模板", SYSTEM_PROMPT)
        self.assertIn("以 LLM 推理为中心", SYSTEM_PROMPT)
        self.assertIn("所有可观察输入都先进入证据链", SYSTEM_PROMPT)
        self.assertIn("所有系统输出都只是推理声明", SYSTEM_PROMPT)
        self.assertIn("所有状态都只是派生结果", SYSTEM_PROMPT)
        self.assertIn("完整上下文重新解释", SYSTEM_PROMPT)
        self.assertIn("用户反馈不是最终事实", SYSTEM_PROMPT)
        self.assertIn("授权、隐私、外部行动和能力声明是硬边界", SYSTEM_PROMPT)
        self.assertIn("当前未接入 Calendar、HealthKit、路线、通知或通信工具", SYSTEM_PROMPT)

    def test_llm_payload_includes_phase_three_point_five_contracts(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "理念上下文候选",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "理念上下文测试",
                    "body": "这是一个基于证据链的候选判断。",
                    "confidence": 0.5,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            payload = client.payloads[0]

        self.assertEqual(payload["core_philosophy"], list(CORE_PHILOSOPHY))
        principles = [item["principle"] for item in payload["core_philosophy"]]
        self.assertEqual(payload["task_type"], "daily_insight")
        self.assertIn("以 LLM 推理为中心，持续解释个人证据链。", principles)
        self.assertIn("所有可观察输入都先进入证据链。", principles)
        self.assertEqual(payload["context_assembly_policy"], list(CONTEXT_ASSEMBLY_POLICY))
        self.assertEqual(payload["output_contract"], list(OUTPUT_CONTRACT))
        self.assertEqual(payload["hard_governance_boundaries"], list(HARD_GOVERNANCE_BOUNDARIES))
        self.assertTrue(payload["governance"]["system_outputs_are_inference_claims"])
        self.assertTrue(payload["governance"]["states_are_derived_not_facts"])
        self.assertTrue(payload["governance"]["llm_must_use_complete_evidence_chain"])
        self.assertTrue(payload["governance"]["permission_privacy_external_action_are_hard_boundaries"])
        self.assertTrue(payload["governance"]["capability_claims_must_match_current_system"])

    def test_large_apple_health_payload_is_compressed_before_llm(self) -> None:
        summary_id = "evt.phase5.health_summary.2026-06-24.apple_health_heartrate.audio_overlap"
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "健康摘要候选",
                        "confidence": 0.5,
                        "evidence_event_ids": [summary_id],
                    }
                ],
                "expression": {
                    "title": "健康摘要测试",
                    "body": "这是基于压缩健康证据的候选判断。",
                    "confidence": 0.5,
                },
            }
        )
        with _memory() as memory:
            for event in _many_health_events():
                memory.upsert_event(event)
            build_daily_state(memory, "2026-06-24", use_llm=True, llm_client=client)
            payload = client.payloads[0]
            summary_event = memory.get_event(summary_id)

        self.assertTrue(payload["evidence_compression"]["applied"])
        self.assertEqual(payload["evidence_compression"]["original_apple_health_event_count"], 201)
        self.assertTrue(summary_event)
        self.assertEqual(summary_event.event_type, "phase5_health_summary")
        self.assertFalse(summary_event.allow_profile)
        payload_event_types = {event["event_type"] for event in payload["events"]}
        self.assertIn("phase5_health_summary", payload_event_types)
        self.assertNotIn("apple_health_heartrate", payload_event_types)

    def test_prompt_and_payload_keep_feedback_epistemic_boundary_separate_from_governance_boundary(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "冲突候选",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "冲突边界测试",
                    "body": "原推理和用户反馈都需要作为证据保留。",
                    "confidence": 0.5,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            payload = client.payloads[0]
            prompt = client.system_prompts[0]

        hard_rules = " ".join(item["rule"] for item in payload["hard_governance_boundaries"])
        self.assertIn("用户反馈和 LLM 推理都不是最终事实", hard_rules)
        self.assertIn("未授权数据源不得读取", hard_rules)
        self.assertIn("不得自动发消息", hard_rules)
        self.assertIn("不能由推理绕过", prompt)
        self.assertIn("不能因为用户反馈就删除或覆盖原推理", prompt)

    def test_feedback_conflict_payload_preserves_original_claim_and_feedback(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "反馈冲突候选",
                        "confidence": 0.55,
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

        self.assertEqual(payload["feedback_events"][0]["id"], feedback.id)
        self.assertEqual(payload["inference_claims"][0]["id"], claim.id)
        self.assertTrue(payload["conflict_bundles"])

    def test_reminder_payload_carries_task_type_and_hard_capability_boundary(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "低打扰提醒候选",
                        "confidence": 0.6,
                        "evidence_event_ids": ["evt-20260617-family-message"],
                    }
                ],
                "expression": {
                    "title": "低打扰提醒",
                    "body": "可以先准备一句低打扰表达，但系统不会自动联系任何人。",
                    "confidence": 0.6,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            build_reminder(memory, "2026-06-17", use_llm=True, llm_client=client)
            payload = client.payloads[0]
            prompt = client.system_prompts[0]

        self.assertEqual(payload["task_type"], "reminder")
        self.assertIn("不得自动发消息、联系他人、创建日程", prompt)
        self.assertIn("当前未接入 Calendar、HealthKit、路线、通知或通信工具", prompt)

    def test_llm_daily_insight_accepts_valid_json_contract(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "id": "context.open.high_load_interaction",
                        "context_type": "高负荷互动前后情境",
                        "confidence": 0.66,
                        "evidence_event_ids": ["evt-20260616-calendar", "evt-20260616-hr"],
                        "alternatives": ["赶路或运动", "睡眠不足"],
                        "uncertainty": "这是候选解释，需要用户确认。",
                    }
                ],
                "profile_candidates": [
                    {
                        "id": "profile.candidate.high_load_before_interaction",
                        "claim": "高负荷互动前可能出现身体激活。",
                        "confidence": 0.52,
                        "evidence_event_ids": ["evt-20260616-calendar", "evt-20260616-hr"],
                    }
                ],
                "evolution_proposals": [
                    {
                        "proposal_type": "expression_preference",
                        "risk": "low",
                        "title": "表达更保守",
                        "rationale": "用户需要更保守表达。",
                        "payload": {"style": "conservative"},
                        "evidence_event_ids": ["evt-20260616-self"],
                    }
                ],
                "expression": {
                    "title": "LLM 今日洞察",
                    "body": "今天更像是高负荷互动前后的候选情境，而不是确定事实。",
                    "alternatives": ["赶路或运动"],
                    "confidence": 0.66,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            proposals = memory.list_evolution_proposals()

        self.assertEqual(insight.title, "LLM 今日洞察")
        self.assertEqual(len(insight.context_hypotheses), 1)
        self.assertTrue(any(proposal.proposal_type == "expression_preference" and proposal.status == "applied" for proposal in proposals))
        self.assertTrue(any(proposal.proposal_type == "profile_candidate" and proposal.status == "pending" for proposal in proposals))

    def test_llm_rejects_unknown_evidence_ids(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "无效证据情境",
                        "confidence": 0.9,
                        "evidence_event_ids": ["not-an-event"],
                    }
                ],
                "expression": {"body": "没有有效证据。"},
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)

        self.assertEqual(len(insight.context_hypotheses), 0)
        self.assertGreater(len(insight.evidence), 0)

    def test_llm_semantic_observations_enter_insight(self) -> None:
        client = FakeLLMClient(
            {
                "semantic_observations": [
                    {
                        "id": "observation.meal_reflection",
                        "observation_type": "进食与补给",
                        "text": "午饭后可能更容易进入安静复盘状态。",
                        "confidence": 0.55,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                        "alternatives": ["也可能只是当天安排较少"],
                    },
                    {
                        "id": "observation.invalid",
                        "observation_type": "进食与补给",
                        "text": "这条观察缺少有效证据。",
                        "confidence": 0.55,
                        "evidence_event_ids": ["not-an-event"],
                    },
                ],
                "contexts": [
                    {
                        "context_type": "低证据日候选",
                        "confidence": 0.4,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "expression": {
                    "title": "日常细节观察",
                    "body": "这只是一个围绕午饭和散步的候选观察。",
                    "confidence": 0.4,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            insight = build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=client)

        self.assertEqual(len(insight.semantic_observations), 1)
        self.assertEqual(insight.semantic_observations[0].observation_type, "进食与补给")
        self.assertIn("午饭后", insight.semantic_observations[0].text)
        self.assertEqual(insight.semantic_observations[0].evidence[0].event_id, "evt-20260626-low-evidence")

    def test_disabled_events_are_not_sent_to_llm(self) -> None:
        client = FakeLLMClient({"contexts": [], "expression": {"body": "无候选。"}})
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            memory.disable_event("evt-20260616-hr")
            build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)

        event_ids = {event["id"] for event in client.payloads[0]["events"]}
        feature_event_ids = {feature["event_id"] for feature in client.payloads[0]["features"]}
        self.assertNotIn("evt-20260616-hr", event_ids)
        self.assertNotIn("evt-20260616-hr", feature_event_ids)

    def test_diagnostic_llm_expression_is_rejected(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "高负荷互动前后情境",
                        "confidence": 0.66,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "错误洞察",
                    "body": "用户社交焦虑。",
                    "confidence": 0.66,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            with self.assertRaises(ValueError):
                build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)

    def test_feedback_as_future_commitment_expression_is_rejected(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "低证据日候选",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260626-low-evidence"],
                    }
                ],
                "expression": {
                    "title": "错误承诺",
                    "body": "系统会据此在类似安静的日子里减少不必要的展开。",
                    "confidence": 0.5,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            with self.assertRaises(ValueError):
                build_daily_state(memory, "2026-06-26", use_llm=True, llm_client=client)

    def test_feedback_as_final_correction_expression_is_rejected(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "反馈后候选",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "错误裁决",
                    "body": "这些都不准确，我们已经调整了理解，未来会更加注意。",
                    "confidence": 0.5,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            with self.assertRaises(ValueError):
                build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)

    def test_expression_mentions_augment_context_evidence(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "项目收尾低打扰情境",
                        "confidence": 0.72,
                        "evidence_event_ids": ["project-preference"],
                        "alternatives": ["也可能只是临时想专注"],
                    }
                ],
                "expression": {
                    "title": "项目收尾提醒",
                    "body": "家人已经询问回家时间，但你偏好低打扰提醒。",
                    "confidence": 0.72,
                },
            }
        )
        with _memory() as memory:
            for event in _project_events():
                memory.upsert_event(event)
            insight = build_daily_state(memory, "2026-06-22", use_llm=True, llm_client=client)

        evidence_ids = {item.event_id for item in insight.context_hypotheses[0].evidence}
        self.assertIn("project-preference", evidence_ids)
        self.assertIn("project-family", evidence_ids)

    def test_expression_mentions_package_pickup_augments_evidence(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "上午计划变更情境",
                        "confidence": 0.72,
                        "evidence_event_ids": ["package-delay"],
                    }
                ],
                "expression": {
                    "title": "计划变更提醒",
                    "body": "上午先处理延误，晚上记得顺路取快递。",
                    "confidence": 0.72,
                },
            }
        )
        with _memory() as memory:
            for event in _package_events():
                memory.upsert_event(event)
            insight = build_daily_state(memory, "2026-06-23", use_llm=True, llm_client=client)

        evidence_ids = {item.event_id for item in insight.context_hypotheses[0].evidence}
        self.assertIn("package-delay", evidence_ids)
        self.assertIn("package-family", evidence_ids)

    def test_expression_mentions_lunch_multitasking_augments_evidence(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "上午计划变更情境",
                        "confidence": 0.72,
                        "evidence_event_ids": ["lunch-delay"],
                    }
                ],
                "expression": {
                    "title": "计划变更提醒",
                    "body": "上午先处理延误，午餐时边用餐边回消息后餐后有点散。",
                    "confidence": 0.72,
                },
            }
        )
        with _memory() as memory:
            for event in _lunch_events():
                memory.upsert_event(event)
            insight = build_daily_state(memory, "2026-06-24", use_llm=True, llm_client=client)

        evidence_ids = {item.event_id for item in insight.context_hypotheses[0].evidence}
        self.assertIn("lunch-delay", evidence_ids)
        self.assertIn("lunch-message", evidence_ids)

    def test_governance_boundary_is_always_high_risk_pending(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "创作过载候选",
                        "confidence": 0.7,
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "evolution_proposals": [
                    {
                        "proposal_type": "governance_boundary",
                        "risk": "low",
                        "title": "限制提醒",
                        "rationale": "创作时少提醒。",
                        "payload": {"boundary": "lower_reminders"},
                        "evidence_event_ids": ["evt-20260616-calendar"],
                    }
                ],
                "expression": {
                    "title": "候选洞察",
                    "body": "这是候选判断。",
                    "confidence": 0.7,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            proposal = next(item for item in memory.list_evolution_proposals() if item.proposal_type == "governance_boundary")

        self.assertEqual(proposal.risk, "high")
        self.assertEqual(proposal.status, "pending")

    def test_duplicate_llm_proposals_are_deduped(self) -> None:
        duplicate = {
            "proposal_type": "feature_weight",
            "risk": "low",
            "title": "提高表达负荷权重",
            "rationale": "相同建议。",
            "payload": {"feature": "expression_load"},
            "evidence_event_ids": ["evt-20260616-speech"],
        }
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "高负荷互动候选",
                        "confidence": 0.7,
                        "evidence_event_ids": ["evt-20260616-speech"],
                    }
                ],
                "evolution_proposals": [duplicate, duplicate],
                "profile_candidates": [
                    {
                        "id": "profile.candidate.dup",
                        "claim": "表达负荷可能较高。",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-speech"],
                    },
                    {
                        "id": "profile.candidate.dup2",
                        "claim": "表达负荷可能较高。",
                        "confidence": 0.5,
                        "evidence_event_ids": ["evt-20260616-speech"],
                    },
                ],
                "expression": {
                    "title": "候选洞察",
                    "body": "这是候选判断。",
                    "confidence": 0.7,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, SAMPLE)
            insight = build_daily_state(memory, "2026-06-16", use_llm=True, llm_client=client)
            proposals = [item for item in memory.list_evolution_proposals() if item.proposal_type == "feature_weight"]

        llm_profile_candidates = [profile for profile in insight.profile_claims if profile.id.startswith("profile.candidate")]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(llm_profile_candidates), 1)

    def test_profile_candidates_drop_allow_profile_false_evidence(self) -> None:
        client = FakeLLMClient(
            {
                "contexts": [
                    {
                        "context_type": "面试前候选情境",
                        "confidence": 0.62,
                        "evidence_event_ids": ["evt-20260625-interview-calendar", "evt-20260625-interview-hr"],
                    }
                ],
                "profile_candidates": [
                    {
                        "id": "profile.candidate.invalid_heart_rate_pattern",
                        "claim": "面试前可能有身体激活模式。",
                        "confidence": 0.6,
                        "evidence_event_ids": ["evt-20260625-interview-hr"],
                    }
                ],
                "evolution_proposals": [
                    {
                        "proposal_type": "profile_candidate",
                        "risk": "high",
                        "title": "确认身体激活画像",
                        "rationale": "引用了禁止画像使用的心率事件。",
                        "payload": {"profile_id": "profile.candidate.invalid_heart_rate_pattern"},
                        "evidence_event_ids": ["evt-20260625-interview-hr"],
                    }
                ],
                "expression": {
                    "title": "面试前候选洞察",
                    "body": "面试前可能有状态变化，但只能作为当天表达证据。",
                    "confidence": 0.62,
                },
            }
        )
        with _memory() as memory:
            ingest_jsonl(memory, PHASE_TWO_SAMPLE)
            insight = build_daily_state(memory, "2026-06-25", use_llm=True, llm_client=client)
            proposals = memory.list_evolution_proposals()

        self.assertFalse(any(profile.id == "profile.candidate.invalid_heart_rate_pattern" for profile in insight.profile_claims))
        self.assertFalse(any(proposal.proposal_type == "profile_candidate" for proposal in proposals))
        self.assertIn("evt-20260625-interview-hr", {item.event_id for item in insight.evidence})

    def test_dns_failure_is_classified_as_network_error(self) -> None:
        failure = RuntimeError("DeepSeek API request failed: nodename nor servname provided, or not known")

        self.assertEqual(classify_llm_exception(failure), LLMFailureType.NETWORK_ERROR)


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


def _project_events():
    from joanna.core.schema import ExperienceEvent

    return [
        ExperienceEvent.from_dict(
            {
                "id": "project-preference",
                "occurred_at": "2026-06-22T21:50:00+08:00",
                "source_type": "manual",
                "source_id": "preference",
                "event_type": "preference_statement",
                "summary": "用户声明：项目收尾时提醒要低打扰。",
                "content": {"claim": "项目收尾时提醒要低打扰。"},
                "confidence": 0.95,
            }
        ),
        ExperienceEvent.from_dict(
            {
                "id": "project-family",
                "occurred_at": "2026-06-22T21:45:00+08:00",
                "source_type": "manual",
                "source_id": "message",
                "event_type": "message_summary",
                "summary": "家人问今晚什么时候回家。",
                "people": ["家人"],
                "confidence": 0.8,
            }
        ),
    ]


def _package_events():
    from joanna.core.schema import ExperienceEvent

    return [
        ExperienceEvent.from_dict(
            {
                "id": "package-delay",
                "occurred_at": "2026-06-23T09:00:00+08:00",
                "source_type": "manual",
                "source_id": "travel",
                "event_type": "self_report",
                "summary": "航班延误，只能重排上午安排。",
                "confidence": 0.9,
            }
        ),
        ExperienceEvent.from_dict(
            {
                "id": "package-family",
                "occurred_at": "2026-06-23T18:30:00+08:00",
                "source_type": "manual",
                "source_id": "message",
                "event_type": "message_summary",
                "summary": "家人提醒今晚回家时取快递，用户回复会顺路处理。",
                "people": ["家人"],
                "confidence": 0.82,
            }
        ),
    ]


def _lunch_events():
    from joanna.core.schema import ExperienceEvent

    return [
        ExperienceEvent.from_dict(
            {
                "id": "lunch-delay",
                "occurred_at": "2026-06-24T09:00:00+08:00",
                "source_type": "manual",
                "source_id": "travel",
                "event_type": "self_report",
                "summary": "航班延误，只能重排上午安排。",
                "confidence": 0.9,
            }
        ),
        ExperienceEvent.from_dict(
            {
                "id": "lunch-message",
                "occurred_at": "2026-06-24T12:45:00+08:00",
                "source_type": "manual",
                "source_id": "meal",
                "event_type": "self_report",
                "summary": "午饭时一边吃饭一边回消息，吃完后感觉有点散。",
                "confidence": 0.8,
            }
        ),
    ]


def _many_health_events():
    from joanna.core.schema import ExperienceEvent

    return [
        ExperienceEvent.from_dict(
            {
                "id": f"evt.health.{index:03d}",
                "occurred_at": f"2026-06-24T10:{index % 60:02d}:00+08:00",
                "source_type": "health_sample",
                "source_id": "apple-health-export-test",
                "event_type": "apple_health_heartrate",
                "summary": f"Apple Health HeartRate 样本：{70 + index % 10} count/min，与录音时间窗重叠。",
                "content": {
                    "value": str(70 + index % 10),
                    "unit": "count/min",
                    "audio_overlap": True,
                    "overlap_audio_segment_ids": ["audioseg-test"],
                    "source_name": "Apple Watch",
                },
                "sensitivity": "sensitive",
                "allow_long_term": True,
                "allow_profile": False,
                "confidence": 0.8,
                "evidence_refs": ["apple-health-export-test", "audioseg-test"],
            }
        )
        for index in range(201)
    ]
