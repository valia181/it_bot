"""
Thread-Safe In-Memory Session Context Manager (Sliding Window & TTL).

Implements TKT-006 | REQ-F-001, REQ-F-006, REQ-NF-004, REQ-NF-006, REQ-NF-008, Constraint 6.4
Strictly aligned with openapi-v2.yaml /sessions and /sessions/{session_id} schemas.
Features:
- Ephemeral in-memory session store keyed by chat_id / session_id (Constraint 6.4: no external DB).
- Sliding context window maintaining at most 6 most recent dialogue turns (REQ-F-006).
- Automatic inactivity expiration after 15 minutes (900 seconds TTL).
- Thread-safe synchronization via threading.RLock (REQ-NF-004).
- Decoupled REST/core interface for deterministic testing without Telegram servers (REQ-NF-008).
"""

from collections import deque
from datetime import datetime, timezone
import threading
import time
from typing import Callable, Deque, Dict, List, Optional

from src.core.exceptions import SessionNotFoundError
from src.core.models import (
    DialogueRole,
    DialogueTurn,
    SessionCommand,
    SessionInitResponse,
    SessionResource,
)


class _UserSession:
    """Internal container for an individual user session."""

    def __init__(self, session_id: str, max_turns: int, created_at: float):
        self.session_id: str = session_id
        self.turns: Deque[DialogueTurn] = deque(maxlen=max_turns)
        self.last_activity: float = created_at
        self.created_at: float = created_at

    def is_expired(self, current_time: float, ttl_seconds: int) -> bool:
        """Check whether the session has exceeded the inactivity TTL."""
        return (current_time - self.last_activity) >= ttl_seconds

    def remaining_ttl(self, current_time: float, ttl_seconds: int) -> int:
        """Return remaining seconds before TTL expiration (clamped between 0 and ttl_seconds)."""
        elapsed = current_time - self.last_activity
        return max(0, min(ttl_seconds, int(ttl_seconds - elapsed)))


class SessionManager:
    """
    Thread-safe in-memory session manager with sliding dialogue window and inactivity TTL.

    Implements TKT-006 | REQ-F-001, REQ-F-006, REQ-NF-004, REQ-NF-008, Constraint 6.4
    """

    DEFAULT_WELCOME_MESSAGE: str = (
        "Вітаю! Я корпоративний ІТ-помічник. Опишіть вашу технічну проблему текстом, і я знайду відповідь у базі знань."
    )
    DEFAULT_HELP_MESSAGE: str = (
        "Опишіть вашу технічну проблему текстом (наприклад, 'не працює принтер' або 'як налаштувати VPN'), і я знайду відповідь у базі знань."
    )

    def __init__(
        self,
        max_turns: int = 6,
        ttl_seconds: int = 900,
        clock: Callable[[], float] = time.time,
    ):
        """
        Initialize the SessionManager.

        :param max_turns: Maximum sliding context window capacity (default: 6 per REQ-F-006).
        :param ttl_seconds: Inactivity expiration timeout in seconds (default: 900s / 15m).
        :param clock: Callable returning current timestamp in seconds (injectable for testing).
        """
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.clock = clock

        # Synchronization lock for thread safety (REQ-NF-004)
        self._lock = threading.RLock()

        # Ephemeral in-memory session storage (Constraint 6.4)
        self._sessions: Dict[str, _UserSession] = {}

    def initialize_session(
        self,
        session_id: str,
        command: Optional[SessionCommand] = None,
    ) -> SessionInitResponse:
        """
        Initialize a new session or reset an existing session's history for session_id.

        Corresponds to /start and /reset commands (REQ-F-001, REQ-F-006).
        Purges any existing turns for the session.

        Implements TKT-006 | REQ-F-001, REQ-F-006, REQ-NF-006
        :param session_id: Unique chat identifier (Telegram chat_id).
        :param command: Telegram command initiating the session (/start, /reset).
        :return: SessionInitResponse matching openapi-v2.yaml schema.
        """
        with self._lock:
            now = self.clock()
            self._sessions[session_id] = _UserSession(
                session_id=session_id,
                max_turns=self.max_turns,
                created_at=now,
            )
            return SessionInitResponse(
                session_id=session_id,
                welcome_message=self.DEFAULT_WELCOME_MESSAGE,
                max_turns=self.max_turns,
                ttl_seconds=self.ttl_seconds,
            )

    def get_session(self, session_id: str) -> SessionResource:
        """
        Retrieve active session context and remaining TTL.

        Automatically evicts and raises SessionNotFoundError if inactive > 900s (REQ-F-006).

        Implements TKT-006 | REQ-F-006, REQ-NF-004
        :param session_id: Target session identifier.
        :return: SessionResource matching openapi-v2.yaml schema.
        :raises SessionNotFoundError: If session does not exist or has expired.
        """
        with self._lock:
            now = self.clock()
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id=session_id)

            if session.is_expired(now, self.ttl_seconds):
                del self._sessions[session_id]
                raise SessionNotFoundError(session_id=session_id)

            return SessionResource(
                session_id=session_id,
                turns=list(session.turns),
                turn_count=len(session.turns),
                max_turns=self.max_turns,
                remaining_ttl_seconds=session.remaining_ttl(now, self.ttl_seconds),
            )

    def add_turn(self, session_id: str, role: DialogueRole, content: str) -> None:
        """
        Append a dialogue turn to the session's sliding context window.

        If the session does not exist or expired via TTL, automatically instantiates a fresh session.
        Enforces sliding window: keeps strictly at most max_turns (6 turns).

        Implements TKT-006 | REQ-F-006, REQ-NF-004
        :param session_id: Target session identifier.
        :param role: Dialogue participant role ('user' or 'assistant').
        :param content: Ukrainian text content of the turn.
        """
        with self._lock:
            now = self.clock()
            session = self._sessions.get(session_id)

            if session is None or session.is_expired(now, self.ttl_seconds):
                session = _UserSession(
                    session_id=session_id,
                    max_turns=self.max_turns,
                    created_at=now,
                )
                self._sessions[session_id] = session

            turn = DialogueTurn(
                role=role,
                content=content,
                timestamp=datetime.now(timezone.utc),
            )
            session.turns.append(turn)
            session.last_activity = now

    def reset_session(self, session_id: str) -> None:
        """
        Explicitly clear all stored dialogue turns for session_id.

        Corresponds to DELETE /sessions/{session_id} (REQ-F-001, REQ-F-006).

        Implements TKT-006 | REQ-F-001, REQ-F-006, REQ-NF-004
        :param session_id: Target session identifier.
        :raises SessionNotFoundError: If session does not exist.
        """
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id=session_id)
            del self._sessions[session_id]

    def get_help_message(self) -> str:
        """
        Return the Ukrainian usage hint prompt for /help command.

        Implements TKT-006 | REQ-F-001, REQ-NF-006
        :return: Ukrainian help prompt string.
        """
        return self.DEFAULT_HELP_MESSAGE

    def get_active_session_count(self) -> int:
        """Return the current count of non-expired sessions in RAM."""
        with self._lock:
            now = self.clock()
            # Clean expired
            expired_ids = [
                sid for sid, s in self._sessions.items() if s.is_expired(now, self.ttl_seconds)
            ]
            for sid in expired_ids:
                del self._sessions[sid]
            return len(self._sessions)
