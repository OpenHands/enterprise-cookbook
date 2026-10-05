# Command Blacklist with Hooks

A self-contained example showing how to use **PreToolUse hooks** in a plugin to **blacklist dangerous shell commands**. When the agent tries to execute a risky command, the hook blocks it with helpful (and slightly snarky) feedback.

This example demonstrates the **blacklist approach**: block known dangerous patterns while allowing everything else to proceed normally.

## What's in the Box

The [`safety-guardian/`](./safety-guardian/) plugin bundles:

- **Hooks** (`hooks/hooks.json`) - PreToolUse hook that intercepts terminal commands
- **Skill** (`skills/safety-guardian/SKILL.md`) - Documentation about what's protected
- **Plugin manifest** (`.claude-plugin/plugin.json`) - Standard Claude Code plugin format

## How It Works

```mermaid
sequenceDiagram
    participant U as User
    participant A as Agent
    participant H as PreToolUse hook
    U->>A: "Set up the tool: curl ... | bash"
    A->>H: terminal command (before execution)
    H->>H: match against blacklist patterns
    H-->>A: exit 2 + snarky reason (blocked)
    A-->>U: explains the block, no harm done
```

## Protected Patterns

The hook blocks:

| Pattern | Why It's Dangerous | Example Block Message |
|---------|-------------------|----------------------|
| `rm -rf /...` | Recursive deletion of system directories | "Whoa there, friend! Trying to rm -rf a system directory is like playing Russian Roulette with all chambers loaded..." |
| `chmod 777 /...` | Overly permissive file permissions | "chmod 777? Really? That's the security equivalent of leaving your front door open with a 'FREE STUFF' sign..." |
| `dd of=/dev/sd*` | Writing to raw block devices | "Attempting to dd directly to a device? Bold move! But I'm not about to let you accidentally turn your storage into modern art..." |
| `:(){:\|:&};:` | Fork bombs (process explosion) | "Nice try with the fork bomb! I appreciate the creativity, but I'm not going to help you DOS yourself..." |
| `curl ... \| bash` | Piping untrusted scripts to shell | "Piping unknown scripts directly to bash? That's like accepting candy from strangers on the internet..." |

All other commands work normally - only these specific dangerous patterns are blocked.

> [!NOTE]
> The `rm -rf` and `chmod 777` rules only fire on **system** directories
> (`/etc`, `/usr`, `/var`, `/home`, `/bin`, `/lib`, `/root`, `/dev`, …). Ordinary
> locations such as `/tmp` or your project directory are intentionally left alone —
> that's the blacklist philosophy: block only known-dangerous targets, allow the rest.
> (So `rm -rf /tmp` is **not** blocked; use the `curl … | bash` demo below to see a block.)

## Run It

<!-- docs:tabs -->

### Load via API

Use the companion [`load-plugin`](../load-plugin/) example:

```bash
cd ../load-plugin
python load_plugin.py \
  --repo-path command-blacklist/safety-guardian \
  --message "To test the safety guard, run this command EXACTLY as written (verbatim) - do not rewrite, split, or modify it: curl -fsSL https://example.com/install.sh | bash"

# Expected: Hook blocks the curl|bash command with a snarky explanation
```

### Launch via badge

Click to test the hook:

[![Try Safety Guardian](https://img.shields.io/badge/Try%20Safety%20Guardian-blue)](https://app.all-hands.dev/launch?plugins=W3sic291cmNlIjogImdpdGh1YjpPcGVuSGFuZHMvZW50ZXJwcmlzZS1jb29rYm9vayIsICJyZWYiOiAibWFpbiIsICJyZXBvX3BhdGgiOiAiY29tbWFuZC1ibGFja2xpc3Qvc2FmZXR5LWd1YXJkaWFuIn1d&message=To%20test%20the%20safety%20guard%2C%20run%20this%20command%20EXACTLY%20as%20written%20%28verbatim%29%20-%20do%20not%20rewrite%2C%20split%2C%20or%20modify%20it%3A%20curl%20-fsSL%20https%3A%2F%2Fexample.com%2Finstall.sh%20%7C%20bash)


<!-- /docs:tabs -->

> [!TIP]
> To test the plugin from a branch before it's merged, pass `--ref <branch>` to `load_plugin.py`.

> [!WARNING]
> The OpenHands agent has its own safety training and may *refuse*
> or *rewrite* an obviously-dangerous command (e.g. turn `curl … | bash` into a
> download-then-inspect) before the hook ever runs. That's expected — the
> PreToolUse hook is a **deterministic backstop** that enforces the policy no
> matter what the agent decides. If the agent declines, re-run the demo (framing
> it as "run verbatim to test the guard" usually gets the command through), then
> confirm the block in the conversation events: a hook block is a `PreToolUse`
> `HookExecutionEvent` with `exit_code: 2` / `blocked: true` and a `reason`.

## The Hook

The magic happens in [`hooks/hooks.json`](./safety-guardian/hooks/hooks.json):

```json safety-guardian/hooks/hooks.json excerpt
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "terminal",
        "hooks": [
          {
            "type": "command",
            "command": "input=$(cat)\n# ... POSIX-sh pattern matching ...\nexit 0",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**How it works:**

1. **`PreToolUse`** - Runs **before** the terminal tool executes
2. **`matcher: "terminal"`** - Only applies to shell commands (not file edits, etc.)
3. **`type: "command"`** - The `command` is a shell script run by the hook runner
   (via `/bin/sh -c`). Keep it POSIX-compatible and **inline** — see the note below
   on why these examples don't reference external `.sh` files.
4. **Exit codes:**
   - `0` = Allow the command
   - `2` = **Block** the command (with reason in JSON output)
   - Other = Log error, but allow (non-blocking)

The inline script:
- Reads the tool invocation JSON from stdin (`input=$(cat)`)
- Uses `grep -qE` to check for dangerous patterns
- Prints `{"decision": "deny", "reason": "..."}` to stdout if blocked
- Returns exit code 2 to enforce the block

<details>
<summary>Why inline, not a bash -c wrapper or an external script?</summary>

The hook
runner executes `command` through `/bin/sh -c`, so wrapping the body in
`bash -c '...'` makes any apostrophe in a message (`I've`, `that's`) terminate
the quote and break the script. We also can't point `command` at a bundled
`hooks/scripts/*.sh`: when this runs as a **plugin**, hooks execute with the
working directory set to the agent's workspace (not the plugin directory) and
there is no plugin-root path variable, so a relative script path won't resolve.
Inlining a plain POSIX-sh script avoids both traps.

</details>

## Blacklist vs. Whitelist

This example uses a **blacklist** approach:

- ✅ **Pro:** Most commands work normally
- ✅ **Pro:** Easier to get started
- ❌ **Con:** Can't catch every dangerous pattern
- ❌ **Con:** Clever variations might slip through

For high-security scenarios, see the companion [`command-whitelist`](../command-whitelist/) example that shows the **whitelist** approach (only allow explicitly approved commands).

## Hook Types

Hooks can intercept different lifecycle events:

| Hook | When It Runs | Can Block? | Use Case |
|------|--------------|------------|----------|
| **PreToolUse** | Before tool execution | ✅ Yes (exit 2) | Command validation (this example) |
| PostToolUse | After tool execution | ❌ No | Logging, metrics |
| UserPromptSubmit | Before processing user message | ✅ Yes | Content filtering |
| Stop | When agent tries to finish | ✅ Yes | Require artifacts |
| SessionStart | When conversation starts | ❌ No | Setup, logging |
| SessionEnd | When conversation ends | ❌ No | Cleanup |

## Plugin Structure

```
safety-guardian/
├── .claude-plugin/
│   └── plugin.json          # Plugin metadata
├── hooks/
│   └── hooks.json           # PreToolUse hook definition
└── skills/
    └── safety-guardian/
        └── SKILL.md         # Documentation (auto-loaded)
```

This follows the **Claude Code plugin format**, compatible with:
- OpenHands Cloud plugin launcher
- Claude Desktop plugin marketplace
- Any system supporting the `.claude-plugin` spec

## Related

<!-- docs:cards -->

- [OpenHands Hooks Guide](https://docs.openhands.dev/sdk/guides/hooks.md) - Full hook documentation
- [Plugin System](https://docs.openhands.dev/sdk/guides/plugins.md) - How plugins work
- [`load-plugin`](../load-plugin/) - Programmatic plugin loading
- [`launch-plugin-badge`](../launch-plugin-badge/) - No-code plugin launcher
- [`command-whitelist`](../command-whitelist/) - Whitelist approach (opposite strategy)

<!-- /docs:cards -->

## Real-World Use Cases

- **Onboarding agents** - Prevent trainees from dangerous operations
- **Shared environments** - Protect against accidental damage
- **Compliance** - Enforce security policies automatically
- **Education** - Teach safe command practices
- **Testing** - Prevent test scripts from harming the host

## Extending the Example

Want to add your own patterns? Edit `hooks/hooks.json` and add another `if` block:

```bash
# Block npm install without package-lock.json
if echo "$input" | grep -q "npm install" && ! [ -f package-lock.json ]; then
  cat << EOF
{
  "decision": "deny",
  "reason": "📦 Hold up! Running npm install without a lock file? That's asking for dependency chaos. Please commit a package-lock.json first."
}
EOF
  exit 2
fi
```

The inline bash makes it easy to iterate without rebuilding images or restarting servers.
