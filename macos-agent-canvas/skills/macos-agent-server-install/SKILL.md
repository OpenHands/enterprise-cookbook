---
name: macos-agent-server-install
description: This skill should be used when the user asks to "install Agent Server on my Mac", "set up a remote macOS backend", "rotate the Mac session API key", or "save the Mac session key in Enterprise". Guides SSH installation and a secret-safe key handoff.
---

# Install a Remote macOS Agent Server

Follow [the recipe](../../README.md) for commands, prerequisites, and testing status. Installation starts from the **operator's workstation**, not an Enterprise sandbox: SSH into the Mac once, run the installer in that Mac shell, and keep it open. Save the new key through Enterprise `Settings > Secrets` and send it from a second workstation terminal. Return to the Mac shell for the operator-authorized restart.

## Before Changing the Mac

1. Obtain the SSH destination, desired service user and port, network path, and permission to install or rotate the service. Inspect the remote host, shell, checkout, `git`, `uv`, and sudo policy. Verify the SSH host key; never add `StrictHostKeyChecking=no`.
2. Default to `--host 127.0.0.1` and plan a private tunnel for later access. Do not bind the service to a public interface just to make the demo work. Its session key authorizes arbitrary shell commands as the service user.
3. Ask whether the key belongs in a *personal* secret or a shared *organization* secret. Shared secrets reach every member's agent runtime in that org; require Admin/Owner permissions. Check for an existing stored key before generating a replacement. Use a unique secret name such as `MAC_AGENT_SERVER_KEY` and avoid name collisions across scopes.

## Install and Rotate

1. Open one interactive SSH login to the Mac as a non-root administrator. In that same Mac shell, clone the draft cookbook branch and run `./oh-agent-server.sh install --host 127.0.0.1` from `macos-agent-canvas` using the recipe's single compound command. If already cloned, inspect the checkout before updating and reinstalling in the same session. Review the installer source before running the compound command, or separate cloning and execution if inspection of downloaded files is required; never pipe a downloaded script to a shell. Authorize sudo interactively there. The installer generates an initial session key and an independent encryption key; it does not display them.
2. On a macOS operator workstation, use `openssl rand -hex 32 | tr -d '\n' | pbcopy` to generate a key without printing it. Have the operator select the intended organization, open Enterprise `Settings > Secrets`, add `MAC_AGENT_SERVER_KEY`, paste it into the value field, and explicitly choose personal or organization scope; shared scope requires Admin/Owner access. No Enterprise terminal or API credential is needed for installation. For a non-Mac workstation use a trusted password manager and adapt the clipboard transfer. Keep the value locally until the Mac has accepted it and restarted; the UI cannot reveal an existing value. Do not put it in a prompt, URL, literal command argument, workspace file, or Git.
3. Default to operator-assisted replacement as the installed service user. From the second **operator workstation** terminal, verify the checkout and stream the local clipboard key over `ssh -T` to `IFS= read -r OHAS_SESSION_API_KEY`. Call `./oh-agent-server.sh key --reset --defer-restart`; it saves the key without sudo. Only after it succeeds, return to the **original Mac SSH shell** and have the operator authorize `restart` there. Do not send the key to a pseudo-terminal or combine it with a sudo password prompt. Use the copy-pasteable commands in the recipe.
4. For optional unattended rotation, ask the Mac administrator to approve a narrowly scoped sudoers rule for `/bin/launchctl print` and `kickstart -k` on this daemon only, not `NOPASSWD: ALL`. Verify both exact permissions with `sudo -n -l` after `sudo -k` **without** sending a key; `sudo -n -v` is not sufficient. Only then use `key --reset --unattended`, which never calls `sudo -v` and uses `sudo -n`. Never edit sudoers or make this permission change without authorization.
5. The installer validates and removes the supplied key from child-process environments, then stores it with restricted permissions. Keep the workstation clipboard value until both vault and Mac agree; if the operator restart fails, retry only `restart`, not key replacement. Check remote `status`, then clear the local clipboard. Distinguish a working `/ready` from authenticated API access and a configured LLM. Record the host/port and secret **name and scope** for the user, never the secret value.

## Recovery and Boundaries

- If secret storage succeeded but rotation failed, retain the value in a local password manager or clipboard until the vault and Mac agree. Avoid `key` without `--reset` or `--copy` because it prints the key. Do not rotate again with an unrelated value while the stored secret is stale.
- Reinstalling the same service user and path preserves stored conversations and keys unless a new `OHAS_SESSION_API_KEY` is supplied. Never use `remove --purge` for routine recovery.
- Do not assume installation sets a model. Provisioning the Enterprise LiteLLM BYOR key and the Mac LLM profile is a later step; `GET /ready` alone does not prove agent conversations can run.
- Confirm live macOS, SSH and firewall behavior with the test Mac before claiming this recipe is production-ready. Do not run install, rotation, or purge without the user's authorization.
