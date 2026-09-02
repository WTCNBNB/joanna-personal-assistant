# Security

## Reporting

This public project does not yet publish a dedicated security contact. Please open a private security advisory if the hosting platform supports it, or avoid posting exploitable details in a public issue.

## Supported Versions

Only the current main branch is expected to receive security fixes until formal releases are tagged.

## Secrets

Do not commit API keys, upload tokens, `.env` files, SQLite databases, audio files, health exports, private endpoint URLs, keystores, certificates, or generated Android build artifacts.

Credentials are read from:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_API_KEY_FILE`
- `DASHSCOPE_API_KEY`
- `DASHSCOPE_API_KEY_FILE`
- `PHASE5_UPLOAD_TOKEN`

Rotate any credential that was ever committed to a public repository or shared in logs.

## Public Receiver Deployments

If exposing `phase5 receive` beyond localhost, use HTTPS and an upload token. The receiver is designed for personal controlled deployments, not as a multi-tenant public ingestion service.
