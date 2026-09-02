from __future__ import annotations

from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from joanna.core.feedback import record_feedback
from joanna.core.memory import JoannaMemory
from joanna.core.reasoning import build_daily_state, build_period_review


VIEWS = {
    "today": "今日",
    "evidence": "证据流",
    "conflicts": "冲突",
    "profiles": "画像",
    "rules": "规则",
    "llm": "LLM 调用",
    "period": "周期",
    "audit": "审计",
}


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    path = Path(db_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._handle_get(path)

        def do_POST(self) -> None:
            self._handle_post(path)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"个人证据链观察台：http://{host}:{port}")
    server.serve_forever()


class _ResponseMixin:
    pass


def _handler_memory(handler: BaseHTTPRequestHandler, db_path: Path) -> JoannaMemory:
    return JoannaMemory(db_path)


def _write_html(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(body.encode("utf-8"))


def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(303)
    handler.send_header("Location", location)
    handler.end_headers()


def _read_form(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    size = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(size).decode("utf-8")
    return {key: values[0] for key, values in parse_qs(raw).items()}


def _handle_get(self: BaseHTTPRequestHandler, db_path: Path) -> None:
    parsed = urlparse(self.path)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    view = query.get("view", "today")
    date = query.get("date", datetime.now().date().isoformat())
    memory = _handler_memory(self, db_path)
    try:
        content = _view(memory, view, date)
    finally:
        memory.close()
    _write_html(self, _layout(view, content, date))


def _handle_post(self: BaseHTTPRequestHandler, db_path: Path) -> None:
    parsed = urlparse(self.path)
    form = _read_form(self)
    try:
        memory = _handler_memory(self, db_path)
        try:
            if parsed.path == "/feedback":
                record_feedback(
                    memory,
                    target_type=form.get("target_type", "event"),
                    target_id=form.get("target_id", ""),
                    text=form.get("text", ""),
                    feedback_type=form.get("feedback_type") or None,
                    metadata={"source": "web"},
                )
                _redirect(self, "/?view=evidence")
                return
            if parsed.path == "/generate-insight":
                date = form.get("date") or datetime.now().date().isoformat()
                use_llm = form.get("use_llm") == "1"
                build_daily_state(memory, date, use_llm=use_llm)
                _redirect(self, f"/?view=today&date={date}")
                return
            if parsed.path == "/period-review":
                start = form.get("from", "")
                end = form.get("to", "")
                use_llm = form.get("use_llm") == "1"
                build_period_review(memory, start, end, use_llm=use_llm)
                _redirect(self, f"/?view=period&date={end or start}")
                return
        finally:
            memory.close()
    except Exception as exc:
        _write_html(self, _layout("today", f"<h2>操作失败</h2><pre>{escape(str(exc))}</pre>", datetime.now().date().isoformat()), status=400)
        return
    _write_html(self, _layout("today", "<h2>未知请求</h2>", datetime.now().date().isoformat()), status=404)


BaseHTTPRequestHandler._handle_get = _handle_get  # type: ignore[attr-defined]
BaseHTTPRequestHandler._handle_post = _handle_post  # type: ignore[attr-defined]


def _layout(view: str, content: str, date: str) -> str:
    nav = "".join(
        f'<a class="{"active" if key == view else ""}" href="/?view={key}&date={escape(date)}">{escape(label)}</a>'
        for key, label in VIEWS.items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人证据链观察台</title>
<style>
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#1f2933; background:#f7f8fa; }}
header {{ padding:16px 24px; background:#ffffff; border-bottom:1px solid #dde3ea; display:flex; gap:20px; align-items:center; flex-wrap:wrap; }}
h1 {{ font-size:18px; margin:0; }}
nav {{ display:flex; gap:8px; flex-wrap:wrap; }}
nav a {{ padding:6px 10px; border-radius:6px; text-decoration:none; color:#405261; }}
nav a.active {{ background:#1f2933; color:#fff; }}
main {{ max-width:1180px; margin:0 auto; padding:20px; }}
h2 {{ font-size:18px; margin:18px 0 10px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:12px; }}
.item {{ background:#fff; border:1px solid #dde3ea; border-radius:8px; padding:12px; margin:8px 0; }}
.muted {{ color:#687782; font-size:13px; }}
label {{ display:block; font-size:13px; color:#405261; margin:8px 0 4px; }}
input, select, textarea {{ width:100%; box-sizing:border-box; padding:8px; border:1px solid #c7d0d9; border-radius:6px; font:inherit; }}
button {{ margin-top:10px; padding:8px 12px; border:0; border-radius:6px; background:#1f2933; color:#fff; cursor:pointer; }}
pre {{ white-space:pre-wrap; word-break:break-word; background:#fff; border:1px solid #dde3ea; border-radius:8px; padding:12px; }}
</style>
</head>
<body>
<header><h1>个人证据链观察台</h1><nav>{nav}</nav></header>
<main>{content}</main>
</body>
</html>"""


def _view(memory: JoannaMemory, view: str, date: str) -> str:
    if view == "evidence":
        return _evidence_view(memory)
    if view == "conflicts":
        return _conflict_view(memory)
    if view == "profiles":
        return _profile_view(memory)
    if view == "rules":
        return _rules_view(memory)
    if view == "llm":
        return _llm_view(memory)
    if view == "period":
        return _period_view(memory, date)
    if view == "audit":
        return _audit_view(memory)
    return _today_view(memory, date)


def _today_view(memory: JoannaMemory, date: str) -> str:
    events = memory.query_events(date=date, include_disabled=True, include_deleted=True)
    claims = [
        claim
        for claim in memory.list_inference_claims(limit=80)
        if any(item.event_id in {event.id for event in events} for item in claim.evidence)
    ]
    return (
        _date_form(date)
        + _generate_form(date)
        + _feedback_form()
        + "<h2>当天事件</h2>"
        + _items([_event_item(event.to_dict()) for event in events])
        + "<h2>相关推理声明</h2>"
        + _items([_claim_item(claim.to_dict()) for claim in claims])
    )


def _evidence_view(memory: JoannaMemory) -> str:
    events = memory.query_events(include_disabled=True, include_deleted=True)
    feedback = memory.list_feedback_events(limit=80)
    claims = memory.list_inference_claims(limit=80)
    body = "<h2>原始经验事件</h2>" + _items([_event_item(event.to_dict()) for event in events])
    body += "<h2>用户反馈事件</h2>" + _items([_feedback_item(item.to_dict()) for item in feedback])
    body += "<h2>推理声明</h2>" + _items([_claim_item(item.to_dict()) for item in claims])
    return body


def _conflict_view(memory: JoannaMemory) -> str:
    bundles = memory.list_conflict_bundles(limit=80)
    return "<h2>冲突上下文</h2>" + _items([_conflict_item(item.to_dict()) for item in bundles])


def _profile_view(memory: JoannaMemory) -> str:
    profiles = memory.list_profiles(include_revoked=True)
    feedback = memory.list_feedback_events(target_type="profile", limit=80)
    return "<h2>画像与反馈</h2>" + _items([_profile_item(item.to_dict()) for item in profiles]) + "<h2>画像反馈事件</h2>" + _items([_feedback_item(item.to_dict()) for item in feedback])


def _rules_view(memory: JoannaMemory) -> str:
    rules = memory.list_semantic_rules(include_inactive=True)
    feedback = [item for item in memory.list_feedback_events(limit=80) if item.target_type in {"rule", "semantic_rule"}]
    return "<h2>运行时规则</h2>" + _items([_rule_item(item.to_dict()) for item in rules]) + "<h2>规则反馈事件</h2>" + _items([_feedback_item(item.to_dict()) for item in feedback])


def _llm_view(memory: JoannaMemory) -> str:
    calls = memory.list_llm_calls(limit=80)
    return "<h2>LLM 调用记录</h2>" + _items([_raw_item(call.to_dict()) for call in calls])


def _period_view(memory: JoannaMemory, date: str) -> str:
    summaries = memory.list_memory_summaries()
    return _period_form(date) + "<h2>长期摘要与周期线索</h2>" + _items([_raw_item(item.to_dict()) for item in summaries])


def _audit_view(memory: JoannaMemory) -> str:
    audits = memory.list_audit_records(limit=100)
    return "<h2>审计记录</h2>" + _items([_raw_item(item.to_dict()) for item in audits])


def _date_form(date: str) -> str:
    return f"""<form method="get">
<input type="hidden" name="view" value="today">
<label>日期</label><input name="date" value="{escape(date)}">
<button>查看</button>
</form>"""


def _generate_form(date: str) -> str:
    return f"""<div class="item"><strong>生成今日洞察</strong>
<form method="post" action="/generate-insight">
<input type="hidden" name="date" value="{escape(date)}">
<label><input type="checkbox" name="use_llm" value="1" style="width:auto"> 真实调用 LLM，外发当天事件、相关反馈、原推理声明和冲突上下文</label>
<button>生成</button>
</form></div>"""


def _period_form(date: str) -> str:
    return f"""<div class="item"><strong>生成周期复盘</strong>
<form method="post" action="/period-review">
<label>开始日期</label><input name="from" value="{escape(date)}">
<label>结束日期</label><input name="to" value="{escape(date)}">
<label><input type="checkbox" name="use_llm" value="1" style="width:auto"> 真实调用 LLM，外发周期事件和冲突上下文</label>
<button>生成</button>
</form></div>"""


def _feedback_form() -> str:
    options = [
        ("", "自动识别"),
        ("deny_claim", "否认判断"),
        ("correct_explanation", "修正解释"),
        ("resist_profile", "抵触画像"),
        ("delete_request", "记录删除请求"),
        ("close_request", "记录关闭请求"),
        ("dislike_expression", "表达反感"),
        ("ask_reason", "追问原因"),
    ]
    option_html = "".join(f'<option value="{value}">{escape(label)}</option>' for value, label in options)
    return f"""<div class="item"><strong>记录反馈事件</strong>
<form method="post" action="/feedback">
<label>目标类型</label><input name="target_type" placeholder="event / context / claim / profile / rule">
<label>目标 ID</label><input name="target_id">
<label>反馈类型</label><select name="feedback_type">{option_html}</select>
<label>反馈内容</label><textarea name="text" rows="3"></textarea>
<button>记录反馈</button>
</form>
<p class="muted">反馈会进入证据流，不会直接覆盖原推理。</p></div>"""


def _items(items: list[str]) -> str:
    if not items:
        return '<p class="muted">暂无记录。</p>'
    return "".join(items)


def _event_item(item: dict) -> str:
    flags = []
    if item.get("disabled"):
        flags.append("维护状态：disabled")
    if item.get("deleted"):
        flags.append("维护状态：deleted")
    return f'<div class="item"><strong>{escape(item["id"])}</strong><div>{escape(item["summary"])}</div><div class="muted">{escape(item["occurred_at"])} {"；".join(flags)}</div></div>'


def _feedback_item(item: dict) -> str:
    return f'<div class="item"><strong>{escape(item["feedback_type"])}</strong><div>{escape(item["text"])}</div><div class="muted">{escape(item["target_type"])}/{escape(item["target_id"])} · {escape(item["created_at"])}</div></div>'


def _claim_item(item: dict) -> str:
    evidence = "、".join(escape(evidence["event_id"]) for evidence in item.get("evidence", []))
    return f'<div class="item"><strong>{escape(item["claim_type"])}</strong><div>{escape(item["text"])}</div><div class="muted">{escape(item["subject_type"])}/{escape(item["subject_id"])} · 证据 {evidence}</div></div>'


def _conflict_item(item: dict) -> str:
    hint = item.get("resolution_hint") or "等待后续推理解释"
    return f'<div class="item"><strong>{escape(item["status"])}</strong><div>{escape(item["summary"])}</div><div class="muted">{escape(item["id"])} · {escape(hint)}</div></div>'


def _profile_item(item: dict) -> str:
    return f'<div class="item"><strong>{escape(item["id"])}</strong><div>{escape(item["claim"])}</div><div class="muted">confidence {float(item["confidence"]):.0%}</div></div>'


def _rule_item(item: dict) -> str:
    return f'<div class="item"><strong>{escape(item["id"])}</strong><div>{escape(item["rule_type"])} / {escape(item["status"])}</div><pre>{escape(str(item["match_spec"]))}</pre></div>'


def _raw_item(item: dict) -> str:
    return f'<div class="item"><pre>{escape(str(item))}</pre></div>'
