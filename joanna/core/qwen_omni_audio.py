from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import wave

from joanna.core.phase5 import AudioProcessorResult


DASHSCOPE_KEY_FILE_ENV = "DASHSCOPE_API_KEY_FILE"
QWEN_OMNI_DEFAULT_MODEL = "qwen3.5-omni-flash"
QWEN_OMNI_ENDPOINTS = {
    "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}


@dataclass(frozen=True)
class AudioSlice:
    path: str
    offset_seconds: float
    duration_seconds: float
    sha256: str
    sample_rate: int
    channels: int
    sample_width: int
    byte_size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QwenOmniAudioProcessor:
    name = "qwen_omni_audio"
    sent_external = True

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = QWEN_OMNI_DEFAULT_MODEL,
        region: str = "beijing",
        root: str | Path = ".joanna/phase5-weektest",
        slice_seconds: int = 60,
        max_slices: int = 3,
        sample_mode: str = "representative",
        timeout: int = 180,
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        if region not in QWEN_OMNI_ENDPOINTS:
            raise ValueError(f"unsupported Qwen Omni region: {region}")
        if sample_mode != "representative":
            raise ValueError(f"unsupported audio sample mode: {sample_mode}")
        if slice_seconds <= 0:
            raise ValueError("slice_seconds must be positive")
        if max_slices <= 0:
            raise ValueError("max_slices must be positive")
        self.api_key = api_key or load_dashscope_api_key()
        self.model = model
        self.region = region
        self.base_url = QWEN_OMNI_ENDPOINTS[region]
        self.root = Path(root)
        self.slice_seconds = slice_seconds
        self.max_slices = max_slices
        self.sample_mode = sample_mode
        self.timeout = timeout
        self._urlopen = urlopen or urllib.request.urlopen

    def process(self, segment: dict[str, Any]) -> AudioProcessorResult:
        if not self.api_key:
            raise RuntimeError("DashScope API key not found. Set DASHSCOPE_API_KEY or DASHSCOPE_API_KEY_FILE.")
        audio_file = segment.get("audio_file") or {}
        audio_path = audio_file.get("stored_path")
        if not audio_path:
            raise ValueError(f"audio segment has no stored audio path: {segment.get('id')}")
        slices = create_representative_wav_slices(
            audio_path=audio_path,
            segment_id=str(segment["id"]),
            output_root=self.root,
            slice_seconds=self.slice_seconds,
            max_slices=self.max_slices,
        )
        analyses: list[dict[str, Any]] = []
        raw_responses: list[dict[str, Any]] = []
        total_usage: dict[str, int] = {}
        started = time.monotonic()
        for audio_slice in slices:
            response = self._call_model(segment, audio_slice)
            parsed = parse_qwen_audio_json(response["text"])
            analyses.append(
                {
                    "slice": audio_slice.to_dict(),
                    "parsed": parsed["data"],
                    "parse_ok": parsed["ok"],
                }
            )
            raw_responses.append(
                {
                    "slice_offset_seconds": audio_slice.offset_seconds,
                    "text": response["text"],
                    "elapsed_seconds": response["elapsed_seconds"],
                }
            )
            for key, value in response.get("usage", {}).items():
                if isinstance(value, int):
                    total_usage[key] = total_usage.get(key, 0) + value
        aggregate = _aggregate_slice_analyses(analyses)
        elapsed = round(time.monotonic() - started, 3)
        metadata = {
            "processor": self.name,
            "model": self.model,
            "region": self.region,
            "base_url": self.base_url,
            "external_payload_type": "audio_slice_base64",
            "sample_mode": self.sample_mode,
            "slice_seconds": self.slice_seconds,
            "slice_count": len(slices),
            "slices": [item.to_dict() for item in slices],
            "slice_analyses": analyses,
            "raw_model_responses": raw_responses,
            "usage": total_usage,
            "elapsed_seconds": elapsed,
            "sent_external": True,
        }
        return AudioProcessorResult(
            transcript_text=aggregate["transcript_text"],
            transcript_confidence=aggregate["transcript_confidence"],
            voice_activity=aggregate["voice_activity"],
            scene_guess=aggregate["scene_guess"],
            speaking_density=aggregate["speaking_density"],
            background=aggregate["background"],
            confidence=aggregate["confidence"],
            metadata=metadata,
        )

    def _call_model(self, segment: dict[str, Any], audio_slice: AudioSlice) -> dict[str, Any]:
        audio_format = Path(audio_slice.path).suffix.lstrip(".").lower() or "wav"
        audio_mime = "audio/wav" if audio_format == "wav" else f"audio/{audio_format}"
        encoded = base64.b64encode(Path(audio_slice.path).read_bytes()).decode("ascii")
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:{audio_mime};base64,{encoded}",
                                "format": audio_format,
                            },
                        },
                        {
                            "type": "text",
                            "text": _analysis_prompt(segment, audio_slice),
                        },
                    ],
                }
            ],
            "modalities": ["text"],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Qwen Omni API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Qwen Omni API request failed: {exc.reason}") from exc
        text, usage = _parse_sse_response(raw)
        if not text.strip():
            raise RuntimeError("Qwen Omni API returned empty text content")
        return {
            "text": text,
            "usage": usage,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def load_dashscope_api_key() -> str | None:
    value = os.environ.get("DASHSCOPE_API_KEY")
    if value:
        return value.strip()
    key_file = os.environ.get(DASHSCOPE_KEY_FILE_ENV)
    if key_file:
        path = Path(key_file).expanduser()
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            return content or None
    return None


def create_representative_wav_slices(
    *,
    audio_path: str | Path,
    segment_id: str,
    output_root: str | Path,
    slice_seconds: int = 60,
    max_slices: int = 3,
) -> list[AudioSlice]:
    source = Path(audio_path)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        raise ValueError(f"audio file not found: {source}")
    base = Path(output_root) / "audio" / "segments" / segment_id
    base.mkdir(parents=True, exist_ok=True)
    with wave.open(str(source), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        sample_rate = reader.getframerate()
        total_frames = reader.getnframes()
        total_seconds = total_frames / sample_rate if sample_rate else 0.0
        offsets = _representative_offsets(total_seconds, slice_seconds, max_slices)
        slices: list[AudioSlice] = []
        for index, offset in enumerate(offsets, start=1):
            start_frame = int(round(offset * sample_rate))
            frame_count = min(int(round(slice_seconds * sample_rate)), max(0, total_frames - start_frame))
            if frame_count <= 0:
                continue
            reader.setpos(start_frame)
            frames = reader.readframes(frame_count)
            duration = frame_count / sample_rate
            out_path = base / f"{segment_id}_offset{int(offset):06d}_{index:02d}.wav"
            with wave.open(str(out_path), "wb") as writer:
                writer.setnchannels(channels)
                writer.setsampwidth(sample_width)
                writer.setframerate(sample_rate)
                writer.writeframes(frames)
            raw = out_path.read_bytes()
            slices.append(
                AudioSlice(
                    path=str(out_path),
                    offset_seconds=round(offset, 3),
                    duration_seconds=round(duration, 3),
                    sha256=hashlib.sha256(raw).hexdigest(),
                    sample_rate=sample_rate,
                    channels=channels,
                    sample_width=sample_width,
                    byte_size=len(raw),
                )
            )
    if not slices:
        raise ValueError(f"failed to create audio slices for segment: {segment_id}")
    return slices


def parse_qwen_audio_json(text: str) -> dict[str, Any]:
    candidate = _extract_json_object(text)
    if not candidate:
        return {"ok": False, "data": {"raw_text": text}}
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        normalized = candidate.replace("“", '"').replace("”", '"')
        try:
            data = json.loads(normalized)
        except json.JSONDecodeError:
            return {"ok": False, "data": {"raw_text": text}}
        if isinstance(data, dict):
            data["_parse_repair"] = "normalized_smart_quotes"
    if not isinstance(data, dict):
        return {"ok": False, "data": {"raw_text": text}}
    return {"ok": True, "data": data}


def _representative_offsets(total_seconds: float, slice_seconds: int, max_slices: int) -> list[float]:
    if total_seconds <= 0:
        return [0.0]
    if total_seconds <= slice_seconds or max_slices == 1:
        return [0.0]
    latest = max(0.0, total_seconds - slice_seconds)
    candidates = [0.0, latest / 2, latest]
    deduped: list[float] = []
    for value in candidates:
        rounded = round(value, 3)
        if all(abs(rounded - existing) >= 1.0 for existing in deduped):
            deduped.append(rounded)
        if len(deduped) >= max_slices:
            break
    return deduped


def _analysis_prompt(segment: dict[str, Any], audio_slice: AudioSlice) -> str:
    return (
        "你是乔纳个人助手的音频观察器。请只根据这段音频输出严格 JSON，不要输出 Markdown 或解释。\n"
        "目标不是普通转录，而是识别可听事件、人声、语气、环境、不可判断项和替代解释。\n"
        "不要把推断写成事实；没有听清就明确 unknown。\n"
        f"原始片段 ID: {segment.get('id')}。\n"
        f"切片相对原始音频起点: {audio_slice.offset_seconds} 秒，切片长度: {audio_slice.duration_seconds} 秒。\n"
        "JSON schema:\n"
        "{\n"
        '  "summary": "一句话概括",\n'
        '  "timeline": [{"start": "00:00.000", "end": "00:01.000", "event": "可听事件", "confidence": 0.0}],\n'
        '  "speech": {"present": false, "transcript": "", "tone": "", "segments": [], "confidence": 0.0},\n'
        '  "voice_activity": "none|low|medium|high|uncertain",\n'
        '  "speaking_density": "none|low|medium|high|uncertain",\n'
        '  "scene_guess": "候选声景",\n'
        '  "background": "背景声",\n'
        '  "confidence": 0.0,\n'
        '  "alternative_explanations": ["其他可能解释"],\n'
        '  "unknowns": ["不能判断的内容"]\n'
        "}"
    )


def _parse_sse_response(raw: str) -> tuple[str, dict[str, int]]:
    parts: list[str] = []
    usage: dict[str, int] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                parts.append(str(content))
        usage_payload = payload.get("usage")
        if isinstance(usage_payload, dict):
            _flatten_usage(usage_payload, usage)
    return "".join(parts), usage


def _flatten_usage(payload: dict[str, Any], out: dict[str, int], prefix: str = "") -> None:
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, int):
            out[name] = out.get(name, 0) + value
        elif isinstance(value, dict):
            _flatten_usage(value, out, f"{name}.")


def _aggregate_slice_analyses(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = [item["parsed"] for item in analyses if item.get("parse_ok") and isinstance(item.get("parsed"), dict)]
    if not parsed:
        return {
            "transcript_text": "",
            "transcript_confidence": 0.0,
            "voice_activity": "uncertain",
            "scene_guess": "模型未返回可解析结构化音频结果",
            "speaking_density": "uncertain",
            "background": "unknown",
            "confidence": 0.0,
        }
    speech_items = [item.get("speech") for item in parsed if isinstance(item.get("speech"), dict)]
    transcripts = [str(item.get("transcript") or "").strip() for item in speech_items if str(item.get("transcript") or "").strip()]
    confidence_values = [_safe_float(item.get("confidence")) for item in parsed if item.get("confidence") is not None]
    speech_confidence = [_safe_float(item.get("confidence")) for item in speech_items if item.get("confidence") is not None]
    return {
        "transcript_text": "\n".join(transcripts),
        "transcript_confidence": _average(speech_confidence) if speech_confidence else 0.0,
        "voice_activity": _most_significant([str(item.get("voice_activity") or "uncertain") for item in parsed]),
        "scene_guess": " / ".join(_unique_texts(_text_value(item.get("scene_guess")) for item in parsed)) or "unknown",
        "speaking_density": _most_significant([str(item.get("speaking_density") or "uncertain") for item in parsed]),
        "background": " / ".join(_unique_texts(_text_value(item.get("background")) for item in parsed)) or "unknown",
        "confidence": _average(confidence_values) if confidence_values else 0.0,
    }


def _most_significant(values: list[str]) -> str:
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "uncertain": -1}
    normalized = [value if value in rank else "uncertain" for value in values]
    return max(normalized or ["uncertain"], key=lambda item: rank[item])


def _unique_texts(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _text_value(value: Any) -> str:
    if isinstance(value, list):
        return " / ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)


def _extract_json_object(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    return stripped[start : end + 1]
