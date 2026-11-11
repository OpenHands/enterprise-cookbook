---
name: macos-agent-server-llm-setup
description: This skill should be used when the user asks to "give the Mac an LLM", "set up the LLM on the remote Mac Agent Server", "copy my secrets to the Mac", "sync LLM settings to the Mac", "check the Mac's LLM key", or "list models for the Mac" from an OpenHands Enterprise conversation. Runs the bundled sync_llm.py script, which copies the user's LLM key for external use and chosen secrets to the remote Agent Server.
---

# Give the Mac an LLM and Secrets

Read [the recipe](../../README.md) first. This skill runs after the Mac Agent Server is
installed and reachable over HTTPS, with `MAC_AGENT_SERVER_KEY` saved in Enterprise Secrets.
The private-network route also requires `MAC_AGENT_SERVER_CA`; the temporary ngrok demo uses
the system trust store instead (see the [install skill](../macos-agent-server-install/SKILL.md)).

The bundled script does the work, so do not call these APIs yourself. It gets the user's
LLM key for external use (BYOR) from Enterprise, checks it against the LLM proxy, has the
Mac make a test request, then saves an LLM profile and the secrets the user chose on the Mac.
Secret values stay in environment variables and are never printed.

## Rules

- Never print, echo, log, or put in a command argument the value of `OPENHANDS_API_KEY`,
  `MAC_AGENT_SERVER_KEY`, the LLM key, or any secret being copied. The script reads them from
  the environment, and the sandbox only injects a secret when its name appears in the
  command. Always pass the `--api-key-env` and `--target-key-env` names as shown below;
  pass `--ca-cert-env MAC_AGENT_SERVER_CA` only for the private-network route.
- If the script says LLM key export is turned off, stop and tell the user to ask their
  OpenHands administrator to turn on "Enable LLM Key Export (BYOR)" in the Admin Console.
  Do not look for another way to read the LLM key.
- Use an `https://` target. For the Mac's self-signed TLS front end, pass
  `--ca-cert-env MAC_AGENT_SERVER_CA` to pin its certificate; for an ngrok HTTPS endpoint,
  omit it so standard public CA validation applies. If the certificate is not accepted,
  stop and ask the operator to check the hostname and the relevant certificate. Do not pass
  `--allow-insecure-http`; it would send the LLM key and secrets unencrypted.
- Copy only the secrets the user names. Ask which ones; never copy all of them.

## Instructions

1. Ask the user for the HTTPS target (for example `https://mac.example.net:18443` or a
   temporary ngrok URL) and whether the Mac TLS front end or a public-CA proxy serves it.
2. List the models the user's key can use, and ask which one the Mac should use:

   ```bash
   python3 <skill-directory>/scripts/sync_llm.py --target "$TARGET_URL" \
     --api-key-env OPENHANDS_API_KEY --target-key-env MAC_AGENT_SERVER_KEY \
     --ca-cert-env MAC_AGENT_SERVER_CA --list-models
   ```

   Replace `$TARGET_URL` with the HTTPS address. **Omit** `--ca-cert-env MAC_AGENT_SERVER_CA`
   for a public-CA ngrok target; keep it for the Mac's self-signed TLS front end. The list may
   be partial.
3. Remove `--list-models`, then preview with `--dry-run`, `--model MODEL`, and one
   `--secret NAME` for each secret to copy. Keep the same target and certificate options.
   Show the plan to the user and wait for approval.
4. Run the same command without `--dry-run`. Show the output as printed. On an error, show
   it and stop; do not retry with different options unless the user asks.
5. To confirm the Mac still holds the current key later, for example after the user
   refreshes their key in Settings, run the command with `--check`. If it reports an older
   key, run step 4 again.

## Files

- `scripts/sync_llm.py`: the sync, with `--list-models`, `--dry-run`, and `--check` modes.
  Python 3.10 or later, standard library only. For the private-network route,
  `$MAC_AGENT_SERVER_CA` holds the Mac's certificate, as PEM or one line of base64; it is not
  needed with ngrok's public certificate. The script works out the Enterprise URL from the
  sandbox environment and the LLM proxy URL from the Enterprise hostname
  (`app.example.com` becomes `llm-proxy.example.com`). Pass `--enterprise-url` or
  `--llm-base-url` if the installation uses custom hostnames.
