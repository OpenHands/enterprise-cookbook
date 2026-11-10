---
name: macos-agent-server-install
description: This skill should be used when the user asks to "install Agent Server on my Mac", "set up a remote macOS backend", "rotate the Mac session API key", or "save the Mac session key in Enterprise". Guides SSH installation and a secret-safe key handoff.
---

# Install a Remote macOS Agent Server

Follow [the recipe](../../README.md) for the commands, prerequisites, and current testing status. Run commands in an OpenHands Enterprise conversation that can reach the Mac over SSH, not on the Mac unless a step explicitly says so.

## Before Changing the Mac

1. Obtain the SSH destination, desired service user and port, network path, and permission to install or rotate the service. Inspect the remote host, shell, checkout, `git`, `uv`, and sudo policy. Verify the SSH host key; never add `StrictHostKeyChecking=no`.
2. Default to `--host 127.0.0.1` and plan a private tunnel for later access. Do not bind the service to a public interface just to make the demo work. Its session key authorizes arbitrary shell commands as the service user.
3. Ask whether the key belongs in a *personal* secret or a shared *organization* secret. Shared secrets reach every member's agent runtime in that org; require Admin/Owner permissions. Check for an existing stored key before generating a replacement. Use a unique secret name such as `MAC_AGENT_SERVER_KEY` and avoid name collisions across scopes.

## Install and Rotate

1. Clone `OpenHands/enterprise-cookbook` on the Mac, inspect `macos-agent-canvas/oh-agent-server.sh`, then run `install` from that directory as a non-root administrator. Request interactive sudo authorization for install. Never pipe a downloaded script to a shell. The installer generates an initial key and an independent encryption key; it does not display them.
2. Generate a random 64-character hex session key with `openssl rand -hex 32` inside the Enterprise session. Save it via the personal secrets API (`POST /api/v1/secrets`) or the org secrets API (`POST /api/organizations/{org_id}/secrets`), using the correct user's Enterprise API credential. Send the JSON body on stdin. Never print the key or put it in a prompt, URL, command argument, workspace file, or Git. If a stored key already exists, reuse it rather than generating an unnecessary rotation.
3. Verify that `sudo -n -v` succeeds over `ssh -T` **before** sending the key. If it fails, stop and ask for an operator-assisted plan; a pseudo-terminal or sudo password prompt would expose the piped key. Stream the key over SSH stdin with `ssh -T` to `IFS= read -r OHAS_SESSION_API_KEY`, export it only in the remote shell, then call `./oh-agent-server.sh key --reset`. The installer validates and removes that variable from child-process environments, stores the key with restricted permissions, and restarts a running service. Use the copy-pasteable command in the recipe; do not improvise shell quoting around the secret.
4. Unset the temporary Enterprise-side variable **only after successful rotation**. Check remote `status`; distinguish a working `/ready` from authenticated API access and a configured LLM. Record the host/port and secret **name and scope** for the user, never the secret value.

## Recovery and Boundaries

- If secret storage succeeded but rotation failed, retain the generated value in the current process until the vault and Mac agree. Avoid `key` without `--reset` or `--copy` because it prints the key. Do not rotate again with an unrelated value while the stored secret is stale.
- Reinstalling the same service user and path preserves stored conversations and keys unless a new `OHAS_SESSION_API_KEY` is supplied. Never use `remove --purge` for routine recovery.
- Do not assume installation sets a model. Provisioning the Enterprise LiteLLM BYOR key and the Mac LLM profile is a later step; `GET /ready` alone does not prove agent conversations can run.
- Confirm live macOS, SSH and firewall behavior with the test Mac before claiming this recipe is production-ready. Do not run install, rotation, or purge without the user's authorization.
