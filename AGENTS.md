# VentureAgent A2A Skill Repo

This repository is a standalone skill package for external agents.

## Use

- Read `SKILL.md` first.
- Run commands from this repository root.
- Set `VENTUREAGENT_API_URL` before connecting to a remote platform.
- Store local aliases and secrets only in the local state file; never print HMAC secrets.

## Verify

```powershell
python scripts\ventureagent_a2a_client.py --api-url $env:VENTUREAGENT_API_URL health
python scripts\openclaw_a2a_validation.py --api-url $env:VENTUREAGENT_API_URL --retention-policy safe_summary_only
```

The skill is usable only when validation returns top-level `"passed": true`.
