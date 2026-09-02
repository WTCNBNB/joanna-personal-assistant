from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from joanna.core.schema import (
    AuditRecord,
    Correction,
    ConflictBundle,
    ConflictBundleStatus,
    Evidence,
    EvolutionProposal,
    ExperienceEvent,
    FeedbackEvent,
    InferenceClaim,
    InferenceClaimType,
    LLMCallRecord,
    MemorySummary,
    MemorySummaryStatus,
    ProfileClaim,
    ProfileStatus,
    ProfileVersion,
    RuleApplication,
    RuleVersion,
    SemanticRule,
    SemanticRuleStatus,
)


SCHEMA = """
create table if not exists events (
    id text primary key,
    occurred_at text not null,
    source_type text not null,
    source_id text not null,
    event_type text not null,
    summary text not null,
    content_json text not null,
    people_json text not null,
    scenes_json text not null,
    sensitivity text not null,
    allow_long_term integer not null,
    allow_profile integer not null,
    confidence real not null,
    evidence_refs_json text not null,
    disabled integer not null default 0,
    deleted integer not null default 0,
    profile_usage_revoked integer not null default 0
);

create table if not exists corrections (
    id text primary key,
    created_at text not null,
    target_layer text not null,
    target_id text not null,
    original text not null,
    correction text not null,
    effect text not null,
    requires_profile_revoke integer not null default 0
);

create table if not exists profiles (
    id text primary key,
    claim text not null,
    profile_type text not null,
    evidence_json text not null,
    created_at text not null,
    updated_at text not null,
    confidence real not null,
    user_confirmed integer not null default 0,
    user_corrected integer not null default 0,
    allowed_for_reasoning integer not null default 1,
    revoked integer not null default 0,
    deleted integer not null default 0
);

create table if not exists insights (
    id text primary key,
    created_at text not null,
    insight_type text not null,
    payload_json text not null
);

create table if not exists audit_records (
    id text primary key,
    created_at text not null,
    action text not null,
    target_type text not null,
    target_id text not null,
    summary text not null,
    payload_json text not null,
    event_ids_json text not null,
    profile_ids_json text not null,
    llm_call_id text
);

create table if not exists llm_calls (
    id text primary key,
    created_at text not null,
    task_type text not null,
    tier text not null,
    model text not null,
    max_tokens integer not null,
    timeout_seconds integer not null,
    prompt_bytes integer not null,
    event_ids_json text not null,
    profile_ids_json text not null,
    sent_external integer not null,
    status text not null,
    failure_type text,
    error_message text not null,
    attempts integer not null,
    response_bytes integer not null,
    feedback_event_ids_json text not null default '[]',
    inference_claim_ids_json text not null default '[]',
    conflict_bundle_ids_json text not null default '[]'
);

create table if not exists feedback_events (
    id text primary key,
    created_at text not null,
    feedback_type text not null,
    target_type text not null,
    target_id text not null,
    text text not null,
    source text not null,
    related_event_ids_json text not null,
    related_profile_ids_json text not null,
    related_rule_ids_json text not null,
    related_claim_ids_json text not null,
    metadata_json text not null
);

create table if not exists inference_claims (
    id text primary key,
    created_at text not null,
    claim_type text not null,
    subject_type text not null,
    subject_id text not null,
    text text not null,
    evidence_json text not null,
    confidence real not null,
    alternatives_json text not null,
    source text not null,
    insight_id text,
    llm_call_id text,
    feedback_event_ids_json text not null,
    conflict_bundle_ids_json text not null,
    metadata_json text not null
);

create table if not exists conflict_bundles (
    id text primary key,
    created_at text not null,
    updated_at text not null,
    status text not null,
    conflict_type text not null,
    summary text not null,
    claim_ids_json text not null,
    feedback_event_ids_json text not null,
    event_ids_json text not null,
    profile_ids_json text not null,
    rule_ids_json text not null,
    resolution_hint text not null,
    llm_call_id text,
    metadata_json text not null
);

create table if not exists memory_summaries (
    id text primary key,
    summary_type text not null,
    status text not null,
    title text not null,
    body text not null,
    time_range text not null,
    source_event_ids_json text not null,
    context_ids_json text not null,
    profile_ids_json text not null,
    created_at text not null,
    updated_at text not null,
    invalidated_by_event_id text
);

create table if not exists profile_versions (
    id text primary key,
    profile_id text not null,
    version integer not null,
    status text not null,
    claim text not null,
    profile_type text not null,
    evidence_json text not null,
    reason text not null,
    created_at text not null,
    updated_at text not null,
    supersedes_version integer
);

create table if not exists evolution_proposals (
    id text primary key,
    proposal_type text not null,
    status text not null,
    risk text not null,
    title text not null,
    rationale text not null,
    payload_json text not null,
    evidence_json text not null,
    created_at text not null,
    applied_at text
);

create table if not exists semantic_rules (
    id text primary key,
    rule_type text not null,
    status text not null,
    version integer not null,
    source text not null,
    created_by_llm_call_id text,
    match_spec_json text not null,
    output_spec_json text not null,
    evidence_event_ids_json text not null,
    confidence real not null,
    created_at text not null,
    updated_at text not null,
    supersedes_version integer,
    rollback_target integer
);

create table if not exists rule_versions (
    id text primary key,
    rule_id text not null,
    version integer not null,
    rule_type text not null,
    status text not null,
    source text not null,
    created_by_llm_call_id text,
    match_spec_json text not null,
    output_spec_json text not null,
    evidence_event_ids_json text not null,
    confidence real not null,
    reason text not null,
    created_at text not null,
    updated_at text not null,
    supersedes_version integer,
    rollback_target integer
);

create table if not exists rule_applications (
    id text primary key,
    rule_id text not null,
    rule_version integer not null,
    applied_at text not null,
    status text not null,
    event_ids_json text not null,
    output_json text not null,
    reason text not null,
    llm_call_id text
);

create table if not exists audio_files (
    id text primary key,
    segment_id text not null,
    device_id text not null,
    original_filename text not null,
    stored_path text not null,
    sha256 text not null,
    byte_size integer not null,
    duration_seconds real,
    sample_rate integer,
    channels integer,
    codec text,
    created_at text not null,
    metadata_json text not null
);

create table if not exists gps_tracks (
    id text primary key,
    segment_id text not null,
    started_at text not null,
    ended_at text not null,
    stored_path text not null,
    point_count integer not null,
    quality text not null,
    metadata_json text not null
);

create table if not exists audio_segments (
    id text primary key,
    audio_file_id text not null,
    gps_track_id text,
    device_id text not null,
    mic_label text not null,
    selected_audio_device_id text not null,
    selected_audio_device_name text not null,
    route_type text not null,
    actual_route_type text not null,
    route_warning text not null,
    started_at text not null,
    ended_at text not null,
    duration_seconds real not null,
    upload_attempt integer not null,
    received_at text not null,
    manifest_path text not null,
    status text not null,
    processing_status text not null,
    derived_event_ids_json text not null,
    metadata_json text not null
);

create table if not exists audio_transcripts (
    id text primary key,
    segment_id text not null,
    created_at text not null,
    processor text not null,
    model text not null,
    text text not null,
    confidence real not null,
    local_only integer not null,
    sent_external integer not null,
    metadata_json text not null
);

create table if not exists audio_features (
    id text primary key,
    segment_id text not null,
    created_at text not null,
    processor text not null,
    voice_activity text not null,
    scene_guess text not null,
    speaking_density text not null,
    background text not null,
    confidence real not null,
    sent_external integer not null,
    metadata_json text not null
);

create table if not exists capture_uploads (
    id text primary key,
    segment_id text,
    received_at text not null,
    source_ip text not null,
    upload_attempt integer not null,
    status text not null,
    error_message text not null,
    metadata_json text not null
);
"""


class JoannaMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_schema()

    def close(self) -> None:
        self.connection.close()

    def _migrate_schema(self) -> None:
        # Tables are created idempotently above; user_version records the latest
        # SQLite layout this process knows how to read.
        self._ensure_column("llm_calls", "feedback_event_ids_json", "text not null default '[]'")
        self._ensure_column("llm_calls", "inference_claim_ids_json", "text not null default '[]'")
        self._ensure_column("llm_calls", "conflict_bundle_ids_json", "text not null default '[]'")
        self.connection.execute("pragma user_version = 7")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.connection.execute(f"pragma table_info({table})").fetchall()
        if column in {row["name"] for row in rows}:
            return
        self.connection.execute(f"alter table {table} add column {column} {definition}")

    def upsert_event(self, event: ExperienceEvent) -> None:
        self.connection.execute(
            """
            insert into events (
                id, occurred_at, source_type, source_id, event_type, summary,
                content_json, people_json, scenes_json, sensitivity,
                allow_long_term, allow_profile, confidence, evidence_refs_json,
                disabled, deleted, profile_usage_revoked
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                occurred_at = excluded.occurred_at,
                source_type = excluded.source_type,
                source_id = excluded.source_id,
                event_type = excluded.event_type,
                summary = excluded.summary,
                content_json = excluded.content_json,
                people_json = excluded.people_json,
                scenes_json = excluded.scenes_json,
                sensitivity = excluded.sensitivity,
                allow_long_term = excluded.allow_long_term,
                allow_profile = excluded.allow_profile,
                confidence = excluded.confidence,
                evidence_refs_json = excluded.evidence_refs_json,
                disabled = excluded.disabled,
                deleted = excluded.deleted,
                profile_usage_revoked = excluded.profile_usage_revoked
            """,
            _event_row(event),
        )
        self.connection.commit()

    def get_event(self, event_id: str, include_deleted: bool = False) -> ExperienceEvent | None:
        if include_deleted:
            row = self.connection.execute("select * from events where id = ?", (event_id,)).fetchone()
        else:
            row = self.connection.execute(
                "select * from events where id = ? and deleted = 0",
                (event_id,),
            ).fetchone()
        return _event_from_row(row) if row else None

    def query_events(
        self,
        date: str | None = None,
        event_type: str | None = None,
        person: str | None = None,
        scene: str | None = None,
        include_disabled: bool = False,
        include_deleted: bool = False,
        profile_eligible_only: bool = False,
    ) -> list[ExperienceEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if date:
            clauses.append("substr(occurred_at, 1, 10) = ?")
            params.append(date)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if not include_disabled:
            clauses.append("disabled = 0")
        if not include_deleted:
            clauses.append("deleted = 0")
        if profile_eligible_only:
            clauses.append("allow_profile = 1")
            clauses.append("profile_usage_revoked = 0")

        sql = "select * from events"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by occurred_at asc"

        rows = self.connection.execute(sql, params).fetchall()
        events = [_event_from_row(row) for row in rows]
        if person:
            events = [event for event in events if person in event.people]
        if scene:
            events = [event for event in events if scene in event.scenes]
        return events

    def query_events_range(
        self,
        start_date: str,
        end_date: str,
        include_disabled: bool = False,
        include_deleted: bool = False,
    ) -> list[ExperienceEvent]:
        clauses = ["substr(occurred_at, 1, 10) >= ?", "substr(occurred_at, 1, 10) <= ?"]
        params: list[Any] = [start_date, end_date]
        if not include_disabled:
            clauses.append("disabled = 0")
        if not include_deleted:
            clauses.append("deleted = 0")
        sql = "select * from events where " + " and ".join(clauses) + " order by occurred_at asc"
        return [_event_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def derived_event_ids_for_audio_segment(self, segment_id: str) -> list[str]:
        row = self.connection.execute(
            "select derived_event_ids_json from audio_segments where id = ?",
            (segment_id,),
        ).fetchone()
        if row:
            return [str(item) for item in json.loads(row["derived_event_ids_json"])]
        rows = self.connection.execute(
            """
            select id from events
            where source_type = 'audio_capture'
              and (source_id = ? or content_json like ?)
              and deleted = 0
            order by occurred_at asc
            """,
            (segment_id, f"%{segment_id}%"),
        ).fetchall()
        return [str(row["id"]) for row in rows]

    def disable_event(self, event_id: str) -> None:
        self.connection.execute("update events set disabled = 1 where id = ?", (event_id,))
        self._record_audit_no_commit(
            action="event_disabled",
            target_type="event",
            target_id=event_id,
            summary=f"禁用事件：{event_id}",
            payload={},
            event_ids=[event_id],
            profile_ids=[],
            llm_call_id=None,
        )
        self._mark_summaries_for_event_no_commit(event_id, MemorySummaryStatus.NEEDS_RECOMPUTE)
        self._mark_profiles_for_event_no_commit(event_id, ProfileStatus.STALE)
        self.connection.commit()

    def delete_event(self, event_id: str) -> None:
        self.connection.execute("update events set deleted = 1 where id = ?", (event_id,))
        self._record_audit_no_commit(
            action="event_deleted",
            target_type="event",
            target_id=event_id,
            summary=f"删除事件：{event_id}",
            payload={},
            event_ids=[event_id],
            profile_ids=[],
            llm_call_id=None,
        )
        self._mark_summaries_for_event_no_commit(event_id, MemorySummaryStatus.NEEDS_RECOMPUTE)
        self._mark_profiles_for_event_no_commit(event_id, ProfileStatus.STALE)
        self.connection.commit()

    def revoke_event_profile_usage(self, event_id: str) -> None:
        self.connection.execute(
            "update events set profile_usage_revoked = 1 where id = ?",
            (event_id,),
        )
        self._record_audit_no_commit(
            action="event_profile_usage_revoked",
            target_type="event",
            target_id=event_id,
            summary=f"撤回事件画像使用权限：{event_id}",
            payload={},
            event_ids=[event_id],
            profile_ids=[],
            llm_call_id=None,
        )
        self._mark_summaries_for_event_no_commit(event_id, MemorySummaryStatus.NEEDS_RECOMPUTE)
        self._mark_profiles_for_event_no_commit(event_id, ProfileStatus.STALE)
        self.connection.commit()

    def add_correction(self, correction: Correction) -> None:
        self.connection.execute(
            """
            insert into corrections (
                id, created_at, target_layer, target_id, original, correction,
                effect, requires_profile_revoke
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                correction.id,
                correction.created_at.isoformat(),
                correction.target_layer,
                correction.target_id,
                correction.original,
                correction.correction,
                correction.effect,
                int(correction.requires_profile_revoke),
            ),
        )
        self._record_audit_no_commit(
            action="correction_recorded",
            target_type=correction.target_layer,
            target_id=correction.target_id,
            summary=f"记录兼容 correction，并转入反馈事件：{correction.target_layer}/{correction.target_id}",
            payload=correction.to_dict(),
            event_ids=[correction.target_id] if correction.target_layer == "event" else [],
            profile_ids=[correction.target_id] if correction.target_layer == "profile" else [],
            llm_call_id=None,
        )
        self.connection.commit()

    def list_corrections(
        self,
        target_layer: str | None = None,
        target_id: str | None = None,
    ) -> list[Correction]:
        clauses: list[str] = []
        params: list[Any] = []
        if target_layer:
            clauses.append("target_layer = ?")
            params.append(target_layer)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        sql = "select * from corrections"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by created_at asc, id asc"
        return [_correction_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def add_feedback_event(self, feedback: FeedbackEvent) -> None:
        self.connection.execute(
            """
            insert into feedback_events (
                id, created_at, feedback_type, target_type, target_id, text, source,
                related_event_ids_json, related_profile_ids_json, related_rule_ids_json,
                related_claim_ids_json, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _feedback_event_row(feedback),
        )
        self._record_audit_no_commit(
            action="feedback_event_recorded",
            target_type=feedback.target_type,
            target_id=feedback.target_id,
            summary=f"记录用户反馈事件：{feedback.feedback_type} -> {feedback.target_type}/{feedback.target_id}",
            payload=feedback.to_dict(),
            event_ids=feedback.related_event_ids,
            profile_ids=feedback.related_profile_ids,
            llm_call_id=None,
        )
        self.connection.commit()

    def get_feedback_event(self, feedback_id: str) -> FeedbackEvent | None:
        row = self.connection.execute("select * from feedback_events where id = ?", (feedback_id,)).fetchone()
        return _feedback_event_from_row(row) if row else None

    def list_feedback_events(
        self,
        target_type: str | None = None,
        target_id: str | None = None,
        feedback_type: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if target_type:
            clauses.append("target_type = ?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if feedback_type:
            clauses.append("feedback_type = ?")
            params.append(feedback_type)
        sql = "select * from feedback_events"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by created_at desc, id desc limit ?"
        params.append(max(1, limit))
        return [_feedback_event_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def upsert_inference_claim(self, claim: InferenceClaim) -> None:
        self._upsert_inference_claim_no_commit(claim)
        self._record_audit_no_commit(
            action="inference_claim_saved",
            target_type=claim.subject_type,
            target_id=claim.subject_id,
            summary=f"保存推理声明：{claim.claim_type} -> {claim.subject_type}/{claim.subject_id}",
            payload=claim.to_dict(),
            event_ids=[item.event_id for item in claim.evidence],
            profile_ids=[claim.subject_id] if claim.subject_type == "profile" else [],
            llm_call_id=claim.llm_call_id,
        )
        self.connection.commit()

    def _upsert_inference_claim_no_commit(self, claim: InferenceClaim) -> None:
        self.connection.execute(
            """
            insert into inference_claims (
                id, created_at, claim_type, subject_type, subject_id, text, evidence_json,
                confidence, alternatives_json, source, insight_id, llm_call_id,
                feedback_event_ids_json, conflict_bundle_ids_json, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                created_at = excluded.created_at,
                claim_type = excluded.claim_type,
                subject_type = excluded.subject_type,
                subject_id = excluded.subject_id,
                text = excluded.text,
                evidence_json = excluded.evidence_json,
                confidence = excluded.confidence,
                alternatives_json = excluded.alternatives_json,
                source = excluded.source,
                insight_id = excluded.insight_id,
                llm_call_id = excluded.llm_call_id,
                feedback_event_ids_json = excluded.feedback_event_ids_json,
                conflict_bundle_ids_json = excluded.conflict_bundle_ids_json,
                metadata_json = excluded.metadata_json
            """,
            _inference_claim_row(claim),
        )

    def get_inference_claim(self, claim_id: str) -> InferenceClaim | None:
        row = self.connection.execute("select * from inference_claims where id = ?", (claim_id,)).fetchone()
        return _inference_claim_from_row(row) if row else None

    def list_inference_claims(
        self,
        subject_type: str | None = None,
        subject_id: str | None = None,
        insight_id: str | None = None,
        llm_call_id: str | None = None,
        limit: int = 100,
    ) -> list[InferenceClaim]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_type:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if subject_id:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if insight_id:
            clauses.append("insight_id = ?")
            params.append(insight_id)
        if llm_call_id:
            clauses.append("llm_call_id = ?")
            params.append(llm_call_id)
        sql = "select * from inference_claims"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by created_at desc, id desc limit ?"
        params.append(max(1, limit))
        return [_inference_claim_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def upsert_conflict_bundle(self, bundle: ConflictBundle) -> None:
        self.connection.execute(
            """
            insert into conflict_bundles (
                id, created_at, updated_at, status, conflict_type, summary,
                claim_ids_json, feedback_event_ids_json, event_ids_json,
                profile_ids_json, rule_ids_json, resolution_hint, llm_call_id,
                metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                updated_at = excluded.updated_at,
                status = excluded.status,
                conflict_type = excluded.conflict_type,
                summary = excluded.summary,
                claim_ids_json = excluded.claim_ids_json,
                feedback_event_ids_json = excluded.feedback_event_ids_json,
                event_ids_json = excluded.event_ids_json,
                profile_ids_json = excluded.profile_ids_json,
                rule_ids_json = excluded.rule_ids_json,
                resolution_hint = excluded.resolution_hint,
                llm_call_id = excluded.llm_call_id,
                metadata_json = excluded.metadata_json
            """,
            _conflict_bundle_row(bundle),
        )
        self._record_audit_no_commit(
            action="conflict_bundle_saved",
            target_type="conflict_bundle",
            target_id=bundle.id,
            summary=f"保存冲突上下文：{bundle.summary}",
            payload=bundle.to_dict(),
            event_ids=bundle.event_ids,
            profile_ids=bundle.profile_ids,
            llm_call_id=bundle.llm_call_id,
        )
        self.connection.commit()

    def get_conflict_bundle(self, bundle_id: str) -> ConflictBundle | None:
        row = self.connection.execute("select * from conflict_bundles where id = ?", (bundle_id,)).fetchone()
        return _conflict_bundle_from_row(row) if row else None

    def list_conflict_bundles(
        self,
        status: str | None = None,
        feedback_event_id: str | None = None,
        claim_id: str | None = None,
        limit: int = 100,
    ) -> list[ConflictBundle]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        sql = "select * from conflict_bundles"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by updated_at desc, id desc limit ?"
        params.append(max(1, limit))
        rows = [_conflict_bundle_from_row(row) for row in self.connection.execute(sql, params).fetchall()]
        if feedback_event_id:
            rows = [row for row in rows if feedback_event_id in row.feedback_event_ids]
        if claim_id:
            rows = [row for row in rows if claim_id in row.claim_ids]
        return rows

    def update_conflict_bundle_resolution(self, bundle_id: str, resolution_hint: str, llm_call_id: str | None) -> None:
        bundle = self.get_conflict_bundle(bundle_id)
        if not bundle:
            return
        updated = ConflictBundle(
            id=bundle.id,
            created_at=bundle.created_at,
            updated_at=datetime.now(),
            status=ConflictBundleStatus.REVIEWED,
            conflict_type=bundle.conflict_type,
            summary=bundle.summary,
            claim_ids=bundle.claim_ids,
            feedback_event_ids=bundle.feedback_event_ids,
            event_ids=bundle.event_ids,
            profile_ids=bundle.profile_ids,
            rule_ids=bundle.rule_ids,
            resolution_hint=resolution_hint,
            llm_call_id=llm_call_id,
            metadata=bundle.metadata,
        )
        self.upsert_conflict_bundle(updated)

    def upsert_profile(self, profile: ProfileClaim) -> None:
        self.connection.execute(
            """
            insert into profiles (
                id, claim, profile_type, evidence_json, created_at, updated_at,
                confidence, user_confirmed, user_corrected, allowed_for_reasoning,
                revoked, deleted
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                claim = excluded.claim,
                profile_type = excluded.profile_type,
                evidence_json = excluded.evidence_json,
                updated_at = excluded.updated_at,
                confidence = excluded.confidence,
                user_confirmed = excluded.user_confirmed,
                user_corrected = max(profiles.user_corrected, excluded.user_corrected),
                allowed_for_reasoning = case
                    when profiles.revoked = 1 or excluded.revoked = 1 then 0
                    when profiles.user_corrected = 1 or excluded.user_corrected = 1 then 0
                    else excluded.allowed_for_reasoning
                end,
                revoked = max(profiles.revoked, excluded.revoked),
                deleted = max(profiles.deleted, excluded.deleted)
            """,
            (
                profile.id,
                profile.claim,
                profile.profile_type,
                json.dumps([item.to_dict() for item in profile.evidence], ensure_ascii=False),
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
                profile.confidence,
                int(profile.user_confirmed),
                int(profile.user_corrected),
                int(profile.allowed_for_reasoning),
                int(profile.revoked),
                int(profile.deleted),
            ),
        )
        row = self.connection.execute(
            "select * from profiles where id = ? and deleted = 0",
            (profile.id,),
        ).fetchone()
        if row:
            self._upsert_profile_version_no_commit(_profile_from_row(row), reason="profile_refreshed")
        self.connection.commit()

    def list_profiles(self, include_revoked: bool = False) -> list[ProfileClaim]:
        sql = "select * from profiles where deleted = 0"
        if not include_revoked:
            sql += " and revoked = 0 and allowed_for_reasoning = 1"
        sql += " order by updated_at desc, id asc"
        return [_profile_from_row(row) for row in self.connection.execute(sql).fetchall()]

    def get_profile(self, profile_id: str, include_revoked: bool = True) -> ProfileClaim | None:
        row = self.connection.execute(
            "select * from profiles where id = ? and deleted = 0",
            (profile_id,),
        ).fetchone()
        if not row:
            return None
        profile = _profile_from_row(row)
        if not include_revoked and (profile.revoked or not profile.allowed_for_reasoning):
            return None
        return profile

    def revoke_profile(self, profile_id: str) -> None:
        self.connection.execute(
            "update profiles set revoked = 1, allowed_for_reasoning = 0 where id = ?",
            (profile_id,),
        )
        self._set_profile_status_no_commit(
            profile_id,
            ProfileStatus.REVOKED,
            reason="用户撤回画像，后续推理不再使用该画像。",
        )
        self._record_audit_no_commit(
            action="profile_revoked",
            target_type="profile",
            target_id=profile_id,
            summary=f"撤回画像：{profile_id}",
            payload={},
            event_ids=[],
            profile_ids=[profile_id],
            llm_call_id=None,
        )
        self.connection.commit()

    def confirm_profile(self, profile_id: str) -> ProfileClaim | None:
        self.connection.execute(
            """
            update profiles
            set user_confirmed = 1,
                user_corrected = 0,
                allowed_for_reasoning = 1,
                revoked = 0,
                updated_at = ?
            where id = ? and deleted = 0
            """,
            (datetime.now().isoformat(timespec="seconds"), profile_id),
        )
        self._set_profile_status_no_commit(
            profile_id,
            ProfileStatus.ACTIVE,
            reason="用户确认画像可参与后续推理。",
        )
        self._record_audit_no_commit(
            action="profile_confirmed",
            target_type="profile",
            target_id=profile_id,
            summary=f"确认画像：{profile_id}",
            payload={},
            event_ids=[],
            profile_ids=[profile_id],
            llm_call_id=None,
        )
        self.connection.commit()
        return self.get_profile(profile_id, include_revoked=True)

    def list_profile_versions(self, profile_id: str) -> list[ProfileVersion]:
        rows = self.connection.execute(
            """
            select * from profile_versions
            where profile_id = ?
            order by version asc
            """,
            (profile_id,),
        ).fetchall()
        return [_profile_version_from_row(row) for row in rows]

    def save_insight(
        self,
        insight_type: str,
        insight_id: str,
        payload: dict[str, Any],
        *,
        event_ids: list[str] | None = None,
        profile_ids: list[str] | None = None,
        llm_call_id: str | None = None,
        used_llm: bool = False,
    ) -> None:
        self.connection.execute(
            """
            insert or replace into insights (id, created_at, insight_type, payload_json)
            values (?, ?, ?, ?)
            """,
            (
                insight_id,
                datetime.now().isoformat(timespec="seconds"),
                insight_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        if event_ids is None:
            event_ids = _event_ids_from_insight_payload(payload)
        if profile_ids is None:
            profile_ids = _profile_ids_from_insight_payload(payload)
        self._record_audit_no_commit(
            action="insight_saved",
            target_type="insight",
            target_id=insight_id,
            summary=f"保存 {insight_type} 洞察，使用 {len(event_ids)} 条事件证据和 {len(profile_ids)} 条画像。",
            payload={
                "insight_type": insight_type,
                "used_llm": used_llm,
            },
            event_ids=event_ids,
            profile_ids=profile_ids,
            llm_call_id=llm_call_id,
        )
        claims = _claims_from_insight_payload(
            insight_id=insight_id,
            payload=payload,
            source="llm" if used_llm else "offline",
            llm_call_id=llm_call_id,
        )
        for claim in claims:
            self._upsert_inference_claim_no_commit(claim)
        if llm_call_id and claims:
            self.connection.execute(
                "update llm_calls set inference_claim_ids_json = ? where id = ?",
                (json.dumps([claim.id for claim in claims], ensure_ascii=False), llm_call_id),
            )
        self.connection.commit()

    def record_audit(
        self,
        action: str,
        target_type: str,
        target_id: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        event_ids: list[str] | None = None,
        profile_ids: list[str] | None = None,
        llm_call_id: str | None = None,
    ) -> AuditRecord:
        record = self._record_audit_no_commit(
            action=action,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
            payload=payload or {},
            event_ids=event_ids or [],
            profile_ids=profile_ids or [],
            llm_call_id=llm_call_id,
        )
        self.connection.commit()
        return record

    def _record_audit_no_commit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        summary: str,
        payload: dict[str, Any],
        event_ids: list[str],
        profile_ids: list[str],
        llm_call_id: str | None,
    ) -> AuditRecord:
        record = AuditRecord(
            id=f"audit-{uuid4().hex[:12]}",
            created_at=datetime.now(),
            action=action,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
            payload=payload,
            event_ids=sorted(set(event_ids)),
            profile_ids=sorted(set(profile_ids)),
            llm_call_id=llm_call_id,
        )
        self.connection.execute(
            """
            insert into audit_records (
                id, created_at, action, target_type, target_id, summary,
                payload_json, event_ids_json, profile_ids_json, llm_call_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.created_at.isoformat(),
                record.action,
                record.target_type,
                record.target_id,
                record.summary,
                json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                json.dumps(record.event_ids, ensure_ascii=False),
                json.dumps(record.profile_ids, ensure_ascii=False),
                record.llm_call_id,
            ),
        )
        return record

    def list_audit_records(
        self,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if target_type:
            clauses.append("target_type = ?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        sql = "select * from audit_records"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by created_at desc, id desc limit ?"
        params.append(max(1, limit))
        return [_audit_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def save_llm_call(self, record: LLMCallRecord) -> None:
        self.connection.execute(
            """
            insert or replace into llm_calls (
                id, created_at, task_type, tier, model, max_tokens, timeout_seconds,
                prompt_bytes, event_ids_json, profile_ids_json, sent_external,
                status, failure_type, error_message, attempts, response_bytes,
                feedback_event_ids_json, inference_claim_ids_json, conflict_bundle_ids_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.created_at.isoformat(),
                record.task_type,
                record.tier,
                record.model,
                record.max_tokens,
                record.timeout_seconds,
                record.prompt_bytes,
                json.dumps(record.event_ids, ensure_ascii=False),
                json.dumps(record.profile_ids, ensure_ascii=False),
                int(record.sent_external),
                record.status,
                record.failure_type,
                record.error_message,
                record.attempts,
                record.response_bytes,
                json.dumps(record.feedback_event_ids, ensure_ascii=False),
                json.dumps(record.inference_claim_ids, ensure_ascii=False),
                json.dumps(record.conflict_bundle_ids, ensure_ascii=False),
            ),
        )
        self._record_audit_no_commit(
            action="llm_call_recorded",
            target_type="llm_call",
            target_id=record.id,
            summary=f"记录 LLM 调用：{record.task_type}/{record.tier}，状态 {record.status}。",
            payload={
                "model": record.model,
                "max_tokens": record.max_tokens,
                "timeout_seconds": record.timeout_seconds,
                "sent_external": record.sent_external,
                "failure_type": record.failure_type,
            },
            event_ids=record.event_ids,
            profile_ids=record.profile_ids,
            llm_call_id=record.id,
        )
        self.connection.commit()

    def list_llm_calls(self, limit: int = 50) -> list[LLMCallRecord]:
        rows = self.connection.execute(
            "select * from llm_calls order by created_at desc, id desc limit ?",
            (max(1, limit),),
        ).fetchall()
        return [_llm_call_from_row(row) for row in rows]

    def get_llm_call(self, call_id: str) -> LLMCallRecord | None:
        row = self.connection.execute("select * from llm_calls where id = ?", (call_id,)).fetchone()
        return _llm_call_from_row(row) if row else None

    def upsert_memory_summary(self, summary: MemorySummary) -> None:
        self.connection.execute(
            """
            insert into memory_summaries (
                id, summary_type, status, title, body, time_range,
                source_event_ids_json, context_ids_json, profile_ids_json,
                created_at, updated_at, invalidated_by_event_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                summary_type = excluded.summary_type,
                status = excluded.status,
                title = excluded.title,
                body = excluded.body,
                time_range = excluded.time_range,
                source_event_ids_json = excluded.source_event_ids_json,
                context_ids_json = excluded.context_ids_json,
                profile_ids_json = excluded.profile_ids_json,
                updated_at = excluded.updated_at,
                invalidated_by_event_id = excluded.invalidated_by_event_id
            """,
            (
                summary.id,
                summary.summary_type,
                summary.status,
                summary.title,
                summary.body,
                summary.time_range,
                json.dumps(summary.source_event_ids, ensure_ascii=False),
                json.dumps(summary.context_ids, ensure_ascii=False),
                json.dumps(summary.profile_ids, ensure_ascii=False),
                summary.created_at.isoformat(),
                summary.updated_at.isoformat(),
                summary.invalidated_by_event_id,
            ),
        )
        self._record_audit_no_commit(
            action="memory_summary_upserted",
            target_type="memory_summary",
            target_id=summary.id,
            summary=f"保存长期记忆摘要：{summary.title}",
            payload={
                "summary_type": summary.summary_type,
                "status": summary.status,
                "time_range": summary.time_range,
            },
            event_ids=summary.source_event_ids,
            profile_ids=summary.profile_ids,
            llm_call_id=None,
        )
        self.connection.commit()

    def list_memory_summaries(
        self,
        status: str | None = None,
        summary_type: str | None = None,
    ) -> list[MemorySummary]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if summary_type:
            clauses.append("summary_type = ?")
            params.append(summary_type)
        sql = "select * from memory_summaries"
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by updated_at desc, id asc"
        return [_summary_from_row(row) for row in self.connection.execute(sql, params).fetchall()]

    def get_memory_summary(self, summary_id: str) -> MemorySummary | None:
        row = self.connection.execute(
            "select * from memory_summaries where id = ?",
            (summary_id,),
        ).fetchone()
        return _summary_from_row(row) if row else None

    def mark_summaries_for_event(self, event_id: str, status: str = MemorySummaryStatus.NEEDS_RECOMPUTE) -> None:
        self._mark_summaries_for_event_no_commit(event_id, status)
        self.connection.commit()

    def _mark_summaries_for_event_no_commit(self, event_id: str, status: str) -> None:
        rows = self.connection.execute("select * from memory_summaries").fetchall()
        now = datetime.now().isoformat(timespec="seconds")
        for row in rows:
            source_event_ids = json.loads(row["source_event_ids_json"])
            if event_id not in source_event_ids:
                continue
            self.connection.execute(
                """
                update memory_summaries
                set status = ?, updated_at = ?, invalidated_by_event_id = ?
                where id = ?
                """,
                (status, now, event_id, row["id"]),
            )
            self._record_audit_no_commit(
                action="memory_summary_invalidated",
                target_type="memory_summary",
                target_id=row["id"],
                summary=f"事件治理变化导致摘要需要重算：{row['id']}",
                payload={"status": status, "invalidated_by_event_id": event_id},
                event_ids=[event_id],
                profile_ids=json.loads(row["profile_ids_json"]),
                llm_call_id=None,
            )

    def _mark_profiles_for_event_no_commit(self, event_id: str, status: str) -> None:
        rows = self.connection.execute("select * from profiles where deleted = 0").fetchall()
        for row in rows:
            evidence = [Evidence(**item) for item in json.loads(row["evidence_json"])]
            if event_id not in {item.event_id for item in evidence}:
                continue
            profile = _profile_from_row(row)
            self._set_profile_status_no_commit(
                profile.id,
                status,
                reason=f"源事件 {event_id} 的治理状态变化，画像需要复核。",
            )
            self._record_audit_no_commit(
                action="profile_marked_stale",
                target_type="profile",
                target_id=profile.id,
                summary=f"源事件治理变化导致画像需要复核：{profile.id}",
                payload={"status": status, "source_event_id": event_id},
                event_ids=[event_id],
                profile_ids=[profile.id],
                llm_call_id=None,
            )

    def _set_profile_status_no_commit(self, profile_id: str, status: str, reason: str) -> None:
        row = self.connection.execute(
            "select * from profiles where id = ? and deleted = 0",
            (profile_id,),
        ).fetchone()
        if not row:
            return
        if status == ProfileStatus.ACTIVE:
            self.connection.execute(
                """
                update profiles
                set user_confirmed = 1, user_corrected = 0,
                    allowed_for_reasoning = 1, revoked = 0, updated_at = ?
                where id = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), profile_id),
            )
        elif status == ProfileStatus.REVOKED:
            self.connection.execute(
                """
                update profiles
                set revoked = 1, allowed_for_reasoning = 0, updated_at = ?
                where id = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), profile_id),
            )
        elif status in {ProfileStatus.CORRECTED, ProfileStatus.STALE}:
            self.connection.execute(
                """
                update profiles
                set user_corrected = 1, allowed_for_reasoning = 0, updated_at = ?
                where id = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), profile_id),
            )
        refreshed = self.connection.execute(
            "select * from profiles where id = ? and deleted = 0",
            (profile_id,),
        ).fetchone()
        if refreshed:
            self._upsert_profile_version_no_commit(_profile_from_row(refreshed), reason=reason, forced_status=status)

    def _upsert_profile_version_no_commit(
        self,
        profile: ProfileClaim,
        reason: str,
        forced_status: str | None = None,
    ) -> None:
        latest = self.connection.execute(
            """
            select * from profile_versions
            where profile_id = ?
            order by version desc
            limit 1
            """,
            (profile.id,),
        ).fetchone()
        status = forced_status or self._status_for_profile(profile, latest)
        evidence_ids = [item.event_id for item in profile.evidence]
        if latest:
            latest_evidence_ids = [item.event_id for item in _profile_version_from_row(latest).evidence]
            if (
                latest["claim"] == profile.claim
                and latest["status"] == status
                and latest["profile_type"] == profile.profile_type
                and sorted(latest_evidence_ids) == sorted(evidence_ids)
            ):
                return
            version = int(latest["version"]) + 1
            supersedes_version = int(latest["version"])
        else:
            version = 1
            supersedes_version = None

        record = ProfileVersion(
            id=f"{profile.id}.v{version}",
            profile_id=profile.id,
            version=version,
            status=status,
            claim=profile.claim,
            profile_type=profile.profile_type,
            evidence=profile.evidence,
            reason=reason,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            supersedes_version=supersedes_version,
        )
        self.connection.execute(
            """
            insert into profile_versions (
                id, profile_id, version, status, claim, profile_type, evidence_json,
                reason, created_at, updated_at, supersedes_version
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.profile_id,
                record.version,
                record.status,
                record.claim,
                record.profile_type,
                json.dumps([item.to_dict() for item in record.evidence], ensure_ascii=False),
                record.reason,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
                record.supersedes_version,
            ),
        )
        self._record_audit_no_commit(
            action="profile_version_recorded",
            target_type="profile",
            target_id=profile.id,
            summary=f"记录画像版本：{profile.id} v{version} ({status})",
            payload={
                "version": version,
                "status": status,
                "reason": reason,
                "supersedes_version": supersedes_version,
            },
            event_ids=evidence_ids,
            profile_ids=[profile.id],
            llm_call_id=None,
        )

    def _status_for_profile(self, profile: ProfileClaim, latest: sqlite3.Row | None) -> str:
        if profile.revoked:
            return ProfileStatus.REVOKED
        if latest and latest["status"] in {ProfileStatus.STALE, ProfileStatus.CORRECTED} and not profile.allowed_for_reasoning:
            return str(latest["status"])
        if profile.user_corrected:
            return ProfileStatus.CORRECTED
        if profile.user_confirmed:
            return ProfileStatus.ACTIVE
        return ProfileStatus.CANDIDATE

    def upsert_evolution_proposal(self, proposal: EvolutionProposal) -> None:
        self.connection.execute(
            """
            insert into evolution_proposals (
                id, proposal_type, status, risk, title, rationale, payload_json,
                evidence_json, created_at, applied_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                proposal_type = excluded.proposal_type,
                status = case
                    when evolution_proposals.status in ('applied', 'rejected') then evolution_proposals.status
                    else excluded.status
                end,
                risk = excluded.risk,
                title = excluded.title,
                rationale = excluded.rationale,
                payload_json = excluded.payload_json,
                evidence_json = excluded.evidence_json,
                applied_at = coalesce(evolution_proposals.applied_at, excluded.applied_at)
            """,
            (
                proposal.id,
                proposal.proposal_type,
                proposal.status,
                proposal.risk,
                proposal.title,
                proposal.rationale,
                json.dumps(proposal.payload, ensure_ascii=False, sort_keys=True),
                json.dumps([item.to_dict() for item in proposal.evidence], ensure_ascii=False),
                proposal.created_at.isoformat(),
                proposal.applied_at.isoformat() if proposal.applied_at else None,
            ),
        )
        self.connection.commit()

    def list_evolution_proposals(self, include_rejected: bool = False) -> list[EvolutionProposal]:
        sql = "select * from evolution_proposals"
        if not include_rejected:
            sql += " where status != 'rejected'"
        sql += " order by created_at desc, id asc"
        return [_evolution_from_row(row) for row in self.connection.execute(sql).fetchall()]

    def get_evolution_proposal(self, proposal_id: str) -> EvolutionProposal | None:
        row = self.connection.execute(
            "select * from evolution_proposals where id = ?",
            (proposal_id,),
        ).fetchone()
        return _evolution_from_row(row) if row else None

    def set_evolution_status(self, proposal_id: str, status: str) -> None:
        applied_at = datetime.now().isoformat(timespec="seconds") if status == "applied" else None
        self.connection.execute(
            """
            update evolution_proposals
            set status = ?, applied_at = coalesce(applied_at, ?)
            where id = ?
            """,
            (status, applied_at, proposal_id),
        )
        self.connection.commit()

    def upsert_semantic_rule(self, rule: SemanticRule, reason: str = "semantic_rule_upserted") -> SemanticRule:
        existing = self.get_semantic_rule(rule.id, include_inactive=True)
        if existing:
            version = existing.version + 1
            supersedes_version = existing.version
            created_at = existing.created_at
        else:
            version = max(1, rule.version)
            supersedes_version = rule.supersedes_version
            created_at = rule.created_at
        now = datetime.now()
        refreshed = SemanticRule(
            id=rule.id,
            rule_type=rule.rule_type,
            status=rule.status,
            version=version,
            source=rule.source,
            created_by_llm_call_id=rule.created_by_llm_call_id,
            match_spec=rule.match_spec,
            output_spec=rule.output_spec,
            evidence_event_ids=rule.evidence_event_ids,
            confidence=rule.confidence,
            created_at=created_at,
            updated_at=now,
            supersedes_version=supersedes_version,
            rollback_target=rule.rollback_target,
        )
        self._write_semantic_rule_no_commit(refreshed)
        self._insert_rule_version_no_commit(refreshed, reason)
        self._record_audit_no_commit(
            action="semantic_rule_upserted",
            target_type="semantic_rule",
            target_id=refreshed.id,
            summary=f"保存运行时语义规则：{refreshed.id} v{refreshed.version}",
            payload=refreshed.to_dict(),
            event_ids=refreshed.evidence_event_ids,
            profile_ids=[],
            llm_call_id=refreshed.created_by_llm_call_id,
        )
        self.connection.commit()
        return refreshed

    def list_semantic_rules(self, include_inactive: bool = False) -> list[SemanticRule]:
        sql = "select * from semantic_rules"
        if not include_inactive:
            sql += " where status = 'active'"
        sql += " order by updated_at desc, id asc"
        return [_semantic_rule_from_row(row) for row in self.connection.execute(sql).fetchall()]

    def get_semantic_rule(self, rule_id: str, include_inactive: bool = False) -> SemanticRule | None:
        row = self.connection.execute("select * from semantic_rules where id = ?", (rule_id,)).fetchone()
        if not row:
            return None
        rule = _semantic_rule_from_row(row)
        if not include_inactive and rule.status != SemanticRuleStatus.ACTIVE:
            return None
        return rule

    def list_rule_versions(self, rule_id: str) -> list[RuleVersion]:
        rows = self.connection.execute(
            "select * from rule_versions where rule_id = ? order by version asc",
            (rule_id,),
        ).fetchall()
        return [_rule_version_from_row(row) for row in rows]

    def list_rule_applications(self, rule_id: str | None = None) -> list[RuleApplication]:
        if rule_id:
            rows = self.connection.execute(
                "select * from rule_applications where rule_id = ? order by applied_at desc, id desc",
                (rule_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "select * from rule_applications order by applied_at desc, id desc",
            ).fetchall()
        return [_rule_application_from_row(row) for row in rows]

    def disable_semantic_rule(self, rule_id: str) -> SemanticRule:
        current = self.get_semantic_rule(rule_id, include_inactive=True)
        if not current:
            raise ValueError(f"semantic rule not found: {rule_id}")
        updated = SemanticRule(
            id=current.id,
            rule_type=current.rule_type,
            status=SemanticRuleStatus.DISABLED,
            version=current.version + 1,
            source=current.source,
            created_by_llm_call_id=current.created_by_llm_call_id,
            match_spec=current.match_spec,
            output_spec=current.output_spec,
            evidence_event_ids=current.evidence_event_ids,
            confidence=current.confidence,
            created_at=current.created_at,
            updated_at=datetime.now(),
            supersedes_version=current.version,
            rollback_target=current.rollback_target,
        )
        self._write_semantic_rule_no_commit(updated)
        self._insert_rule_version_no_commit(updated, "user_disabled_rule")
        self._record_audit_no_commit(
            action="semantic_rule_disabled",
            target_type="semantic_rule",
            target_id=rule_id,
            summary=f"禁用运行时语义规则：{rule_id}",
            payload={"version": updated.version},
            event_ids=updated.evidence_event_ids,
            profile_ids=[],
            llm_call_id=None,
        )
        self.connection.commit()
        return updated

    def rollback_semantic_rule(self, rule_id: str, to_version: int) -> SemanticRule:
        current = self.get_semantic_rule(rule_id, include_inactive=True)
        if not current:
            raise ValueError(f"semantic rule not found: {rule_id}")
        target = self.connection.execute(
            "select * from rule_versions where rule_id = ? and version = ?",
            (rule_id, to_version),
        ).fetchone()
        if not target:
            raise ValueError(f"rule version not found: {rule_id} v{to_version}")
        target_version = _rule_version_from_row(target)
        updated = SemanticRule(
            id=current.id,
            rule_type=target_version.rule_type,
            status=SemanticRuleStatus.ACTIVE,
            version=current.version + 1,
            source=target_version.source,
            created_by_llm_call_id=target_version.created_by_llm_call_id,
            match_spec=target_version.match_spec,
            output_spec=target_version.output_spec,
            evidence_event_ids=target_version.evidence_event_ids,
            confidence=target_version.confidence,
            created_at=current.created_at,
            updated_at=datetime.now(),
            supersedes_version=current.version,
            rollback_target=to_version,
        )
        self._write_semantic_rule_no_commit(updated)
        self._insert_rule_version_no_commit(updated, f"rollback_to_v{to_version}")
        self._record_audit_no_commit(
            action="semantic_rule_rolled_back",
            target_type="semantic_rule",
            target_id=rule_id,
            summary=f"回滚运行时语义规则：{rule_id} -> v{to_version}",
            payload={"version": updated.version, "rollback_target": to_version},
            event_ids=updated.evidence_event_ids,
            profile_ids=[],
            llm_call_id=None,
        )
        self.connection.commit()
        return updated

    def record_rule_application(
        self,
        rule_id: str,
        event_ids: list[str],
        output: dict[str, Any],
        *,
        status: str = "applied",
        reason: str = "rule_applied",
        llm_call_id: str | None = None,
    ) -> RuleApplication:
        rule = self.get_semantic_rule(rule_id, include_inactive=True)
        if not rule:
            raise ValueError(f"semantic rule not found: {rule_id}")
        record = RuleApplication(
            id=f"ruleapp-{uuid4().hex[:12]}",
            rule_id=rule.id,
            rule_version=rule.version,
            applied_at=datetime.now(),
            status=status,
            event_ids=sorted(set(event_ids)),
            output=output,
            reason=reason,
            llm_call_id=llm_call_id,
        )
        self.connection.execute(
            """
            insert into rule_applications (
                id, rule_id, rule_version, applied_at, status, event_ids_json,
                output_json, reason, llm_call_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.rule_id,
                record.rule_version,
                record.applied_at.isoformat(),
                record.status,
                json.dumps(record.event_ids, ensure_ascii=False),
                json.dumps(record.output, ensure_ascii=False, sort_keys=True),
                record.reason,
                record.llm_call_id,
            ),
        )
        self._record_audit_no_commit(
            action="semantic_rule_application_recorded",
            target_type="semantic_rule",
            target_id=rule.id,
            summary=f"记录运行时语义规则应用：{rule.id} ({status})",
            payload=record.to_dict(),
            event_ids=record.event_ids,
            profile_ids=[],
            llm_call_id=record.llm_call_id,
        )
        self.connection.commit()
        return record

    def _write_semantic_rule_no_commit(self, rule: SemanticRule) -> None:
        self.connection.execute(
            """
            insert into semantic_rules (
                id, rule_type, status, version, source, created_by_llm_call_id,
                match_spec_json, output_spec_json, evidence_event_ids_json,
                confidence, created_at, updated_at, supersedes_version, rollback_target
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                rule_type = excluded.rule_type,
                status = excluded.status,
                version = excluded.version,
                source = excluded.source,
                created_by_llm_call_id = excluded.created_by_llm_call_id,
                match_spec_json = excluded.match_spec_json,
                output_spec_json = excluded.output_spec_json,
                evidence_event_ids_json = excluded.evidence_event_ids_json,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at,
                supersedes_version = excluded.supersedes_version,
                rollback_target = excluded.rollback_target
            """,
            _semantic_rule_row(rule),
        )

    def _insert_rule_version_no_commit(self, rule: SemanticRule, reason: str) -> None:
        version = RuleVersion(
            id=f"{rule.id}.v{rule.version}",
            rule_id=rule.id,
            version=rule.version,
            rule_type=rule.rule_type,
            status=rule.status,
            source=rule.source,
            created_by_llm_call_id=rule.created_by_llm_call_id,
            match_spec=rule.match_spec,
            output_spec=rule.output_spec,
            evidence_event_ids=rule.evidence_event_ids,
            confidence=rule.confidence,
            reason=reason,
            created_at=rule.updated_at,
            updated_at=rule.updated_at,
            supersedes_version=rule.supersedes_version,
            rollback_target=rule.rollback_target,
        )
        self.connection.execute(
            """
            insert or replace into rule_versions (
                id, rule_id, version, rule_type, status, source, created_by_llm_call_id,
                match_spec_json, output_spec_json, evidence_event_ids_json,
                confidence, reason, created_at, updated_at, supersedes_version, rollback_target
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _rule_version_row(version),
        )


def _event_row(event: ExperienceEvent) -> tuple[Any, ...]:
    return (
        event.id,
        event.occurred_at.isoformat(),
        event.source_type,
        event.source_id,
        event.event_type,
        event.summary,
        json.dumps(event.content, ensure_ascii=False, sort_keys=True),
        json.dumps(event.people, ensure_ascii=False),
        json.dumps(event.scenes, ensure_ascii=False),
        event.sensitivity,
        int(event.allow_long_term),
        int(event.allow_profile),
        event.confidence,
        json.dumps(event.evidence_refs, ensure_ascii=False),
        int(event.disabled),
        int(event.deleted),
        int(event.profile_usage_revoked),
    )


def _semantic_rule_row(rule: SemanticRule) -> tuple[Any, ...]:
    return (
        rule.id,
        rule.rule_type,
        rule.status,
        rule.version,
        rule.source,
        rule.created_by_llm_call_id,
        json.dumps(rule.match_spec, ensure_ascii=False, sort_keys=True),
        json.dumps(rule.output_spec, ensure_ascii=False, sort_keys=True),
        json.dumps(rule.evidence_event_ids, ensure_ascii=False),
        rule.confidence,
        rule.created_at.isoformat(),
        rule.updated_at.isoformat(),
        rule.supersedes_version,
        rule.rollback_target,
    )


def _rule_version_row(version: RuleVersion) -> tuple[Any, ...]:
    return (
        version.id,
        version.rule_id,
        version.version,
        version.rule_type,
        version.status,
        version.source,
        version.created_by_llm_call_id,
        json.dumps(version.match_spec, ensure_ascii=False, sort_keys=True),
        json.dumps(version.output_spec, ensure_ascii=False, sort_keys=True),
        json.dumps(version.evidence_event_ids, ensure_ascii=False),
        version.confidence,
        version.reason,
        version.created_at.isoformat(),
        version.updated_at.isoformat(),
        version.supersedes_version,
        version.rollback_target,
    )


def _feedback_event_row(feedback: FeedbackEvent) -> tuple[Any, ...]:
    return (
        feedback.id,
        feedback.created_at.isoformat(),
        feedback.feedback_type,
        feedback.target_type,
        feedback.target_id,
        feedback.text,
        feedback.source,
        json.dumps(feedback.related_event_ids, ensure_ascii=False),
        json.dumps(feedback.related_profile_ids, ensure_ascii=False),
        json.dumps(feedback.related_rule_ids, ensure_ascii=False),
        json.dumps(feedback.related_claim_ids, ensure_ascii=False),
        json.dumps(feedback.metadata, ensure_ascii=False, sort_keys=True),
    )


def _inference_claim_row(claim: InferenceClaim) -> tuple[Any, ...]:
    return (
        claim.id,
        claim.created_at.isoformat(),
        claim.claim_type,
        claim.subject_type,
        claim.subject_id,
        claim.text,
        json.dumps([item.to_dict() for item in claim.evidence], ensure_ascii=False),
        claim.confidence,
        json.dumps(claim.alternatives, ensure_ascii=False),
        claim.source,
        claim.insight_id,
        claim.llm_call_id,
        json.dumps(claim.feedback_event_ids, ensure_ascii=False),
        json.dumps(claim.conflict_bundle_ids, ensure_ascii=False),
        json.dumps(claim.metadata, ensure_ascii=False, sort_keys=True),
    )


def _conflict_bundle_row(bundle: ConflictBundle) -> tuple[Any, ...]:
    return (
        bundle.id,
        bundle.created_at.isoformat(),
        bundle.updated_at.isoformat(),
        bundle.status,
        bundle.conflict_type,
        bundle.summary,
        json.dumps(bundle.claim_ids, ensure_ascii=False),
        json.dumps(bundle.feedback_event_ids, ensure_ascii=False),
        json.dumps(bundle.event_ids, ensure_ascii=False),
        json.dumps(bundle.profile_ids, ensure_ascii=False),
        json.dumps(bundle.rule_ids, ensure_ascii=False),
        bundle.resolution_hint,
        bundle.llm_call_id,
        json.dumps(bundle.metadata, ensure_ascii=False, sort_keys=True),
    )


def _event_from_row(row: sqlite3.Row) -> ExperienceEvent:
    return ExperienceEvent(
        id=row["id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        source_type=row["source_type"],
        source_id=row["source_id"],
        event_type=row["event_type"],
        summary=row["summary"],
        content=json.loads(row["content_json"]),
        people=json.loads(row["people_json"]),
        scenes=json.loads(row["scenes_json"]),
        sensitivity=row["sensitivity"],
        allow_long_term=bool(row["allow_long_term"]),
        allow_profile=bool(row["allow_profile"]),
        confidence=float(row["confidence"]),
        evidence_refs=json.loads(row["evidence_refs_json"]),
        disabled=bool(row["disabled"]),
        deleted=bool(row["deleted"]),
        profile_usage_revoked=bool(row["profile_usage_revoked"]),
    )


def _correction_from_row(row: sqlite3.Row) -> Correction:
    return Correction(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        target_layer=row["target_layer"],
        target_id=row["target_id"],
        original=row["original"],
        correction=row["correction"],
        effect=row["effect"],
        requires_profile_revoke=bool(row["requires_profile_revoke"]),
    )


def _feedback_event_from_row(row: sqlite3.Row) -> FeedbackEvent:
    return FeedbackEvent(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        feedback_type=row["feedback_type"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        text=row["text"],
        source=row["source"],
        related_event_ids=json.loads(row["related_event_ids_json"]),
        related_profile_ids=json.loads(row["related_profile_ids_json"]),
        related_rule_ids=json.loads(row["related_rule_ids_json"]),
        related_claim_ids=json.loads(row["related_claim_ids_json"]),
        metadata=json.loads(row["metadata_json"]),
    )


def _inference_claim_from_row(row: sqlite3.Row) -> InferenceClaim:
    return InferenceClaim(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        claim_type=row["claim_type"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        text=row["text"],
        evidence=[Evidence(**item) for item in json.loads(row["evidence_json"])],
        confidence=float(row["confidence"]),
        alternatives=json.loads(row["alternatives_json"]),
        source=row["source"],
        insight_id=row["insight_id"],
        llm_call_id=row["llm_call_id"],
        feedback_event_ids=json.loads(row["feedback_event_ids_json"]),
        conflict_bundle_ids=json.loads(row["conflict_bundle_ids_json"]),
        metadata=json.loads(row["metadata_json"]),
    )


def _conflict_bundle_from_row(row: sqlite3.Row) -> ConflictBundle:
    return ConflictBundle(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        status=row["status"],
        conflict_type=row["conflict_type"],
        summary=row["summary"],
        claim_ids=json.loads(row["claim_ids_json"]),
        feedback_event_ids=json.loads(row["feedback_event_ids_json"]),
        event_ids=json.loads(row["event_ids_json"]),
        profile_ids=json.loads(row["profile_ids_json"]),
        rule_ids=json.loads(row["rule_ids_json"]),
        resolution_hint=row["resolution_hint"],
        llm_call_id=row["llm_call_id"],
        metadata=json.loads(row["metadata_json"]),
    )


def _profile_from_row(row: sqlite3.Row) -> ProfileClaim:
    evidence = [Evidence(**item) for item in json.loads(row["evidence_json"])]
    return ProfileClaim(
        id=row["id"],
        claim=row["claim"],
        profile_type=row["profile_type"],
        evidence=evidence,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        confidence=float(row["confidence"]),
        user_confirmed=bool(row["user_confirmed"]),
        user_corrected=bool(row["user_corrected"]),
        allowed_for_reasoning=bool(row["allowed_for_reasoning"]),
        revoked=bool(row["revoked"]),
        deleted=bool(row["deleted"]),
    )


def _profile_version_from_row(row: sqlite3.Row) -> ProfileVersion:
    evidence = [Evidence(**item) for item in json.loads(row["evidence_json"])]
    return ProfileVersion(
        id=row["id"],
        profile_id=row["profile_id"],
        version=int(row["version"]),
        status=row["status"],
        claim=row["claim"],
        profile_type=row["profile_type"],
        evidence=evidence,
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        supersedes_version=int(row["supersedes_version"]) if row["supersedes_version"] is not None else None,
    )


def _evolution_from_row(row: sqlite3.Row) -> EvolutionProposal:
    evidence = [Evidence(**item) for item in json.loads(row["evidence_json"])]
    applied_at = datetime.fromisoformat(row["applied_at"]) if row["applied_at"] else None
    return EvolutionProposal(
        id=row["id"],
        proposal_type=row["proposal_type"],
        status=row["status"],
        risk=row["risk"],
        title=row["title"],
        rationale=row["rationale"],
        payload=json.loads(row["payload_json"]),
        evidence=evidence,
        created_at=datetime.fromisoformat(row["created_at"]),
        applied_at=applied_at,
    )


def _audit_from_row(row: sqlite3.Row) -> AuditRecord:
    return AuditRecord(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        action=row["action"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        summary=row["summary"],
        payload=json.loads(row["payload_json"]),
        event_ids=json.loads(row["event_ids_json"]),
        profile_ids=json.loads(row["profile_ids_json"]),
        llm_call_id=row["llm_call_id"],
    )


def _llm_call_from_row(row: sqlite3.Row) -> LLMCallRecord:
    return LLMCallRecord(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        task_type=row["task_type"],
        tier=row["tier"],
        model=row["model"],
        max_tokens=int(row["max_tokens"]),
        timeout_seconds=int(row["timeout_seconds"]),
        prompt_bytes=int(row["prompt_bytes"]),
        event_ids=json.loads(row["event_ids_json"]),
        profile_ids=json.loads(row["profile_ids_json"]),
        sent_external=bool(row["sent_external"]),
        status=row["status"],
        failure_type=row["failure_type"],
        error_message=row["error_message"],
        attempts=int(row["attempts"]),
        response_bytes=int(row["response_bytes"]),
        feedback_event_ids=json.loads(row["feedback_event_ids_json"]),
        inference_claim_ids=json.loads(row["inference_claim_ids_json"]),
        conflict_bundle_ids=json.loads(row["conflict_bundle_ids_json"]),
    )


def _summary_from_row(row: sqlite3.Row) -> MemorySummary:
    return MemorySummary(
        id=row["id"],
        summary_type=row["summary_type"],
        status=row["status"],
        title=row["title"],
        body=row["body"],
        time_range=row["time_range"],
        source_event_ids=json.loads(row["source_event_ids_json"]),
        context_ids=json.loads(row["context_ids_json"]),
        profile_ids=json.loads(row["profile_ids_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        invalidated_by_event_id=row["invalidated_by_event_id"],
    )


def _semantic_rule_from_row(row: sqlite3.Row) -> SemanticRule:
    return SemanticRule(
        id=row["id"],
        rule_type=row["rule_type"],
        status=row["status"],
        version=int(row["version"]),
        source=row["source"],
        created_by_llm_call_id=row["created_by_llm_call_id"],
        match_spec=json.loads(row["match_spec_json"]),
        output_spec=json.loads(row["output_spec_json"]),
        evidence_event_ids=json.loads(row["evidence_event_ids_json"]),
        confidence=float(row["confidence"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        supersedes_version=int(row["supersedes_version"]) if row["supersedes_version"] is not None else None,
        rollback_target=int(row["rollback_target"]) if row["rollback_target"] is not None else None,
    )


def _rule_version_from_row(row: sqlite3.Row) -> RuleVersion:
    return RuleVersion(
        id=row["id"],
        rule_id=row["rule_id"],
        version=int(row["version"]),
        rule_type=row["rule_type"],
        status=row["status"],
        source=row["source"],
        created_by_llm_call_id=row["created_by_llm_call_id"],
        match_spec=json.loads(row["match_spec_json"]),
        output_spec=json.loads(row["output_spec_json"]),
        evidence_event_ids=json.loads(row["evidence_event_ids_json"]),
        confidence=float(row["confidence"]),
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        supersedes_version=int(row["supersedes_version"]) if row["supersedes_version"] is not None else None,
        rollback_target=int(row["rollback_target"]) if row["rollback_target"] is not None else None,
    )


def _rule_application_from_row(row: sqlite3.Row) -> RuleApplication:
    return RuleApplication(
        id=row["id"],
        rule_id=row["rule_id"],
        rule_version=int(row["rule_version"]),
        applied_at=datetime.fromisoformat(row["applied_at"]),
        status=row["status"],
        event_ids=json.loads(row["event_ids_json"]),
        output=json.loads(row["output_json"]),
        reason=row["reason"],
        llm_call_id=row["llm_call_id"],
    )


def _claims_from_insight_payload(
    *,
    insight_id: str,
    payload: dict[str, Any],
    source: str,
    llm_call_id: str | None,
) -> list[InferenceClaim]:
    created_at = datetime.now()
    claims: list[InferenceClaim] = []
    insight_type = str(payload.get("insight_type") or "")

    for index, item in enumerate(payload.get("semantic_observations", []), start=1):
        evidence = _evidence_from_payload(item.get("evidence", []))
        text = str(item.get("text") or "")
        if not evidence or not text:
            continue
        claims.append(
            InferenceClaim(
                id=f"claim.{insight_id}.semantic.{index}",
                created_at=created_at,
                claim_type=InferenceClaimType.SEMANTIC_OBSERVATION,
                subject_type="semantic_observation",
                subject_id=str(item.get("id") or f"semantic.{index}"),
                text=text,
                evidence=evidence,
                confidence=_clamped_confidence(item.get("confidence")),
                alternatives=[str(value) for value in item.get("alternatives", [])],
                source=source,
                insight_id=insight_id,
                llm_call_id=llm_call_id,
                metadata={"insight_type": insight_type, "observation_type": item.get("observation_type", "")},
            )
        )

    for index, item in enumerate(payload.get("context_hypotheses", []), start=1):
        evidence = _evidence_from_payload(item.get("evidence", []))
        text = str(item.get("context_type") or "")
        uncertainty = str(item.get("uncertainty") or "")
        if uncertainty:
            text = f"{text}：{uncertainty}" if text else uncertainty
        if not evidence or not text:
            continue
        claims.append(
            InferenceClaim(
                id=f"claim.{insight_id}.context.{index}",
                created_at=created_at,
                claim_type=InferenceClaimType.CONTEXT,
                subject_type="context",
                subject_id=str(item.get("id") or f"context.{index}"),
                text=text,
                evidence=evidence,
                confidence=_clamped_confidence(item.get("confidence")),
                alternatives=[str(value) for value in item.get("alternatives", [])],
                source=source,
                insight_id=insight_id,
                llm_call_id=llm_call_id,
                metadata={"insight_type": insight_type, "time_range": item.get("time_range", "")},
            )
        )

    for index, item in enumerate(payload.get("profile_claims", []), start=1):
        evidence = _evidence_from_payload(item.get("evidence", []))
        text = str(item.get("claim") or "")
        if not evidence or not text:
            continue
        claims.append(
            InferenceClaim(
                id=f"claim.{insight_id}.profile.{index}",
                created_at=created_at,
                claim_type=InferenceClaimType.PROFILE,
                subject_type="profile",
                subject_id=str(item.get("id") or f"profile.{index}"),
                text=text,
                evidence=evidence,
                confidence=_clamped_confidence(item.get("confidence")),
                alternatives=[],
                source=source,
                insight_id=insight_id,
                llm_call_id=llm_call_id,
                metadata={"insight_type": insight_type, "profile_type": item.get("profile_type", "")},
            )
        )

    expression_evidence = _evidence_from_payload(payload.get("evidence", []))
    expression_text = str(payload.get("body") or "")
    if expression_evidence and expression_text:
        claims.append(
            InferenceClaim(
                id=f"claim.{insight_id}.expression",
                created_at=created_at,
                claim_type=InferenceClaimType.EXPRESSION,
                subject_type="insight",
                subject_id=insight_id,
                text=expression_text,
                evidence=expression_evidence,
                confidence=_clamped_confidence(payload.get("confidence")),
                alternatives=[str(value) for value in payload.get("alternatives", [])],
                source=source,
                insight_id=insight_id,
                llm_call_id=llm_call_id,
                metadata={"insight_type": insight_type, "title": payload.get("title", "")},
            )
        )

    return claims


def _evidence_from_payload(items: list[dict[str, Any]]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            evidence.append(Evidence(**item))
        except Exception:
            continue
    return evidence


def _clamped_confidence(value: Any, default: float = 0.45) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _event_ids_from_insight_payload(payload: dict[str, Any]) -> list[str]:
    ids: set[str] = set()
    for item in payload.get("evidence", []):
        event_id = item.get("event_id")
        if event_id:
            ids.add(str(event_id))
    for context in payload.get("context_hypotheses", []):
        for item in context.get("evidence", []):
            event_id = item.get("event_id")
            if event_id:
                ids.add(str(event_id))
    return sorted(ids)


def _profile_ids_from_insight_payload(payload: dict[str, Any]) -> list[str]:
    ids: set[str] = set()
    for item in payload.get("profile_claims", []):
        profile_id = item.get("id")
        if profile_id:
            ids.add(str(profile_id))
    return sorted(ids)
