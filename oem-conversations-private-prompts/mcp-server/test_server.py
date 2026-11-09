#!/usr/bin/env python3
"""
Unit tests for the Wanderlust MCP Server.

Tests cover:
- MCP protocol (initialize, tools/list, tools/call)
- Authentication (MCP token, customer credentials)
- Tool implementations (request_travel_guide, check_guide_status, list_my_requests)
- Database operations
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient


# Set test environment before importing server
os.environ["MCP_AUTH_TOKEN"] = "test-mcp-token"
os.environ["OPENHANDS_API_KEY"] = "test-api-key"

from database import Database, RequestStatus
from server import MCP_AUTH_TOKEN, app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Headers with valid MCP authentication."""
    return {"Authorization": f"Bearer {MCP_AUTH_TOKEN}"}


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = Database(db_path)
    yield db
    os.unlink(db_path)


# =============================================================================
# Health Check Tests
# =============================================================================


class TestHealthCheck:
    def test_health_endpoint(self, client):
        """Test the health check endpoint."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["server"] == "wanderlust-mcp"
        assert "openhands_configured" in data

    def test_root_endpoint(self, client):
        """Test the root info endpoint."""
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Wanderlust MCP Server"
        assert "tools" in data


# =============================================================================
# MCP Protocol Tests
# =============================================================================


class TestMCPProtocol:
    def test_mcp_without_auth(self, client):
        """Test MCP endpoint requires authentication."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
            },
        )
        assert resp.status_code == 401

    def test_mcp_with_wrong_token(self, client):
        """Test MCP endpoint rejects wrong token."""
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_mcp_initialize(self, client, auth_headers):
        """Test MCP initialize method."""
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert "result" in data
        assert data["result"]["serverInfo"]["name"] == "wanderlust-travel"

    def test_mcp_tools_list(self, client, auth_headers):
        """Test MCP tools/list method."""
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        tools = data["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "request_travel_guide" in tool_names
        assert "check_guide_status" in tool_names
        assert "list_my_requests" in tool_names

    def test_mcp_unknown_method(self, client, auth_headers):
        """Test MCP returns error for unknown method."""
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "unknown/method"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


# =============================================================================
# Tool Call Tests
# =============================================================================


class TestToolCalls:
    def test_request_travel_guide_invalid_customer(self, client, auth_headers):
        """Test request_travel_guide with invalid customer credentials."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "request_travel_guide",
                    "arguments": {
                        "customer_id": "invalid-customer",
                        "customer_secret": "wrong-secret",
                        "sandbox_id": "test-sandbox",
                        "destination": "Paris",
                        "preferences": "foodie_adventure",
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        assert result.get("isError") is True
        assert "Authentication failed" in result["content"][0]["text"]

    def test_request_travel_guide_valid_customer(self, client, auth_headers):
        """Test request_travel_guide with valid customer credentials."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "request_travel_guide",
                    "arguments": {
                        "customer_id": "demo-customer-001",
                        "customer_secret": "demo-customer-001-secret",
                        "sandbox_id": "test-sandbox-123",
                        "destination": "Tokyo",
                        "preferences": "foodie_adventure",
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        # Should NOT be an error (background task will fail, but request accepted)
        assert result.get("isError") is not True
        # Should contain request ID
        content = result["content"][0]["text"]
        assert "Request ID:" in content
        assert "Tokyo" in content

    def test_check_guide_status_invalid_customer(self, client, auth_headers):
        """Test check_guide_status with invalid credentials."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "check_guide_status",
                    "arguments": {
                        "customer_id": "invalid",
                        "customer_secret": "invalid",
                        "request_id": "some-request-id",
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        assert result.get("isError") is True
        assert "Authentication failed" in result["content"][0]["text"]

    def test_check_guide_status_not_found(self, client, auth_headers):
        """Test check_guide_status with non-existent request."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "check_guide_status",
                    "arguments": {
                        "customer_id": "demo-customer-001",
                        "customer_secret": "demo-customer-001-secret",
                        "request_id": "nonexistent-request-id",
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        assert result.get("isError") is True
        assert "not found" in result["content"][0]["text"]

    def test_list_my_requests_empty(self, client, auth_headers):
        """Test list_my_requests with no requests."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {
                    "name": "list_my_requests",
                    "arguments": {
                        "customer_id": "test-customer-002",
                        "customer_secret": "test-customer-002-secret",
                    },
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data["result"]
        content = result["content"][0]["text"]
        assert "don't have any" in content or "Your Requests" in content

    def test_unknown_tool(self, client, auth_headers):
        """Test calling unknown tool returns error."""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 40,
                "method": "tools/call",
                "params": {
                    "name": "unknown_tool",
                    "arguments": {},
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert "Unknown tool" in data["error"]["message"]


# =============================================================================
# Database Tests
# =============================================================================


class TestDatabase:
    def test_customer_validation_valid(self, temp_db):
        """Test validating correct customer credentials."""
        # Demo customer: secret should be "{customer_id}-secret"
        assert temp_db.validate_customer(
            "demo-customer-001", "demo-customer-001-secret"
        )

    def test_customer_validation_invalid(self, temp_db):
        """Test validating incorrect customer credentials."""
        assert not temp_db.validate_customer("demo-customer-001", "wrong-secret")
        assert not temp_db.validate_customer("nonexistent", "any-secret")

    def test_create_guide_request(self, temp_db):
        """Test creating a guide request."""
        request_id = temp_db.create_guide_request(
            customer_id="demo-customer-001",
            sandbox_id="test-sandbox",
            destination="Paris",
            preferences="foodie_adventure",
            customer_name="Test User",
        )
        assert request_id is not None

        # Verify we can retrieve it
        request = temp_db.get_guide_request(request_id)
        assert request is not None
        assert request["destination"] == "Paris"
        assert request["preferences"] == "foodie_adventure"
        assert request["status"] == RequestStatus.PENDING.value

    def test_update_guide_request_status(self, temp_db):
        """Test updating guide request status."""
        request_id = temp_db.create_guide_request(
            customer_id="demo-customer-001",
            sandbox_id="test-sandbox",
            destination="Tokyo",
            preferences="nightlife",
        )

        # Update to processing
        temp_db.update_guide_request_status(
            request_id,
            RequestStatus.PROCESSING,
            private_conversation_id="conv-123",
        )

        request = temp_db.get_guide_request(request_id)
        assert request["status"] == RequestStatus.PROCESSING.value
        assert request["private_conversation_id"] == "conv-123"
        assert request["started_at"] is not None

        # Update to completed
        temp_db.update_guide_request_status(
            request_id,
            RequestStatus.COMPLETED,
            result_path="/workspace/travel_guide.html",
        )

        request = temp_db.get_guide_request(request_id)
        assert request["status"] == RequestStatus.COMPLETED.value
        assert request["result_path"] == "/workspace/travel_guide.html"
        assert request["completed_at"] is not None

    def test_get_requests_by_customer(self, temp_db):
        """Test getting requests by customer."""
        # Create multiple requests
        for city in ["Paris", "Tokyo", "Rome"]:
            temp_db.create_guide_request(
                customer_id="demo-customer-001",
                sandbox_id="test-sandbox",
                destination=city,
                preferences="foodie_adventure",
            )

        requests = temp_db.get_requests_by_customer("demo-customer-001")
        assert len(requests) == 3

        # Verify all cities are present (order may vary when created at same timestamp)
        destinations = set(r["destination"] for r in requests)
        assert destinations == {"Paris", "Tokyo", "Rome"}

    def test_get_requests_by_customer_limit(self, temp_db):
        """Test limiting requests returned."""
        for i in range(5):
            temp_db.create_guide_request(
                customer_id="demo-customer-001",
                sandbox_id="test-sandbox",
                destination=f"City{i}",
                preferences="foodie_adventure",
            )

        requests = temp_db.get_requests_by_customer("demo-customer-001", limit=2)
        assert len(requests) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
