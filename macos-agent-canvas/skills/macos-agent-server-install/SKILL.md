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
3. Default to operator-assisted replacement as the installed service user. Verify the checkout, then stream the key over SSH stdin with `ssh -T` to `IFS= read -r OHAS_SESSION_API_KEY`. Call `./oh-agent-server.sh key --reset --defer-restart`; it saves the key without sudo. Only after that returns successfully, open a **separate** `ssh -t` session for an operator to authorize `restart`. Do not send the key to a pseudo-terminal or combine it with a sudo password prompt. Use the copy-pasteable commands in the recipe.
4. For optional unattended rotation, ask the Mac administrator to approve a narrowly scoped sudoers rule for `/bin/launchctl print` and `kickstart -k` on this daemon only, not `NOPASSWD: ALL`. Verify both exact permissions with `sudo -n -l` after `sudo -k` **without** sending a key; `sudo -n -v` is not sufficient. Only then use `key --reset --unattended`, which never calls `sudo -v` and uses `sudo -n`. Never edit sudoers or make this permission change without authorization.
5. The installer validates and removes the supplied key from child-process environments, then stores it with restricted permissions. Unset the Enterprise-side variable **after it is saved**; if the separate restart fails, retry only `restart`, not key replacement. Check remote `status`; distinguish a working `/ready` from authenticated API access and a configured LLM. Record the host/port and secret **name and scope** for the user, never the secret value.

## Recovery and Boundaries

- If secret storage succeeded but rotation failed, retain the generated value in the current process until the vault and Mac agree. Avoid `key` without `--reset` or `--copy` because it prints the key. Do not rotate again with an unrelated value while the stored secret is stale.
- Reinstalling the same service user and path preserves stored conversations and keys unless a new `OHAS_SESSION_API_KEY` is supplied. Never use `remove --purge` for routine recovery.
- Do not assume installation sets a model. Provisioning the Enterprise LiteLLM BYOR key and the Mac LLM profile is a later step; `GET /ready` alone does not prove agent conversations can run.
- Confirm live macOS, SSH and firewall behavior with the test Mac before claiming this recipe is production-ready. Do not run install, rotation, or purge without the user's authorization.
