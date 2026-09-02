# Privacy

Joanna is intended for local-first personal evidence experiments. Treat all real event logs, audio, GPS tracks, health exports, transcripts, SQLite databases, and model outputs as private data.

## Data Stored Locally

The CLI stores runtime data in SQLite. The default root is `.joanna/`, which is ignored by Git. Phase 5 can also store raw uploaded audio files, GPS JSON, manifests, transcripts, audio features, and derived events under the configured Phase 5 root.

Do not publish:

- `.joanna/`
- SQLite databases
- raw or sliced audio
- GPS traces
- Apple Health exports
- upload tokens
- API keys
- logs containing real event IDs, model call IDs, device IDs, or private endpoint URLs

## External Model Calls

Text reasoning can call DeepSeek when `DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY_FILE` is configured. Audio processing can call DashScope Qwen Omni when `DASHSCOPE_API_KEY` or `DASHSCOPE_API_KEY_FILE` is configured.

Use `--no-llm` where supported when you want an offline path. Before using real data with external providers, review the exact data being sent, the provider account terms, and your own data retention requirements.

## Audio And Location

The Android Phase 5 app can record microphone audio, collect location points, cache failed uploads, and upload segments to a receiver URL you configure. This is highly sensitive data. Use it only on devices and networks you control, with explicit consent from affected people and with local laws in mind.

Public receiver deployments should use HTTPS, an upload token, and a proxy or tunnel that does not persist request bodies.

## Inference Boundaries

Joanna stores model output as inference claims, not final facts. Feedback, corrections, denials, deletion requests, and profile objections become evidence for later reasoning; they do not silently rewrite history. Profiles and derived conclusions should remain explainable through source evidence.
