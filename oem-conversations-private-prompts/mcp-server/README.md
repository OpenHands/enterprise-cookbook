# MCP Server

The secure proxy layer that connects customer conversations to private processing.

## Purpose

This HTTP-based MCP server:
- Authenticates incoming requests with token-based auth
- Validates customer ID and customer secret
- Starts private OpenHands conversations with proprietary plugins
- Tracks request state in SQLite (supports multiple concurrent requests)
- Returns results to the customer-facing conversation

## Architecture

```
Customer Conversation ──► MCP Server ──► Private Conversation
        │                     │                    │
        │                     │                    │
        ▼                     ▼                    ▼
   launch-plugin          SQLite DB        proprietary-plugin
   (MCP client)         (request tracking)        │
                                                   ▼
                                           Shared Sandbox
                                          (HTML/CSS/Assets)
```

## MCP Transport

Uses **HTTP MCP** (Streamable HTTP) - simple request/response without SSE:
- `POST /mcp` - JSON-RPC 2.0 messages
- `GET /health` - Health check
- `GET /` - Server info

## MCP Tools

### `request_travel_guide`
Request a personalized travel guide. Returns a `request_id` for tracking.

**Parameters:**
- `customer_id` (required): Your Wanderlust customer ID
- `customer_secret` (required): Your Wanderlust customer secret
- `sandbox_id` (required): The sandbox ID for the current conversation
- `conversation_id` (optional): Current conversation ID for tracking
- `destination` (required): City name (e.g., "Paris", "Tokyo")
- `preferences` (required): Travel style (e.g., "foodie_adventure", "romantic_getaway")
- `customer_name` (optional): Customer name for personalization

### `check_guide_status`
Check the status of a specific guide request.

**Parameters:**
- `customer_id` (required): Your Wanderlust customer ID
- `customer_secret` (required): Your Wanderlust customer secret
- `request_id` (required): The request ID from `request_travel_guide`

### `list_my_requests`
List your recent guide requests (useful for multiple concurrent requests).

**Parameters:**
- `customer_id` (required): Your Wanderlust customer ID
- `customer_secret` (required): Your Wanderlust customer secret
- `limit` (optional): Maximum requests to return (default: 5)

## Authentication

1. **MCP Auth Token**: Bearer token in Authorization header for all MCP calls
2. **Customer Credentials**: `customer_id` + `customer_secret` validated per tool call
   - Demo validation: secret = `{customer_id}-secret`

## Database Schema

```sql
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    customer_secret_hash TEXT NOT NULL,
    name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE guide_requests (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    public_conversation_id TEXT,
    private_conversation_id TEXT,
    destination TEXT NOT NULL,
    preferences TEXT NOT NULL,
    customer_name TEXT,
    status TEXT DEFAULT 'pending',  -- pending, processing, completed, failed
    result_path TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

## Running

```bash
# Install dependencies
uv sync

# Set environment variables
export OPENHANDS_API_KEY="sk-oh-..."
export MCP_AUTH_TOKEN="your-mcp-token"

# Run the server
uv run uvicorn server:app --host 0.0.0.0 --port 8080
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENHANDS_API_KEY` | API key for starting private conversations | (required) |
| `OPENHANDS_API_URL` | OpenHands API base URL | `https://app.all-hands.dev/api` |
| `MCP_AUTH_TOKEN` | Token for authenticating MCP calls | `wanderlust-mcp-secret-token` |

## Files

- `server.py` - Main FastAPI MCP server
- `database.py` - SQLite state management
- `conversation_manager.py` - OpenHands API integration for private conversations
- `pyproject.toml` - Python dependencies
