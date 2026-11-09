#!/usr/bin/env python3
"""
Wanderlust MCP Server - HTTP-based MCP server for travel guide generation.

This server exposes MCP tools that the customer-facing conversation can call.
It handles:
- Token-based authentication
- Customer credential validation
- Starting private conversations for guide generation
- Tracking request status in SQLite
- Returning results to the customer conversation

MCP Transport: Streamable HTTP (simple request/response, no SSE)
- POST /mcp - JSON-RPC 2.0 messages
- GET /health - Health check

Usage:
    export OPENHANDS_API_KEY="sk-oh-..."
    export MCP_AUTH_TOKEN="your-mcp-token"
    uv run uvicorn server:app --host 0.0.0.0 --port 8080
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from conversation_manager import process_guide_request
from database import Database, RequestStatus
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel


# =============================================================================
# Configuration
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Environment variables
OPENHANDS_API_KEY = os.environ.get("OPENHANDS_API_KEY", "")
OPENHANDS_API_URL = os.environ.get("OPENHANDS_API_URL", "https://app.all-hands.dev/api")
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "wanderlust-mcp-secret-token")
# MCP_SERVER_PUBLIC_URL is the public URL where the MCP server can be reached
# Used for callbacks from private conversations
MCP_SERVER_PUBLIC_URL = os.environ.get("MCP_SERVER_PUBLIC_URL", "")

# Database
db = Database()


# =============================================================================
# FastAPI App
# =============================================================================


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """App lifespan - startup and shutdown."""
    logger.info("Wanderlust MCP Server starting...")
    logger.info(f"OpenHands API URL: {OPENHANDS_API_URL}")
    logger.info(f"MCP Auth Token configured: {'Yes' if MCP_AUTH_TOKEN else 'No'}")
    logger.info(f"OpenHands API Key configured: {'Yes' if OPENHANDS_API_KEY else 'No'}")
    logger.info(f"MCP Server Public URL: {MCP_SERVER_PUBLIC_URL or '(not set)'}")
    yield
    logger.info("Wanderlust MCP Server shutting down...")


app = FastAPI(
    title="Wanderlust MCP Server",
    description="MCP server for generating travel guides with private conversations",
    version="0.1.0",
    lifespan=lifespan,
)


# =============================================================================
# Authentication
# =============================================================================


def verify_mcp_token(authorization: str | None) -> bool:
    """Verify the MCP authentication token."""
    if not authorization:
        return False
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        return token == MCP_AUTH_TOKEN
    return authorization == MCP_AUTH_TOKEN


def verify_customer(customer_id: str, customer_secret: str) -> bool:
    """Verify customer credentials."""
    return db.validate_customer(customer_id, customer_secret)


# =============================================================================
# MCP Protocol Handlers
# =============================================================================


class MCPRequest(BaseModel):
    """MCP JSON-RPC request."""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = None


def mcp_response(request_id: Any, result: Any) -> dict:
    """Create an MCP JSON-RPC response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def mcp_error(request_id: Any, code: int, message: str) -> dict:
    """Create an MCP JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


# =============================================================================
# MCP Tools Definition
# =============================================================================

MCP_TOOLS = [
    {
        "name": "request_travel_guide",
        "description": (
            "Request a personalized Wanderlust™ travel guide for a destination. "
            "The guide will be generated using our proprietary insider network and "
            "delivered as a beautifully styled web page. Generation takes 2-3 minutes. "
            "Returns a request_id to track this specific request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "City name (e.g., 'Paris', 'Tokyo', 'New York')",
                },
                "preferences": {
                    "type": "string",
                    "enum": [
                        "beach_relaxation",
                        "cultural_exploration",
                        "foodie_adventure",
                        "romantic_getaway",
                        "budget_travel",
                        "nightlife",
                    ],
                    "description": "Travel style preference",
                },
                "customer_name": {
                    "type": "string",
                    "description": "Optional: Customer name for personalization",
                },
            },
            "required": ["destination", "preferences"],
        },
    },
    {
        "name": "check_guide_status",
        "description": (
            "Check the status of a travel guide generation request. "
            "Returns current status and, if complete, the URL to view the guide."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "The request ID returned from request_travel_guide",
                },
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "list_my_requests",
        "description": (
            "List your recent travel guide requests. Useful if you have multiple "
            "requests in progress or want to check the status of past requests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of requests to return (default: 5)",
                    "default": 5,
                },
            },
            "required": [],
        },
    },
]


# =============================================================================
# Tool Implementations
# =============================================================================


async def handle_request_travel_guide(
    params: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Handle request_travel_guide tool call."""
    # Extract and validate parameters
    customer_id = params.get("customer_id", "")
    customer_secret = params.get("customer_secret", "")
    project_id = params.get("project_id", "")
    destination = params.get("destination", "")
    preferences = params.get("preferences", "")
    customer_name = params.get("customer_name")

    # Validate customer credentials
    if not verify_customer(customer_id, customer_secret):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "❌ Authentication failed. Invalid customer credentials.",
                }
            ],
            "isError": True,
        }

    # Validate required fields
    if not project_id or not destination or not preferences:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "❌ Missing required fields: project_id, destination, "
                        "and preferences are required."
                    ),
                }
            ],
            "isError": True,
        }

    # Look up sandbox_id from project_id
    project = db.get_project(project_id)
    if not project:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"❌ Invalid project_id: '{project_id}'. Project "
                        "not found or inactive."
                    ),
                }
            ],
            "isError": True,
        }

    sandbox_id = project["sandbox_id"]

    # Verify customer matches project
    if project["customer_id"] != customer_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "❌ Access denied. This project belongs to a "
                        "different customer."
                    ),
                }
            ],
            "isError": True,
        }

    # Check if OpenHands API key is configured
    if not OPENHANDS_API_KEY:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "❌ Server configuration error: OpenHands API "
                        "key not configured."
                    ),
                }
            ],
            "isError": True,
        }

    # Create the request in database
    request_id = db.create_guide_request(
        customer_id=customer_id,
        sandbox_id=sandbox_id,
        destination=destination,
        preferences=preferences,
        customer_name=customer_name,
        public_conversation_id=None,  # No longer tracking this
    )

    logger.info(
        f"Created guide request {request_id} for {destination} (project: "
        f"{project_id}, sandbox: {sandbox_id})"
    )

    # Build callback URL for private conversation to call when done
    callback_url = (
        f"{MCP_SERVER_PUBLIC_URL}/guide-complete" if MCP_SERVER_PUBLIC_URL else ""
    )

    # Start background task to generate the guide
    background_tasks.add_task(
        process_guide_request,
        api_key=OPENHANDS_API_KEY,
        request_id=request_id,
        sandbox_id=sandbox_id,
        destination=destination,
        preferences=preferences,
        customer_name=customer_name,
        api_url=OPENHANDS_API_URL,
        callback_url=callback_url,
        auth_token=MCP_AUTH_TOKEN,
    )

    # Return immediate response with suggestions for what to do while waiting
    suggestions = get_waiting_suggestions(destination, preferences)

    return {
        "content": [
            {
                "type": "text",
                "text": f"""✨ Your Wanderlust™ Travel Guide is being prepared!

**Request ID:** `{request_id}`
**Destination:** {destination}
**Style:** {preferences.replace("_", " ").title()}

⏱️ **Estimated time:** 2-3 minutes

While our insider network curates your personalized guide,
here are some things to discuss:

{suggestions}

Use the `check_guide_status` tool with your request ID
to see when your guide is ready!""",
            }
        ],
    }


async def handle_check_guide_status(params: dict[str, Any]) -> dict[str, Any]:
    """Handle check_guide_status tool call."""
    customer_id = params.get("customer_id", "")
    customer_secret = params.get("customer_secret", "")
    request_id = params.get("request_id", "")

    # Validate customer credentials
    if not verify_customer(customer_id, customer_secret):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "❌ Authentication failed. Invalid customer credentials.",
                }
            ],
            "isError": True,
        }

    # Get request from database
    request = db.get_guide_request(request_id)
    if not request:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"❌ Request not found: {request_id}",
                }
            ],
            "isError": True,
        }

    # Verify the request belongs to this customer
    if request["customer_id"] != customer_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "❌ Access denied. This request belongs to a "
                        "different customer."
                    ),
                }
            ],
            "isError": True,
        }

    status = request["status"]
    destination = request["destination"]

    if status == RequestStatus.PENDING.value:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"⏳ **Status: Queued**\n\nYour guide for {destination} "
                        "is waiting to be processed. Please check "
                        "again in a moment."
                    ),
                }
            ],
        }

    elif status == RequestStatus.PROCESSING.value:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "🔄 **Status: Generating**\n\nOur insider network "
                        f"is actively curating your {destination} guide. "
                        "This usually takes 2-3 minutes total."
                    ),
                }
            ],
        }

    elif status == RequestStatus.COMPLETED.value:
        # Guide is ready! Construct the URL
        # The guide is served on port 12000 of the sandbox
        guide_url = "https://work-1-{sandbox_host}/travel_guide.html".format(
            sandbox_host="YOUR_SANDBOX_HOST"  # This would be dynamically determined
        )

        guide_url = request.get("result_url")
        if guide_url:
            url_text = f"**Guide URL:** {guide_url}"
        else:
            url_text = (
                "**Note:** URL not available - guide is at /workspace/travel_guide.html"
            )

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"""🎉 **Your Wanderlust™ Guide is Ready!**

**Destination:** {destination}
{url_text}

Simply click the link above to view your personalized travel guide!

Enjoy your journey! 🌟""",
                }
            ],
        }

    elif status == RequestStatus.FAILED.value:
        error = request.get("error_message", "Unknown error")
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "❌ **Status: Failed**\n\nUnfortunately, we "
                        f"couldn't generate your guide.\n\nError: {error}\n\nPlease "
                        "try again or contact support."
                    ),
                }
            ],
            "isError": True,
        }

    else:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"❓ Unknown status: {status}",
                }
            ],
        }


async def handle_list_my_requests(params: dict[str, Any]) -> dict[str, Any]:
    """Handle list_my_requests tool call."""
    customer_id = params.get("customer_id", "")
    customer_secret = params.get("customer_secret", "")
    limit = params.get("limit", 5)

    # Validate customer credentials
    if not verify_customer(customer_id, customer_secret):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "❌ Authentication failed. Invalid customer credentials.",
                }
            ],
            "isError": True,
        }

    # Get requests from database
    requests = db.get_requests_by_customer(customer_id, limit=limit)

    if not requests:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "📋 **Your Requests**\n\nYou don't have any "
                        "travel guide requests yet. Use `request_travel_guide` "
                        "to create one!"
                    ),
                }
            ],
        }

    # Format the request list
    lines = ["📋 **Your Recent Travel Guide Requests**\n"]

    status_emoji = {
        "pending": "⏳",
        "processing": "🔄",
        "completed": "✅",
        "failed": "❌",
    }

    for req in requests:
        emoji = status_emoji.get(req["status"], "❓")
        lines.append(
            f"- **{req['destination']}** ({req['preferences'].replace('_', ' ')})\n"
            f"  {emoji} Status: {req['status'].upper()}\n"
            f"  ID: `{req['id']}`\n"
            f"  Created: {req['created_at']}\n"
        )

    lines.append("\nUse `check_guide_status` with a request ID for more details.")

    return {
        "content": [
            {
                "type": "text",
                "text": "\n".join(lines),
            }
        ],
    }


def get_waiting_suggestions(destination: str, preferences: str) -> str:
    """Get conversation suggestions while waiting for the guide."""
    base_tips = {
        "paris": [
            "The best time to visit Paris is spring (April-June) or "
            "fall (September-November)",
            "Consider getting a Paris Museum Pass if you plan to visit "
            "multiple attractions",
            "The metro is the most efficient way to get around the city",
        ],
        "tokyo": [
            "Spring (cherry blossom season) and fall are the best times to visit Tokyo",
            "Get a Suica or Pasmo card for easy public transit",
            "Many restaurants don't accept credit cards, so carry some cash",
        ],
        "new york": [
            "Walking is often faster than taking a cab in Manhattan",
            "Broadway shows often have rush tickets available day-of",
            "The subway runs 24/7, unlike most other cities",
        ],
        "rome": [
            "Book tickets in advance for popular attractions like the "
            "Vatican and Colosseum",
            "Restaurants near major tourist sites are often tourist traps",
            "The historic center is very walkable",
        ],
        "bangkok": [
            "The BTS Skytrain and MRT are the best ways to avoid traffic",
            "Street food is both delicious and safe at busy stalls",
            "Dress modestly when visiting temples",
        ],
        "sydney": [
            "The Opal card works on all public transport",
            "Don't underestimate distances - Australia is huge!",
            "Beach culture is huge - bring sunscreen",
        ],
    }

    preference_tips = {
        "foodie_adventure": "Ask me about local food etiquette or must-try dishes!",
        "romantic_getaway": "I can suggest some romantic activities or scenic spots!",
        "cultural_exploration": (
            "Would you like to know about local customs or hidden museums?"
        ),
        "budget_travel": "I have tips for saving money without missing out!",
        "nightlife": "Ask about the best neighborhoods for nightlife!",
        "beach_relaxation": "I can recommend the best beaches and sunset spots!",
    }

    dest_lower = destination.lower()
    tips = base_tips.get(
        dest_lower,
        [
            "Research local customs before you arrive",
            "Download offline maps for when you don't have data",
            "Learn a few basic phrases in the local language",
        ],
    )

    pref_tip = preference_tips.get(
        preferences, "Feel free to ask me anything about your trip!"
    )

    tips_text = "\n".join(f"• {tip}" for tip in tips[:3])
    return f"""**Quick tips for {destination}:**
{tips_text}

💬 {pref_tip}"""


# =============================================================================
# MCP Endpoints (SSE Transport)
# =============================================================================

# Store for SSE clients: session_id -> asyncio.Queue
sse_clients: dict[str, asyncio.Queue] = {}
sse_lock = asyncio.Lock()


async def process_mcp_request(
    body: dict,
    background_tasks: BackgroundTasks,
    customer_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Process an MCP JSON-RPC request and return the response.

    Args:
        body: JSON-RPC request body
        background_tasks: FastAPI background tasks
        customer_id: Customer ID from X-Customer-ID header (preferred) or tool params
        project_id: Project ID from X-Project-ID header (preferred) or tool params
    """
    method = body.get("method", "")
    request_id = body.get("id")
    params = body.get("params", {})

    logger.info(f"MCP request: {method}")

    # Handle methods
    if method == "initialize":
        return mcp_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "wanderlust-travel",
                    "version": "0.1.0",
                },
            },
        )

    elif method == "notifications/initialized":
        return mcp_response(request_id, {})

    elif method == "tools/list":
        return mcp_response(request_id, {"tools": MCP_TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Inject credentials from headers into arguments (headers take precedence)
        # This works around the SDK not expanding secrets in MCP tool params
        if customer_id:
            arguments["customer_id"] = customer_id
            # Secret convention for demo: "{customer_id}-secret"
            arguments["customer_secret"] = f"{customer_id}-secret"
        if project_id:
            arguments["project_id"] = project_id

        if tool_name == "request_travel_guide":
            result = await handle_request_travel_guide(arguments, background_tasks)
            return mcp_response(request_id, result)

        elif tool_name == "check_guide_status":
            result = await handle_check_guide_status(arguments)
            return mcp_response(request_id, result)

        elif tool_name == "list_my_requests":
            result = await handle_list_my_requests(arguments)
            return mcp_response(request_id, result)

        else:
            return mcp_error(request_id, -32601, f"Unknown tool: {tool_name}")

    else:
        return mcp_error(request_id, -32601, f"Unknown method: {method}")


@app.get("/mcp")
async def handle_mcp_sse(
    authorization: str | None = Header(None),
):
    """
    SSE endpoint for MCP - maintains connection for server-to-client messages.
    """
    if not verify_mcp_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing MCP token")

    session_id = str(uuid.uuid4())
    client_queue: asyncio.Queue = asyncio.Queue()

    async with sse_lock:
        sse_clients[session_id] = client_queue

    logger.info(f"SSE connection opened: {session_id}")

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Send endpoint event with session ID - tells client where to POST
            # This follows the MCP SSE transport spec
            yield f"event: endpoint\ndata: /mcp?session={session_id}\n\n"

            while True:
                try:
                    # Wait for messages to send to client
                    message = await asyncio.wait_for(client_queue.get(), timeout=30.0)
                    yield f"event: message\ndata: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            async with sse_lock:
                sse_clients.pop(session_id, None)
            logger.info(f"SSE connection closed: {session_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/mcp")
async def handle_mcp(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(None),
    x_customer_id: str | None = Header(None, alias="X-Customer-ID"),
    x_project_id: str | None = Header(None, alias="X-Project-ID"),
    session: str | None = None,
):
    """
    Handle MCP JSON-RPC requests via POST.

    Supports:
    - initialize
    - notifications/initialized
    - tools/list
    - tools/call

    Customer credentials can be passed via headers (recommended):
    - X-Customer-ID: Customer identifier
    - X-Project-ID: Project identifier

    If session parameter is provided, responses are sent via SSE.
    Otherwise, response is returned directly (for simple HTTP mode).
    """
    # Verify MCP token
    if not verify_mcp_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing MCP token")

    # Parse request
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(mcp_error(None, -32700, "Parse error"))

    # Handle single request or batch
    if isinstance(body, list):
        # Batch request - not implemented for simplicity
        return JSONResponse(mcp_error(None, -32600, "Batch requests not supported"))

    # Log header credentials if present
    if x_customer_id or x_project_id:
        logger.info(
            f"MCP request with header credentials: customer={x_customer_id}, "
            f"project={x_project_id}"
        )

    response = await process_mcp_request(
        body,
        background_tasks,
        customer_id=x_customer_id,
        project_id=x_project_id,
    )

    # If session provided, send response via SSE channel
    if session:
        async with sse_lock:
            if session in sse_clients:
                await sse_clients[session].put(response)
                # Return 202 Accepted - response sent via SSE
                return JSONResponse({"status": "accepted"}, status_code=202)

    # Otherwise return response directly
    return JSONResponse(response)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "server": "wanderlust-mcp",
        "version": "0.1.0",
        "openhands_configured": bool(OPENHANDS_API_KEY),
    }


@app.get("/")
async def root():
    """Root endpoint with info."""
    return {
        "name": "Wanderlust MCP Server",
        "version": "0.1.0",
        "endpoints": {
            "mcp": "POST /mcp - MCP JSON-RPC endpoint",
            "health": "GET /health - Health check",
            "projects": "POST /projects - Seed project→sandbox mapping (admin)",
        },
        "tools": [t["name"] for t in MCP_TOOLS],
    }


# =============================================================================
# Project Management API (for demo host)
# =============================================================================


class CreateProjectRequest(BaseModel):
    """Request to create/seed a project."""

    project_id: str
    sandbox_id: str
    customer_id: str
    customer_name: str | None = None


@app.post("/projects")
async def create_project(
    request: CreateProjectRequest,
    authorization: str | None = Header(None),
):
    """
    Create a project mapping project_id to sandbox_id.

    Called by the demo host to seed the database before starting
    the customer conversation. Requires MCP auth token.
    """
    # Verify MCP token (same auth as MCP endpoint)
    if not verify_mcp_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")

    # Check if project already exists
    existing = db.get_project(request.project_id)
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Project '{request.project_id}' already exists"
        )

    # Create the project
    try:
        db.create_project(
            project_id=request.project_id,
            sandbox_id=request.sandbox_id,
            customer_id=request.customer_id,
            customer_name=request.customer_name,
        )
        logger.info(
            f"Created project {request.project_id} -> sandbox {request.sandbox_id} "
            f"for customer {request.customer_id}"
        )
        return {
            "status": "created",
            "project_id": request.project_id,
            "sandbox_id": request.sandbox_id,
            "customer_id": request.customer_id,
        }
    except Exception as e:
        logger.error(f"Failed to create project: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    authorization: str | None = Header(None),
):
    """Get project details. Requires MCP auth token."""
    if not verify_mcp_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")

    project = db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    return project


@app.delete("/projects/{project_id}")
async def deactivate_project(
    project_id: str,
    authorization: str | None = Header(None),
):
    """Deactivate a project. Requires MCP auth token."""
    if not verify_mcp_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")

    success = db.deactivate_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    logger.info(f"Deactivated project {project_id}")
    return {"status": "deactivated", "project_id": project_id}


# =============================================================================
# Guide Completion Callback (called by private conversation)
# =============================================================================


class GuideCompleteRequest(BaseModel):
    """Request body for guide completion callback."""

    request_id: str
    guide_url: str
    destination: str | None = None


@app.post("/guide-complete")
async def guide_complete(
    request: GuideCompleteRequest,
    authorization: str | None = Header(None),
):
    """
    Callback endpoint for private conversation to signal guide completion.

    Called by the proprietary plugin when the travel guide is ready:
    - Updates the guide request status to 'completed'
    - Stores the full public URL

    The private conversation calls this directly instead of relying on
    polling/detection logic.
    """
    # Verify MCP token (same auth as other endpoints)
    if not verify_mcp_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token")

    # Look up the request
    guide_request = db.get_guide_request(request.request_id)
    if not guide_request:
        raise HTTPException(
            status_code=404, detail=f"Guide request '{request.request_id}' not found"
        )

    # Update status to completed with the URL
    success = db.update_guide_request_status(
        request.request_id,
        RequestStatus.COMPLETED,
        result_url=request.guide_url,
    )

    if not success:
        raise HTTPException(
            status_code=500, detail="Failed to update guide request status"
        )

    logger.info(
        f"Guide complete: request={request.request_id}, url={request.guide_url}"
    )

    return {
        "status": "ok",
        "request_id": request.request_id,
        "guide_url": request.guide_url,
    }


# =============================================================================
# Main
# =============================================================================


def main():
    """Run the server."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
