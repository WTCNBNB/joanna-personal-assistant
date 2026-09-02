from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from joanna.core.phase5 import PHASE5_ROOT, default_phase5_db


DEFAULT_IMPORT_DIR = PHASE5_ROOT / "imports" / "2026-06-25-dji-health-align"
DEFAULT_KNOWN_GAP_START = "2026-06-24T23:58:26.895000+08:00"
DEFAULT_KNOWN_GAP_END = "2026-06-25T08:42:17+08:00"


@dataclass(frozen=True)
class Phase5VerificationExpectations:
    audio_segments: int = 12
    audio_transcripts: int = 12
    audio_features: int = 12
    active_audio_scene: int = 12
    apple_health_events: int = 4449
    qwen_audits: int = 12
    qwen_slice_count: int = 36
    qwen_region: str = "beijing"
    known_gap_start: str = DEFAULT_KNOWN_GAP_START
    known_gap_end: str = DEFAULT_KNOWN_GAP_END


def verify_phase5_weektest(
    *,
    db_path: str | Path = default_phase5_db(),
    import_dir: str | Path = DEFAULT_IMPORT_DIR,
    expectations: Phase5VerificationExpectations | None = None,
) -> dict[str, Any]:
    expected = expectations or Phase5VerificationExpectations()
    db = Path(db_path)
    imports = Path(import_dir)
    checks: dict[str, Any] = {
        "db_path": str(db),
        "import_dir": str(imports),
        "expected": expected.__dict__,
    }
    issues: list[str] = []

    if not db.exists():
        return _result(checks, [f"phase5 database not found: {db}"])

    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        _collect_counts(connection, checks)
        _collect_health_alignment(connection, checks)
        _collect_qwen_audit(connection, checks, expected.qwen_region)
        _collect_timeline(connection, checks)

    _collect_import_files(imports, checks, expected)
    _validate_checks(checks, expected, issues)
    return _result(checks, issues)


def _collect_counts(connection: sqlite3.Connection, checks: dict[str, Any]) -> None:
    row = connection.execute(
        """
        select
            (select count(*) from audio_segments) as audio_segments,
            (select count(*) from audio_segments where processing_status = 'processed') as processed_audio_segments,
            (select count(*) from audio_transcripts) as audio_transcripts,
            (select count(*) from audio_features) as audio_features,
            (select count(*) from audio_features where processor = 'qwen_omni_audio' and sent_external = 1) as qwen_audio_features,
            (select count(*) from audio_features where processor = 'fake_audio_processor') as fake_audio_features,
            (select count(*) from audio_transcripts where processor = 'fake_audio_processor') as fake_audio_transcripts,
            (select count(*) from events where event_type = 'audio_scene' and disabled = 0 and deleted = 0) as active_audio_scene,
            (select count(*) from events where event_type = 'audio_scene' and disabled = 0 and deleted = 0 and allow_profile != 0) as profile_allowed_audio_scene,
            (select count(*) from events where event_type like 'apple_health_%' and disabled = 0 and deleted = 0) as apple_health_events,
            (select count(*) from events where event_type like 'health_%' and disabled = 0 and deleted = 0) as legacy_health_prefix_events,
            (select count(*) from llm_calls) as llm_calls
        """
    ).fetchone()
    checks["counts"] = dict(row)


def _collect_health_alignment(connection: sqlite3.Connection, checks: dict[str, Any]) -> None:
    row = connection.execute(
        """
        select
            min(occurred_at) as min_occurred_at,
            max(occurred_at) as max_occurred_at,
            sum(case when json_extract(content_json, '$.audio_overlap') = 1 then 1 else 0 end) as audio_overlap_true,
            sum(case when json_extract(content_json, '$.audio_overlap') = 0 then 1 else 0 end) as audio_overlap_false,
            sum(case when json_array_length(json_extract(content_json, '$.overlap_audio_segment_ids')) > 0 then 1 else 0 end) as overlap_ids_present,
            sum(case when json_extract(content_json, '$.audio_overlap') = 1 and json_array_length(json_extract(content_json, '$.overlap_audio_segment_ids')) = 0 then 1 else 0 end) as overlap_true_without_ids,
            sum(case when json_extract(content_json, '$.audio_overlap') = 0 and json_array_length(json_extract(content_json, '$.overlap_audio_segment_ids')) > 0 then 1 else 0 end) as overlap_false_with_ids
        from events
        where event_type like 'apple_health_%' and disabled = 0 and deleted = 0
        """
    ).fetchone()
    checks["health_alignment"] = dict(row)


def _collect_qwen_audit(connection: sqlite3.Connection, checks: dict[str, Any], region: str) -> None:
    row = connection.execute(
        """
        select
            count(*) as qwen_audits,
            coalesce(sum(json_extract(payload_json, '$.slice_count')), 0) as qwen_slice_count,
            coalesce(sum(json_extract(payload_json, '$.usage.total_tokens')), 0) as qwen_total_tokens,
            min(created_at) as first_qwen_audit_at,
            max(created_at) as last_qwen_audit_at
        from audit_records
        where action = 'audio_segment_processed'
          and json_extract(payload_json, '$.processor') = 'qwen_omni_audio'
          and json_extract(payload_json, '$.sent_external') = 1
          and json_extract(payload_json, '$.external_payload_type') = 'audio_slice_base64'
          and json_extract(payload_json, '$.region') = ?
        """,
        (region,),
    ).fetchone()
    checks["qwen_audit"] = dict(row)


def _collect_timeline(connection: sqlite3.Connection, checks: dict[str, Any]) -> None:
    row = connection.execute(
        "select min(started_at) as audio_start, max(ended_at) as audio_end from audio_segments"
    ).fetchone()
    checks["timeline"] = dict(row)


def _collect_import_files(import_dir: Path, checks: dict[str, Any], expected: Phase5VerificationExpectations) -> None:
    report = import_dir / "timeline_alignment_report.md"
    health_jsonl = import_dir / "apple_health_events_20260624_182949_to_20260625_090438.jsonl"
    checks["import_files"] = {
        "timeline_alignment_report_exists": report.exists(),
        "health_jsonl_exists": health_jsonl.exists(),
        "health_jsonl_rows": _count_lines(health_jsonl) if health_jsonl.exists() else None,
        "known_gap_in_report": _report_contains(report, [expected.known_gap_start, expected.known_gap_end]),
        "manifest_exists": (import_dir / "import_manifest.json").exists(),
        "import_result_exists": (import_dir / "import_result.json").exists(),
        "fake_retraction_exists": (import_dir / "fake_audio_processor_retraction.json").exists(),
    }


def _validate_checks(
    checks: dict[str, Any],
    expected: Phase5VerificationExpectations,
    issues: list[str],
) -> None:
    counts = checks["counts"]
    health = checks["health_alignment"]
    qwen = checks["qwen_audit"]
    files = checks["import_files"]

    _expect_equal(issues, "audio_segments", counts["audio_segments"], expected.audio_segments)
    _expect_equal(issues, "processed_audio_segments", counts["processed_audio_segments"], expected.audio_segments)
    _expect_equal(issues, "audio_transcripts", counts["audio_transcripts"], expected.audio_transcripts)
    _expect_equal(issues, "audio_features", counts["audio_features"], expected.audio_features)
    _expect_equal(issues, "qwen_audio_features", counts["qwen_audio_features"], expected.audio_features)
    _expect_equal(issues, "active_audio_scene", counts["active_audio_scene"], expected.active_audio_scene)
    _expect_equal(issues, "apple_health_events", counts["apple_health_events"], expected.apple_health_events)
    _expect_equal(issues, "legacy_health_prefix_events", counts["legacy_health_prefix_events"], 0)
    _expect_equal(issues, "fake_audio_features", counts["fake_audio_features"], 0)
    _expect_equal(issues, "fake_audio_transcripts", counts["fake_audio_transcripts"], 0)
    _expect_equal(issues, "profile_allowed_audio_scene", counts["profile_allowed_audio_scene"], 0)
    _expect_equal(issues, "qwen_audits", qwen["qwen_audits"], expected.qwen_audits)
    _expect_equal(issues, "qwen_slice_count", qwen["qwen_slice_count"], expected.qwen_slice_count)
    _expect_equal(issues, "overlap_true_without_ids", health["overlap_true_without_ids"], 0)
    _expect_equal(issues, "overlap_false_with_ids", health["overlap_false_with_ids"], 0)

    if health["audio_overlap_true"] <= 0:
        issues.append("expected at least one Apple Health event overlapping audio")
    if health["audio_overlap_false"] <= 0:
        issues.append("expected at least one Apple Health event in the known audio gap")
    if health["audio_overlap_true"] + health["audio_overlap_false"] != expected.apple_health_events:
        issues.append("Apple Health audio_overlap true/false counts do not cover all health events")

    for key in [
        "timeline_alignment_report_exists",
        "health_jsonl_exists",
        "known_gap_in_report",
        "manifest_exists",
        "import_result_exists",
        "fake_retraction_exists",
    ]:
        if not files[key]:
            issues.append(f"missing or incomplete import artifact: {key}")


def _expect_equal(issues: list[str], name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        issues.append(f"{name}: expected {expected}, got {actual}")


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _report_contains(path: Path, expected_texts: list[str]) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return all(item in text for item in expected_texts)


def _result(checks: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    return {
        "ok": not issues,
        "issues": issues,
        "checks": checks,
    }


def dumps_verification(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
