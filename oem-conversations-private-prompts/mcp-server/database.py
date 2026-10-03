"""
SQLite database for tracking conversation state.

Tracks the relationship between:
- Customer-facing (public) conversations
- Private conversations started by the MCP server
- Guide generation requests and their status
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class RequestStatus(str, Enum):
    """Status of a guide generation request."""

    PENDING = "pending"  # Request received, not yet started
    PROCESSING = "processing"  # Private conversation is working
    COMPLETED = "completed"  # Guide generated successfully
    FAILED = "failed"  # Generation failed


class Database:
    """SQLite database for MCP server state."""

    def __init__(self, db_path: str = "./mcp_state.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._connect() as conn:
            conn.executescript("""
                -- Customers table for validating customer credentials
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    customer_secret_hash TEXT NOT NULL,
                    name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Projects table: maps project_id to sandbox_id
                -- Seeded by demo host before customer conversation starts
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    sandbox_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    customer_name TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                );

                -- Guide generation requests
                CREATE TABLE IF NOT EXISTS guide_requests (
                    id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    project_id TEXT,
                    sandbox_id TEXT NOT NULL,
                    public_conversation_id TEXT,
                    private_conversation_id TEXT,
                    destination TEXT NOT NULL,
                    preferences TEXT NOT NULL,
                    customer_name TEXT,
                    status TEXT DEFAULT 'pending',
                    result_url TEXT,  -- public guide URL (served by private conv)
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );

                -- Index for efficient status queries
                CREATE INDEX IF NOT EXISTS idx_guide_requests_status
                    ON guide_requests(status);
                CREATE INDEX IF NOT EXISTS idx_guide_requests_customer
                    ON guide_requests(customer_id);
                CREATE INDEX IF NOT EXISTS idx_guide_requests_sandbox
                    ON guide_requests(sandbox_id);
                CREATE INDEX IF NOT EXISTS idx_guide_requests_project
                    ON guide_requests(project_id);
                CREATE INDEX IF NOT EXISTS idx_projects_sandbox
                    ON projects(sandbox_id);

                -- Insert demo customers if they don't exist
                INSERT OR IGNORE INTO customers
                    (customer_id, customer_secret_hash, name)
                VALUES
                    ('demo-customer-001', 'demo-secret-hash-001', 'Demo Travel Agency'),
                    ('test-customer-002', 'test-secret-hash-002', 'Test Corp');
            """)

    @contextmanager
    def _connect(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Customer operations

    def validate_customer(self, customer_id: str, customer_secret: str) -> bool:
        """
        Validate customer credentials.

        In production, this would use proper password hashing.
        For this demo, we use a simple convention: secret = id + '-secret'
        """
        # Demo validation: secret should be "{customer_id}-secret"
        expected_secret = f"{customer_id}-secret"
        return customer_secret == expected_secret

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        """Get customer by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
            ).fetchone()
            return dict(row) if row else None

    # Guide request operations

    def create_guide_request(
        self,
        customer_id: str,
        sandbox_id: str,
        destination: str,
        preferences: str,
        customer_name: str | None = None,
        public_conversation_id: str | None = None,
    ) -> str:
        """Create a new guide generation request. Returns request ID."""
        request_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO guide_requests
                (id, customer_id, sandbox_id, public_conversation_id,
                 destination, preferences, customer_name, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    customer_id,
                    sandbox_id,
                    public_conversation_id,
                    destination,
                    preferences,
                    customer_name,
                    RequestStatus.PENDING.value,
                ),
            )
        return request_id

    def get_guide_request(self, request_id: str) -> dict[str, Any] | None:
        """Get a guide request by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM guide_requests WHERE id = ?", (request_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_guide_request_status(
        self,
        request_id: str,
        status: RequestStatus,
        private_conversation_id: str | None = None,
        result_url: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Update the status of a guide request."""
        with self._connect() as conn:
            updates = ["status = ?"]
            params: list[Any] = [status.value]

            if status == RequestStatus.PROCESSING:
                updates.append("started_at = ?")
                params.append(datetime.now(timezone.utc).isoformat())

            if status in (RequestStatus.COMPLETED, RequestStatus.FAILED):
                updates.append("completed_at = ?")
                params.append(datetime.now(timezone.utc).isoformat())

            if private_conversation_id:
                updates.append("private_conversation_id = ?")
                params.append(private_conversation_id)

            if result_url:
                updates.append("result_url = ?")
                params.append(result_url)

            if error_message:
                updates.append("error_message = ?")
                params.append(error_message)

            params.append(request_id)

            cursor = conn.execute(
                f"UPDATE guide_requests SET {', '.join(updates)} WHERE id = ?", params
            )
            return cursor.rowcount > 0

    def get_pending_requests(self) -> list[dict[str, Any]]:
        """Get all pending guide requests."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM guide_requests WHERE status = ? ORDER BY created_at",
                (RequestStatus.PENDING.value,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_requests_by_sandbox(self, sandbox_id: str) -> list[dict[str, Any]]:
        """Get all guide requests for a sandbox."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM guide_requests WHERE sandbox_id = ? "
                "ORDER BY created_at DESC",
                (sandbox_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_requests_by_customer(
        self,
        customer_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get recent guide requests for a customer."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM guide_requests
                WHERE customer_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (customer_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    # Project operations

    def create_project(
        self,
        project_id: str,
        sandbox_id: str,
        customer_id: str,
        customer_name: str | None = None,
    ) -> str:
        """
        Create a new project mapping project_id to sandbox_id.

        Called by the demo host to seed the database before
        starting the customer conversation.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects
                    (project_id, sandbox_id, customer_id, customer_name)
                VALUES (?, ?, ?, ?)
                """,
                (project_id, sandbox_id, customer_id, customer_name),
            )
        return project_id

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """Get a project by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ? AND active = 1",
                (project_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_project_by_sandbox(self, sandbox_id: str) -> dict[str, Any] | None:
        """Get a project by sandbox ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE sandbox_id = ? AND active = 1",
                (sandbox_id,),
            ).fetchone()
            return dict(row) if row else None

    def deactivate_project(self, project_id: str) -> bool:
        """Mark a project as inactive (soft delete)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE projects SET active = 0 WHERE project_id = ?", (project_id,)
            )
            return cursor.rowcount > 0

    def get_sandbox_id_for_project(self, project_id: str) -> str | None:
        """
        Look up sandbox_id for a given project_id.

        This is the key lookup used by request_travel_guide
        to determine which sandbox to use for the private conversation.
        """
        project = self.get_project(project_id)
        return project["sandbox_id"] if project else None
