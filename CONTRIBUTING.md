# Contributing

Contributions should preserve Joanna's core boundaries:

- local-first storage by default
- no hidden external calls
- no LLM output treated as final fact
- no automatic external actions without explicit authorization
- auditable evidence, feedback, profile, and model-call records

Run tests before submitting changes:

```bash
python3 -m unittest discover -s tests
```

Tests should use temporary databases and fake model clients unless the change explicitly concerns live provider integration. Do not include real personal data in fixtures.
