# macOS Agent Server from OpenHands Enterprise

An operator installs OpenHands Agent Server on a Mac over SSH from their own workstation.
After installation, an OpenHands Enterprise conversation calls the Mac's Agent Server REST API
from its sandbox using a session key saved in Enterprise `Settings > Secrets`. Enterprise does
**not** SSH to the Mac or receive the operator's SSH credentials. The Mac is a separate host,
not an Enterprise sandbox. This draft has not been tested on a real Mac.

> [!WARNING]
> **Restricted-network evaluation only: this draft uses plain HTTP.** The session API key,
> requests, and responses travel without transport encryption. A firewall limits who can
> connect, but does not prevent someone with access to the internal network path from reading
> or modifying traffic. Possession of the key permits agent execution as the Mac service user.
> Use only on a customer-controlled, tightly restricted network with explicit acceptance of
> this risk. Do not use for sensitive or production workloads; add TLS or an encrypted private
> tunnel before broader use. Printing the key once in the operator's terminal also exposes it
> to terminal scrollback, screen sharing, and recordings.

## How It Works

```mermaid
sequenceDiagram
    participant O as Operator workstation
    participant M as Mac over operator SSH
    participant V as Enterprise secret settings
    participant E as Enterprise sandbox
    participant A as Mac Agent Server
    O->>M: Install as Mac admin (authorize sudo)
    M->>A: Register launchd service; generate session key
    M-->>O: Show key once in operator terminal
    O->>V: Paste key into Settings > Secrets
    E->>A: Call REST API over restricted internal HTTP with key header
```

The installer generates an independent `OH_SECRET_KEY` to encrypt the Mac server's stored
secrets. The session key authenticates HTTP clients; it is **not** an LLM provider key or an SSH
key. See the [install skill](./skills/macos-agent-server-install/SKILL.md) for the agent-facing
checklist and the [operations skill](./skills/macos-agent-server-operations/SKILL.md) for later
management. Both are bundled in this recipe's [plugin](./.claude-plugin/plugin.json).

## Prerequisites

- The operator has a working SSH login as a **non-root macOS administrator** and can authorize
  `sudo`. Verify the Mac's SSH host key before accepting it. These credentials remain with the
  operator, never in Enterprise Secrets or an agent conversation.
- The Mac has `git`, `curl`, `openssl`, and `uv` available to the service account. The installer
  creates a Python 3.12 virtual environment. By default, the service runs as the installing
  account; for a dedicated non-admin service account, provision its home and `uv` first and
  pass `--user ACCOUNT` to the installer. That account's files and processes will be available
  to anyone with the session key. The operator still runs the installer and authorizes sudo.
- Choose a **stable private IP address assigned to the Mac** and a hostname resolvable from
  Enterprise sandboxes. The customer's firewall must allow TCP port `18100` **only from the
  intended Enterprise sandbox network**. Verify that sandbox egress and the Mac's application
  firewall permit the connection. SSH is needed only between the operator workstation and Mac;
  successful operator SSH does not establish sandbox-to-Mac reachability. Do not expose the API
  on the public internet.
- The operator has Enterprise browser access to the intended organization and
  `Settings > Secrets`. A member can save a personal secret; an Admin/Owner is required for an
  organization-shared secret. A new conversation must have the secret in its scope and, if
  applicable, selected by its Agent Profile.

## Install on the Mac

From a terminal on **your own workstation**, SSH into the Mac as the administrator. Verify its
SSH host key before accepting it:

```bash
export MAC_SSH=admin@mac.example.net
ssh "$MAC_SSH"
```

In that **Mac shell**, replace `10.0.0.25` with the Mac's actual stable private interface
address. Until this recipe is merged, use the `macOS-agent-canvas` branch; change it to `main`
after publication. Review the source beforehand, or separate the clone and install to inspect
the downloaded files. The `&&` operators stop the install if an earlier command fails. Do not
pipe a downloaded script to a shell.

```bash
export MAC_PRIVATE_IP=10.0.0.25
git clone --branch macOS-agent-canvas https://github.com/OpenHands/enterprise-cookbook.git "$HOME/enterprise-cookbook" &&
  cd "$HOME/enterprise-cookbook/macos-agent-canvas" &&
  ./oh-agent-server.sh install --host "$MAC_PRIVATE_IP"
```

For a dedicated existing service account, add `--user ACCOUNT` to the install command; verify
`uv` is available to that user first. The script runs as the **operator**, not root, and prompts
for the Mac administrator's sudo password when registering or starting the launchd daemon. It
installs a session key and an independent encryption key but **does not print either key**.
If this checkout already exists, inspect its branch and changes, update it with `git fetch` /
`git checkout`, and install from `macos-agent-canvas`. Reinstalling with the same user/path
preserves keys and data unless explicitly overridden.

The installer checks readiness automatically. To check the unauthenticated endpoint yourself
from the same Mac shell (substitute the actual address/port if changed), run:

```bash
curl -fsS --max-time 3 "http://$MAC_PRIVATE_IP:18100/ready"
```

A successful `/ready` response confirms the process, **not** API authentication, network
reachability from an Enterprise sandbox, or a configured LLM.

## Save the Session Key in Enterprise

In the **Mac shell on the operator workstation**, deliberately display the generated key once:

```bash
./oh-agent-server.sh key
```

The key appears in that terminal's output. Copy it directly into the Enterprise **web UI**:
select the intended organization, open `Settings > Secrets`, choose `Add a new secret`, name it
`MAC_AGENT_SERVER_KEY`, and paste the value into the **value** field. For a personal secret,
leave `Share with organization` unchecked; only an Admin/Owner should check it when every member
of that organization is authorized to control the Mac. Confirm the secret name appears.
Do not paste the key into chat, a command argument, a URL, Git, or an agent transcript. Prevent
terminal recording or screen sharing while displaying it; clear the workstation clipboard and
terminal scrollback when finished. Enterprise settings will not show the stored value again.

If the secret name already exists, **stop**: do not overwrite the Mac key or create a second
value unintentionally. Replacing a saved value in the UI requires deleting and re-adding that
secret, and existing conversations may retain an older value. Key rotation is outside this
first-install flow. See [Enterprise Secrets Management](https://docs.openhands.dev/enterprise/secrets-management)
for scope and rotation details. Once the value is saved, the operator may exit the Mac SSH
shell; no Enterprise SSH credential is needed.

## Verify from an Enterprise Sandbox

Start a **new** Enterprise conversation in the organization with access to
`MAC_AGENT_SERVER_KEY`. If using this recipe as a plugin, include the following `plugins` field
in the conversation-start request; this is JSON for the request, **not** a shell command:

```json
{
  "plugins": [{
    "source": "github:OpenHands/enterprise-cookbook",
    "ref": "macOS-agent-canvas",
    "repo_path": "macos-agent-canvas"
  }]
}
```

See [`load-plugin`](../load-plugin/) for a full conversation-start example. Plugins provide
instructions, not credentials or network access. In the **Enterprise sandbox's terminal**,
replace `mac.internal.example` with the Mac's private hostname and run this **read-only,
authenticated** REST check. The secret is exported into the command environment when referenced
by name; neither the command nor its output displays its value:

```bash
curl -fsS --max-time 10 --output /dev/null \
  -H "X-Session-API-Key: $MAC_AGENT_SERVER_KEY" \
  'http://mac.internal.example:18100/api/settings' &&
  printf 'Authenticated Mac Agent Server API is reachable.\n'
```

`/api/settings` requires the session key; suppressing its response avoids displaying settings
in the transcript. If this fails, stop and check the Mac address, firewall and sandbox egress,
secret scope, and Agent Profile selection. Do not paste the key into chat to debug it. A
successful request proves this sandbox can authenticate to this API, **not** that the Mac has an
LLM profile or can run an agent conversation.

> [!IMPORTANT]
> This recipe has not been exercised against a real Mac. Verify macOS launchd, application
> firewall behavior, Enterprise sandbox egress, and authenticated REST access on a test Mac
> before publishing it as a working installation guide.

## Manage the Service

Run these from your operator workstation; each SSH command uses the Mac checkout (and a
pseudo-terminal when sudo may prompt):

```bash
ssh "$MAC_SSH" 'cd "$HOME/enterprise-cookbook/macos-agent-canvas" && ./oh-agent-server.sh status'
ssh "$MAC_SSH" 'cd "$HOME/enterprise-cookbook/macos-agent-canvas" && ./oh-agent-server.sh logs'
ssh -t "$MAC_SSH" 'cd "$HOME/enterprise-cookbook/macos-agent-canvas" && ./oh-agent-server.sh restart'
```

`start` re-enables startup at boot, `stop` disables it until a later `start`, and `restart`
preserves the key. `remove --purge` destroys the installation and conversations; it is not
routine management. `key` displays the credential, so use it only in a private operator
terminal. The default directory is the service user's `~/.openhands-agent-server` (virtual
environment, logs, data, `config.env`). That directory is mode `700`; `config.env` is mode
`600`. Changing service users does not migrate keys or conversations.

## Later Work

1. **Encrypted transport:** Add TLS or an encrypted private tunnel before sensitive or
   production use; plain HTTP exposes the session key to the network path. Keep the Agent
   Server API off the public internet.
2. **LLM profile:** Check that BYOR key export is permitted in Enterprise, discover models
   served by its LiteLLM proxy, ensure that proxy is reachable from the Mac, then configure a
   Mac Agent Server LLM profile. The Enterprise app API key, LLM provider key, and Mac session
   key are different credentials.
3. **Delegate macOS work:** Start and monitor Mac Agent Server conversations with authenticated
   REST requests from the Enterprise sandbox after validating the LLM profile and the host's
   tool permissions. No Enterprise SSH access is part of this flow.

## APIs Used

| Endpoint | Purpose |
|---|---|
| `GET /ready` on the Mac | Unauthenticated process check |
| `GET /api/settings` on the Mac | Read-only authenticated REST connectivity check |

The operator saves the key via Enterprise `Settings > Secrets`, not an Enterprise API call.

## Related

<!-- docs:cards -->

- [Enterprise Secrets Management](https://docs.openhands.dev/enterprise/secrets-management) - Secret scopes and permissions
- [Agent Server Overview](https://docs.openhands.dev/sdk/guides/agent-server/overview) - The Mac's HTTP REST service
- [`aws-secrets-sync`](../aws-secrets-sync/) - Another example of personal and organization secrets

<!-- /docs:cards -->
