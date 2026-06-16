"""Validate VentureAgent A2A behavior from OpenClaw or any local agent runner."""

from __future__ import annotations

import argparse
import hmac
import json
import sys
import time
from hashlib import sha256
from typing import Any
from uuid import uuid4

import httpx

REQUIRED_TOOLS = {
    "get_agent_card",
    "register_agent",
    "create_blackbox_session",
    "send_message",
    "send_a2a_message",
    "get_a2a_task",
    "get_session_summary",
    "get_session_provenance",
    "finalize_session",
    "run_demo_exchange",
}
REQUIRED_PROTOCOLS = {"skill_api", "webhook", "a2a_http_json"}
FORBIDDEN_TIMELINE_FIELDS = {
    "auth_key_id",
    "content",
    "raw_content",
    "raw_content_encrypted",
    "raw_content_hash",
}


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
        "X-VentureAgent-Timestamp": timestamp,
        "X-VentureAgent-Signature": signature,
    }


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    evidence: Any,
    *,
    severity: str = "required",
) -> None:
    checks.append(
        {
            "name": name,
            "passed": passed,
            "severity": severity,
            "evidence": evidence,
        }
    )


def report_passed(checks: list[dict[str, Any]]) -> bool:
    return all(check["passed"] or check["severity"] != "required" for check in checks)


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
    headers = {"Content-Type": "application/json"} if secret is None else signed_headers(secret, body)
    headers.update(extra_headers or {})
    response = client.post(f"{api_url}{path}", content=body, headers=headers, timeout=15)
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
    headers = {} if secret is None else signed_headers(secret, b"")
    if agent_id:
        headers["X-VentureAgent-Agent-Id"] = agent_id
    headers.update(extra_headers or {})
    response = client.get(f"{api_url}{path}", headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def get_text(client: httpx.Client, api_url: str, path: str) -> str:
    response = client.get(f"{api_url}{path}", timeout=15)
    response.raise_for_status()
    return response.text


def validate_capabilities(capabilities: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    tools = set(capabilities.get("tools", []))
    protocols = set(capabilities.get("protocols", []))
    add_check(
        checks,
        "capabilities_required_tools",
        REQUIRED_TOOLS.issubset(tools),
        {"required": sorted(REQUIRED_TOOLS), "actual": sorted(tools)},
    )
    add_check(
        checks,
        "capabilities_external_protocols",
        REQUIRED_PROTOCOLS.issubset(protocols),
        {"required": sorted(REQUIRED_PROTOCOLS), "actual": sorted(protocols)},
    )
    add_check(
        checks,
        "capabilities_policy_version",
        bool(capabilities.get("policy_version")),
        capabilities.get("policy_version"),
    )


def validate_agent_card(agent_card: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    text = json.dumps(agent_card, ensure_ascii=False)
    add_check(
        checks,
        "agent_card_http_json_transport",
        agent_card.get("preferredTransport") == "HTTP+JSON",
        agent_card.get("preferredTransport"),
    )
    add_check(
        checks,
        "agent_card_declares_message_send",
        str(agent_card.get("metadata", {}).get("endpoints", {}).get("messageSend", "")).endswith(
            "/api/v1/a2a/message:send"
        ),
        agent_card.get("metadata", {}).get("endpoints", {}),
    )
    add_check(
        checks,
        "agent_card_does_not_leak_secret_fields",
        "auth_key_id" not in text and "raw_content" not in text,
        sorted(agent_card.keys()),
    )


def validate_skill_manifest(manifest: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    text = json.dumps(manifest, ensure_ascii=False)
    commands = manifest.get("commands") or {}
    safety = manifest.get("safety") or {}
    add_check(
        checks,
        "skill_manifest_external_agent_flow",
        manifest.get("name") == "ventureagent-a2a"
        and manifest.get("protocol") == "skill_api"
        and str(manifest.get("skill_url", "")).endswith("/api/v1/a2a/skill.md")
        and str(manifest.get("agent_card_url", "")).endswith("/api/v1/a2a/agent-card"),
        {
            "name": manifest.get("name"),
            "protocol": manifest.get("protocol"),
            "skill_url": manifest.get("skill_url"),
            "agent_card_url": manifest.get("agent_card_url"),
        },
    )
    add_check(
        checks,
        "skill_manifest_commands_complete",
        {
            "registration-challenge",
            "register",
            "remember-agent",
            "session",
            "a2a-send",
            "human-send",
            "summary",
            "finalize",
            "provenance",
        }.issubset(set(commands)),
        {"commands": sorted(commands)},
    )
    add_check(
        checks,
        "skill_manifest_no_secret_leak",
        "auth_key_id" not in text
        and "raw_content_encrypted" not in text
        and "raw_content_hash" not in text
        and "openclaw-founder-secret" not in text
        and "openclaw-investor-secret" not in text
        and safety.get("raw_content_returned") is False,
        {
            "keys": sorted(manifest.keys()),
            "raw_content_returned": safety.get("raw_content_returned"),
        },
    )


def validate_skill_markdown(markdown: str, checks: list[dict[str, Any]]) -> None:
    mojibake_markers = ["\u6d63", "\u69db", "\u9396", "\u6d93", "\u8be7"]
    required_fragments = [
        "name: ventureagent-a2a",
        "VENTUREAGENT_API_URL",
        "registration-challenge",
        "remember-agent",
        "a2a-send",
        "human-send",
        "provenance",
        "safe_summary_only",
        "off-chain proof",
        "chain_anchor_status=not_anchored",
    ]
    add_check(
        checks,
        "skill_markdown_machine_readable",
        markdown.isascii() and all(fragment in markdown for fragment in required_fragments),
        {
            "ascii": markdown.isascii(),
            "missing": [fragment for fragment in required_fragments if fragment not in markdown],
        },
    )
    add_check(
        checks,
        "skill_markdown_no_mojibake",
        not any(marker in markdown for marker in mojibake_markers),
        {"length": len(markdown)},
    )
    add_check(
        checks,
        "skill_markdown_safety_boundary",
        "Do not send customer lists" in markdown
        and "does not return raw messages" in markdown
        and "real blockchain transaction" in markdown,
        {"has_safety_rules": "## Safety Rules" in markdown},
    )


def validate_agent_response(agent: dict[str, Any], role: str, checks: list[dict[str, Any]]) -> None:
    add_check(
        checks,
        f"{role}_agent_registered",
        bool(agent.get("id")) and agent.get("role") == role,
        {"id": agent.get("id"), "role": agent.get("role")},
    )
    add_check(
        checks,
        f"{role}_agent_external_skill_api",
        agent.get("source") == "external" and agent.get("protocol") == "skill_api",
        {"source": agent.get("source"), "protocol": agent.get("protocol")},
    )
    add_check(
        checks,
        f"{role}_agent_secret_not_leaked",
        "auth_key_id" not in agent,
        sorted(agent.keys()),
    )


def validate_message_response(
    response: dict[str, Any],
    name: str,
    checks: list[dict[str, Any]],
) -> None:
    add_check(
        checks,
        f"{name}_message_has_safe_summary",
        bool(response.get("safe_summary")),
        {"message_id": response.get("message_id"), "decision": response.get("decision")},
    )
    add_check(
        checks,
        f"{name}_message_has_audit_hash",
        bool(response.get("raw_content_hash")),
        {"raw_content_hash": response.get("raw_content_hash")},
    )
    add_check(
        checks,
        f"{name}_message_does_not_echo_raw_content",
        "content" not in response and "raw_content" not in response,
        sorted(response.keys()),
    )
    add_check(
        checks,
        f"{name}_message_has_runtime_trace",
        bool(response.get("trace_id")) and bool(response.get("span_id")) and bool(response.get("run_id")),
        {
            "trace_id": response.get("trace_id"),
            "span_id": response.get("span_id"),
            "run_id": response.get("run_id"),
        },
    )
    add_check(
        checks,
        f"{name}_message_has_policy_decision",
        (response.get("policy_decision") or {}).get("output_visibility") == "safe_summary",
        response.get("policy_decision"),
    )


def validate_standard_task(task: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    history = task.get("history") or []
    artifacts = task.get("artifacts") or []
    leaked = "raw_content" in json.dumps(task, ensure_ascii=False) or "auth_key_id" in json.dumps(
        task, ensure_ascii=False
    )
    add_check(
        checks,
        "a2a_task_shape",
        task.get("kind") == "task" and bool(task.get("id")) and bool(task.get("contextId")),
        {"kind": task.get("kind"), "id": task.get("id"), "contextId": task.get("contextId")},
    )
    add_check(
        checks,
        "a2a_task_history_uses_text_parts",
        bool(history) and all((item.get("parts") or [{}])[0].get("kind") == "text" for item in history),
        {"history_count": len(history)},
    )
    add_check(
        checks,
        "a2a_task_has_no_forbidden_fields",
        not leaked,
        {"history_count": len(history)},
    )
    add_check(
        checks,
        "a2a_task_has_runtime_trace",
        bool((task.get("metadata") or {}).get("trace_id"))
        and bool((task.get("metadata") or {}).get("run_id")),
        task.get("metadata") or {},
    )
    add_check(
        checks,
        "a2a_task_history_has_policy_decision",
        bool(history)
        and all(
            (item.get("metadata") or {}).get("trace_id")
            and (item.get("metadata") or {}).get("span_id")
            and ((item.get("metadata") or {}).get("policy_decision") or {}).get("decision")
            for item in history
        ),
        {"history_count": len(history)},
    )
    if artifacts:
        add_check(
            checks,
            "a2a_task_verdict_artifact",
            artifacts[0].get("artifactId") == "meeting-verdict",
            artifacts[0],
        )


def validate_provenance(provenance: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    text = json.dumps(provenance, ensure_ascii=False)
    messages = provenance.get("messages") or {}
    audit = provenance.get("audit") or {}
    verdict = provenance.get("verdict") or {}
    token_readiness = provenance.get("token_readiness") or {}
    leaked = (
        '"safe_summary":' in text
        or '"raw_content_encrypted":' in text
        or "[安全摘要]" in text
        or '"auth_key_id":' in text
    )
    add_check(
        checks,
        "provenance_shape",
        provenance.get("kind") == "ventureagent.interaction_proof"
        and provenance.get("version") == "1.0"
        and bool(provenance.get("session_id")),
        {
            "kind": provenance.get("kind"),
            "version": provenance.get("version"),
            "session_id": provenance.get("session_id"),
        },
    )
    add_check(
        checks,
        "provenance_hashes_present",
        bool(provenance.get("provenance_hash"))
        and bool(provenance.get("root_hash"))
        and bool(audit.get("latest_event_hash"))
        and bool(verdict.get("verdict_hash"))
        and bool(messages.get("raw_content_hashes"))
        and bool(messages.get("safe_summary_hashes")),
        {
            "has_provenance_hash": bool(provenance.get("provenance_hash")),
            "has_root_hash": bool(provenance.get("root_hash")),
            "has_latest_event_hash": bool(audit.get("latest_event_hash")),
            "has_verdict_hash": bool(verdict.get("verdict_hash")),
        },
    )
    add_check(
        checks,
        "provenance_has_runtime_trace",
        bool(provenance.get("trace_id"))
        and bool(provenance.get("run_id"))
        and bool((audit.get("trace_ids") or [])),
        {
            "trace_id": provenance.get("trace_id"),
            "run_id": provenance.get("run_id"),
            "audit_trace_ids": audit.get("trace_ids"),
        },
    )
    add_check(
        checks,
        "provenance_not_yet_anchored",
        provenance.get("chain_anchor_status") == "not_anchored",
        provenance.get("chain_anchor_status"),
    )
    add_check(
        checks,
        "provenance_token_boundary",
        token_readiness.get("boundary") == "off_chain_proof_only"
        and token_readiness.get("token_issuance_status") == "not_issued"
        and "no_chain_transaction" in (token_readiness.get("disallowed_actions") or []),
        {
            "boundary": token_readiness.get("boundary"),
            "token_issuance_status": token_readiness.get("token_issuance_status"),
            "disallowed_actions": token_readiness.get("disallowed_actions"),
        },
    )
    future_anchor_policy = token_readiness.get("future_anchor_policy") or {}
    required_before_anchor = future_anchor_policy.get("required_before_anchor") or []
    add_check(
        checks,
        "provenance_token_readiness_boundary",
        token_readiness.get("chain_action_status") == "no_chain_transaction"
        and token_readiness.get("automatic_reward_status") == "disabled"
        and token_readiness.get("human_approval_required") is True
        and future_anchor_policy.get("allowed_current_action") == "review_off_chain_proof"
        and "token_program_enabled" in required_before_anchor,
        {
            "chain_action_status": token_readiness.get("chain_action_status"),
            "automatic_reward_status": token_readiness.get("automatic_reward_status"),
            "human_approval_required": token_readiness.get("human_approval_required"),
            "future_anchor_policy": future_anchor_policy,
        },
    )
    add_check(
        checks,
        "provenance_no_text_leak",
        not leaked,
        {"keys": sorted(provenance.keys())},
    )


def validate_timeline_contract(
    timeline: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    expected_retention_policy: str,
) -> None:
    session = timeline.get("session") or {}
    messages = timeline.get("messages") or []
    leaked_fields = sorted(
        {
            field
            for message in messages
            for field in FORBIDDEN_TIMELINE_FIELDS
            if field in message
        }
    )

    add_check(
        checks,
        "timeline_retention_policy_matches",
        session.get("retention_policy") == expected_retention_policy,
        {
            "expected": expected_retention_policy,
            "actual": session.get("retention_policy"),
        },
    )
    add_check(
        checks,
        "timeline_has_safe_messages",
        len(messages) >= 2 and all(message.get("safe_summary") for message in messages),
        {"message_count": len(messages)},
    )
    add_check(
        checks,
        "timeline_messages_have_summary_hash",
        bool(messages) and all(message.get("safe_summary_hash") for message in messages),
        {"message_count": len(messages)},
    )
    add_check(
        checks,
        "timeline_has_runtime_trace",
        bool(session.get("trace_id"))
        and bool(session.get("run_id"))
        and bool(messages)
        and all(
            message.get("trace_id")
            and message.get("span_id")
            and message.get("run_id")
            and (message.get("policy_decision") or {}).get("decision")
            for message in messages
        ),
        {
            "session_trace_id": session.get("trace_id"),
            "message_count": len(messages),
        },
    )
    add_check(
        checks,
        "timeline_no_forbidden_fields",
        not leaked_fields,
        {"forbidden_fields": sorted(FORBIDDEN_TIMELINE_FIELDS), "leaked": leaked_fields},
    )


def validate_final_verdict(verdict: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    add_check(
        checks,
        "finalize_returns_meeting_verdict",
        verdict.get("meeting_verdict") in {"strong_yes", "yes", "maybe", "no"},
        verdict.get("meeting_verdict"),
    )
    add_check(
        checks,
        "finalize_returns_score",
        isinstance(verdict.get("score_total"), int | float),
        verdict.get("score_total"),
    )
    add_check(
        checks,
        "finalize_returns_next_step",
        bool(verdict.get("next_step")),
        verdict.get("next_step"),
    )


def run_validation(api_url: str, retention_policy: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    suffix = str(int(time.time()))
    founder_secret = f"openclaw-founder-secret-{suffix}"
    investor_secret = f"openclaw-investor-secret-{suffix}"
    validation_account_suffix = uuid4().hex[:12]

    with httpx.Client(trust_env=False) as client:
        health = get_json(client, api_url, "/api/v1/health")
        add_check(checks, "api_health_ok", health.get("status") == "ok", health)

        capabilities = get_json(client, api_url, "/api/v1/a2a/capabilities")
        validate_capabilities(capabilities, checks)

        agent_card = get_json(client, api_url, "/api/v1/a2a/agent-card")
        validate_agent_card(agent_card, checks)

        skill_manifest = get_json(client, api_url, "/api/v1/a2a/skill-manifest")
        validate_skill_manifest(skill_manifest, checks)

        skill_markdown = get_text(client, api_url, "/api/v1/a2a/skill.md")
        validate_skill_markdown(skill_markdown, checks)

        validation_account = post_json(
            client,
            api_url,
            "/api/v1/auth/register",
            {
                "email": f"openclaw-validation-{validation_account_suffix}@ventureagent.local",
                "password": f"ventureagent-validation-{validation_account_suffix}",
                "display_name": f"OpenClaw Validation {validation_account_suffix}",
                "account_type": "operator",
            },
        )
        owner_account_id = validation_account["account"]["id"]
        add_check(
            checks,
            "validation_owner_account_created",
            bool(owner_account_id),
            {"owner_account_id": owner_account_id},
        )

        founder = post_json(
            client,
            api_url,
            "/api/v1/a2a/agents/register",
            {
                "name": f"OpenClaw Founder Agent {suffix}",
                "role": "founder",
                "owner_account_id": owner_account_id,
                "source": "external",
                "protocol": "skill_api",
                "auth_key_id": founder_secret,
                "capabilities": ["pitch_summary", "answer_due_diligence", "safe_disclosure"],
                "description": "OpenClaw 外部创业者 Agent 验收账号。",
            },
        )
        investor = post_json(
            client,
            api_url,
            "/api/v1/a2a/agents/register",
            {
                "name": f"OpenClaw Investor Agent {suffix}",
                "role": "investor",
                "owner_account_id": owner_account_id,
                "source": "external",
                "protocol": "skill_api",
                "auth_key_id": investor_secret,
                "capabilities": ["investment_filter", "risk_questions", "meeting_verdict"],
                "description": "OpenClaw 外部投资人 Agent 验收账号。",
            },
        )
        validate_agent_response(founder, "founder", checks)
        validate_agent_response(investor, "investor", checks)

        session = post_json(
            client,
            api_url,
            "/api/v1/a2a/sessions",
            {
                "founder_agent_id": founder["id"],
                "investor_agent_id": investor["id"],
                "communication_mode": "blackbox_auto",
                "retention_policy": retention_policy,
                "max_rounds": 5,
                "policy_version": "blackbox-v1",
            },
        )
        session_id = session["session_id"]
        add_check(
            checks,
            "blackbox_session_created",
            bool(session_id) and session.get("status") in {"blackbox_running", "ready"},
            session,
        )

        founder_message = post_json(
            client,
            api_url,
            f"/api/v1/a2a/sessions/{session_id}/messages",
            {
                "from_agent_id": founder["id"],
                "to_agent_id": investor["id"],
                "content": (
                    "我们是面向中小企业的合同审查工作流 Agent。"
                    "当前只披露客户类型、问题强度、可公开验证信号和下一步材料范围。"
                ),
                "role": "founder",
                "disclosure_level": "L1",
            },
            secret=founder_secret,
        )
        investor_message = post_json(
            client,
            api_url,
            f"/api/v1/a2a/sessions/{session_id}/messages",
            {
                "from_agent_id": investor["id"],
                "to_agent_id": founder["id"],
                "content": (
                    "请说明目标客户、预算区间、当前最强验证信号，"
                    "以及下一轮白盒沟通需要准备哪些可核验材料。"
                ),
                "role": "investor",
                "disclosure_level": "L1",
            },
            secret=investor_secret,
        )
        validate_message_response(founder_message, "founder", checks)
        validate_message_response(investor_message, "investor", checks)

        standard_task = post_json(
            client,
            api_url,
            "/api/v1/a2a/message:send",
            {
                "message": {
                    "role": "user",
                    "taskId": session_id,
                    "contextId": session_id,
                    "parts": [
                        {
                            "kind": "text",
                            "text": "标准 A2A HTTP+JSON 入口只提交 L1 安全补充摘要。",
                        }
                    ],
                    "metadata": {
                        "from_agent_id": founder["id"],
                        "to_agent_id": investor["id"],
                        "role": "founder",
                        "disclosure_level": "L1",
                    },
                },
                "configuration": {"blocking": True},
            },
            secret=founder_secret,
            extra_headers={"A2A-Version": "0.3"},
        )
        validate_standard_task(standard_task, checks)

        timeline = get_json(
            client,
            api_url,
            f"/api/v1/a2a/sessions/{session_id}/summary",
            secret=founder_secret,
            agent_id=founder["id"],
        )
        validate_timeline_contract(timeline, checks, expected_retention_policy=retention_policy)

        verdict = post_json(
            client,
            api_url,
            f"/api/v1/a2a/sessions/{session_id}/finalize",
            {},
            secret=founder_secret,
            extra_headers={"X-VentureAgent-Agent-Id": founder["id"]},
        )
        validate_final_verdict(verdict, checks)

        finalized_task = get_json(
            client,
            api_url,
            f"/api/v1/a2a/tasks/{session_id}",
            secret=founder_secret,
            agent_id=founder["id"],
            extra_headers={"A2A-Version": "0.3"},
        )
        validate_standard_task(finalized_task, checks)

        provenance = get_json(
            client,
            api_url,
            f"/api/v1/a2a/sessions/{session_id}/provenance",
            secret=founder_secret,
            agent_id=founder["id"],
        )
        validate_provenance(provenance, checks)

    passed = report_passed(checks)
    return {
        "passed": passed,
        "api_url": api_url,
        "retention_policy": retention_policy,
        "session_id": session_id,
        "founder_agent_id": founder["id"],
        "investor_agent_id": investor["id"],
        "summary": {
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "failed": sum(1 for check in checks if not check["passed"]),
        },
        "checks": checks,
        "verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--retention-policy", default="safe_summary_only")
    args = parser.parse_args()

    try:
        report = run_validation(args.api_url.rstrip("/"), args.retention_policy)
    except httpx.HTTPError as exc:
        report = {
            "passed": False,
            "error": str(exc),
            "api_url": args.api_url,
            "retention_policy": args.retention_policy,
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
