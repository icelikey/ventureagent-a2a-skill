"""OpenClaw-friendly CLI client for VentureAgent A2A black-box sessions."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = os.environ.get("VENTUREAGENT_API_URL", "http://127.0.0.1:8000")
DEFAULT_HTTP_TIMEOUT = float(os.environ.get("VENTUREAGENT_A2A_TIMEOUT", "60"))
DEFAULT_HTTP_RETRIES = int(os.environ.get("VENTUREAGENT_A2A_RETRIES", "3"))
DEFAULT_STATE_PATH = Path(
    os.environ.get(
        "VENTUREAGENT_A2A_STATE",
        str(Path.home() / ".openclaw" / "ventureagent-a2a" / "state.json"),
    )
)


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def signed_headers(secret: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "bypass-tunnel-reminder": "ventureagent-a2a-client",
        "X-VentureAgent-Timestamp": timestamp,
        "X-VentureAgent-Signature": signature,
    }


def unsigned_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "bypass-tunnel-reminder": "ventureagent-a2a-client",
    }


def get_headers() -> dict[str, str]:
    return {"bypass-tunnel-reminder": "ventureagent-a2a-client"}


def request_with_retries(
    method: str,
    client: httpx.Client,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(1, DEFAULT_HTTP_RETRIES + 1):
        try:
            return client.request(method, url, timeout=DEFAULT_HTTP_TIMEOUT, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt >= DEFAULT_HTTP_RETRIES:
                break
            time.sleep(min(2 * attempt, 8))
    assert last_error is not None
    raise last_error


def empty_state() -> dict[str, Any]:
    return {"agents": {}, "sessions": {}}


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("agents", {})
    data.setdefault("sessions", {})
    return data


def save_state(state: dict[str, Any], path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_agent(
    state: dict[str, Any],
    alias: str,
    agent: dict[str, Any],
    secret: str,
) -> None:
    state.setdefault("agents", {})[alias] = {
        "id": agent["id"],
        "name": agent.get("name"),
        "role": agent.get("role"),
        "source": agent.get("source"),
        "protocol": agent.get("protocol"),
        "secret": secret,
        "updated_at": int(time.time()),
    }


def remember_session(
    state: dict[str, Any],
    alias: str,
    session_id: str,
    founder: str,
    investor: str,
    verifier: str | None = None,
) -> None:
    state.setdefault("sessions", {})[alias] = {
        "id": session_id,
        "founder": founder,
        "investor": investor,
        "verifier": verifier,
        "updated_at": int(time.time()),
    }


def resolve_agent(state: dict[str, Any], alias_or_id: str) -> dict[str, Any]:
    agents = state.get("agents", {})
    if alias_or_id in agents:
        return agents[alias_or_id]
    for agent in agents.values():
        if agent.get("id") == alias_or_id:
            return agent
    return {"id": alias_or_id, "secret": None}


def resolve_agent_id(state: dict[str, Any], alias_or_id: str) -> str:
    return str(resolve_agent(state, alias_or_id)["id"])


def resolve_agent_secret(
    state: dict[str, Any],
    alias_or_id: str,
    explicit_secret: str | None,
) -> str:
    if explicit_secret:
        return explicit_secret
    secret = resolve_agent(state, alias_or_id).get("secret")
    if not secret:
        raise ValueError(
            f"missing secret for agent '{alias_or_id}'. Register it with an alias or pass --secret."
        )
    return str(secret)


def resolve_session_id(state: dict[str, Any], alias_or_id: str) -> str:
    sessions = state.get("sessions", {})
    if alias_or_id in sessions:
        return str(sessions[alias_or_id]["id"])
    for session in sessions.values():
        if session.get("id") == alias_or_id:
            return str(session["id"])
    return alias_or_id


def resolve_session_record(state: dict[str, Any], alias_or_id: str) -> dict[str, Any] | None:
    sessions = state.get("sessions", {})
    if alias_or_id in sessions:
        return sessions[alias_or_id]
    for session in sessions.values():
        if session.get("id") == alias_or_id:
            return session
    return None


def session_reader_auth(
    state: dict[str, Any],
    alias_or_id: str,
) -> tuple[str | None, str | None]:
    session = resolve_session_record(state, alias_or_id)
    if not session:
        return None, None
    reader_alias = session.get("founder") or session.get("investor") or session.get("verifier")
    if not reader_alias:
        return None, None
    agent = resolve_agent(state, str(reader_alias))
    secret = agent.get("secret")
    if not secret:
        return None, None
    return str(agent.get("id")) if agent.get("id") else None, str(secret)


def redacted_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "agents": {
            alias: {
                **{k: v for k, v in agent.items() if k != "secret"},
                "has_secret": bool(agent.get("secret")),
            }
            for alias, agent in state.get("agents", {}).items()
        },
        "sessions": state.get("sessions", {}),
    }


def post_json(
    client: httpx.Client,
    api_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    secret: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = canonical_json(payload)
    headers = (
        unsigned_headers()
        if secret is None
        else signed_headers(secret, body)
    )
    headers.update(extra_headers or {})
    response = request_with_retries("POST", client, f"{api_url}{path}", content=body, headers=headers)
    response.raise_for_status()
    return response.json()


def get_json(
    client: httpx.Client,
    api_url: str,
    path: str,
    *,
    secret: str | None = None,
    agent_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = get_headers() if secret is None else signed_headers(secret, b"")
    if agent_id:
        headers["X-VentureAgent-Agent-Id"] = agent_id
    headers.update(extra_headers or {})
    response = request_with_retries("GET", client, f"{api_url}{path}", headers=headers)
    response.raise_for_status()
    return response.json()


def output_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_health(args: argparse.Namespace) -> dict[str, Any]:
    with httpx.Client(trust_env=False) as client:
        return get_json(client, args.api_url, "/api/v1/health")


def cmd_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    with httpx.Client(trust_env=False) as client:
        return get_json(client, args.api_url, "/api/v1/a2a/capabilities")


def cmd_agent_card(args: argparse.Namespace) -> dict[str, Any]:
    with httpx.Client(trust_env=False) as client:
        return get_json(client, args.api_url, "/api/v1/a2a/agent-card")


def cmd_skill_manifest(args: argparse.Namespace) -> dict[str, Any]:
    with httpx.Client(trust_env=False) as client:
        return get_json(client, args.api_url, "/api/v1/a2a/skill-manifest")


def cmd_skill(args: argparse.Namespace) -> dict[str, Any]:
    with httpx.Client(trust_env=False) as client:
        response = request_with_retries(
            "GET",
            client,
            f"{args.api_url}/api/v1/a2a/skill.md",
            headers=get_headers(),
        )
        response.raise_for_status()
        return {"markdown": response.text}


def cmd_registration_challenge(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "name": args.name,
        "role": args.role,
        "protocol": args.protocol,
        "verification_method": args.verification_method,
        "verification_handle": args.verification_handle,
        "owner_account_id": args.owner_account_id,
        "agent_card_url": args.agent_card_url,
    }
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            "/api/v1/a2a/agents/registration-challenge",
            {k: v for k, v in payload.items() if v is not None},
        )


def cmd_register(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    secret = args.secret or secrets.token_urlsafe(24)
    payload = {
        "name": args.name,
        "role": args.role,
        "owner_account_id": args.owner_account_id,
        "source": "external",
        "protocol": "skill_api",
        "auth_key_id": secret,
        "capabilities": args.capability,
        "description": args.description,
        "verification_method": args.verification_method,
        "verification_handle": args.verification_handle,
        "verification_proof_url": args.verification_proof_url,
        "registration_challenge_token": args.registration_challenge_token,
        "agent_card_url": args.agent_card_url,
    }
    with httpx.Client(trust_env=False) as client:
        agent = post_json(
            client,
            args.api_url,
            "/api/v1/a2a/agents/register",
            {k: v for k, v in payload.items() if v is not None},
        )

    remember_agent(state, args.alias, agent, secret)
    save_state(state, args.state_file)
    return {
        "registered": True,
        "alias": args.alias,
        "agent": agent,
        "secret_stored": True,
    }


def cmd_remember_agent(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    agent = {
        "id": args.agent_id,
        "name": args.name,
        "role": args.role,
        "source": "external",
        "protocol": args.protocol,
    }
    remember_agent(state, args.alias, agent, args.secret)
    save_state(state, args.state_file)
    return {
        "remembered": True,
        "alias": args.alias,
        "agent": {**agent, "has_secret": True},
    }


def cmd_session(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    founder_id = resolve_agent_id(state, args.founder)
    investor_id = resolve_agent_id(state, args.investor)
    verifier_id = resolve_agent_id(state, args.verifier) if args.verifier else None
    payload = {
        "founder_agent_id": founder_id,
        "investor_agent_id": investor_id,
        "verifier_agent_id": verifier_id,
        "communication_mode": args.communication_mode,
        "retention_policy": args.retention_policy,
        "max_rounds": args.max_rounds,
        "policy_version": args.policy_version,
    }
    with httpx.Client(trust_env=False) as client:
        session = post_json(client, args.api_url, "/api/v1/a2a/sessions", payload)

    remember_session(
        state,
        args.alias,
        session["session_id"],
        args.founder,
        args.investor,
        args.verifier,
    )
    save_state(state, args.state_file)
    return {"created": True, "alias": args.alias, **session}


def cmd_send(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    from_agent_id = resolve_agent_id(state, args.from_agent)
    to_agent_id = resolve_agent_id(state, args.to_agent)
    secret = resolve_agent_secret(state, args.from_agent, args.secret)
    payload = {
        "from_agent_id": from_agent_id,
        "to_agent_id": to_agent_id,
        "content": args.content,
        "role": args.role,
        "disclosure_level": args.disclosure_level,
        "mutual_consent": args.mutual_consent,
    }
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/messages",
            payload,
            secret=secret,
        )


def cmd_a2a_send(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    from_agent_id = resolve_agent_id(state, args.from_agent)
    to_agent_id = resolve_agent_id(state, args.to_agent)
    secret = resolve_agent_secret(state, args.from_agent, args.secret)
    payload = {
        "message": {
            "role": "user",
            "taskId": session_id,
            "contextId": session_id,
            "parts": [{"kind": "text", "text": args.content}],
            "metadata": {
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "role": args.role,
                "disclosure_level": args.disclosure_level,
                "mutual_consent": args.mutual_consent,
            },
        },
        "configuration": {"blocking": True},
    }
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            "/api/v1/a2a/message:send",
            payload,
            secret=secret,
            extra_headers={"A2A-Version": args.a2a_version},
        )


def cmd_human_send(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    payload = {
        "human_role": args.role,
        "content": args.content,
        "disclosure_level": args.disclosure_level,
        "mutual_consent": args.mutual_consent,
    }
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/human-messages",
            payload,
        )


def cmd_summary(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    agent_id, secret = session_reader_auth(state, args.session)
    with httpx.Client(trust_env=False) as client:
        return get_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/summary",
            secret=secret,
            agent_id=agent_id,
        )


def cmd_provenance(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    agent_id, secret = session_reader_auth(state, args.session)
    with httpx.Client(trust_env=False) as client:
        return get_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/provenance",
            secret=secret,
            agent_id=agent_id,
        )


def cmd_anchor_packet(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    agent_id, secret = session_reader_auth(state, args.session)
    with httpx.Client(trust_env=False) as client:
        return get_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/anchor-packet",
            secret=secret,
            agent_id=agent_id,
        )


def cmd_finalize(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    agent_id, secret = session_reader_auth(state, args.session)
    extra_headers = {"X-VentureAgent-Agent-Id": agent_id} if agent_id else None
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/finalize",
            {},
            secret=secret,
            extra_headers=extra_headers,
        )


def cmd_verify(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    session_id = resolve_session_id(state, args.session)
    verifier_id = resolve_agent_id(state, args.verifier) if args.verifier else None
    payload = {
        "verifier_agent_id": verifier_id,
        "profile": args.profile,
    }
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            f"/api/v1/a2a/sessions/{session_id}/verification",
            payload,
        )


def cmd_task(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    task_id = resolve_session_id(state, args.task)
    agent_id, secret = session_reader_auth(state, args.task)
    with httpx.Client(trust_env=False) as client:
        return get_json(
            client,
            args.api_url,
            f"/api/v1/a2a/tasks/{task_id}",
            secret=secret,
            agent_id=agent_id,
            extra_headers={"A2A-Version": args.a2a_version},
        )


def cmd_tasks(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    agent_id = None
    secret = None
    if state.get("agents"):
        first_alias = next(iter(state["agents"]))
        agent = resolve_agent(state, first_alias)
        agent_id = str(agent.get("id")) if agent.get("id") else None
        secret = agent.get("secret")
    with httpx.Client(trust_env=False) as client:
        return get_json(
            client,
            args.api_url,
            f"/api/v1/a2a/tasks?limit={args.limit}",
            secret=secret,
            agent_id=agent_id,
            extra_headers={"A2A-Version": args.a2a_version},
        )


def cmd_cancel_task(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file)
    task_id = resolve_session_id(state, args.task)
    agent_id, secret = session_reader_auth(state, args.task)
    extra_headers = {"A2A-Version": args.a2a_version}
    if agent_id:
        extra_headers["X-VentureAgent-Agent-Id"] = agent_id
    with httpx.Client(trust_env=False) as client:
        return post_json(
            client,
            args.api_url,
            f"/api/v1/a2a/tasks/{task_id}:cancel",
            {},
            secret=secret,
            extra_headers=extra_headers,
        )


def cmd_demo(args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "communication_mode": args.communication_mode,
        "retention_policy": args.retention_policy,
        "max_rounds": args.max_rounds,
        "policy_version": args.policy_version,
        "verifier_enabled": args.verifier_enabled,
        "verifier_profile": args.verifier_profile,
    }
    with httpx.Client(trust_env=False) as client:
        return post_json(client, args.api_url, "/api/v1/a2a/demo/run", payload)


def cmd_state(args: argparse.Namespace) -> dict[str, Any]:
    return redacted_state(load_state(args.state_file))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health")
    health.set_defaults(func=cmd_health)

    capabilities = subparsers.add_parser("capabilities")
    capabilities.set_defaults(func=cmd_capabilities)

    agent_card = subparsers.add_parser("agent-card")
    agent_card.set_defaults(func=cmd_agent_card)

    skill_manifest = subparsers.add_parser("skill-manifest")
    skill_manifest.set_defaults(func=cmd_skill_manifest)

    skill = subparsers.add_parser("skill")
    skill.set_defaults(func=cmd_skill)

    challenge = subparsers.add_parser("registration-challenge")
    challenge.add_argument("--name", required=True)
    challenge.add_argument("--role", required=True, choices=["founder", "investor", "verifier"])
    challenge.add_argument("--protocol", default="skill_api", choices=["skill_api", "webhook"])
    challenge.add_argument(
        "--verification-method",
        default="x_post",
        choices=[
            "x_post",
            "wechat_moments",
            "domain_well_known",
            "github_gist",
            "agent_card",
        ],
    )
    challenge.add_argument("--verification-handle")
    challenge.add_argument("--owner-account-id")
    challenge.add_argument("--agent-card-url")
    challenge.set_defaults(func=cmd_registration_challenge)

    register = subparsers.add_parser("register")
    register.add_argument("--alias", required=True)
    register.add_argument("--name", required=True)
    register.add_argument("--role", required=True, choices=["founder", "investor", "verifier"])
    register.add_argument("--secret")
    register.add_argument("--description", default="")
    register.add_argument("--capability", action="append", default=[])
    register.add_argument("--owner-account-id")
    register.add_argument(
        "--verification-method",
        choices=[
            "x_post",
            "wechat_moments",
            "domain_well_known",
            "github_gist",
            "agent_card",
        ],
    )
    register.add_argument("--verification-handle")
    register.add_argument("--verification-proof-url")
    register.add_argument("--registration-challenge-token")
    register.add_argument("--agent-card-url")
    register.set_defaults(func=cmd_register)

    remember = subparsers.add_parser("remember-agent")
    remember.add_argument("--alias", required=True)
    remember.add_argument("--agent-id", required=True)
    remember.add_argument("--name", required=True)
    remember.add_argument("--role", required=True, choices=["founder", "investor", "verifier"])
    remember.add_argument("--secret", required=True)
    remember.add_argument("--protocol", default="skill_api", choices=["skill_api", "webhook"])
    remember.set_defaults(func=cmd_remember_agent)

    session = subparsers.add_parser("session")
    session.add_argument("--alias", required=True)
    session.add_argument("--founder", required=True)
    session.add_argument("--investor", required=True)
    session.add_argument("--verifier")
    session.add_argument("--communication-mode", default="blackbox_auto")
    session.add_argument("--retention-policy", default="safe_summary_only")
    session.add_argument("--max-rounds", type=int, default=5)
    session.add_argument("--policy-version", default="blackbox-v1")
    session.set_defaults(func=cmd_session)

    send = subparsers.add_parser("send")
    send.add_argument("--session", required=True)
    send.add_argument("--from-agent", required=True)
    send.add_argument("--to-agent", required=True)
    send.add_argument("--role", required=True, choices=["founder", "investor"])
    send.add_argument("--content", required=True)
    send.add_argument("--secret")
    send.add_argument("--disclosure-level", default="L1")
    send.add_argument("--mutual-consent", action="store_true")
    send.set_defaults(func=cmd_send)

    a2a_send = subparsers.add_parser("a2a-send")
    a2a_send.add_argument("--session", required=True)
    a2a_send.add_argument("--from-agent", required=True)
    a2a_send.add_argument("--to-agent", required=True)
    a2a_send.add_argument("--role", required=True, choices=["founder", "investor"])
    a2a_send.add_argument("--content", required=True)
    a2a_send.add_argument("--secret")
    a2a_send.add_argument("--disclosure-level", default="L1")
    a2a_send.add_argument("--mutual-consent", action="store_true")
    a2a_send.add_argument("--a2a-version", default="0.3")
    a2a_send.set_defaults(func=cmd_a2a_send)

    human_send = subparsers.add_parser("human-send")
    human_send.add_argument("--session", required=True)
    human_send.add_argument("--role", required=True, choices=["founder", "investor"])
    human_send.add_argument("--content", required=True)
    human_send.add_argument("--disclosure-level", default="L1")
    human_send.add_argument("--mutual-consent", action="store_true")
    human_send.set_defaults(func=cmd_human_send)

    summary = subparsers.add_parser("summary")
    summary.add_argument("--session", required=True)
    summary.set_defaults(func=cmd_summary)

    provenance = subparsers.add_parser("provenance")
    provenance.add_argument("--session", required=True)
    provenance.set_defaults(func=cmd_provenance)

    anchor_packet = subparsers.add_parser("anchor-packet")
    anchor_packet.add_argument("--session", required=True)
    anchor_packet.set_defaults(func=cmd_anchor_packet)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--session", required=True)
    finalize.set_defaults(func=cmd_finalize)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--session", required=True)
    verify.add_argument("--verifier")
    verify.add_argument(
        "--profile",
        default="source_check",
        choices=["source_check", "market_check", "compliance_check"],
    )
    verify.set_defaults(func=cmd_verify)

    task = subparsers.add_parser("task")
    task.add_argument("--task", required=True)
    task.add_argument("--a2a-version", default="0.3")
    task.set_defaults(func=cmd_task)

    tasks = subparsers.add_parser("tasks")
    tasks.add_argument("--limit", type=int, default=20)
    tasks.add_argument("--a2a-version", default="0.3")
    tasks.set_defaults(func=cmd_tasks)

    cancel_task = subparsers.add_parser("cancel-task")
    cancel_task.add_argument("--task", required=True)
    cancel_task.add_argument("--a2a-version", default="0.3")
    cancel_task.set_defaults(func=cmd_cancel_task)

    demo = subparsers.add_parser("demo")
    demo.add_argument("--communication-mode", default="blackbox_auto")
    demo.add_argument("--retention-policy", default="safe_summary_only")
    demo.add_argument("--max-rounds", type=int, default=5)
    demo.add_argument("--policy-version", default="blackbox-v1")
    demo.add_argument("--verifier-enabled", action="store_true")
    demo.add_argument(
        "--verifier-profile",
        default="source_check",
        choices=["source_check", "market_check", "compliance_check"],
    )
    demo.set_defaults(func=cmd_demo)

    state = subparsers.add_parser("state")
    state.set_defaults(func=cmd_state)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.api_url = args.api_url.rstrip("/")

    try:
        output_json(args.func(args))
    except (httpx.HTTPError, ValueError) as exc:
        output_json({"ok": False, "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
