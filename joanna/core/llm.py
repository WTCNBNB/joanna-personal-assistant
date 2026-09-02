from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from typing import Protocol


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_KEY_FILE_ENV = "DEEPSEEK_API_KEY_FILE"


class LLMClient(Protocol):
    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        ...


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEEPSEEK_DEFAULT_MODEL,
        base_url: str = DEEPSEEK_BASE_URL,
        timeout: int = 180,
        max_tokens: int = 8192,
    ) -> None:
        self.api_key = api_key or load_deepseek_api_key()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("DeepSeek API key not found. Set DEEPSEEK_API_KEY or provide the configured key file.")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "stream": False,
            "max_tokens": self.max_tokens,
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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"DeepSeek API request failed: {exc.reason}") from exc

        content = payload["choices"][0]["message"]["content"]
        if not content:
            raise RuntimeError("DeepSeek API returned empty JSON content")
        return json.loads(content)


def load_deepseek_api_key() -> str | None:
    value = os.environ.get("DEEPSEEK_API_KEY")
    if value:
        return value.strip()
    key_file = os.environ.get(DEEPSEEK_KEY_FILE_ENV)
    if key_file:
        path = Path(key_file).expanduser()
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            return content or None
    return None
