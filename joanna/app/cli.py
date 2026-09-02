from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback
from typing import Any

from joanna.adapters.manual import ingest_jsonl
from joanna.core.correction import record_correction
from joanna.core.evolution import approve_proposal, reject_proposal
from joanna.core.feedback import record_feedback
from joanna.core.features import extract_features
from joanna.core.memory import JoannaMemory
from joanna.core.phase5 import (
    PHASE5_ROOT,
    build_reflection_report,
    default_phase5_db,
    derive_events,
    get_segment,
    list_segments,
    process_segment,
    receive_segment_from_files,
)
from joanna.core.qwen_omni_audio import QWEN_OMNI_DEFAULT_MODEL, QwenOmniAudioProcessor
from joanna.core.reasoning import build_daily_state, build_event_review, build_period_review, build_reminder
from joanna.core.summaries import build_memory_summaries


DEFAULT_DB = ".joanna/local.db"


def main() -> None:
    parser = argparse.ArgumentParser(prog="joanna")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--debug", action="store_true", help="Show internal traceback on errors")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest JSONL experience events")
    ingest_parser.add_argument("jsonl_path")

    events_parser = subparsers.add_parser("events", help="Inspect or govern events")
    events_sub = events_parser.add_subparsers(dest="events_command", required=True)
    events_list = events_sub.add_parser("list", help="List events")
    events_list.add_argument("--date")
    events_list.add_argument("--type")
    events_list.add_argument("--person")
    events_list.add_argument("--scene")
    events_list.add_argument("--include-disabled", action="store_true")
    events_disable = events_sub.add_parser("disable", help="Disable an event")
    events_disable.add_argument("event_id")
    events_delete = events_sub.add_parser("delete", help="Delete an event")
    events_delete.add_argument("event_id")
    events_revoke = events_sub.add_parser("revoke-profile-use", help="Revoke event profile usage")
    events_revoke.add_argument("event_id")

    features_parser = subparsers.add_parser("features", help="Inspect extracted generic experience features")
    features_sub = features_parser.add_subparsers(dest="features_command", required=True)
    features_list = features_sub.add_parser("list", help="List features")
    features_list.add_argument("--date")

    insight_parser = subparsers.add_parser("insight", help="Generate insights")
    insight_sub = insight_parser.add_subparsers(dest="insight_command", required=True)
    insight_today = insight_sub.add_parser("today", help="Generate daily state insight")
    insight_today.add_argument("--date", required=True)
    insight_today.add_argument("--json", action="store_true")
    insight_today.add_argument("--llm", action="store_true", help="Compatibility flag; LLM is now the default")
    insight_today.add_argument("--no-llm", action="store_true", help="Use local offline rule path")

    review_parser = subparsers.add_parser("review", help="Generate event review")
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)
    review_event = review_sub.add_parser("event", help="Review one event")
    review_event.add_argument("event_id")
    review_event.add_argument("--json", action="store_true")
    review_event.add_argument("--llm", action="store_true", help="Compatibility flag; LLM is now the default")
    review_event.add_argument("--no-llm", action="store_true", help="Use local offline rule path")
    review_period = review_sub.add_parser("period", help="Review a date range")
    review_period.add_argument("--from", dest="start_date", required=True)
    review_period.add_argument("--to", dest="end_date", required=True)
    review_period.add_argument("--json", action="store_true")
    review_period.add_argument("--llm", action="store_true", help="Compatibility flag; LLM is now the default")
    review_period.add_argument("--no-llm", action="store_true", help="Use local offline rule path")
    review_period.add_argument("--allow-huge", action="store_true", help="Allow huge LLM tier after evidence compression")

    suggest_parser = subparsers.add_parser("suggest", help="Generate suggestions")
    suggest_sub = suggest_parser.add_subparsers(dest="suggest_command", required=True)
    suggest_reminder = suggest_sub.add_parser("reminder", help="Suggest gentle reminder")
    suggest_reminder.add_argument("--date", required=True)
    suggest_reminder.add_argument("--json", action="store_true")
    suggest_reminder.add_argument("--llm", action="store_true", help="Compatibility flag; LLM is now the default")
    suggest_reminder.add_argument("--no-llm", action="store_true", help="Use local offline rule path")

    correct_parser = subparsers.add_parser("correct", help="Record user correction")
    correct_parser.add_argument("--target-type", required=True)
    correct_parser.add_argument("--target-id", required=True)
    correct_parser.add_argument("--text", required=True)
    correct_parser.add_argument("--original", default="")

    feedback_parser = subparsers.add_parser("feedback", help="Record or inspect user feedback events")
    feedback_sub = feedback_parser.add_subparsers(dest="feedback_command", required=True)
    feedback_record = feedback_sub.add_parser("record", help="Record a feedback event")
    feedback_record.add_argument("--target-type", required=True)
    feedback_record.add_argument("--target-id", required=True)
    feedback_record.add_argument("--text", required=True)
    feedback_record.add_argument("--type")
    feedback_list = feedback_sub.add_parser("list", help="List feedback events")
    feedback_list.add_argument("--target-type")
    feedback_list.add_argument("--target-id")
    feedback_list.add_argument("--type")
    feedback_list.add_argument("--limit", type=int, default=50)
    feedback_explain = feedback_sub.add_parser("explain", help="Explain one feedback event")
    feedback_explain.add_argument("feedback_id")

    claims_parser = subparsers.add_parser("claims", help="Inspect persisted inference claims")
    claims_sub = claims_parser.add_subparsers(dest="claims_command", required=True)
    claims_list = claims_sub.add_parser("list", help="List inference claims")
    claims_list.add_argument("--subject-type")
    claims_list.add_argument("--subject-id")
    claims_list.add_argument("--insight-id")
    claims_list.add_argument("--limit", type=int, default=50)
    claims_explain = claims_sub.add_parser("explain", help="Explain one inference claim")
    claims_explain.add_argument("claim_id")

    conflicts_parser = subparsers.add_parser("conflicts", help="Inspect conflict bundles")
    conflicts_sub = conflicts_parser.add_subparsers(dest="conflicts_command", required=True)
    conflicts_list = conflicts_sub.add_parser("list", help="List conflict bundles")
    conflicts_list.add_argument("--status")
    conflicts_list.add_argument("--limit", type=int, default=50)
    conflicts_explain = conflicts_sub.add_parser("explain", help="Explain one conflict bundle")
    conflicts_explain.add_argument("conflict_id")

    profiles_parser = subparsers.add_parser("profiles", help="Inspect or govern profiles")
    profiles_sub = profiles_parser.add_subparsers(dest="profiles_command", required=True)
    profiles_list = profiles_sub.add_parser("list", help="List profiles")
    profiles_list.add_argument("--include-revoked", action="store_true")
    profiles_explain = profiles_sub.add_parser("explain", help="Explain one profile")
    profiles_explain.add_argument("profile_id")
    profiles_history = profiles_sub.add_parser("history", help="List profile version history")
    profiles_history.add_argument("profile_id")
    profiles_confirm = profiles_sub.add_parser("confirm", help="Confirm one profile for future reasoning")
    profiles_confirm.add_argument("profile_id")
    profiles_revoke = profiles_sub.add_parser("revoke", help="Revoke one profile")
    profiles_revoke.add_argument("profile_id")

    corrections_parser = subparsers.add_parser("corrections", help="List corrections")
    corrections_parser.add_argument("--target-type")
    corrections_parser.add_argument("--target-id")

    summaries_parser = subparsers.add_parser("summaries", help="Build or inspect long-term memory summaries")
    summaries_sub = summaries_parser.add_subparsers(dest="summaries_command", required=True)
    summaries_build = summaries_sub.add_parser("build", help="Build summaries for a date range")
    summaries_build.add_argument("--from", dest="start_date", required=True)
    summaries_build.add_argument("--to", dest="end_date", required=True)
    summaries_list = summaries_sub.add_parser("list", help="List memory summaries")
    summaries_list.add_argument("--status")
    summaries_list.add_argument("--type")
    summaries_explain = summaries_sub.add_parser("explain", help="Explain one memory summary")
    summaries_explain.add_argument("summary_id")

    audit_parser = subparsers.add_parser("audit", help="Inspect governance audit records")
    audit_sub = audit_parser.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_sub.add_parser("list", help="List audit records")
    audit_list.add_argument("--action")
    audit_list.add_argument("--target-type")
    audit_list.add_argument("--target-id")
    audit_list.add_argument("--limit", type=int, default=50)
    audit_export = audit_sub.add_parser("export", help="Export audit records as JSON")
    audit_export.add_argument("--action")
    audit_export.add_argument("--target-type")
    audit_export.add_argument("--target-id")
    audit_export.add_argument("--limit", type=int, default=500)

    llm_parser = subparsers.add_parser("llm", help="Inspect governed LLM calls")
    llm_sub = llm_parser.add_subparsers(dest="llm_command", required=True)
    llm_calls = llm_sub.add_parser("calls", help="List or explain LLM calls")
    llm_calls_sub = llm_calls.add_subparsers(dest="llm_calls_command", required=True)
    llm_calls_list = llm_calls_sub.add_parser("list", help="List LLM calls")
    llm_calls_list.add_argument("--limit", type=int, default=50)
    llm_calls_explain = llm_calls_sub.add_parser("explain", help="Explain one LLM call")
    llm_calls_explain.add_argument("call_id")

    rules_parser = subparsers.add_parser("rules", help="Inspect or govern runtime semantic rules")
    rules_sub = rules_parser.add_subparsers(dest="rules_command", required=True)
    rules_list = rules_sub.add_parser("list", help="List runtime semantic rules")
    rules_list.add_argument("--include-inactive", action="store_true")
    rules_explain = rules_sub.add_parser("explain", help="Explain one runtime semantic rule")
    rules_explain.add_argument("rule_id")
    rules_history = rules_sub.add_parser("history", help="List runtime semantic rule versions")
    rules_history.add_argument("rule_id")
    rules_disable = rules_sub.add_parser("disable", help="Disable one runtime semantic rule")
    rules_disable.add_argument("rule_id")
    rules_rollback = rules_sub.add_parser("rollback", help="Rollback one runtime semantic rule")
    rules_rollback.add_argument("rule_id")
    rules_rollback.add_argument("--to-version", type=int, required=True)

    evolution_parser = subparsers.add_parser("evolution", help="Inspect or govern self-evolution proposals")
    evolution_sub = evolution_parser.add_subparsers(dest="evolution_command", required=True)
    evolution_list = evolution_sub.add_parser("list", help="List evolution proposals")
    evolution_list.add_argument("--include-rejected", action="store_true")
    evolution_approve = evolution_sub.add_parser("approve", help="Approve one high-risk proposal")
    evolution_approve.add_argument("proposal_id")
    evolution_reject = evolution_sub.add_parser("reject", help="Reject one proposal")
    evolution_reject.add_argument("proposal_id")

    web_parser = subparsers.add_parser("web", help="Start local evidence-chain observer")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    phase5_parser = subparsers.add_parser("phase5", help="Run phase 5 week-test capture workflows")
    phase5_parser.add_argument("--root", default=str(PHASE5_ROOT), help="Phase 5 week-test storage root")
    phase5_sub = phase5_parser.add_subparsers(dest="phase5_command", required=True)
    phase5_receive = phase5_sub.add_parser("receive", help="Start local phase 5 HTTP receiver")
    phase5_receive.add_argument("--host", default="127.0.0.1")
    phase5_receive.add_argument("--port", type=int, default=18787)
    phase5_receive.add_argument("--upload-token", help="Require this token in ?token=, X-Joanna-Phase5-Token, or Bearer auth")
    phase5_upload = phase5_sub.add_parser("upload", help="Register a local simulated segment upload")
    phase5_upload.add_argument("--audio", required=True)
    phase5_upload.add_argument("--gps")
    phase5_upload.add_argument("--metadata", help="Path to metadata JSON")
    phase5_upload.add_argument("--metadata-json", help="Inline metadata JSON")
    phase5_segments = phase5_sub.add_parser("segments", help="List or explain captured segments")
    phase5_segments_sub = phase5_segments.add_subparsers(dest="phase5_segments_command", required=True)
    phase5_segments_list = phase5_segments_sub.add_parser("list", help="List captured segments")
    phase5_segments_list.add_argument("--limit", type=int, default=50)
    phase5_segments_explain = phase5_segments_sub.add_parser("explain", help="Explain one captured segment")
    phase5_segments_explain.add_argument("segment_id")
    phase5_process = phase5_sub.add_parser("process", help="Process one captured segment with a configured real audio processor")
    phase5_process.add_argument("segment_id")
    _add_phase5_process_options(phase5_process)
    phase5_process_pending = phase5_sub.add_parser("process-pending", help="Process pending captured segments with Qwen Omni")
    phase5_process_pending.add_argument("--limit", type=int, default=12)
    _add_phase5_process_options(phase5_process_pending)
    phase5_derive = phase5_sub.add_parser("derive", help="Derive Joanna evidence events from one segment")
    phase5_derive.add_argument("segment_id")
    phase5_reflect = phase5_sub.add_parser("reflect", help="Build offline reflection report for segment feedback")
    phase5_reflect.add_argument("segment_id")
    phase5_reflect.add_argument("--feedback-id")

    args = parser.parse_args()
    if args.command == "phase5" and args.db == DEFAULT_DB:
        args.db = str(default_phase5_db(args.root))
    memory = JoannaMemory(args.db)
    try:
        try:
            _dispatch(args, memory)
        except Exception as exc:
            if args.debug:
                traceback.print_exc()
            else:
                print(f"错误：{_friendly_error(exc)}", file=sys.stderr)
            raise SystemExit(1) from exc
    finally:
        memory.close()


def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    if "DashScope API key" in message:
        return message
    if "api key" in message.lower():
        return f"{message} 默认推理会调用 LLM；如需离线规则路径，请加 --no-llm。"
    return message


def _add_phase5_process_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=QWEN_OMNI_DEFAULT_MODEL)
    parser.add_argument("--region", choices=["beijing", "singapore"], default="beijing")
    parser.add_argument("--slice-seconds", type=int, default=60)
    parser.add_argument("--max-slices-per-segment", type=int, default=3)
    parser.add_argument("--sample-mode", choices=["representative"], default="representative")
    parser.add_argument("--derive", action="store_true")


def _dispatch(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.command == "ingest":
        count = ingest_jsonl(memory, args.jsonl_path)
        print(f"已写入 {count} 条个人经验事件：{args.jsonl_path}")
        return

    if args.command == "events":
        _events(args, memory)
        return

    if args.command == "features":
        _features(args, memory)
        return

    if args.command == "insight":
        insight = build_daily_state(memory, args.date, use_llm=not args.no_llm)
        _print_insight(insight.to_dict(), as_json=args.json)
        return

    if args.command == "review":
        if args.review_command == "event":
            insight = build_event_review(memory, args.event_id, use_llm=not args.no_llm)
        elif args.review_command == "period":
            insight = build_period_review(
                memory,
                args.start_date,
                args.end_date,
                use_llm=not args.no_llm,
                allow_huge=args.allow_huge,
            )
        else:
            raise SystemExit(f"unknown review command: {args.review_command}")
        _print_insight(insight.to_dict(), as_json=args.json)
        return

    if args.command == "suggest":
        insight = build_reminder(memory, args.date, use_llm=not args.no_llm)
        _print_insight(insight.to_dict(), as_json=args.json)
        return

    if args.command == "correct":
        correction = record_correction(
            memory,
            target_layer=args.target_type,
            target_id=args.target_id,
            text=args.text,
            original=args.original,
        )
        print("已记录为用户反馈事件；原推理和反馈会并存进入后续推理。")
        print(json.dumps(correction.to_dict(), ensure_ascii=False, indent=2))
        return

    if args.command == "feedback":
        _feedback(args, memory)
        return

    if args.command == "claims":
        _claims(args, memory)
        return

    if args.command == "conflicts":
        _conflicts(args, memory)
        return

    if args.command == "profiles":
        _profiles(args, memory)
        return

    if args.command == "corrections":
        rows = [
            item.to_dict()
            for item in memory.list_corrections(
                target_layer=args.target_type,
                target_id=args.target_id,
            )
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if args.command == "summaries":
        _summaries(args, memory)
        return

    if args.command == "audit":
        _audit(args, memory)
        return

    if args.command == "llm":
        _llm(args, memory)
        return

    if args.command == "rules":
        _rules(args, memory)
        return

    if args.command == "evolution":
        _evolution(args, memory)
        return

    if args.command == "phase5":
        _phase5(args, memory)
        return

    if args.command == "web":
        from joanna.app.web import serve

        serve(memory.path, host=args.host, port=args.port)
        return

    raise SystemExit(f"unknown command: {args.command}")


def _events(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.events_command == "list":
        events = memory.query_events(
            date=args.date,
            event_type=args.type,
            person=args.person,
            scene=args.scene,
            include_disabled=args.include_disabled,
        )
        print(json.dumps([event.to_dict() for event in events], ensure_ascii=False, indent=2))
        return
    if args.events_command == "disable":
        memory.disable_event(args.event_id)
        print(f"维护命令已禁用事件：{args.event_id}")
        return
    if args.events_command == "delete":
        memory.delete_event(args.event_id)
        print(f"维护命令已标记删除事件：{args.event_id}")
        return
    if args.events_command == "revoke-profile-use":
        memory.revoke_event_profile_usage(args.event_id)
        print(f"维护命令已撤回事件画像使用权限：{args.event_id}")
        return
    raise SystemExit(f"unknown events command: {args.events_command}")


def _features(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.features_command == "list":
        events = memory.query_events(date=args.date)
        features = extract_features(events, memory=memory)
        print(json.dumps([feature.to_dict() for feature in features], ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown features command: {args.features_command}")


def _feedback(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.feedback_command == "record":
        feedback = record_feedback(
            memory,
            target_type=args.target_type,
            target_id=args.target_id,
            text=args.text,
            feedback_type=args.type,
        )
        print("已记录反馈事件；这不是最终裁决，会和原推理一起进入后续上下文。")
        print(json.dumps(feedback.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.feedback_command == "list":
        rows = [
            item.to_dict()
            for item in memory.list_feedback_events(
                target_type=args.target_type,
                target_id=args.target_id,
                feedback_type=args.type,
                limit=args.limit,
            )
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.feedback_command == "explain":
        feedback = memory.get_feedback_event(args.feedback_id)
        if not feedback:
            raise SystemExit(f"feedback event not found: {args.feedback_id}")
        payload = feedback.to_dict()
        payload["conflict_bundles"] = [
            item.to_dict()
            for item in memory.list_conflict_bundles(feedback_event_id=feedback.id)
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown feedback command: {args.feedback_command}")


def _claims(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.claims_command == "list":
        rows = [
            item.to_dict()
            for item in memory.list_inference_claims(
                subject_type=args.subject_type,
                subject_id=args.subject_id,
                insight_id=args.insight_id,
                limit=args.limit,
            )
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.claims_command == "explain":
        claim = memory.get_inference_claim(args.claim_id)
        if not claim:
            raise SystemExit(f"inference claim not found: {args.claim_id}")
        payload = claim.to_dict()
        payload["conflict_bundles"] = [
            item.to_dict()
            for item in memory.list_conflict_bundles(claim_id=claim.id)
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown claims command: {args.claims_command}")


def _conflicts(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.conflicts_command == "list":
        rows = [
            item.to_dict()
            for item in memory.list_conflict_bundles(status=args.status, limit=args.limit)
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.conflicts_command == "explain":
        bundle = memory.get_conflict_bundle(args.conflict_id)
        if not bundle:
            raise SystemExit(f"conflict bundle not found: {args.conflict_id}")
        payload = bundle.to_dict()
        payload["claims"] = [
            claim.to_dict()
            for claim_id in bundle.claim_ids
            if (claim := memory.get_inference_claim(claim_id)) is not None
        ]
        payload["feedback_events"] = [
            feedback.to_dict()
            for feedback_id in bundle.feedback_event_ids
            if (feedback := memory.get_feedback_event(feedback_id)) is not None
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown conflicts command: {args.conflicts_command}")


def _profiles(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.profiles_command == "list":
        rows = [item.to_dict() for item in memory.list_profiles(include_revoked=args.include_revoked)]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.profiles_command == "explain":
        profile = memory.get_profile(args.profile_id)
        if not profile:
            raise SystemExit(f"profile not found: {args.profile_id}")
        payload = profile.to_dict()
        payload["versions"] = [item.to_dict() for item in memory.list_profile_versions(args.profile_id)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.profiles_command == "history":
        rows = [item.to_dict() for item in memory.list_profile_versions(args.profile_id)]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.profiles_command == "confirm":
        profile = memory.confirm_profile(args.profile_id)
        if not profile:
            raise SystemExit(f"profile not found: {args.profile_id}")
        print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.profiles_command == "revoke":
        memory.revoke_profile(args.profile_id)
        print(f"维护命令已撤回画像：{args.profile_id}")
        return
    raise SystemExit(f"unknown profiles command: {args.profiles_command}")


def _audit(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.audit_command in {"list", "export"}:
        rows = [
            item.to_dict()
            for item in memory.list_audit_records(
                action=args.action,
                target_type=args.target_type,
                target_id=args.target_id,
                limit=args.limit,
            )
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown audit command: {args.audit_command}")


def _summaries(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.summaries_command == "build":
        rows = [
            item.to_dict()
            for item in build_memory_summaries(memory, args.start_date, args.end_date)
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.summaries_command == "list":
        rows = [
            item.to_dict()
            for item in memory.list_memory_summaries(
                status=args.status,
                summary_type=args.type,
            )
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.summaries_command == "explain":
        summary = memory.get_memory_summary(args.summary_id)
        if not summary:
            raise SystemExit(f"summary not found: {args.summary_id}")
        events = [
            event.to_dict()
            for event_id in summary.source_event_ids
            if (event := memory.get_event(event_id, include_deleted=True)) is not None
        ]
        print(
            json.dumps(
                {
                    "summary": summary.to_dict(),
                    "source_events": events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    raise SystemExit(f"unknown summaries command: {args.summaries_command}")


def _llm(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.llm_command == "calls" and args.llm_calls_command == "list":
        rows = [item.to_dict() for item in memory.list_llm_calls(limit=args.limit)]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.llm_command == "calls" and args.llm_calls_command == "explain":
        call = memory.get_llm_call(args.call_id)
        if not call:
            raise SystemExit(f"llm call not found: {args.call_id}")
        print(json.dumps(call.to_dict(), ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown llm command: {args.llm_command}")


def _rules(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.rules_command == "list":
        rows = [item.to_dict() for item in memory.list_semantic_rules(include_inactive=args.include_inactive)]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.rules_command == "explain":
        rule = memory.get_semantic_rule(args.rule_id, include_inactive=True)
        if not rule:
            raise SystemExit(f"rule not found: {args.rule_id}")
        payload = rule.to_dict()
        payload["versions"] = [item.to_dict() for item in memory.list_rule_versions(args.rule_id)]
        payload["applications"] = [item.to_dict() for item in memory.list_rule_applications(args.rule_id)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.rules_command == "history":
        rows = [item.to_dict() for item in memory.list_rule_versions(args.rule_id)]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.rules_command == "disable":
        rule = memory.disable_semantic_rule(args.rule_id)
        print(json.dumps(rule.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.rules_command == "rollback":
        rule = memory.rollback_semantic_rule(args.rule_id, args.to_version)
        print(json.dumps(rule.to_dict(), ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown rules command: {args.rules_command}")


def _evolution(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.evolution_command == "list":
        rows = [
            item.to_dict()
            for item in memory.list_evolution_proposals(include_rejected=args.include_rejected)
        ]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.evolution_command == "approve":
        proposal = approve_proposal(memory, args.proposal_id)
        print(json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.evolution_command == "reject":
        proposal = reject_proposal(memory, args.proposal_id)
        print(json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown evolution command: {args.evolution_command}")


def _phase5(args: argparse.Namespace, memory: JoannaMemory) -> None:
    if args.phase5_command == "receive":
        from joanna.app.phase5_receiver import serve_phase5_receiver

        serve_phase5_receiver(
            db_path=memory.path,
            root=args.root,
            host=args.host,
            port=args.port,
            upload_token=args.upload_token,
        )
        return
    if args.phase5_command == "upload":
        metadata = _load_phase5_metadata(args.metadata, args.metadata_json)
        segment = receive_segment_from_files(
            memory,
            root=args.root,
            audio_path=args.audio,
            gps_path=args.gps,
            metadata=metadata,
        )
        print(json.dumps(segment.to_dict(), ensure_ascii=False, indent=2))
        return
    if args.phase5_command == "segments":
        if args.phase5_segments_command == "list":
            print(json.dumps(list_segments(memory, limit=args.limit), ensure_ascii=False, indent=2))
            return
        if args.phase5_segments_command == "explain":
            segment = get_segment(memory, args.segment_id)
            if not segment:
                raise SystemExit(f"audio segment not found: {args.segment_id}")
            print(json.dumps(segment, ensure_ascii=False, indent=2))
            return
    if args.phase5_command == "process":
        processor = _phase5_qwen_processor(args)
        processed = process_segment(memory, args.segment_id, processor)
        payload: dict[str, Any] = {"segment_id": args.segment_id, "processed": processed}
        if args.derive:
            payload["derived_events"] = [event.to_dict() for event in derive_events(memory, args.segment_id)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.phase5_command == "process-pending":
        processor = _phase5_qwen_processor(args)
        rows = memory.connection.execute(
            """
            select id from audio_segments
            where processing_status in ('pending', 'pending_real_audio_processor')
            order by started_at asc, id asc
            limit ?
            """,
            (max(1, args.limit),),
        ).fetchall()
        results = []
        for row in rows:
            segment_id = row["id"]
            processed = process_segment(memory, segment_id, processor)
            item: dict[str, Any] = {"segment_id": segment_id, "processed": processed}
            if args.derive:
                item["derived_events"] = [event.to_dict() for event in derive_events(memory, segment_id)]
            results.append(item)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if args.phase5_command == "derive":
        events = derive_events(memory, args.segment_id)
        print(json.dumps([event.to_dict() for event in events], ensure_ascii=False, indent=2))
        return
    if args.phase5_command == "reflect":
        report = build_reflection_report(memory, args.segment_id, feedback_id=args.feedback_id)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown phase5 command: {args.phase5_command}")


def _load_phase5_metadata(metadata_path: str | None, metadata_json: str | None) -> dict[str, Any]:
    if metadata_path and metadata_json:
        raise ValueError("use only one of --metadata or --metadata-json")
    if metadata_path:
        return json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    if metadata_json:
        return json.loads(metadata_json)
    raise ValueError("phase5 upload requires --metadata or --metadata-json")


def _phase5_qwen_processor(args: argparse.Namespace) -> QwenOmniAudioProcessor:
    return QwenOmniAudioProcessor(
        model=args.model,
        region=args.region,
        root=args.root,
        slice_seconds=args.slice_seconds,
        max_slices=args.max_slices_per_segment,
        sample_mode=args.sample_mode,
    )


def _print_insight(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"标题：{payload['title']}")
    print(f"类型：{payload['insight_type']}")
    print(f"置信度：{payload['confidence']:.0%}")
    print()
    print(payload["body"])
    print()
    if payload.get("semantic_observations"):
        print("语义观察：")
        for observation in payload["semantic_observations"]:
            evidence_ids = "、".join(item["event_id"] for item in observation["evidence"])
            alternatives = "、".join(observation.get("alternatives", [])) or "需要继续确认"
            print(
                f"- {observation['observation_type']}：{observation['text']}"
                f"（证据 {evidence_ids}，替代解释：{alternatives}）"
            )
        print()
    print("证据：")
    for item in payload["evidence"]:
        print(f"- {item['event_id']}: {item['summary']}（置信度 {item['confidence']:.0%}）")
    print()
    print("替代解释：")
    for item in payload["alternatives"]:
        print(f"- {item}")
    print()
    print("治理边界：")
    for item in payload["governance_notes"]:
        print(f"- {item}")
    print()
    print(f"反馈入口：{payload['correction_prompt']}")


if __name__ == "__main__":
    main()
