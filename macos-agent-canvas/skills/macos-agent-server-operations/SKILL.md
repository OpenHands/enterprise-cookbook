---
name: macos-agent-server-operations
description: This skill should be used when the user asks to "check the remote Mac agent server", "view Mac Agent Server logs", "restart the macOS backend", or "delegate a macOS task to the remote agent server" from an OpenHands Enterprise conversation. Distinguishes operator-only SSH management from authenticated REST access by Enterprise sandboxes.
---

# Operate a macOS Agent Server from Enterprise

Read [the recipe](../../README.md) before using the remote Mac. The Mac runs a separate host
runtime with access to its service user's files and processes, not an isolated Enterprise
sandbox. The private-network route serves HTTPS through the Mac's TLS front end with
`MAC_AGENT_SERVER_CA` pinned in the sandbox; the temporary ngrok demo serves public-CA HTTPS
through ngrok to the Mac's loopback-only HTTP server. Possession of the session key permits
command execution as the service user, so keep the private route network-restricted and
stop the ngrok tunnel after the demo.

## Service Management Is Operator-Only

- An operator with existing Mac SSH access runs `./oh-agent-server.sh status`, `logs`
  (`logs --tls` only for the private-network TLS front end), `start`, `stop`, or `restart`
  from the installed checkout. These act on the installed launchd service(s), not ngrok.
  The Enterprise agent has **no Mac SSH key or sudo permission**. Ask the operator to run
  these commands if needed; do not request their password, SSH key, or a printed session key
  in chat.
- The operator's `stop` disables boot startup until `start`; `restart` preserves the key.
  `remove --purge` destroys the installation and conversations. Logs may include sensitive
  output and must not be pasted unredacted into chat.
- `/health` and `/ready` are unauthenticated process checks, not proof that the session key,
  sandbox network path, or LLM profile works. Use the recipe's authenticated, read-only REST
  check from the **Enterprise sandbox** for the first two. In the private-network route,
  `status` also reports the certificate's expiry; a replaced certificate needs a new
  `MAC_AGENT_SERVER_CA` secret. Ngrok's status is checked separately by the operator.

## Before Delegating Work

1. Confirm the chosen HTTPS address is reachable **from the calling Enterprise sandbox**.
   For the private route, check that the certificate matches the host and the firewall allows
   only the intended sandbox network to reach port `18443`. For the temporary ngrok route,
   check that its agent is still running on the Mac and its public HTTPS endpoint is online.
   `localhost` in Enterprise is not the Mac; operator SSH does not prove sandbox reachability.
2. Refer to the Enterprise secrets by their unique names (`MAC_AGENT_SERVER_KEY`, and
   `MAC_AGENT_SERVER_CA` only for the private-network certificate). Their values are injected
   into the sandbox terminal environment when the command references the name; never query
   the registry for them, print the variables, put the key in a URL, or include it in a literal
   command argument. Confirm personal/organization scope and Agent Profile selection.
3. Check that the Mac has an active, responding LLM profile. The session key is not an LLM
   provider key. If none exists, or the check fails, use the
   [LLM setup skill](../macos-agent-server-llm-setup/SKILL.md) (`sync_llm.py --check`, then the
   sync) before going on.
4. Before starting a Mac Agent Server conversation, confirm the workspace path, service user,
   tool permissions, expected outputs, and data sensitivity with the user. Authenticate REST
   calls using `X-Session-API-Key` and observe the result. Do not default to unrestricted host
   changes or assume `/ready` means a conversation will run successfully.

The later delegation installment will supply a validated conversation-start request and REST
monitoring commands after testing the Mac and LLM profile end to end.
