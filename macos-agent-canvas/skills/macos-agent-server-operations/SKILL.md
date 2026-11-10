---
name: macos-agent-server-operations
description: This skill should be used when the user asks to "check the remote Mac agent server", "view Mac Agent Server logs", "restart the macOS backend", or "delegate a macOS task to the remote agent server" from an OpenHands Enterprise conversation. Covers safe day-to-day operations and delegation prerequisites.
---

# Operate a Remote macOS Agent Server

Read [the recipe](../../README.md) before using a remote Mac. Treat the Mac as a distinct host runtime with direct access to that user's files and processes, not as an isolated Enterprise sandbox. Confirm the target host and obtain permission before changing service state or running a new agent conversation.

## Service Management

- Run `./oh-agent-server.sh status` over the established SSH connection from the existing Mac checkout; report the service, PID, and readiness without reading or printing its key. Use `logs` only after considering whether logs may contain sensitive output; never paste unredacted credentials into chat.
- Use `start`, `stop`, or `restart` only on request. `stop` disables boot startup until `start`; `restart` preserves the key. Avoid `remove --purge` and `key` without `--reset` for routine operations.
- Treat `/health` and `/ready` as unauthenticated process checks, **not** proof that the session key or LLM profile works. Confirm authentication through a key-protected API over a private path once that path is configured.

## Before Delegating Work

1. Identify the Mac's private endpoint. A service bound to `127.0.0.1` needs a tunnel in the **Enterprise conversation's network namespace**; Enterprise's `localhost` does not reach the Mac directly. A VPN endpoint must be reachable from that namespace. Do not expose port `18100` publicly or skip SSH host-key verification.
2. Refer to the Enterprise secret by its unique name (for example, `MAC_AGENT_SERVER_KEY`) so the secret is injected into the terminal environment. Never query the vault for the value, print the variable, add it to a URL, or place it in a command argument. Confirm the secret's personal or organization scope and any Agent Profile secret selection.
3. Check that the Mac has an active, responding LLM profile. The server's session key is **not** the Enterprise LiteLLM BYOR key. If no profile exists, stop and finish the LLM provisioning installment first; do not create an LLM key or profile just to make a health check pass.
4. Before starting a remote Agent Server conversation, confirm the Mac workspace path, service user, tool permissions, expected outputs, and data sensitivity with the user. Send an explicit workspace and task-specific prompt; do not default to unrestricted changes on the host. Authenticate API calls with `X-Session-API-Key` over the private route and observe the resulting conversation before reporting success.

This first version of the skill supports service checks and lays out guardrails. The recipe's later delegation installment will supply a validated conversation-start request and event-monitoring commands after testing the Mac and LLM profile end to end.
