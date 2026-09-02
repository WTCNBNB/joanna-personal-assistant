from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

from joanna.app import cli as joanna_cli
from joanna.app.phase5_receiver import _is_authorized, _request_path
from joanna.core.feedback import record_feedback
from joanna.core.memory import JoannaMemory
from joanna.core.phase5 import (
    AudioProcessorResult,
    build_reflection_report,
    derive_events,
    get_segment,
    process_segment,
    receive_segment,
)
from joanna.core.phase5_verification import (
    DEFAULT_KNOWN_GAP_END,
    DEFAULT_KNOWN_GAP_START,
    Phase5VerificationExpectations,
    verify_phase5_weektest,
)
from joanna.core.qwen_omni_audio import (
    QWEN_OMNI_DEFAULT_MODEL,
    QwenOmniAudioProcessor,
    create_representative_wav_slices,
    load_dashscope_api_key,
    parse_qwen_audio_json,
)
from joanna.core.reasoning import build_daily_state
from joanna.core.schema import ExperienceEvent, SensitivityLevel, SourceType


ROOT = Path(__file__).resolve().parents[1]


class Phase5WeektestTest(unittest.TestCase):
    def test_receive_segment_writes_files_manifest_sqlite_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "phase5"
            with _memory(Path(tmp) / "phase5.db") as memory:
                received = receive_segment(
                    memory,
                    root=root,
                    audio_bytes=b"fake-audio",
                    audio_filename="sample.wav",
                    gps_bytes=json.dumps(_gps_payload()).encode("utf-8"),
                    gps_filename="sample.gps.json",
                    metadata=_metadata(),
                )
                segment = get_segment(memory, received.segment_id)
                audits = memory.list_audit_records(action="audio_segment_received")

            self.assertEqual(received.sha256, "69538b86470d5575fc0181cf3b0d0e79ecacb05b6bc6f58c17e759154848e35f")
            self.assertTrue(Path(received.audio_path).exists())
            self.assertTrue(Path(received.gps_path or "").exists())
            self.assertTrue(Path(received.manifest_path).exists())
            self.assertEqual(segment["gps_track"]["point_count"], 2)
            self.assertEqual(segment["processing_status"], "pending")
            self.assertTrue(audits)
            self.assertFalse(audits[0].payload["sent_external"])

    def test_process_and_derive_events_keep_audio_as_sensitive_non_profile_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "phase5"
            with _memory(Path(tmp) / "phase5.db") as memory:
                received = _receive_default(memory, root)
                processed = process_segment(memory, received.segment_id, _TestAudioProcessor())
                events = derive_events(memory, received.segment_id)

                self.assertTrue(processed["transcript_id"].startswith("transcript-"))
                self.assertEqual({event.event_type for event in events}, {"audio_scene", "location_trace"})
                for event in events:
                    self.assertEqual(event.source_type, "audio_capture")
                    self.assertEqual(event.sensitivity, "sensitive")
                    self.assertFalse(event.allow_profile)
                    self.assertIn(received.segment_id, event.evidence_refs)

    def test_audio_segment_feedback_links_derived_claims_and_reflection_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "phase5"
            with _memory(Path(tmp) / "phase5.db") as memory:
                received = receive_segment(
                    memory,
                    root=root,
                    audio_bytes=b"fake-audio",
                    audio_filename="sample.wav",
                    gps_bytes=json.dumps(_gps_payload()).encode("utf-8"),
                    gps_filename="sample.gps.json",
                    metadata=_metadata(),
                )
                process_segment(memory, received.segment_id, _TestAudioProcessor(scene_guess="podcast_or_media"))
                derive_events(memory, received.segment_id)
                insight = build_daily_state(memory, "2026-06-24", use_llm=False)
                claims = memory.list_inference_claims(insight_id=insight.id)
                feedback = record_feedback(
                    memory,
                    target_type="audio_segment",
                    target_id=received.segment_id,
                    text="这不是会议，是我在听播客。",
                )
                conflicts = memory.list_conflict_bundles(feedback_event_id=feedback.id)
                report = build_reflection_report(memory, received.segment_id, feedback_id=feedback.id)

            self.assertTrue(claims)
            self.assertTrue(feedback.related_event_ids)
            self.assertTrue(feedback.related_claim_ids)
            self.assertTrue(conflicts)
            self.assertIn("播客", " ".join(report["reflection_hypotheses"]))
            self.assertIn("不外发原始音频", " ".join(report["governance_notes"]))

    def test_derive_events_requires_real_audio_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "phase5"
            with _memory(Path(tmp) / "phase5.db") as memory:
                received = _receive_default(memory, root)

                with self.assertRaisesRegex(ValueError, "no real audio features"):
                    derive_events(memory, received.segment_id)

    def test_phase5_cli_defaults_to_weektest_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "sample.wav"
            gps = tmp_path / "sample.gps.json"
            metadata = tmp_path / "metadata.json"
            root = tmp_path / "phase5-root"
            audio.write_bytes(b"fake-audio")
            gps.write_text(json.dumps(_gps_payload()), encoding="utf-8")
            metadata.write_text(json.dumps(_metadata(), ensure_ascii=False), encoding="utf-8")

            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "joanna.app.cli",
                    "phase5",
                    "--root",
                    str(root),
                    "upload",
                    "--audio",
                    str(audio),
                    "--gps",
                    str(gps),
                    "--metadata",
                    str(metadata),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((root / "phase5-weektest.db").exists())

    def test_phase5_receiver_accepts_token_from_forwarded_public_url(self) -> None:
        self.assertEqual(_request_path("/api/phase5/segments?token=secret"), "/api/phase5/segments")
        self.assertTrue(_is_authorized("/api/phase5/segments?token=secret", {}, "secret"))
        self.assertTrue(_is_authorized("/api/phase5/segments", {"X-Joanna-Phase5-Token": "secret"}, "secret"))
        self.assertTrue(_is_authorized("/api/phase5/segments", {"Authorization": "Bearer secret"}, "secret"))
        self.assertFalse(_is_authorized("/api/phase5/segments?token=wrong", {}, "secret"))
        self.assertTrue(_is_authorized("/api/phase5/segments", {}, ""))

    def test_qwen_key_loader_prefers_environment_then_configured_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "dashscope.txt"
            key_file.write_text("file-key\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key", "DASHSCOPE_API_KEY_FILE": str(key_file)}, clear=True):
                self.assertEqual(load_dashscope_api_key(), "env-key")
            with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY_FILE": str(key_file)}, clear=True):
                self.assertEqual(load_dashscope_api_key(), "file-key")

    def test_qwen_processor_requires_dashscope_key_before_audio_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_key_file = Path(tmp) / "missing-key.txt"
            with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY_FILE": str(missing_key_file)}, clear=True):
                processor = QwenOmniAudioProcessor(root=Path(tmp) / "phase5")
                with self.assertRaisesRegex(RuntimeError, "DashScope API key not found"):
                    processor.process({"id": "audioseg-missing-key", "audio_file": {"stored_path": "not-read.wav"}})

    def test_wav_slicing_preserves_audio_format_and_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.wav"
            _write_test_wav(source, seconds=4, sample_rate=8000, sample_width=2)

            slices = create_representative_wav_slices(
                audio_path=source,
                segment_id="audioseg-test",
                output_root=tmp_path / "phase5",
                slice_seconds=1,
                max_slices=3,
            )

            self.assertEqual([item.offset_seconds for item in slices], [0.0, 1.5, 3.0])
            for item in slices:
                with wave.open(item.path, "rb") as reader:
                    self.assertEqual(reader.getframerate(), 8000)
                    self.assertEqual(reader.getnchannels(), 1)
                    self.assertEqual(reader.getsampwidth(), 2)
                self.assertEqual(item.duration_seconds, 1.0)
                self.assertTrue(item.sha256)

    def test_qwen_processor_builds_streaming_audio_request_and_stores_external_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "sample.wav"
            _write_test_wav(audio, seconds=1, sample_rate=8000, sample_width=2)
            requests: list[dict] = []
            with _memory(tmp_path / "phase5.db") as memory:
                received = receive_segment(
                    memory,
                    root=tmp_path / "phase5",
                    audio_bytes=audio.read_bytes(),
                    audio_filename="sample.wav",
                    gps_bytes=json.dumps(_gps_payload()).encode("utf-8"),
                    gps_filename="sample.gps.json",
                    metadata=_metadata(),
                )
                processor = QwenOmniAudioProcessor(
                    api_key="test-key",
                    root=tmp_path / "phase5",
                    slice_seconds=1,
                    max_slices=1,
                    urlopen=_fake_qwen_urlopen(requests),
                )
                processed = process_segment(memory, received.segment_id, processor)
                segment = get_segment(memory, received.segment_id)
                audits = memory.list_audit_records(action="audio_segment_processed")

            self.assertTrue(processed["sent_external"])
            self.assertEqual(segment["processing_status"], "processed")
            self.assertEqual(segment["features"][0]["processor"], "qwen_omni_audio")
            self.assertTrue(segment["features"][0]["sent_external"])
            self.assertEqual(segment["features"][0]["metadata"]["external_payload_type"], "audio_slice_base64")
            self.assertEqual(audits[0].payload["slice_count"], 1)
            self.assertNotIn("test-key", json.dumps(segment, ensure_ascii=False))
            self.assertNotIn("test-key", json.dumps(audits[0].to_dict(), ensure_ascii=False))
            body = requests[0]
            self.assertEqual(body["model"], QWEN_OMNI_DEFAULT_MODEL)
            self.assertTrue(body["stream"])
            self.assertEqual(body["modalities"], ["text"])
            audio_part = body["messages"][0]["content"][0]["input_audio"]
            self.assertIn("data:audio/wav;base64,", audio_part["data"])

    def test_qwen_non_json_response_is_preserved_without_fact_claim(self) -> None:
        parsed = parse_qwen_audio_json("不是 JSON")

        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["data"]["raw_text"], "不是 JSON")

    def test_qwen_json_parser_repairs_smart_quotes(self) -> None:
        parsed = parse_qwen_audio_json('{"summary":"测试”,"confidence":0.8}')

        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["summary"], "测试")
        self.assertEqual(parsed["data"]["_parse_repair"], "normalized_smart_quotes")

    def test_phase5_process_dispatch_uses_qwen_processor_and_can_derive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with _memory(tmp_path / "phase5.db") as memory:
                received = _receive_default(memory, tmp_path / "phase5")
                args = mock.Mock(
                    phase5_command="process",
                    segment_id=received.segment_id,
                    derive=True,
                    model=QWEN_OMNI_DEFAULT_MODEL,
                    region="beijing",
                    root=str(tmp_path / "phase5"),
                    slice_seconds=60,
                    max_slices_per_segment=3,
                    sample_mode="representative",
                )
                with mock.patch.object(joanna_cli, "_phase5_qwen_processor", return_value=_TestAudioProcessor()):
                    with mock.patch("builtins.print"):
                        joanna_cli._phase5(args, memory)
                segment = get_segment(memory, received.segment_id)

            self.assertEqual(segment["processing_status"], "processed")
            self.assertTrue(segment["derived_event_ids"])

    def test_http_multipart_parser_accepts_audio_gps_metadata(self) -> None:
        from joanna.app.phase5_receiver import _read_multipart

        boundary = "joanna-test-boundary"
        metadata = json.dumps(_metadata(), ensure_ascii=False)
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="metadata"\r\n\r\n'
            f"{metadata}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="sample.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
            "fake-audio\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="gps"; filename="sample.gps.json"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{json.dumps(_gps_payload())}\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        handler = _FakeHandler(
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            body=body,
        )

        parsed = _read_multipart(handler)

        self.assertEqual(parsed["audio"]["filename"], "sample.wav")
        self.assertEqual(parsed["audio"]["bytes"], b"fake-audio")
        self.assertEqual(json.loads(parsed["metadata"]["bytes"].decode("utf-8"))["device_id"], "TEST_ANDROID_DEVICE")

    def test_http_multipart_parser_accepts_hbuilderx_gps_json_field(self) -> None:
        from joanna.app.phase5_receiver import _optional_field_bytes, _read_multipart

        boundary = "joanna-hbuilderx-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="metadata"\r\n\r\n'
            f"{json.dumps(_metadata(), ensure_ascii=False)}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="gps_json"\r\n\r\n'
            f"{json.dumps(_gps_payload())}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="sample.m4a"\r\n'
            "Content-Type: audio/mp4\r\n\r\n"
            "fake-audio\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        handler = _FakeHandler(
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            body=body,
        )

        parsed = _read_multipart(handler)

        self.assertEqual(parsed["audio"]["filename"], "sample.m4a")
        self.assertIsNone(_optional_field_bytes(parsed, "gps"))
        self.assertEqual(json.loads(_optional_field_bytes(parsed, "gps_json").decode("utf-8"))["points"][0]["lat"], 30.1)

    def test_internal_recorder_route_does_not_raise_bluetooth_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "phase5"
            with _memory(Path(tmp) / "phase5.db") as memory:
                received = receive_segment(
                    memory,
                    root=root,
                    audio_bytes=b"fake-audio",
                    audio_filename="sample.wav",
                    gps_bytes=None,
                    gps_filename=None,
                    metadata={
                        **_metadata(),
                        "selected_audio_device_id": "dji-mic2-internal",
                        "selected_audio_device_name": "DJI Mic 2 internal recorder",
                        "route_type": "internal_recorder",
                        "actual_route_type": "internal_recorder",
                    },
                )
                segment = get_segment(memory, received.segment_id)

            self.assertEqual(segment["route_warning"], "")

    def test_native_android_metadata_optional_diagnostics_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "phase5"
            with _memory(Path(tmp) / "phase5.db") as memory:
                received = receive_segment(
                    memory,
                    root=root,
                    audio_bytes=b"fake-native-audio",
                    audio_filename="native.wav",
                    gps_bytes=json.dumps(_gps_payload()).encode("utf-8"),
                    gps_filename="native.gps.json",
                    metadata={
                        **_metadata(),
                        "capture_app": "native_android",
                        "capture_client_version": "0.1.0-native",
                        "network_mode": "wlan",
                        "audio_bytes_written": 1920000,
                        "read_success_count": 1800,
                        "read_error_count": 0,
                        "byte_peak": 88,
                        "non_zero_samples": 2048,
                        "gps_point_count": 2,
                        "upload_attempt": 2,
                        "cached_upload": True,
                        "client_cached_at": "2026-06-24T10:22:00+08:00",
                    },
                )
                segment = get_segment(memory, received.segment_id)

            self.assertEqual(segment["audio_file"]["metadata"]["capture_app"], "native_android")
            self.assertEqual(segment["audio_file"]["metadata"]["network_mode"], "wlan")
            self.assertEqual(segment["gps_track"]["point_count"], 2)
            self.assertEqual(segment["metadata"]["audio_bytes_written"], 1920000)
            self.assertEqual(segment["metadata"]["upload_attempt"], 2)
            self.assertTrue(segment["metadata"]["cached_upload"])
            self.assertEqual(segment["metadata"]["client_cached_at"], "2026-06-24T10:22:00+08:00")

    def test_phase5_verifier_uses_apple_health_prefix_and_audio_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "phase5"
            import_dir = root / "imports" / "2026-06-25-dji-health-align"
            import_dir.mkdir(parents=True)
            (import_dir / "timeline_alignment_report.md").write_text(
                f"Known gap: {DEFAULT_KNOWN_GAP_START} to {DEFAULT_KNOWN_GAP_END}\n",
                encoding="utf-8",
            )
            (import_dir / "apple_health_events_20260624_182949_to_20260625_090438.jsonl").write_text(
                "{}\n{}\n",
                encoding="utf-8",
            )
            (import_dir / "import_manifest.json").write_text("{}", encoding="utf-8")
            (import_dir / "import_result.json").write_text("{}", encoding="utf-8")
            (import_dir / "fake_audio_processor_retraction.json").write_text("{}", encoding="utf-8")

            with _memory(tmp_path / "phase5.db") as memory:
                received = _receive_default(memory, root)
                _record_qwen_processed_audio(memory, received.segment_id)
                derive_events(memory, received.segment_id)
                _upsert_health_event(memory, "evt.health.overlap", True, [received.segment_id])
                _upsert_health_event(memory, "evt.health.gap", False, [])

                result = verify_phase5_weektest(
                    db_path=tmp_path / "phase5.db",
                    import_dir=import_dir,
                    expectations=Phase5VerificationExpectations(
                        audio_segments=1,
                        audio_transcripts=1,
                        audio_features=1,
                        active_audio_scene=1,
                        apple_health_events=2,
                        qwen_audits=1,
                        qwen_slice_count=3,
                    ),
                )

            self.assertTrue(result["ok"], result["issues"])
            self.assertEqual(result["checks"]["counts"]["apple_health_events"], 2)
            self.assertEqual(result["checks"]["counts"]["legacy_health_prefix_events"], 0)
            self.assertEqual(result["checks"]["health_alignment"]["audio_overlap_true"], 1)
            self.assertEqual(result["checks"]["health_alignment"]["audio_overlap_false"], 1)
            self.assertEqual(result["checks"]["health_alignment"]["overlap_ids_present"], 1)


class _FakeHandler:
    def __init__(self, *, headers: dict[str, str], body: bytes) -> None:
        self.headers = headers
        self.rfile = BytesIO(body)


class _TestAudioProcessor:
    name = "test_audio_processor"
    model = "test-only"
    sent_external = False

    def __init__(self, *, scene_guess: str = "meeting_like") -> None:
        self.scene_guess = scene_guess

    def process(self, segment: dict) -> AudioProcessorResult:
        return AudioProcessorResult(
            transcript_text="测试音频处理结果，不用于真实数据。",
            transcript_confidence=0.9,
            voice_activity="high",
            scene_guess=self.scene_guess,
            speaking_density="medium",
            background="stable_indoor",
            confidence=0.85,
            metadata={"test_only": True},
        )


def _write_test_wav(path: Path, *, seconds: int, sample_rate: int, sample_width: int) -> None:
    frame_count = seconds * sample_rate
    silence = b"\x00" * frame_count * sample_width
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(silence)


def _fake_qwen_urlopen(requests: list[dict]):
    def _urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))
        payload = {
            "choices": [
                {
                    "delta": {
                        "content": json.dumps(
                            {
                                "summary": "安静室内，有轻微环境声。",
                                "timeline": [{"start": "00:00.000", "end": "00:01.000", "event": "安静背景", "confidence": 0.8}],
                                "speech": {"present": False, "transcript": "", "tone": "", "segments": [], "confidence": 0.8},
                                "voice_activity": "none",
                                "speaking_density": "none",
                                "scene_guess": "quiet_indoor",
                                "background": "low_room_tone",
                                "confidence": 0.82,
                                "alternative_explanations": ["可能是静音片段"],
                                "unknowns": ["无法判断具体地点"],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"input_tokens": 7, "output_tokens": 5},
        }
        raw = f"data: {json.dumps(payload, ensure_ascii=False)}\n\ndata: [DONE]\n\n"
        return _FakeHTTPResponse(raw)

    return _urlopen


class _FakeHTTPResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return self.text.encode("utf-8")


def _receive_default(memory: JoannaMemory, root: Path):
    return receive_segment(
        memory,
        root=root,
        audio_bytes=b"fake-audio",
        audio_filename="sample.wav",
        gps_bytes=json.dumps(_gps_payload()).encode("utf-8"),
        gps_filename="sample.gps.json",
        metadata=_metadata(),
    )


def _record_qwen_processed_audio(memory: JoannaMemory, segment_id: str) -> None:
    created_at = "2026-06-25T14:50:21"
    memory.connection.execute(
        """
        insert into audio_transcripts (
            id, segment_id, created_at, processor, model, text,
            confidence, local_only, sent_external, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "transcript-qwen-test",
            segment_id,
            created_at,
            "qwen_omni_audio",
            QWEN_OMNI_DEFAULT_MODEL,
            "测试转写。",
            0.9,
            0,
            1,
            json.dumps({"slice_count": 3}),
        ),
    )
    memory.connection.execute(
        """
        insert into audio_features (
            id, segment_id, created_at, processor, voice_activity,
            scene_guess, speaking_density, background, confidence,
            sent_external, metadata_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "audiofeat-qwen-test",
            segment_id,
            created_at,
            "qwen_omni_audio",
            "low",
            "quiet_indoor",
            "low",
            "low_room_tone",
            0.86,
            1,
            json.dumps({"external_payload_type": "audio_slice_base64"}),
        ),
    )
    memory.connection.execute(
        "update audio_segments set processing_status = ? where id = ?",
        ("processed", segment_id),
    )
    memory.connection.commit()
    memory.record_audit(
        action="audio_segment_processed",
        target_type="audio_segment",
        target_id=segment_id,
        summary="Qwen Omni test processing",
        payload={
            "processor": "qwen_omni_audio",
            "model": QWEN_OMNI_DEFAULT_MODEL,
            "sent_external": True,
            "external_payload_type": "audio_slice_base64",
            "slice_count": 3,
            "region": "beijing",
            "usage": {"total_tokens": 100},
        },
        event_ids=[],
        profile_ids=[],
    )


def _upsert_health_event(memory: JoannaMemory, event_id: str, audio_overlap: bool, overlap_ids: list[str]) -> None:
    memory.upsert_event(
        ExperienceEvent(
            id=event_id,
            occurred_at=datetime.fromisoformat("2026-06-24T10:20:30+08:00"),
            source_type=SourceType.HEALTH_SAMPLE,
            source_id="apple-health-export-test",
            event_type="apple_health_heartrate",
            summary="Apple Health HeartRate test sample.",
            content={
                "audio_overlap": audio_overlap,
                "overlap_audio_segment_ids": overlap_ids,
            },
            scenes=["五期真实测试", "健康数据"],
            sensitivity=SensitivityLevel.SENSITIVE,
            allow_long_term=True,
            allow_profile=False,
            confidence=0.8,
            evidence_refs=["apple-health-export-test", *overlap_ids],
        )
    )


def _metadata() -> dict:
    return {
        "device_id": "TEST_ANDROID_DEVICE",
        "mic_label": "DJI Mic 2",
        "started_at": "2026-06-24T10:20:00+08:00",
        "ended_at": "2026-06-24T10:21:00+08:00",
        "duration_seconds": 60,
        "segment_index": 1,
        "selected_audio_device_id": "bt-dji-mic2",
        "selected_audio_device_name": "DJI Mic 2 Bluetooth",
        "route_type": "bluetooth",
        "actual_route_type": "bluetooth",
        "sample_rate": 48000,
        "channels": 1,
        "codec": "wav",
    }


def _gps_payload() -> dict:
    return {
        "points": [
            {"time": "2026-06-24T10:20:05+08:00", "lat": 30.1, "lng": 120.1, "accuracy_m": 15},
            {"time": "2026-06-24T10:20:45+08:00", "lat": 30.1001, "lng": 120.1002, "accuracy_m": 16},
        ]
    }


class _memory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.memory: JoannaMemory | None = None

    def __enter__(self) -> JoannaMemory:
        self.memory = JoannaMemory(self.path)
        return self.memory

    def __exit__(self, *args) -> None:
        assert self.memory is not None
        self.memory.close()
