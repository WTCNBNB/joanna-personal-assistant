# Joanna Personal Assistant

[![CI](https://github.com/WTCNBNB/joanna-personal-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/WTCNBNB/joanna-personal-assistant/actions/workflows/ci.yml)

Joanna is a local-first personal evidence and reasoning kernel. It stores personal experience events in SQLite, derives explainable context hypotheses, records user feedback as evidence, and keeps LLM calls behind explicit credentials and audit records.

This repository is the public engineering version. It does not include private runtime data, real audio, GPS tracks, Apple Health exports, API keys, local deployment logs, or personal infrastructure configuration.

## What It Does

- Ingest JSONL experience events into a local SQLite store.
- Build daily insights, event reviews, period reviews, and gentle reminders.
- Track inference claims, feedback events, conflict bundles, profiles, summaries, audits, and LLM call records.
- Keep an offline rule path available with `--no-llm`.
- Provide a Phase 5 experimental Android capture app for opt-in audio/GPS segment upload to a receiver you control.

## Privacy Model

Joanna is designed around evidence boundaries, not automatic truth. User feedback, model output, and derived profiles remain traceable claims. The system does not automatically contact other people, create calendar entries, send files, modify external services, or treat LLM output as final fact.

Some commands can send event summaries or audio slices to third-party model providers after you configure credentials. Use `--no-llm` for offline reasoning where supported. Read [PRIVACY.md](PRIVACY.md) before using this with real personal data.

## Requirements

- Python 3.11+
- No Python runtime dependencies for the core CLI
- Android Studio or Android SDK/JDK 17 only if building `android/phase5-capture-native`

## Quick Start

```bash
python3 -m unittest discover -s tests
python3 -m joanna.app.cli --db /tmp/joanna-demo.db ingest samples/phase_one_events.jsonl
python3 -m joanna.app.cli --db /tmp/joanna-demo.db insight today --date 2026-06-16 --no-llm
```

Install an editable local CLI:

```bash
python3 -m pip install -e .
joanna --db /tmp/joanna-demo.db events list
```

## Optional Model Credentials

Create a local environment file from `.env.example`, or export variables in your shell:

```bash
export DEEPSEEK_API_KEY="..."
export DASHSCOPE_API_KEY="..."
```

You can also point to local key files without hardcoding paths:

```bash
export DEEPSEEK_API_KEY_FILE="/path/to/deepseek-key.txt"
export DASHSCOPE_API_KEY_FILE="/path/to/dashscope-key.txt"
```

Do not commit key files, SQLite databases, audio files, health exports, or local receiver tokens.

## Phase 5 Receiver

The Phase 5 receiver accepts local audio/GPS segment uploads:

```bash
python3 -m joanna.app.cli phase5 receive --host 127.0.0.1 --port 18787
```

For phone uploads on the same LAN, bind to a reachable private address and set a token when appropriate:

```bash
PHASE5_UPLOAD_TOKEN="replace-with-a-long-random-token" \
python3 -m joanna.app.cli phase5 receive --host 0.0.0.0 --port 18787
```

See [android/phase5-capture-native/README.md](android/phase5-capture-native/README.md) for Android build and upload details.

## Samples

Files in `samples/` are synthetic fixtures for development and tests. They are not real personal records.

## Development

```bash
python3 -m unittest discover -s tests
```

The core code uses the Python standard library and SQLite. Tests use fake model clients and temporary databases by default, so they do not need API keys and should not send data to external providers.

## License

Apache License 2.0. See [LICENSE](LICENSE).
