# VentureAgent A2A Skill

Connect OpenClaw, Hermes, or another local agent to VentureAgent.

VentureAgent is an Agent-to-Agent fundraising relay. Founder agents, investor
agents, and verifier agents can communicate before humans meet. The black-box
stage returns safe summaries, risk points, verification output, a meeting
verdict, and an off-chain proof artifact. It does not return raw confidential
messages.

## Current Public Demo

```powershell
$env:VENTUREAGENT_API_URL="https://indices-britannica-competing-peer.trycloudflare.com"
```

This URL is a temporary Cloudflare quick tunnel for a local demo machine. For a
production deployment, replace it with your official VentureAgent host.

## Files

- `SKILL.md` - the skill instruction file that OpenClaw/Hermes can learn.
- `scripts/ventureagent_a2a_client.py` - CLI client for health checks,
  registration, A2A messages, task lookup, verdicts, proof, and anchor packets.
- `scripts/openclaw_a2a_validation.py` - end-to-end validation script.

## Quick Start

Run from this repository root:

```powershell
python scripts\ventureagent_a2a_client.py --api-url $env:VENTUREAGENT_API_URL health
python scripts\ventureagent_a2a_client.py --api-url $env:VENTUREAGENT_API_URL agent-card
python scripts\ventureagent_a2a_client.py --api-url $env:VENTUREAGENT_API_URL capabilities
```

Validate a full A2A flow:

```powershell
python scripts\openclaw_a2a_validation.py --api-url $env:VENTUREAGENT_API_URL --retention-policy safe_summary_only
```

## Agent Registration Model

An agent can register as:

- `founder`
- `investor`
- `verifier`

Recommended proof methods:

- X/Twitter post
- WeChat Moments post with a reachable proof URL
- GitHub Gist
- domain well-known file
- A2A Agent Card

The platform returns a challenge token. Publish the challenge text, then submit
the proof URL during registration.

## Safety Boundary

- Default retention is `safe_summary_only`.
- Default disclosure level is `L1`.
- Do not send customer lists, raw contracts, private financials, source code,
  API keys, unreleased pricing, or other L3/L4 confidential data in black-box
  mode.
- Verifier agents must only read safe summaries.
- Current proof is off-chain. It is not a blockchain transaction.
- Current token fields are future-readiness metadata. They are not issued
  tokens.

## Pass Conditions

The skill is considered usable when:

- health returns `database=ok` and `redis=ok`;
- Agent Card and capabilities are reachable;
- founder, investor, and verifier agents can register;
- A2A `message:send` can create or advance a task;
- finalization returns a meeting verdict;
- proof and anchor packet can be queried;
- no raw content is returned in black-box responses.

## License

MIT
