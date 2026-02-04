"""
APN Core - Database Module
SQLite persistence layer for peers, sessions, tasks, and other stateful data.

Part of the Alpha Protocol Network (APN Core v1.0.0)
"""
import json
import logging
import aiosqlite
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

logger = logging.getLogger("apn.database")


class APNDatabase:
    """
    SQLite database manager for APN Core.

    Provides persistent storage for:
    - Peer nodes and their metadata
    - Secure sessions
    - Local tasks
    - Contribution settings
    - Mesh messages (for replay/sync)
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database and create tables if needed"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with self._get_connection() as db:
            await self._create_tables(db)
            await db.commit()

        logger.info(f"Database initialized: {self.db_path}")

    @asynccontextmanager
    async def _get_connection(self):
        """Get database connection with proper error handling"""
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def _create_tables(self, db: aiosqlite.Connection) -> None:
        """Create database tables"""

        # Peers table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS peers (
                node_id TEXT PRIMARY KEY,
                public_key TEXT NOT NULL,
                roles TEXT DEFAULT '[]',
                capabilities TEXT DEFAULT '{}',
                payment_address TEXT DEFAULT '',
                connected_at TEXT,
                last_seen TEXT,
                status TEXT DEFAULT 'offline',
                metadata TEXT DEFAULT '{}'
            )
        """)

        # Secure sessions table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS secure_sessions (
                peer_id TEXT PRIMARY KEY,
                send_key BLOB NOT NULL,
                recv_key BLOB NOT NULL,
                send_nonce INTEGER DEFAULT 0,
                recv_nonce INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                last_used TEXT,
                FOREIGN KEY (peer_id) REFERENCES peers (node_id)
            )
        """)

        # Tasks table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                assigned_to TEXT DEFAULT '',
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'pending',
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                created_by TEXT NOT NULL,
                synced_from TEXT,
                metadata TEXT DEFAULT '{}'
            )
        """)

        # Peer connections table (for mesh topology)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS peer_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_url TEXT NOT NULL UNIQUE,
                node_id TEXT,
                status TEXT DEFAULT 'disconnected',
                connected_at TEXT,
                last_keepalive TEXT,
                retry_count INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)

        # Contribution settings table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Audit log for security events
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                peer_id TEXT,
                details TEXT DEFAULT '{}',
                ip_address TEXT,
                success INTEGER DEFAULT 1
            )
        """)

        # Create indexes for common queries
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type)")

    # ============= Peer Operations =============

    async def save_peer(
        self,
        node_id: str,
        public_key: str,
        roles: List[str] = None,
        capabilities: Dict[str, Any] = None,
        payment_address: str = "",
    ) -> None:
        """Save or update a peer node"""
        async with self._get_connection() as db:
            now = datetime.now().isoformat()
            await db.execute("""
                INSERT INTO peers (node_id, public_key, roles, capabilities, payment_address, connected_at, last_seen, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'online')
                ON CONFLICT(node_id) DO UPDATE SET
                    public_key = excluded.public_key,
                    roles = excluded.roles,
                    capabilities = excluded.capabilities,
                    payment_address = excluded.payment_address,
                    last_seen = excluded.last_seen,
                    status = 'online'
            """, (
                node_id,
                public_key,
                json.dumps(roles or []),
                json.dumps(capabilities or {}),
                payment_address,
                now,
                now,
            ))
            await db.commit()
        logger.debug(f"Peer saved: {node_id}")

    async def get_peer(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get peer by node ID"""
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT * FROM peers WHERE node_id = ?", (node_id,)
            )
            row = await cursor.fetchone()
            if row:
                return self._row_to_dict(row)
        return None

    async def get_all_peers(self) -> List[Dict[str, Any]]:
        """Get all registered peers"""
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT * FROM peers ORDER BY last_seen DESC")
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def update_peer_status(self, node_id: str, status: str) -> None:
        """Update peer connection status"""
        async with self._get_connection() as db:
            await db.execute(
                "UPDATE peers SET status = ?, last_seen = ? WHERE node_id = ?",
                (status, datetime.now().isoformat(), node_id)
            )
            await db.commit()

    # ============= Session Operations =============

    async def save_session(
        self,
        peer_id: str,
        send_key: bytes,
        recv_key: bytes,
    ) -> None:
        """Save secure session keys"""
        async with self._get_connection() as db:
            now = datetime.now().isoformat()
            await db.execute("""
                INSERT INTO secure_sessions (peer_id, send_key, recv_key, created_at, last_used)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                    send_key = excluded.send_key,
                    recv_key = excluded.recv_key,
                    send_nonce = 0,
                    recv_nonce = 0,
                    created_at = excluded.created_at,
                    last_used = excluded.last_used
            """, (peer_id, send_key, recv_key, now, now))
            await db.commit()
        logger.debug(f"Session saved for peer: {peer_id}")

    async def get_session(self, peer_id: str) -> Optional[Dict[str, Any]]:
        """Get secure session for peer"""
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT * FROM secure_sessions WHERE peer_id = ?", (peer_id,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None

    async def update_session_nonce(self, peer_id: str, send_nonce: int = None, recv_nonce: int = None) -> None:
        """Update session nonce counters"""
        async with self._get_connection() as db:
            updates = []
            params = []
            if send_nonce is not None:
                updates.append("send_nonce = ?")
                params.append(send_nonce)
            if recv_nonce is not None:
                updates.append("recv_nonce = ?")
                params.append(recv_nonce)

            if updates:
                updates.append("last_used = ?")
                params.append(datetime.now().isoformat())
                params.append(peer_id)

                await db.execute(
                    f"UPDATE secure_sessions SET {', '.join(updates)} WHERE peer_id = ?",
                    params
                )
                await db.commit()

    async def delete_session(self, peer_id: str) -> None:
        """Delete secure session"""
        async with self._get_connection() as db:
            await db.execute("DELETE FROM secure_sessions WHERE peer_id = ?", (peer_id,))
            await db.commit()
        logger.debug(f"Session deleted for peer: {peer_id}")

    # ============= Task Operations =============

    async def create_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new task"""
        async with self._get_connection() as db:
            now = datetime.now().isoformat()
            await db.execute("""
                INSERT INTO tasks (id, title, description, assigned_to, priority, status, due_date, created_at, created_by, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task["id"],
                task["title"],
                task.get("description", ""),
                task.get("assigned_to", ""),
                task.get("priority", "medium"),
                task.get("status", "pending"),
                task.get("due_date"),
                now,
                task["created_by"],
                json.dumps(task.get("metadata", {})),
            ))
            await db.commit()

        task["created_at"] = now
        logger.info(f"Task created: {task['id']} - {task['title']}")
        return task

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task by ID"""
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = await cursor.fetchone()
            if row:
                return self._row_to_dict(row)
        return None

    async def get_tasks(self, assigned_to: str = None, status: str = None) -> List[Dict[str, Any]]:
        """Get tasks with optional filters"""
        async with self._get_connection() as db:
            query = "SELECT * FROM tasks WHERE 1=1"
            params = []

            if assigned_to:
                query += " AND assigned_to = ?"
                params.append(assigned_to)
            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC"

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update task fields"""
        async with self._get_connection() as db:
            # Build update query
            allowed_fields = ["title", "description", "assigned_to", "priority", "status", "due_date"]
            set_clauses = []
            params = []

            for field in allowed_fields:
                if field in updates:
                    set_clauses.append(f"{field} = ?")
                    params.append(updates[field])

            if not set_clauses:
                return await self.get_task(task_id)

            set_clauses.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(task_id)

            await db.execute(
                f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?",
                params
            )
            await db.commit()

        logger.info(f"Task updated: {task_id}")
        return await self.get_task(task_id)

    async def sync_task(self, task: Dict[str, Any], synced_from: str) -> bool:
        """Sync task from mesh peer (avoid duplicates)"""
        existing = await self.get_task(task["id"])
        if existing:
            return False

        task["synced_from"] = synced_from
        await self.create_task(task)
        logger.info(f"Task synced from {synced_from}: {task['id']}")
        return True

    # ============= Peer Connection Operations =============

    async def save_peer_connection(self, peer_url: str, node_id: str = None, status: str = "connected") -> None:
        """Save peer connection state"""
        async with self._get_connection() as db:
            now = datetime.now().isoformat()
            await db.execute("""
                INSERT INTO peer_connections (peer_url, node_id, status, connected_at, last_keepalive)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(peer_url) DO UPDATE SET
                    node_id = COALESCE(excluded.node_id, peer_connections.node_id),
                    status = excluded.status,
                    connected_at = CASE WHEN excluded.status = 'connected' THEN excluded.connected_at ELSE peer_connections.connected_at END,
                    last_keepalive = excluded.last_keepalive,
                    retry_count = CASE WHEN excluded.status = 'connected' THEN 0 ELSE peer_connections.retry_count END
            """, (peer_url, node_id, status, now, now))
            await db.commit()

    async def update_peer_connection_status(self, peer_url: str, status: str, increment_retry: bool = False) -> None:
        """Update peer connection status"""
        async with self._get_connection() as db:
            if increment_retry:
                await db.execute(
                    "UPDATE peer_connections SET status = ?, retry_count = retry_count + 1 WHERE peer_url = ?",
                    (status, peer_url)
                )
            else:
                await db.execute(
                    "UPDATE peer_connections SET status = ?, last_keepalive = ? WHERE peer_url = ?",
                    (status, datetime.now().isoformat(), peer_url)
                )
            await db.commit()

    async def get_peer_connections(self) -> List[Dict[str, Any]]:
        """Get all peer connections"""
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT * FROM peer_connections")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ============= Settings Operations =============

    async def save_setting(self, key: str, value: Any) -> None:
        """Save a setting"""
        async with self._get_connection() as db:
            await db.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """, (key, json.dumps(value), datetime.now().isoformat()))
            await db.commit()

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value"""
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            if row:
                return json.loads(row["value"])
        return default

    # ============= Audit Log Operations =============

    async def log_audit_event(
        self,
        event_type: str,
        peer_id: str = None,
        details: Dict[str, Any] = None,
        ip_address: str = None,
        success: bool = True,
    ) -> None:
        """Log a security audit event"""
        async with self._get_connection() as db:
            await db.execute("""
                INSERT INTO audit_log (timestamp, event_type, peer_id, details, ip_address, success)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                event_type,
                peer_id,
                json.dumps(details or {}),
                ip_address,
                1 if success else 0,
            ))
            await db.commit()

    async def get_audit_logs(self, limit: int = 100, event_type: str = None) -> List[Dict[str, Any]]:
        """Get recent audit logs"""
        async with self._get_connection() as db:
            query = "SELECT * FROM audit_log"
            params = []

            if event_type:
                query += " WHERE event_type = ?"
                params.append(event_type)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ============= Utility Methods =============

    def _row_to_dict(self, row: aiosqlite.Row) -> Dict[str, Any]:
        """Convert database row to dictionary with JSON parsing"""
        result = dict(row)

        # Parse JSON fields
        for field in ["roles", "capabilities", "metadata"]:
            if field in result and isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except json.JSONDecodeError:
                    pass

        return result

    async def close(self) -> None:
        """Close database connection"""
        if self._connection:
            await self._connection.close()
            self._connection = None


# Global database instance
_db_instance: Optional[APNDatabase] = None


async def get_database(db_path: Path = None) -> APNDatabase:
    """Get or create database instance"""
    global _db_instance

    if _db_instance is None:
        if db_path is None:
            from .settings import get_settings
            settings = get_settings()
            db_path = settings.full_database_path

        _db_instance = APNDatabase(db_path)
        await _db_instance.initialize()

    return _db_instance


async def close_database() -> None:
    """Close database connection"""
    global _db_instance
    if _db_instance:
        await _db_instance.close()
        _db_instance = None
