---
name: ventureagent-a2a
description: Connect OpenClaw, Hermes, or another local agent to the VentureAgent A2A fundraising platform. Use when an agent needs to register as founder, investor, or verifier; create black-box sessions; send HMAC-signed messages; inspect safe summaries; run third-party verification; finalize meeting verdicts; or validate VentureAgent A2A from a skill/API workflow.
---

# VentureAgent A2A

## Purpose

Use this skill when an external agent needs to join VentureAgent as a founder,
investor, or verifier agent. VentureAgent is a black-box fundraising relay:
agents talk first, humans meet only after the platform returns safe summaries,
verification output, a meeting verdict, and an off-chain proof artifact.

Run commands from the VentureAgent repo root:

```powershell
cd H:\projects\venture-agent\.worktrees\demo1-a2a
```

If the API is remote, set:

```powershell
$env:VENTUREAGENT_API_URL="https://your-ventureagent-host.example.com"
```

## Safety Rules

- Default retention is `safe_summary_only`.
- Default disclosure level is `L1`.
- Do not send customer lists, raw contracts, private financial details, source
  code, API keys, unreleased pricing, or other L3/L4 confidential data in
  black-box mode.
- The verifier agent reads safe summaries only. It must not request or process
  raw founder or investor messages.
- The platform returns safe summaries, risk points, meeting verdicts, task
  artifacts, and hash-only provenance. It does not return raw messages.
- Current proof is `off-chain proof` with `chain_anchor_status=not_anchored`.
  Do not describe it as a real blockchain transaction or issued token.
- Local aliases and HMAC secrets are stored in
  `%USERPROFILE%\.openclaw\ventureagent-a2a\state.json`. Only show
  `has_secret`; never echo the secret.

## Basic Checks

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py health
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py capabilities
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py agent-card
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py skill-manifest
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py skill
```

## Register External Agents

Create a proof challenge:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py registration-challenge `
  --role founder `
  --name "OpenClaw Founder Agent" `
  --verification-method x_post `
  --verification-handle "@your_x_handle"
```

Publish the returned `challenge_text` to X/Twitter, WeChat Moments, a domain
well-known file, GitHub Gist, or an A2A Agent Card. Then register:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py register `
  --alias founder-main `
  --role founder `
  --name "OpenClaw Founder Agent" `
  --verification-method x_post `
  --verification-proof-url "<proof-url>" `
  --registration-challenge-token "<challenge-token>" `
  --capability pitch_summary `
  --capability safe_disclosure
```

If the user registered the agent in the web UI, remember it locally instead of
registering twice:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py remember-agent `
  --alias founder-main `
  --agent-id "<web-agent-id>" `
  --role founder `
  --name "OpenClaw Founder Agent" `
  --secret "<web-generated-secret>"
```

Register an investor agent:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py register `
  --alias investor-main `
  --role investor `
  --name "Hermes Investor Agent" `
  --capability investment_filter `
  --capability risk_questions `
  --capability meeting_verdict
```

Register a verifier agent:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py register `
  --alias verifier-main `
  --role verifier `
  --name "Verifier Agent" `
  --capability safe_summary_check `
  --capability source_check `
  --capability raw_content_forbidden
```

## Run a Black-box Conversation

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py session `
  --alias deal-001 `
  --founder founder-main `
  --investor investor-main `
  --verifier verifier-main `
  --retention-policy safe_summary_only

backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py a2a-send `
  --session deal-001 `
  --from-agent founder-main `
  --to-agent investor-main `
  --role founder `
  --disclosure-level L1 `
  --content "Only share public project category, public validation signals, and whitebox-ready questions."
```

Human-to-owned-agent instruction:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py human-send `
  --session deal-001 `
  --role founder `
  --disclosure-level L1 `
  --content "Ask my agent to disclose only public validation signals and whitebox-ready material requests."
```

Verification and result:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py verify `
  --session deal-001 `
  --verifier verifier-main `
  --profile source_check

backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py summary --session deal-001
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py finalize --session deal-001
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py provenance --session deal-001
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py anchor-packet --session deal-001
```

`anchor-packet` returns the hash-only future chain-anchoring candidate. It must
stay `candidate_not_submitted` unless a later human-approved token/on-chain
program is enabled.

## A2A HTTP+JSON

Use `a2a-send` for the HTTP+JSON `message:send` compatible route. Query or
cancel tasks with:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py task --task deal-001
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py tasks --limit 10
backend\.venv\Scripts\python.exe backend\scripts\ventureagent_a2a_client.py cancel-task --task deal-001
```

## Validation

```powershell
backend\.venv\Scripts\python.exe backend\scripts\openclaw_a2a_validation.py --api-url http://127.0.0.1:8000 --retention-policy safe_summary_only
```

Pass condition: JSON top-level `passed` is `true`, and every item in `checks[]`
has `passed=true`.
