---
name: macos-agent-server-install
description: This skill should be used when the user asks to "install Agent Server on my Mac", "set up macOS for Enterprise REST access", or "save the Mac session key in Enterprise". Guides an operator-run SSH installation behind a TLS front end, and the one-time handoff of the session key and certificate through Enterprise Secrets.
---

# Install a macOS Agent Server for Enterprise REST Access

Follow [the recipe](../../README.md) for commands and prerequisites. The **operator**, not the
Enterprise agent, SSHs into the Mac, authorizes sudo, runs the installer, and pastes the generated
session key and certificate into Enterprise `Settings > Secrets`. Do not request an SSH private key
or sudo password for an agent conversation. The agent later calls the Mac's REST API over HTTPS
from its Enterprise sandbox; no Enterprise SSH access is needed.

## Before Installation

1. Confirm the operator has authorized access to the Mac, verified its SSH host key, and chosen
   a service account. A dedicated non-admin account needs a home and `uv` accessible before
   `--user ACCOUNT` is used. The Mac's agent-server runs tools as that account without
   container isolation.
2. Ask which **host name or IP address Enterprise sandboxes use to reach the Mac**. The installer
   issues its certificate for that name (`--tls-name`), and clients refuse any other name. Confirm
   it resolves from the intended sandboxes, and that the customer firewall allows TCP `18443`
   (the TLS port) only from them. `--allow-from CIDR` adds a second limit inside the Mac. SSH
   reachability from the operator's workstation is not proof of REST reachability from Enterprise.
3. Explain what the protection covers. The Agent Server listens only on `127.0.0.1`; a TLS front
   end on the Mac's network port encrypts traffic, and the sandbox trusts only that Mac's
   certificate. Anyone with the session key can still run commands as the service user, so the
   firewall, `--allow-from`, and a dedicated account matter.
4. Ask whether the Enterprise secrets should be personal or organization-shared. Shared secrets
   reach every member's agent runtime in the organization and require Admin/Owner rights to
   create. Check for preexisting `MAC_AGENT_SERVER_KEY` and `MAC_AGENT_SERVER_CA` secrets before
   installation; do not replace one accidentally.

## Operator Steps

1. Have the operator run the cookbook's clone/install command **in their own Mac SSH shell**, as
   a non-root Mac administrator with sudo authorization, with `--tls-name NAME`. The installer
   generates both keys and the certificate, registers two launchd services, and checks both; it
   does not print the key on install. If there is an existing checkout, inspect its changes and
   update deliberately before reinstalling.
2. Have the operator run `./oh-agent-server.sh key` **in that same private terminal**, copy the
   displayed value into Enterprise `Settings > Secrets` as `MAC_AGENT_SERVER_KEY`, and choose
   the intended scope. Avoid screen sharing, terminal recording, clipboard sync, chat, command
   arguments, and workspace files while handling the value. Clear the clipboard and terminal
   scrollback afterward. Do not retrieve, print, or ask for the key in an agent conversation.
3. Have the operator run `./oh-agent-server.sh ca` and save its one-line output as
   `MAC_AGENT_SERVER_CA` with the same scope. It is a public certificate, so it is safe to show.
4. Start a new Enterprise conversation with access to both secrets. Verify authenticated
   `GET /api/settings` from **its sandbox** over HTTPS, with an `X-Session-API-Key` header
   referencing `$MAC_AGENT_SERVER_KEY` and `curl --cacert` fed from `$MAC_AGENT_SERVER_CA`,
   suppressing the response body. A Mac-local `/ready` succeeds without authentication and
   cannot prove this path works. If the check fails, investigate the host name against the
   certificate, the firewall and `--allow-from`, sandbox egress, secret scope, and Agent Profile
   selection without asking the operator to reveal the key.

## Boundaries

- This installs and verifies REST access. To give the Mac an LLM and secrets, use the
  [LLM setup skill](../macos-agent-server-llm-setup/SKILL.md). The session key is not an LLM
  provider key. Neither skill demonstrates successful agent delegation.
- Only the operator manages the launchd services by SSH; the Enterprise agent must not assume it
  can restart them or read files on the Mac without an authenticated Agent Server API call.
- Key rotation is not part of first setup. If a saved secret already exists, stop and reconcile
  it with the Mac rather than replacing it with a mismatched value. The certificate expires
  after about 825 days; renewing it means saving a new `MAC_AGENT_SERVER_CA`.
- Do not install, remove, rotate, or contact a real Mac without the user's explicit permission.
  Report the host, port, secret **names and scope**, and test status, never a secret value.
