from __future__ import annotations

from pathlib import Path

from joanna.core.memory import JoannaMemory
from joanna.core.schema import ExperienceEvent


def ingest_jsonl(memory: JoannaMemory, path: str | Path) -> int:
    count = 0
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                event = ExperienceEvent.from_json(raw)
            except Exception as exc:
                raise ValueError(f"{source}:{line_number}: {exc}") from exc
            memory.upsert_event(event)
            count += 1
    return count
