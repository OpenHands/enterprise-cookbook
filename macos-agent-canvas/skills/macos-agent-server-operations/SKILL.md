---
name: macos-agent-server-operations
description: This skill should be used when the user asks to "check the remote Mac agent server", "view Mac Agent Server logs", "restart the macOS backend", or "delegate a macOS task to the remote agent server" from an OpenHands Enterprise conversation. Distinguishes operator-only SSH management from authenticated REST access by Enterprise sandboxes.
---

# Operate a macOS Agent Server from Enterprise

Read [the recipe](../../README.md) before using the remote Mac. The Mac runs a separate host
runtime with access to its service user's files and processes, not an isolated Enterprise
sandbox. Its API is plain HTTP in this draft; use it only on the agreed restricted customer
network. Do not treat a firewall as transport encryption.

## Service Management Is Operator-Only

- An operator with existing Mac SSH access runs `./oh-agent-server.sh status`, `logs`, `start`,
  `stop`, or `restart` from the installed checkout. The Enterprise agent has **no Mac SSH key or
  sudo permission**. Ask the operator to run these commands if needed; do not request their
  password, SSH key, or a printed session key in chat.
- The operator's `stop` disables boot startup until `start`; `restart` preserves the key.
  `remove --purge` destroys the installation and conversations. Logs may include sensitive
  output and must not be pasted unredacted into chat.
- `/health` and `/ready` are unauthenticated process checks, not proof that the session key,
  sandbox network path, or LLM profile works. Use the recipe's authenticated, read-only REST
  check from the **Enterprise sandbox** for the first two.

## Before Delegating Work

1. Confirm a private HTTP address and TCP port reachable **from the calling Enterprise sandbox**
   and firewall rules allowing only the intended sandbox network. A service bound to
   `127.0.0.1` is not remotely reachable; `localhost` in Enterprise is not the Mac. Do not use
   a public address or assume operator SSH implies sandbox reachability.
2. Refer to the Enterprise secret by its unique name (`MAC_AGENT_SERVER_KEY`). Its value is
   injected into the sandbox terminal environment when the command references the name; never
   query the registry for the value, print the variable, put it in a URL, or include it in a
   literal command argument. Confirm personal/organization scope and Agent Profile selection.
3. Check that the Mac has an active, responding LLM profile. The session key is not an LLM
   provider key. If none exists, stop until the LLM provisioning installment is complete.
4. Before starting a Mac Agent Server conversation, confirm the workspace path, service user,
   tool permissions, expected outputs, and data sensitivity with the user. Authenticate REST
   calls using `X-Session-API-Key` and observe the result. Do not default to unrestricted host
   changes or assume `/ready` means a conversation will run successfully.

The later delegation installment will supply a validated conversation-start request and REST
monitoring commands after testing the Mac and LLM profile end to end. Plain HTTP must be
replaced with TLS or an encrypted private tunnel before sensitive or production use.
