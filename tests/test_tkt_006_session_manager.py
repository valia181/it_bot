"""
Unit, Concurrency, and Contract Verification for Session Manager.

Implements TKT-006 | REQ-F-001, REQ-F-006, REQ-NF-004, REQ-NF-006, REQ-NF-008, Constraint 6.4
Acceptance Criteria: AC-F-001, AC-F-006, AC-NF-004
BDD Scenarios: Scenario 1 (Session Initialization & Reset), Scenario 9 (Inactivity TTL Expiration)
Contracts: openapi-v2.yaml /sessions and /sessions/{session_id} schemas
"""

import threading
import time
from typing import List
import pytest

from src.core.exceptions import SessionNotFoundError
from src.core.models import (
    ApiErrorCode,
    DialogueRole,
    DialogueTurn,
    SessionCommand,
    SessionInitResponse,
    SessionResource,
)
from src.core.session_manager import SessionManager


def test_tkt_006_session_initialization_and_reset_scenario_1():
    """
    Verify REQ-F-001 / AC-F-001 / BDD Scenario 1:
    Initializing or resetting session for chat_id clears any existing dialogue history
    and returns a Ukrainian welcome message matching openapi-v2.yaml SessionInitResponse.

    Implements TKT-006 | REQ-F-001, REQ-F-006 | AC-F-001 | BDD Scenario 1
    """
    manager = SessionManager()
    session_id = "1001"

    # Step 1: Initialize session
    init_res: SessionInitResponse = manager.initialize_session(session_id=session_id, command=SessionCommand.START)
    assert init_res.session_id == session_id
    assert init_res.max_turns == 6
    assert init_res.ttl_seconds == 900
    assert len(init_res.welcome_message) > 0
    assert "ІТ-помічник" in init_res.welcome_message

    # Step 2: Add dialogue turns
    manager.add_turn(session_id, DialogueRole.USER, "У мене зламався принтер")
    manager.add_turn(session_id, DialogueRole.ASSISTANT, "Перевірте лоток для паперу.")
    state_before = manager.get_session(session_id)
    assert state_before.turn_count == 2

    # Step 3: Trigger /reset command
    reset_res = manager.initialize_session(session_id=session_id, command=SessionCommand.RESET)
    assert reset_res.session_id == session_id
    state_after = manager.get_session(session_id)
    assert state_after.turn_count == 0
    assert state_after.turns == []


def test_tkt_006_sliding_window_capacity_6_turns():
    """
    Verify REQ-F-006 / AC-F-006:
    Sending 8 consecutive turns retains strictly the last 6 turns in chronological order.

    Implements TKT-006 | REQ-F-006 | AC-F-006
    """
    manager = SessionManager()
    session_id = "test_user_sliding"

    manager.initialize_session(session_id)

    # Append 8 turns
    for turn_idx in range(1, 9):
        role = DialogueRole.USER if turn_idx % 2 != 0 else DialogueRole.ASSISTANT
        manager.add_turn(session_id, role, f"Повідомлення #{turn_idx}")

    session: SessionResource = manager.get_session(session_id)

    # Strictly 6 turns retained
    assert session.turn_count == 6
    assert len(session.turns) == 6
    assert session.max_turns == 6

    # Verify oldest turns #1 and #2 were evicted, keeping #3 through #8
    expected_contents = [f"Повідомлення #{idx}" for idx in range(3, 9)]
    actual_contents = [turn.content for turn in session.turns]
    assert actual_contents == expected_contents


def test_tkt_006_inactivity_ttl_expiration_scenario_9():
    """
    Verify REQ-F-006 / AC-F-006 / BDD Scenario 9:
    A session inactive for more than 15 minutes (900s) is automatically purged upon subsequent access.
    New message after TTL initiates a fresh clean session.

    Implements TKT-006 | REQ-F-006 | AC-F-006 | BDD Scenario 9
    """
    # Deterministic clock simulation
    current_simulated_time = 10000.0

    def mock_clock() -> float:
        return current_simulated_time

    manager = SessionManager(clock=mock_clock)
    session_id = "2002"

    manager.initialize_session(session_id)
    # Add 4 turns at 10:00 (t = 10000.0)
    for idx in range(1, 5):
        role = DialogueRole.USER if idx % 2 != 0 else DialogueRole.ASSISTANT
        manager.add_turn(session_id, role, f"Діалог 10:00 #{idx}")

    active_session = manager.get_session(session_id)
    assert active_session.turn_count == 4
    assert active_session.remaining_ttl_seconds == 900

    # Advance time by 10 minutes (600s) -> remaining 300s
    current_simulated_time += 600.0
    mid_session = manager.get_session(session_id)
    assert mid_session.turn_count == 4
    assert mid_session.remaining_ttl_seconds == 300

    # Advance time to 16 minutes after last activity (960s total > 900s TTL)
    current_simulated_time += 360.0  # 600 + 360 = 960s

    # Inactivity TTL expired: get_session raises SessionNotFoundError
    with pytest.raises(SessionNotFoundError) as exc_info:
        manager.get_session(session_id)
    assert exc_info.value.error_code == ApiErrorCode.SESSION_NOT_FOUND

    # Sending a new message after expiration starts a clean session
    manager.add_turn(session_id, DialogueRole.USER, "Нове повідомлення о 10:16")
    fresh_session = manager.get_session(session_id)
    assert fresh_session.turn_count == 1
    assert fresh_session.turns[0].content == "Нове повідомлення о 10:16"


def test_tkt_006_20_thread_concurrency_stress_rlock():
    """
    Verify REQ-NF-004 / AC-NF-004:
    20 concurrent worker threads updating, reading, resetting, and querying distinct sessions
    under threading.RLock execute with zero race conditions or cross-session leakage.

    Implements TKT-006 | REQ-NF-004 | AC-NF-004
    """
    manager = SessionManager()
    exceptions: List[Exception] = []

    def user_worker(thread_id: int):
        session_id = f"user_thread_{thread_id}"
        try:
            manager.initialize_session(session_id)
            for turn in range(12):
                manager.add_turn(
                    session_id,
                    DialogueRole.USER if turn % 2 == 0 else DialogueRole.ASSISTANT,
                    f"Turn {turn} for {session_id}",
                )
                state = manager.get_session(session_id)
                assert state.session_id == session_id
                assert state.turn_count <= 6
                # Verify cross-session context isolation: every turn belongs to this thread's session
                for t in state.turns:
                    assert session_id in t.content
            # Reset halfway through
            if thread_id % 3 == 0:
                manager.reset_session(session_id)
                manager.initialize_session(session_id)
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=user_worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(exceptions) == 0, f"Concurrent session exceptions: {exceptions}"


def test_tkt_006_openapi_session_not_found():
    """
    Verify openapi-v2.yaml error handling:
    Accessing or resetting a non-existent session raises SessionNotFoundError with SESSION_NOT_FOUND.

    Implements TKT-006 | REQ-F-006
    """
    manager = SessionManager()
    missing_id = "non_existent_chat_9999"

    with pytest.raises(SessionNotFoundError) as exc_info:
        manager.get_session(missing_id)
    assert exc_info.value.error_code == ApiErrorCode.SESSION_NOT_FOUND

    with pytest.raises(SessionNotFoundError) as exc_del:
        manager.reset_session(missing_id)
    assert exc_del.value.error_code == ApiErrorCode.SESSION_NOT_FOUND


def test_tkt_006_help_command_prompt():
    """
    Verify REQ-F-001 / AC-F-001:
    /help command provides concise Ukrainian prompt explaining how to ask an IT question.

    Implements TKT-006 | REQ-F-001 | AC-F-001
    """
    manager = SessionManager()
    help_text = manager.get_help_message()
    assert len(help_text) > 0
    # Ukrainian localization check
    assert "проблем" in help_text or "ІТ" in help_text or "запит" in help_text
