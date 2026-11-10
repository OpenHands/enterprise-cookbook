---
name: macos-agent-server-install
description: This skill should be used when the user asks to "install Agent Server on my Mac", "set up macOS for Enterprise REST access", or "save the Mac session key in Enterprise". Guides an operator-run SSH installation and one-time session-key handoff through Enterprise Secrets.
---

# Install a macOS Agent Server for Enterprise REST Access

Follow [the recipe](../../README.md) for commands and prerequisites. The **operator**, not the
Enterprise agent, SSHs into the Mac, authorizes sudo, runs the installer, and pastes the generated
session key into Enterprise `Settings > Secrets`. Do not request an SSH private key or sudo
password for an agent conversation. The agent later calls the Mac's REST API from its Enterprise
sandbox; no Enterprise SSH access is needed.

## Before Installation

1. Confirm the operator has authorized access to the Mac, verified its SSH host key, and chosen
   a service account. A dedicated non-admin account needs a home and `uv` accessible before
   `--user ACCOUNT` is used. The Mac's agent-server runs tools as that account without
   container isolation.
2. Confirm the Mac's hostname resolves from the intended Enterprise sandboxes and the customer
   firewall restricts TCP `18100` to those sandboxes. On first install, the server binds to
   **all Mac interfaces** (`0.0.0.0`) unless explicitly configured otherwise; this makes the
   firewall restriction essential. An existing install may retain a different bind address.
   SSH reachability from the operator's workstation is not proof of REST reachability from
   Enterprise.
3. Explain the draft's **plain-HTTP risk** before proceeding: the session key, requests, and
   responses are unencrypted in transit. A restricted customer network does not provide TLS.
   Do not call this production-safe or use it for sensitive workloads.
4. Ask whether the Enterprise secret should be personal or organization-shared. Shared secrets
   reach every member's agent runtime in the organization and require Admin/Owner rights to
   create. Check for a preexisting secret name before installation; do not replace one
   accidentally.

## Operator Steps

1. Have the operator run the cookbook's clone/install command **in their own Mac SSH shell**, as
   a non-root Mac administrator with sudo authorization. The installer generates both keys,
   registers launchd, and checks `/ready`; it does not print the key on install. If there is
   an existing checkout, inspect its changes and update deliberately before reinstalling.
2. Have the operator run `./oh-agent-server.sh key` **in that same private terminal**, copy the
   displayed value into Enterprise `Settings > Secrets` as `MAC_AGENT_SERVER_KEY`, and choose
   the intended scope. Avoid screen sharing, terminal recording, clipboard sync, chat, command
   arguments, and workspace files while handling the value. Clear the clipboard and terminal
   scrollback afterward. Do not retrieve, print, or ask for the key in an agent conversation.
3. Start a new Enterprise conversation with access to the secret. Verify authenticated
   `GET /api/settings` from **its sandbox** with an `X-Session-API-Key` header referencing
   `$MAC_AGENT_SERVER_KEY`, suppressing the response body. A Mac-local `/ready` succeeds without
   authentication and cannot prove this path works. If the check fails, investigate the private
   address, firewall, sandbox egress, secret scope, and Agent Profile selection without asking
   the operator to reveal the value.

## Boundaries

- This draft installs and verifies REST access. It does **not** configure a Mac LLM profile or
  demonstrate successful agent delegation. The session key is not an LLM provider key.
- Only the operator manages the launchd service by SSH; the Enterprise agent must not assume it
  can restart the daemon or read files on the Mac without an authenticated Agent Server API call.
- Key rotation is not part of first setup. If a saved secret already exists, stop and reconcile
  it with the Mac rather than replacing it with a mismatched value.
- Do not install, remove, rotate, or contact a real Mac without the user's explicit permission.
  Report the host, port, secret **name and scope**, and test status, never the secret value.
