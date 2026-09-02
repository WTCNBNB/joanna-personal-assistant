from __future__ import annotations

from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from joanna.core.memory import JoannaMemory
from joanna.core.phase5 import receive_segment, record_capture_upload_failure


MAX_UPLOAD_BYTES = 128 * 1024 * 1024


def serve_phase5_receiver(
    *,
    db_path: str | Path,
    root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    upload_token: str | None = None,
) -> None:
    db = Path(db_path)
    base = Path(root)
    required_token = upload_token or os.environ.get("PHASE5_UPLOAD_TOKEN", "")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if _request_path(self.path) == "/health":
                if not _is_authorized(self.path, self.headers, required_token):
                    _write_json(self, {"ok": False, "error": "unauthorized"}, status=401)
                    return
                _write_json(self, {"ok": True, "db": str(db), "root": str(base), "auth_required": bool(required_token)})
                return
            _write_json(self, {"error": "not found"}, status=404)

        def do_POST(self) -> None:
            if _request_path(self.path) != "/api/phase5/segments":
                _write_json(self, {"error": "not found"}, status=404)
                return
            if not _is_authorized(self.path, self.headers, required_token):
                _write_json(self, {"ok": False, "error": "unauthorized"}, status=401)
                return
            self._handle_segment()

        def log_message(self, format: str, *args) -> None:
            return

        def _handle_segment(self) -> None:
            memory = JoannaMemory(db)
            metadata: dict[str, Any] = {}
            try:
                upload = _read_multipart(self)
                metadata = json.loads(_field_text(upload, "metadata"))
                audio = _field_bytes(upload, "audio")
                audio_filename = upload["audio"].get("filename") or "segment.wav"
                gps = _optional_field_bytes(upload, "gps") or _optional_field_bytes(upload, "gps_json")
                gps_filename = upload.get("gps", {}).get("filename") or "segment.gps.json"
                result = receive_segment(
                    memory,
                    root=base,
                    audio_bytes=audio,
                    audio_filename=audio_filename,
                    gps_bytes=gps,
                    gps_filename=gps_filename,
                    metadata=metadata,
                    source_ip=self.client_address[0],
                )
            except Exception as exc:
                try:
                    record_capture_upload_failure(
                        memory,
                        metadata=metadata,
                        error_message=str(exc),
                        source_ip=self.client_address[0],
                    )
                except Exception:
                    pass
                _write_json(self, {"ok": False, "error": str(exc)}, status=400)
                return
            finally:
                memory.close()
            _write_json(self, {"ok": True, "segment": result.to_dict()})

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"第五期本机接收端：http://{host}:{port}")
    print(f"健康检查：http://{host}:{port}/health")
    if required_token:
        print("上传鉴权：已启用 PHASE5_UPLOAD_TOKEN")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("第五期本机接收端已停止")
    finally:
        server.server_close()


def _read_multipart(handler: BaseHTTPRequestHandler) -> dict[str, dict[str, Any]]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Content-Type must be multipart/form-data")
    size = int(handler.headers.get("Content-Length", "0") or "0")
    if size <= 0:
        raise ValueError("empty upload")
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"upload too large: {size} bytes")
    body = handler.rfile.read(size)
    raw = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=policy.default).parsebytes(raw)
    fields: dict[str, dict[str, Any]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fields[str(name)] = {
            "filename": part.get_filename(),
            "content_type": part.get_content_type(),
            "bytes": part.get_payload(decode=True) or b"",
        }
    return fields


def _field_bytes(upload: dict[str, dict[str, Any]], name: str) -> bytes:
    if name not in upload:
        raise ValueError(f"missing multipart field: {name}")
    payload = upload[name]["bytes"]
    if not payload:
        raise ValueError(f"empty multipart field: {name}")
    return payload


def _optional_field_bytes(upload: dict[str, dict[str, Any]], name: str) -> bytes | None:
    if name not in upload:
        return None
    return upload[name]["bytes"] or None


def _field_text(upload: dict[str, dict[str, Any]], name: str) -> str:
    return _field_bytes(upload, name).decode("utf-8")


def _request_path(path: str) -> str:
    return urlsplit(path).path


def _is_authorized(path: str, headers: Mapping[str, str], required_token: str) -> bool:
    if not required_token:
        return True
    supplied = parse_qs(urlsplit(path).query).get("token", [""])[0]
    if not supplied:
        supplied = headers.get("X-Joanna-Phase5-Token", "")
    if not supplied:
        authorization = headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ").strip()
    return hmac.compare_digest(supplied, required_token)


def _write_json(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
