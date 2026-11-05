"""
Conversation Manager for starting and monitoring private OpenHands conversations.

This module handles:
1. Starting private conversations in the SAME sandbox as the customer conversation
2. Loading the proprietary plugin with secret prompts
3. Monitoring conversation progress via events
4. Detecting when the travel guide is ready
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from database import Database, RequestStatus


logger = logging.getLogger(__name__)


class ConversationStatus(str, Enum):
    """Status of a conversation."""

    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PrivateConversationResult:
    """Result of a private conversation."""

    success: bool
    conversation_id: str | None
    guide_url: str | None  # Full public URL to the guide
    error: str | None


class ConversationManager:
    """
    Manages private OpenHands conversations for guide generation.

    Key features:
    - Starts conversations in an existing sandbox (shared with customer)
    - Loads proprietary plugin with secret prompts
    - Monitors progress via events API
    - Detects completion via specific markers in output
    """

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://app.all-hands.dev/api",
        db: Database | None = None,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.db = db or Database()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError(
                "ConversationManager must be used as async context manager"
            )
        return self._client

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Access-Token": self.api_key,
            "Content-Type": "application/json",
        }

    # =========================================================================
    # Sandbox Operations
    # =========================================================================

    async def get_sandbox(self, sandbox_id: str) -> dict[str, Any] | None:
        """Get sandbox info by ID."""
        resp = await self.client.get(
            f"{self.api_url}/v1/sandboxes/search",
            headers=self.headers,
        )
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            if item.get("id") == sandbox_id:
                return item
        return None

    async def wait_for_sandbox(
        self,
        sandbox_id: str,
        timeout: int = 180,
        poll_interval: int = 2,
    ) -> dict[str, Any] | None:
        """Wait for sandbox to be in RUNNING state."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            sandbox = await self.get_sandbox(sandbox_id)
            if sandbox and sandbox.get("status") == "RUNNING":
                return sandbox
            await asyncio.sleep(poll_interval)
        return None

    # =========================================================================
    # Conversation Operations
    # =========================================================================

    async def start_private_conversation(
        self,
        sandbox_id: str,
        destination: str,
        preferences: str,
        customer_name: str | None = None,
        plugin_source: str = "github:OpenHands/enterprise-cookbook",
        plugin_path: str = "oem-conversations-private-prompts/proprietary-plugin",
        plugin_ref: str = "feature/oem-conversations-private-prompts",
        request_id: str = "",
        callback_url: str = "",
        auth_token: str = "",
    ) -> dict[str, Any]:
        """
        Start a private conversation in an existing sandbox.

        This conversation:
        - Uses the SAME sandbox as the customer conversation (shared filesystem!)
        - Loads the proprietary plugin with secret prompts
        - Receives destination/preferences as initial message
        - Will generate the travel guide and start a web server
        - Calls back to the MCP server when complete

        Args:
            sandbox_id: ID of the existing sandbox (from customer conversation)
            destination: City name (e.g., "Paris")
            preferences: Travel preference type (e.g., "foodie_adventure")
            customer_name: Optional name for personalization
            plugin_source: GitHub repo for proprietary plugin
            plugin_path: Path within repo to plugin directory
            request_id: Guide request ID for callback
            callback_url: MCP server callback URL
            auth_token: Auth token for callback

        Returns:
            AppConversationStartTask response from API
        """
        # Construct the initial message that triggers guide generation
        initial_prompt = self._build_guide_prompt(
            destination,
            preferences,
            customer_name,
            request_id,
            callback_url,
            auth_token,
        )

        payload = {
            "sandbox_id": sandbox_id,
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": initial_prompt}],
            },
            "plugins": [
                {
                    "source": plugin_source,
                    "repo_path": plugin_path,
                    "ref": plugin_ref,
                }
            ],
            "title": f"[Private] Travel Guide: {destination}",
        }

        logger.info(
            f"Starting private conversation for {destination} in sandbox {sandbox_id}"
        )
        logger.debug(f"Plugin: {plugin_source} / {plugin_path} @ {plugin_ref}")

        resp = await self.client.post(
            f"{self.api_url}/v1/app-conversations",
            headers=self.headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def _build_guide_prompt(
        self,
        destination: str,
        preferences: str,
        customer_name: str | None,
        request_id: str,
        callback_url: str,
        auth_token: str,
    ) -> str:
        """Build the initial prompt for guide generation."""
        name_part = f" for {customer_name}" if customer_name else ""
        return f"""Generate a Wanderlust™ Premium Travel Guide{name_part}.

Destination: {destination}
Travel Style: {preferences}

## Callback Information
When the guide is ready, you MUST call the callback to notify the MCP server:

```bash
curl -X POST "{callback_url}" \\
  -H "Authorization: Bearer {auth_token}" \\
  -H "Content-Type: application/json" \\
  -d '{{"request_id": "{request_id}", "guide_url": "<YOUR_GUIDE_URL>",
       "destination": "{destination}"}}'
```

Replace `<YOUR_GUIDE_URL>` with the actual public URL (e.g., https://work-1-xxx.prod-runtime.all-hands.dev/travel_guide.html).

## Instructions
1. Load the secret restaurant database
2. Apply the Vibes-to-Venue Mapping Protocol™
3. Generate the HTML guide using the Wanderlust™ Brand Format
4. Save it to /workspace/travel_guide.html
5. Start the web server on port 12000
6. Discover your public URL and call the callback above

Begin now."""

    async def _wait_for_conversation_ready(
        self,
        start_task_id: str,
        timeout: float = 120,
        poll_interval: float = 2,
    ) -> str | None:
        """Wait for a start task to complete and return the conversation ID.

        Args:
            start_task_id: The ID returned from POST /app-conversations
            timeout: Maximum time to wait in seconds
            poll_interval: How often to poll in seconds

        Returns:
            The conversation ID once ready, or None if timeout/error
        """
        import time

        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                resp = await self.client.get(
                    f"{self.api_url}/v1/app-conversations/start-tasks/search",
                    headers=self.headers,
                    params={"ids": start_task_id},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])

                for item in items:
                    if item.get("id") == start_task_id:
                        status = item.get("status")
                        if status == "READY":
                            return item.get("app_conversation_id")
                        elif status in ("FAILED", "ERROR"):
                            logger.error(f"Start task failed: {item.get('detail')}")
                            return None
                        # Still working, keep polling
                        break

            except Exception as e:
                logger.warning(f"Error polling start task: {e}")

            await asyncio.sleep(poll_interval)

        logger.error(f"Timeout waiting for start task {start_task_id}")
        return None

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """Get conversation by ID."""
        resp = await self.client.get(
            f"{self.api_url}/v1/app-conversations",
            headers=self.headers,
            params={"ids": [conversation_id]},
        )
        resp.raise_for_status()
        items = resp.json()
        return items[0] if items else None

    async def get_conversation_events(
        self,
        conversation_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get events from a conversation."""
        resp = await self.client.get(
            f"{self.api_url}/v1/conversation/{conversation_id}/events/search",
            headers=self.headers,
            params={"limit": limit},
        )
        if resp.status_code == 200:
            return resp.json().get("items", [])
        return []

    async def check_guide_ready(self, conversation_id: str) -> tuple[bool, str | None]:
        """
        Check if the travel guide is ready by looking for completion markers or URLs.

        Detection methods (in priority order):
        1. TRAVEL_GUIDE_READY: <url> marker
        2. Any URL containing travel_guide.html
        3. Phrases like "guide is ready", "Your guide", etc. with a URL

        Returns:
            (is_ready, guide_url or None)
        """
        import re

        events = await self.get_conversation_events(conversation_id, limit=100)

        def extract_text(content: Any) -> str:
            """Extract text from various content formats."""
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Handle list of content blocks: [{"type": "text", "text": "..."}]
                texts = []
                for item in content:
                    if isinstance(item, dict):
                        texts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        texts.append(item)
                return "\n".join(texts)
            if isinstance(content, dict):
                return content.get("text", "")
            return ""

        def extract_url_from_text(text: str) -> str | None:
            """Extract guide URL from text using multiple patterns."""
            # Pattern 1: "TRAVEL_GUIDE_READY: <url>" marker
            match = re.search(r"TRAVEL_GUIDE_READY[:\s]+(\S+)", text)
            if match:
                url = match.group(1)
                if url.startswith("http"):
                    return url

            # Pattern 2: Any URL containing travel_guide.html
            match = re.search(r'(https?://[^\s<>"\'`]+travel_guide\.html)', text)
            if match:
                return match.group(1)

            # Pattern 3: "Live preview:" or "Live URL:" patterns
            match = re.search(
                r"(?:Live preview|Live URL|Guide URL|preview)[:\s*]+(\S+)",
                text,
                re.IGNORECASE,
            )
            if match:
                url = match.group(1)
                if url.startswith("http"):
                    return url

            return None

        def is_completion_message(text: str) -> bool:
            """Check if the text indicates guide completion."""
            # Must have explicit completion markers - not just mention of "guide"
            completion_phrases = [
                "TRAVEL_GUIDE_READY",
                "guide is ready",
                "Guide is ready",
                "is now live",
                "has been generated",
                "Access Your Guide",
            ]
            # Also require the text to contain a URL to travel_guide.html
            has_url = "travel_guide.html" in text and "http" in text
            has_phrase = any(phrase in text for phrase in completion_phrases)
            return has_phrase or has_url

        for event in events:
            # Check message events (most likely to have completion message)
            llm_msg = event.get("llm_message", {})
            if isinstance(llm_msg, dict):
                msg_text = extract_text(llm_msg.get("content", ""))
                if is_completion_message(msg_text):
                    url = extract_url_from_text(msg_text)
                    if url:
                        return True, url
                    # Guide is ready but no URL found - still mark as ready
                    if (
                        "TRAVEL_GUIDE_READY" in msg_text
                        or "guide is ready" in msg_text.lower()
                    ):
                        return True, None

            # Check direct message field
            message = event.get("message", [])
            msg_text = extract_text(message)
            if is_completion_message(msg_text):
                url = extract_url_from_text(msg_text)
                if url:
                    return True, url

            # Check observation content
            obs = event.get("observation", {})
            if isinstance(obs, dict):
                content_text = extract_text(obs.get("content", ""))
                if is_completion_message(content_text):
                    url = extract_url_from_text(content_text)
                    if url:
                        return True, url

            # Check action messages
            action = event.get("action", {})
            if isinstance(action, dict):
                action_text = extract_text(
                    action.get("message", "") or action.get("content", "")
                )
                if is_completion_message(action_text):
                    url = extract_url_from_text(action_text)
                    if url:
                        return True, url

        return False, None

    async def check_conversation_error(self, conversation_id: str) -> str | None:
        """Check if conversation has encountered an error."""
        events = await self.get_conversation_events(conversation_id, limit=50)

        for event in events:
            kind = event.get("kind", "")
            if kind in (
                "AgentErrorEvent",
                "ConversationErrorEvent",
                "ServerErrorEvent",
            ):
                obs = event.get("observation", {})
                if isinstance(obs, dict):
                    return obs.get("content", str(obs))[:500]
        return None

    # =========================================================================
    # High-Level Operations
    # =========================================================================

    async def generate_travel_guide(
        self,
        request_id: str,
        sandbox_id: str,
        destination: str,
        preferences: str,
        customer_name: str | None = None,
        callback_url: str = "",
        auth_token: str = "",
    ) -> PrivateConversationResult:
        """
        Start a private conversation to generate a travel guide.

        This starts the conversation and returns immediately.
        The private conversation will call back to the MCP server
        when the guide is ready (via /guide-complete endpoint).

        Args:
            request_id: The guide request ID from database
            sandbox_id: ID of the sandbox to use
            destination: City name
            preferences: Travel style
            customer_name: Optional customer name
            callback_url: URL for private conversation to call when done
            auth_token: Auth token for callback

        Returns:
            PrivateConversationResult with conversation start info
        """
        private_conv_id = None

        try:
            # Update status to processing
            self.db.update_guide_request_status(
                request_id,
                RequestStatus.PROCESSING,
            )

            # Start the private conversation with callback info
            result = await self.start_private_conversation(
                sandbox_id=sandbox_id,
                destination=destination,
                preferences=preferences,
                customer_name=customer_name,
                request_id=request_id,
                callback_url=callback_url,
                auth_token=auth_token,
            )

            # The API returns a start task - wait for conversation to be ready
            start_task_id = result.get("id")
            logger.info(f"Private conversation start task: {start_task_id}")

            private_conv_id = await self._wait_for_conversation_ready(start_task_id)
            if not private_conv_id:
                raise RuntimeError(
                    f"Start task {start_task_id} did not produce a conversation"
                )

            logger.info(f"Private conversation started: {private_conv_id}")

            # Update with conversation ID - status remains PROCESSING
            # The /guide-complete callback will update to COMPLETED when done
            self.db.update_guide_request_status(
                request_id,
                RequestStatus.PROCESSING,
                private_conversation_id=private_conv_id,
            )

            # Return immediately - no polling needed!
            # The private conversation will call /guide-complete when done
            return PrivateConversationResult(
                success=True,
                conversation_id=private_conv_id,
                guide_url=None,  # Will be set by callback
                error=None,
            )

        except Exception as e:
            error_msg = f"Failed to start guide generation: {e}"
            logger.exception(error_msg)
            self.db.update_guide_request_status(
                request_id,
                RequestStatus.FAILED,
                error_message=error_msg,
            )
            return PrivateConversationResult(
                success=False,
                conversation_id=private_conv_id,
                guide_url=None,
                error=error_msg,
            )


# =============================================================================
# Background Task Runner
# =============================================================================


async def process_guide_request(
    api_key: str,
    request_id: str,
    sandbox_id: str,
    destination: str,
    preferences: str,
    customer_name: str | None = None,
    api_url: str = "https://app.all-hands.dev/api",
    callback_url: str = "",
    auth_token: str = "",
) -> PrivateConversationResult:
    """
    Process a guide request in the background.

    This function is designed to be run as a background task.
    The private conversation will call back to the MCP server when complete.
    """
    db = Database()

    async with ConversationManager(api_key, api_url, db) as manager:
        return await manager.generate_travel_guide(
            request_id=request_id,
            sandbox_id=sandbox_id,
            destination=destination,
            preferences=preferences,
            customer_name=customer_name,
            callback_url=callback_url,
            auth_token=auth_token,
        )
