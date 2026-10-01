"""WebSocket realtime gateway for admin and customer live updates.

Only authenticated sockets are ever registered here (see ``routes.py``). Admin
sockets remember which login session opened them, so logging out or the
session expiring closes them instead of leaving a live feed open.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Set

from fastapi import WebSocket

# Custom close code: the login session behind this socket is no longer valid.
CLOSE_SESSION_ENDED = 4401


@dataclass
class AdminConnection:
    session_key: str  # SHA-256 hash of the admin login token
    expires_at: datetime


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ConnectionManager:
    def __init__(self):
        self.admin_connections: Dict[WebSocket, AdminConnection] = {}
        self.customer_connections: Dict[str, Set[WebSocket]] = {}

    async def connect_admin(self, websocket: WebSocket, session_key: str, expires_at: datetime):
        await websocket.accept()
        self.admin_connections[websocket] = AdminConnection(session_key, _as_utc(expires_at))

    def disconnect_admin(self, websocket: WebSocket):
        self.admin_connections.pop(websocket, None)

    async def _close_quietly(self, websocket: WebSocket, code: int = CLOSE_SESSION_ENDED):
        try:
            await websocket.close(code=code)
        except Exception:
            pass

    async def close_admin_session(self, session_key: str):
        """Close every admin socket opened by this login session (used on logout)."""
        for websocket, connection in list(self.admin_connections.items()):
            if connection.session_key == session_key:
                self.admin_connections.pop(websocket, None)
                await self._close_quietly(websocket)

    async def connect_customer(self, websocket: WebSocket, table_session_id: str):
        await websocket.accept()
        self.customer_connections.setdefault(table_session_id, set()).add(websocket)

    def disconnect_customer(self, websocket: WebSocket, table_session_id: str):
        sockets = self.customer_connections.get(table_session_id)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                self.customer_connections.pop(table_session_id, None)

    def has_listeners(self) -> bool:
        return bool(self.admin_connections) or any(self.customer_connections.values())

    async def broadcast_to_admin(self, event_type: str, payload: Dict[str, Any]):
        message = json.dumps({"type": event_type, "payload": payload}, default=str)
        now = datetime.now(timezone.utc)
        for websocket, connection in list(self.admin_connections.items()):
            if connection.expires_at <= now:
                self.admin_connections.pop(websocket, None)
                await self._close_quietly(websocket)
                continue
            try:
                await websocket.send_text(message)
            except Exception:
                self.admin_connections.pop(websocket, None)

    async def broadcast_to_table(self, table_session_id: str, event_type: str, payload: Dict[str, Any]):
        sockets = self.customer_connections.get(table_session_id)
        if not sockets:
            return
        message = json.dumps({"type": event_type, "payload": payload}, default=str)
        for websocket in list(sockets):
            try:
                await websocket.send_text(message)
            except Exception:
                sockets.discard(websocket)


ws_manager = ConnectionManager()
