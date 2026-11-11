---
name: macos-agent-server-install
description: This skill should be used when the user asks to "install Agent Server on my Mac", "set up macOS for Enterprise REST access", or "save the Mac session key in Enterprise". Guides operator-run SSH installation with private-network TLS or a temporary ngrok demo, and the session key handoff through Enterprise Secrets.
---

# Install a macOS Agent Server for Enterprise REST Access

Follow [the recipe](../../README.md) for commands and prerequisites. The **operator**, not the
Enterprise agent, SSHs into the Mac, authorizes sudo, runs the installer, and saves the
generated session key in Enterprise `Settings > Secrets` (and the Mac certificate for the
private-network route). Do not request an SSH private key or sudo password for an agent
conversation. The agent later calls the Mac's REST API over HTTPS from its Enterprise sandbox;
no Enterprise SSH access is needed.

## Before Installation

1. Confirm the operator has authorized access to the Mac, verified its SSH host key, and chosen
   a service account. A dedicated non-admin account needs a home and `uv` accessible before
   `--user ACCOUNT` is used. The Mac's agent-server runs tools as that account without
   container isolation.
2. Ask which HTTPS route the operator wants. For the private-network route, get the Mac host
   name/IP the sandboxes can reach, use `--tls-name NAME`, and limit the firewall's TCP `18443`
   access to the sandbox network; `--allow-from CIDR` adds a second restriction. For a temporary
   ngrok demo, confirm an available hostname and an authenticated ngrok agent on the Mac. SSH
   reachability from the operator's workstation is not proof of REST reachability from Enterprise.
3. Explain what the protection covers. The Agent Server stays on `127.0.0.1` in both routes;
   private-network clients pin the Mac certificate, while ngrok terminates public HTTPS and
   forwards over loopback. Anyone with the session key can run commands as the service user;
   the ngrok route exposes a public API, so disable request inspection and stop the tunnel
   after the demo. The private route should use the firewall and `--allow-from`; a dedicated
   service account is useful in either route.
4. Ask whether Enterprise secrets should be personal or organization-shared. Shared secrets
   reach every member's agent runtime in the organization and require Admin/Owner rights to
   create. Check for an existing `MAC_AGENT_SERVER_KEY` (and `MAC_AGENT_SERVER_CA` for the
   private route) before installation; do not replace one accidentally.

## Operator Steps

1. Have the operator clone/install **in their own Mac SSH shell**, as a non-root Mac
   administrator with sudo authorization: `install --tls-name NAME` for the private-network
   route or `install --no-tls --host 127.0.0.1` for ngrok. The installer generates both keys
   and (for the private route) the certificate, registers the appropriate launchd service(s),
   and never prints the session key on install. The operator separately starts ngrok with
   `--inspect=false` and an available HTTPS hostname for the demo. If there is an existing
   checkout, inspect its changes and update deliberately before reinstalling.
2. Have the operator run `./oh-agent-server.sh key` **in that same private terminal**, copy the
   displayed value into Enterprise `Settings > Secrets` as `MAC_AGENT_SERVER_KEY`, and choose
   the intended scope. Avoid screen sharing, terminal recording, clipboard sync, chat, command
   arguments, and workspace files while handling the value. Clear the clipboard and terminal
   scrollback afterward. Do not retrieve, print, or ask for the key in an agent conversation.
3. For the private-network route only, have the operator run `./oh-agent-server.sh ca` and save
   its one-line output as `MAC_AGENT_SERVER_CA` with the same scope. It is a public certificate.
4. Start a new Enterprise conversation with access to the required secret(s). Verify
   authenticated `GET /api/settings` from **its sandbox** over HTTPS, with an
   `X-Session-API-Key` header referencing `$MAC_AGENT_SERVER_KEY`, suppressing the response
   body. For the private-network route, pin `$MAC_AGENT_SERVER_CA` with `curl --cacert`;
   for ngrok, use normal public-CA verification without `--cacert`. A Mac-local `/ready`
   succeeds without authentication and cannot prove this path works. If the check fails,
   investigate the hostname, network/endpoint reachability, certificate trust, secret scope,
   and Agent Profile selection without asking the operator to reveal the key.

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
