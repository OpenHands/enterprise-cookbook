# Launch Plugin

Plugin for the customer-facing conversation.

## Purpose

This plugin:
- Provides the MCP config to connect to the Wanderlust MCP server
- Includes prompts for friendly customer interaction
- Contains NO proprietary secrets or techniques (those are in proprietary-plugin)

## Key Features

- **Customer-Friendly Prompts**: Instructions for warm, engaging interactions
- **MCP Integration**: Config for connecting to the MCP server
- **Variable Expansion**: Uses `${MCP_SERVER_URL}` and `${MCP_AUTH_TOKEN}` from secrets
- **No Secrets**: This plugin is safe to load in customer-visible conversations

## Files

- `SKILL.md` - Customer interaction guidelines and conversation flow
- `mcp.json` - MCP server configuration with variable expansion

## MCP Tools (from server)

The MCP server exposes these tools:

- `request_travel_guide` - Start guide generation, returns request_id
- `check_guide_status` - Check specific request status
- `list_my_requests` - List customer's recent requests

## Required Secrets

When starting a conversation with this plugin, inject these secrets:

| Secret | Description |
|--------|-------------|
| `MCP_SERVER_URL` | Base URL of the MCP server (e.g., `https://work-1-xxx.prod-runtime.all-hands.dev`) |
| `MCP_AUTH_TOKEN` | Bearer token for MCP authentication |
| `WANDERLUST_CUSTOMER_ID` | Customer identifier for the session |
| `WANDERLUST_CUSTOMER_SECRET` | Customer authentication secret |

## Loading the Plugin

```python
# When starting a customer conversation via API:
payload = {
    "sandbox_id": sandbox_id,
    "initial_message": {...},
    "secrets": {
        "MCP_SERVER_URL": "https://your-mcp-server-url",
        "MCP_AUTH_TOKEN": "your-mcp-token",
        "WANDERLUST_CUSTOMER_ID": "demo-customer-001",
        "WANDERLUST_CUSTOMER_SECRET": "demo-customer-001-secret",
    },
    "plugins": [
        {
            "source": "github:OpenHands/enterprise-cookbook",
            "repo_path": "oem-conversations-private-prompts/launch-plugin",
        }
    ],
}
```

## What This Plugin Does NOT Contain

- ❌ Proprietary prompts (those are in `proprietary-plugin`)
- ❌ Secret restaurant database (that's in `proprietary-plugin`)
- ❌ HTML/CSS styling instructions (those are in `proprietary-plugin`)
- ❌ Any information about "Uncle Mortimer" 🤫

The customer-facing agent only knows how to:
1. Have friendly conversations about travel
2. Call MCP tools to request guides
3. Share the results when ready

It has NO knowledge of how the guides are actually generated!
