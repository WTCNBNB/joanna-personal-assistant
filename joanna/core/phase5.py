from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import uuid4

from joanna.core.memory import JoannaMemory
from joanna.core.schema import ExperienceEvent, SensitivityLevel, SourceType


PHASE5_ROOT = Path(".joanna/phase5-weektest")
PHASE5_DB_NAME = "phase5-weektest.db"


@dataclass(frozen=True)
class ReceivedSegment:
    segment_id: str
    audio_file_id: str
    gps_track_id: str | None
    audio_path: str
    gps_path: str | None
    manifest_path: str
    sha256: str
    route_warning: str
    received_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AudioProcessorResult:
    transcript_text: str
    transcript_confidence: float
    voice_activity: str
    scene_guess: str
    speaking_density: str
    background: str
    confidence: float
    metadata: dict[str, Any]


class AudioProcessor(Protocol):
    name: str
    model: str
    sent_external: bool

    def process(self, segment: dict[str, Any]) -> AudioProcessorResult:
        raise NotImplementedError


def default_phase5_db(root: str | Path = PHASE5_ROOT) -> Path:
    return Path(root) / PHASE5_DB_NAME


def ensure_phase5_layout(root: str | Path = PHASE5_ROOT) -> Path:
    base = Path(root)
    for relative in [
        "inbox",
        "audio/raw",
        "audio/segments",
        "audio/rejected",
        "gps",
        "transcripts",
        "features",
        "manifests",
        "logs",
    ]:
        (base / relative).mkdir(parents=True, exist_ok=True)
    return base


def receive_segment(
    memory: JoannaMemory,
    *,
    root: str | Path,
    audio_bytes: bytes,
    audio_filename: str,
    gps_bytes: bytes | None,
    gps_filename: str | None,
    metadata: dict[str, Any],
    source_ip: str = "local",
) -> ReceivedSegment:
    if not audio_bytes:
        raise ValueError("audio file is required")
    normalized = _normalize_metadata(metadata)
    base = ensure_phase5_layout(root)
    started = _parse_datetime(normalized["started_at"])
    ended = _parse_datetime(normalized["ended_at"])
    date = started.date().isoformat()
    index = int(normalized.get("segment_index") or normalized.get("sequence") or 1)
    segment_id = normalized.get("segment_id") or f"audioseg-{started:%Y%m%d-%H%M%S}-{index:04d}"
    device_slug = _slug(normalized["device_id"])
    stem = f"{device_slug}_{started:%Y%m%dT%H%M%S%z}_{index:04d}"
    audio_suffix = _safe_suffix(audio_filename, ".wav")
    audio_dir = base / "audio" / "raw" / date
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"{stem}{audio_suffix}"
    audio_path.write_bytes(audio_bytes)
    audio_sha = hashlib.sha256(audio_bytes).hexdigest()

    gps_track_id: str | None = None
    gps_path: Path | None = None
    gps_payload: Any = None
    point_count = 0
    if gps_bytes:
        gps_payload = _load_json_bytes(gps_bytes, "gps")
        point_count = _gps_point_count(gps_payload)
        gps_dir = base / "gps" / date
        gps_dir.mkdir(parents=True, exist_ok=True)
        gps_path = gps_dir / f"{stem}.gps.json"
        gps_path.write_text(json.dumps(gps_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        gps_track_id = f"gpstrack-{started:%Y%m%d-%H%M%S}-{index:04d}"

    received_at = datetime.now().isoformat(timespec="seconds")
    route_warning = _route_warning(normalized)
    manifest_path = base / "manifests" / f"{date}.jsonl"
    audio_file_id = f"audiofile-{started:%Y%m%d-%H%M%S}-{index:04d}"
    duration_seconds = _duration_seconds(started, ended, normalized)

    manifest_record = {
        "segment_id": segment_id,
        "device_id": normalized["device_id"],
        "mic_label": normalized["mic_label"],
        "selected_audio_device_id": normalized["selected_audio_device_id"],
        "selected_audio_device_name": normalized["selected_audio_device_name"],
        "route_type": normalized["route_type"],
        "actual_route_type": normalized["actual_route_type"],
        "route_warning": route_warning,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": duration_seconds,
        "audio_path": str(audio_path),
        "gps_path": str(gps_path) if gps_path else None,
        "gps_point_count": point_count,
        "sha256": audio_sha,
        "upload_attempt": int(normalized.get("upload_attempt") or 1),
        "received_at": received_at,
        "sent_external": False,
    }
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest_record, ensure_ascii=False, sort_keys=True) + "\n")

    _insert_segment_rows(
        memory,
        segment_id=segment_id,
        audio_file_id=audio_file_id,
        gps_track_id=gps_track_id,
        audio_path=audio_path,
        gps_path=gps_path,
        audio_filename=audio_filename,
        sha256=audio_sha,
        byte_size=len(audio_bytes),
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        duration_seconds=duration_seconds,
        point_count=point_count,
        gps_payload=gps_payload,
        metadata=normalized,
        route_warning=route_warning,
        manifest_path=manifest_path,
        received_at=received_at,
        source_ip=source_ip,
    )
    return ReceivedSegment(
        segment_id=segment_id,
        audio_file_id=audio_file_id,
        gps_track_id=gps_track_id,
        audio_path=str(audio_path),
        gps_path=str(gps_path) if gps_path else None,
        manifest_path=str(manifest_path),
        sha256=audio_sha,
        route_warning=route_warning,
        received_at=received_at,
    )


def receive_segment_from_files(
    memory: JoannaMemory,
    *,
    root: str | Path,
    audio_path: str | Path,
    gps_path: str | Path | None,
    metadata: dict[str, Any],
    source_ip: str = "local-cli",
) -> ReceivedSegment:
    audio_source = Path(audio_path)
    gps_source = Path(gps_path) if gps_path else None
    return receive_segment(
        memory,
        root=root,
        audio_bytes=audio_source.read_bytes(),
        audio_filename=audio_source.name,
        gps_bytes=gps_source.read_bytes() if gps_source else None,
        gps_filename=gps_source.name if gps_source else None,
        metadata=metadata,
        source_ip=source_ip,
    )


def list_segments(memory: JoannaMemory, limit: int = 50) -> list[dict[str, Any]]:
    rows = memory.connection.execute(
        "select * from audio_segments order by started_at desc, id desc limit ?",
        (max(1, limit),),
    ).fetchall()
    return [_segment_from_row(row) for row in rows]


def get_segment(memory: JoannaMemory, segment_id: str) -> dict[str, Any] | None:
    row = memory.connection.execute("select * from audio_segments where id = ?", (segment_id,)).fetchone()
    if not row:
        return None
    segment = _segment_from_row(row)
    segment["audio_file"] = _single_row(memory, "audio_files", "segment_id", segment_id)
    segment["gps_track"] = _single_row(memory, "gps_tracks", "segment_id", segment_id)
    segment["transcripts"] = _rows(memory, "audio_transcripts", "segment_id", segment_id)
    segment["features"] = _rows(memory, "audio_features", "segment_id", segment_id)
    segment["uploads"] = _rows(memory, "capture_uploads", "segment_id", segment_id)
    segment["derived_events"] = [
        event.to_dict()
        for event_id in segment["derived_event_ids"]
        if (event := memory.get_event(event_id, include_deleted=True)) is not None
    ]
    return segment


def process_segment(
    memory: JoannaMemory,
    segment_id: str,
    processor: AudioProcessor,
) -> dict[str, Any]:
    segment = get_segment(memory, segment_id)
    if not segment:
        raise ValueError(f"audio segment not found: {segment_id}")
    result = processor.process(segment)
    now = datetime.now().isoformat(timespec="seconds")
    transcript_id = f"transcript-{uuid4().hex[:12]}"
    feature_id = f"audiofeat-{uuid4().hex[:12]}"
    memory.connection.execute(
        """
        insert into audio_transcripts (
            id, segment_id, created_at, processor, model, text, confidence,
            local_only, sent_external, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transcript_id,
            segment_id,
            now,
            processor.name,
            processor.model,
            result.transcript_text,
            result.transcript_confidence,
            int(not processor.sent_external),
            int(processor.sent_external),
            json.dumps(result.metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    memory.connection.execute(
        """
        insert into audio_features (
            id, segment_id, created_at, processor, voice_activity, scene_guess,
            speaking_density, background, confidence, sent_external, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feature_id,
            segment_id,
            now,
            processor.name,
            result.voice_activity,
            result.scene_guess,
            result.speaking_density,
            result.background,
            result.confidence,
            int(processor.sent_external),
            json.dumps(result.metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    memory.connection.execute(
        "update audio_segments set processing_status = ? where id = ?",
        ("processed", segment_id),
    )
    memory.record_audit(
        action="audio_segment_processed",
        target_type="audio_segment",
        target_id=segment_id,
        summary=f"处理音频片段：{segment_id}，processor={processor.name}，sent_external={processor.sent_external}",
        payload={
            "transcript_id": transcript_id,
            "feature_id": feature_id,
            "processor": processor.name,
            "model": processor.model,
            "sent_external": processor.sent_external,
            "external_payload_type": result.metadata.get("external_payload_type"),
            "slice_count": result.metadata.get("slice_count"),
            "region": result.metadata.get("region"),
            "usage": result.metadata.get("usage", {}),
            "elapsed_seconds": result.metadata.get("elapsed_seconds"),
        },
        event_ids=[],
        profile_ids=[],
    )
    return {
        "transcript_id": transcript_id,
        "feature_id": feature_id,
        "sent_external": processor.sent_external,
    }


def derive_events(memory: JoannaMemory, segment_id: str) -> list[ExperienceEvent]:
    segment = get_segment(memory, segment_id)
    if not segment:
        raise ValueError(f"audio segment not found: {segment_id}")
    features = segment.get("features") or []
    if not features:
        raise ValueError(f"audio segment has no real audio features: {segment_id}")
    latest_feature = features[-1]
    occurred_at = _parse_datetime(segment["started_at"])
    event_ids: list[str] = []
    audio_event = ExperienceEvent(
        id=f"evt.{segment_id}.audio_scene",
        occurred_at=occurred_at,
        source_type=SourceType.AUDIO_CAPTURE,
        source_id=segment_id,
        event_type="audio_scene",
        summary=_audio_scene_summary(segment, latest_feature),
        content={
            "audio_segment_id": segment_id,
            "audio_feature_id": latest_feature["id"],
            "audio_file_id": segment["audio_file_id"],
            "gps_track_id": segment.get("gps_track_id"),
            "voice_activity": latest_feature["voice_activity"],
            "scene_guess": latest_feature["scene_guess"],
            "speaking_density": latest_feature["speaking_density"],
            "background": latest_feature["background"],
            "raw_audio_retained": True,
            "sent_external": bool(latest_feature["sent_external"]),
            "route_type": segment["route_type"],
            "actual_route_type": segment["actual_route_type"],
            "route_warning": segment["route_warning"],
        },
        scenes=["音频周测"],
        sensitivity=SensitivityLevel.SENSITIVE,
        allow_long_term=True,
        allow_profile=False,
        confidence=float(latest_feature["confidence"]),
        evidence_refs=[segment_id],
    )
    memory.upsert_event(audio_event)
    event_ids.append(audio_event.id)

    if segment.get("gps_track_id"):
        gps_event = ExperienceEvent(
            id=f"evt.{segment_id}.location_trace",
            occurred_at=occurred_at,
            source_type=SourceType.AUDIO_CAPTURE,
            source_id=segment_id,
            event_type="location_trace",
            summary=f"{_display_time_range(segment)} 记录到 GPS 轨迹点 {segment['gps_track']['point_count']} 个，仅作为位置证据，不直接推断具体场景。",
            content={
                "audio_segment_id": segment_id,
                "gps_track_id": segment["gps_track_id"],
                "gps_path": segment["gps_track"]["stored_path"],
                "point_count": segment["gps_track"]["point_count"],
                "quality": segment["gps_track"]["quality"],
            },
            scenes=["位置轨迹"],
            sensitivity=SensitivityLevel.SENSITIVE,
            allow_long_term=True,
            allow_profile=False,
            confidence=0.72 if segment["gps_track"]["point_count"] else 0.35,
            evidence_refs=[segment_id, segment["gps_track_id"]],
        )
        memory.upsert_event(gps_event)
        event_ids.append(gps_event.id)

    memory.connection.execute(
        "update audio_segments set derived_event_ids_json = ? where id = ?",
        (json.dumps(event_ids, ensure_ascii=False), segment_id),
    )
    memory.record_audit(
        action="audio_segment_events_derived",
        target_type="audio_segment",
        target_id=segment_id,
        summary=f"音频片段进入乔纳证据链：{segment_id} -> {', '.join(event_ids)}",
        payload={"derived_event_ids": event_ids},
        event_ids=event_ids,
        profile_ids=[],
    )
    return [event for event_id in event_ids if (event := memory.get_event(event_id)) is not None]


def build_reflection_report(
    memory: JoannaMemory,
    segment_id: str,
    feedback_id: str | None = None,
) -> dict[str, Any]:
    segment = get_segment(memory, segment_id)
    if not segment:
        raise ValueError(f"audio segment not found: {segment_id}")
    feedback = memory.get_feedback_event(feedback_id) if feedback_id else None
    feedback_payload = feedback.to_dict() if feedback else None
    conflicts = memory.list_conflict_bundles(feedback_event_id=feedback.id) if feedback else []
    neighboring = _neighbor_segments(memory, segment)
    route_warning = segment.get("route_warning") or ""
    hypotheses = [
        "音频判断只是派生解释，不是事实本身；需要和原音频、GPS、相邻片段和用户反馈并看。",
    ]
    if feedback and any(term in feedback.text for term in ["播客", "外放", "视频"]):
        hypotheses.append("可能把外放播客或视频中的人声误判为真实多人对话，需要检查近场人声、说话人结构和设备 route。")
    if route_warning:
        hypotheses.append(f"片段存在麦克风 route 风险：{route_warning}")
    if segment.get("gps_track") and int(segment["gps_track"]["point_count"]) == 0:
        hypotheses.append("该片段没有 GPS 点，不能从位置轨迹支撑具体场景判断。")
    return {
        "segment": segment,
        "feedback": feedback_payload,
        "conflict_bundles": [item.to_dict() for item in conflicts],
        "neighbor_segments": neighboring,
        "reflection_hypotheses": hypotheses,
        "governance_notes": [
            "用户反馈不是最终裁决，会作为新证据进入后续推理。",
            "本报告不修改画像、不删除原判断、不外发原始音频。",
        ],
    }


def record_capture_upload_failure(
    memory: JoannaMemory,
    *,
    metadata: dict[str, Any],
    error_message: str,
    source_ip: str,
) -> None:
    memory.connection.execute(
        """
        insert into capture_uploads (
            id, segment_id, received_at, source_ip, upload_attempt,
            status, error_message, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"upload-{uuid4().hex[:12]}",
            str(metadata.get("segment_id") or ""),
            datetime.now().isoformat(timespec="seconds"),
            source_ip,
            int(metadata.get("upload_attempt") or 1),
            "rejected",
            error_message,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    memory.record_audit(
        action="capture_upload_rejected",
        target_type="audio_segment",
        target_id=str(metadata.get("segment_id") or "unknown"),
        summary=f"拒绝音频上传：{error_message}",
        payload={"metadata": metadata},
        event_ids=[],
        profile_ids=[],
    )


def _insert_segment_rows(
    memory: JoannaMemory,
    *,
    segment_id: str,
    audio_file_id: str,
    gps_track_id: str | None,
    audio_path: Path,
    gps_path: Path | None,
    audio_filename: str,
    sha256: str,
    byte_size: int,
    started_at: str,
    ended_at: str,
    duration_seconds: float,
    point_count: int,
    gps_payload: Any,
    metadata: dict[str, Any],
    route_warning: str,
    manifest_path: Path,
    received_at: str,
    source_ip: str,
) -> None:
    memory.connection.execute(
        """
        insert into audio_files (
            id, segment_id, device_id, original_filename, stored_path, sha256,
            byte_size, duration_seconds, sample_rate, channels, codec,
            created_at, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audio_file_id,
            segment_id,
            metadata["device_id"],
            audio_filename,
            str(audio_path),
            sha256,
            byte_size,
            duration_seconds,
            _optional_int(metadata.get("sample_rate")),
            _optional_int(metadata.get("channels")),
            str(metadata.get("codec") or ""),
            received_at,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    if gps_track_id and gps_path:
        memory.connection.execute(
            """
            insert into gps_tracks (
                id, segment_id, started_at, ended_at, stored_path,
                point_count, quality, metadata_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gps_track_id,
                segment_id,
                started_at,
                ended_at,
                str(gps_path),
                point_count,
                "has_points" if point_count else "empty",
                json.dumps({"gps_payload": gps_payload}, ensure_ascii=False, sort_keys=True),
            ),
        )
    memory.connection.execute(
        """
        insert into audio_segments (
            id, audio_file_id, gps_track_id, device_id, mic_label,
            selected_audio_device_id, selected_audio_device_name, route_type,
            actual_route_type, route_warning, started_at, ended_at,
            duration_seconds, upload_attempt, received_at, manifest_path,
            status, processing_status, derived_event_ids_json, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            segment_id,
            audio_file_id,
            gps_track_id,
            metadata["device_id"],
            metadata["mic_label"],
            metadata["selected_audio_device_id"],
            metadata["selected_audio_device_name"],
            metadata["route_type"],
            metadata["actual_route_type"],
            route_warning,
            started_at,
            ended_at,
            duration_seconds,
            int(metadata.get("upload_attempt") or 1),
            received_at,
            str(manifest_path),
            "received",
            "pending",
            "[]",
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    memory.connection.execute(
        """
        insert into capture_uploads (
            id, segment_id, received_at, source_ip, upload_attempt,
            status, error_message, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"upload-{uuid4().hex[:12]}",
            segment_id,
            received_at,
            source_ip,
            int(metadata.get("upload_attempt") or 1),
            "accepted",
            "",
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        ),
    )
    memory.record_audit(
        action="audio_segment_received",
        target_type="audio_segment",
        target_id=segment_id,
        summary=f"接收音频周测片段：{segment_id}，sha256={sha256[:12]}，route={metadata['actual_route_type']}",
        payload={
            "audio_file_id": audio_file_id,
            "gps_track_id": gps_track_id,
            "sha256": sha256,
            "manifest_path": str(manifest_path),
            "route_warning": route_warning,
            "sent_external": False,
        },
        event_ids=[],
        profile_ids=[],
    )


def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(metadata)
    required = [
        "device_id",
        "mic_label",
        "started_at",
        "ended_at",
        "selected_audio_device_id",
        "selected_audio_device_name",
        "route_type",
    ]
    missing = [key for key in required if not str(normalized.get(key) or "").strip()]
    if missing:
        raise ValueError(f"metadata missing required fields: {', '.join(missing)}")
    normalized["actual_route_type"] = str(normalized.get("actual_route_type") or normalized["route_type"])
    _parse_datetime(str(normalized["started_at"]))
    _parse_datetime(str(normalized["ended_at"]))
    return normalized


def _route_warning(metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("route_warning") or "").strip()
    if explicit:
        return explicit
    actual = str(metadata.get("actual_route_type") or "").lower()
    if actual in {"internal_recorder", "device_internal_recorder"}:
        return ""
    selected = f"{metadata.get('route_type', '')} {metadata.get('selected_audio_device_name', '')}".lower()
    if ("bluetooth" in selected or "蓝牙" in selected or "dji" in selected) and "bluetooth" not in actual and "蓝牙" not in actual:
        return "selected audio input looks bluetooth/DJI but actual route is not marked as bluetooth"
    return ""


def _duration_seconds(started: datetime, ended: datetime, metadata: dict[str, Any]) -> float:
    if metadata.get("duration_seconds") is not None:
        return float(metadata["duration_seconds"])
    return max(0.0, (ended - started).total_seconds())


def _parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _load_json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from exc


def _gps_point_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ["points", "locations", "track"]:
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def _safe_suffix(filename: str, default: str) -> str:
    suffix = Path(filename).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        return suffix
    return default


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "device"


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


def _segment_from_row(row: Any) -> dict[str, Any]:
    payload = dict(row)
    payload["derived_event_ids"] = json.loads(payload.pop("derived_event_ids_json"))
    payload["metadata"] = json.loads(payload.pop("metadata_json"))
    return payload


def _single_row(memory: JoannaMemory, table: str, column: str, value: str) -> dict[str, Any] | None:
    row = memory.connection.execute(f"select * from {table} where {column} = ? order by rowid desc limit 1", (value,)).fetchone()
    if not row:
        return None
    return _json_row(dict(row))


def _rows(memory: JoannaMemory, table: str, column: str, value: str) -> list[dict[str, Any]]:
    rows = memory.connection.execute(f"select * from {table} where {column} = ? order by rowid asc", (value,)).fetchall()
    return [_json_row(dict(row)) for row in rows]


def _json_row(payload: dict[str, Any]) -> dict[str, Any]:
    for key in list(payload):
        if key.endswith("_json"):
            new_key = key[: -len("_json")]
            payload[new_key] = json.loads(payload.pop(key))
    return payload


def _audio_scene_summary(segment: dict[str, Any], feature: dict[str, Any]) -> str:
    return (
        f"{_display_time_range(segment)} 音频处理检测到 {feature['voice_activity']} 人声活动，"
        f"候选声景为 {feature['scene_guess']}。这是音频派生解释，不是事实结论。"
    )


def _display_time_range(segment: dict[str, Any]) -> str:
    started = _parse_datetime(segment["started_at"])
    ended = _parse_datetime(segment["ended_at"])
    return f"{started:%H:%M}-{ended:%H:%M}"


def _neighbor_segments(memory: JoannaMemory, segment: dict[str, Any]) -> list[dict[str, Any]]:
    rows = memory.connection.execute(
        """
        select * from audio_segments
        where id != ?
        order by abs(strftime('%s', started_at) - strftime('%s', ?)) asc
        limit 4
        """,
        (segment["id"], segment["started_at"]),
    ).fetchall()
    return [_segment_from_row(row) for row in rows]
