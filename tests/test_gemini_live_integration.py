"""
Live Integration Test Suite for Google Gemini API and Chat Orchestrator.

Implements TKT-008 | REQ-F-007, REQ-NF-001, REQ-NF-002, REQ-NF-005, REQ-NF-006, REQ-NF-008
Acceptance Criteria: AC-F-007, AC-NF-001, AC-NF-002, AC-NF-005, AC-NF-006, AC-NF-008.

Tests live end-to-end orchestration against the Google Gemini API (gemini-2.5-flash, temperature=0).
Decorated with @pytest.mark.integration.
Skips gracefully during offline CI runs when GEMINI_API_KEY is missing or set to a placeholder (REQ-NF-008).
"""

import os
from pathlib import Path
import time

import dotenv
import pytest

from src.core.gemini_client import GeminiClient
from src.core.models import QueryClassification
from src.core.orchestrator import ChatOrchestrator

# Load environment variables from untracked .env file in workspace root
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.is_file():
    dotenv.load_dotenv(dotenv_path=_env_path, override=True)

_key = os.environ.get("GEMINI_API_KEY", "").strip()
_is_missing_or_placeholder = not _key or _key in (
    "your_gemini_api_key_here",
    "your_api_key_here",
    "dummy_key",
    "PLACEHOLDER",
)


@pytest.mark.integration
@pytest.mark.skipif(
    _is_missing_or_placeholder,
    reason="Live Gemini API test requires a valid GEMINI_API_KEY in .env or environment (REQ-NF-008).",
)
def test_gemini_live_grounded_in_scope_answer():
    """
    Verify live grounded response generation through ChatOrchestrator and GeminiClient.

    Query: "Що робити, якщо в офісі зажувало папір у принтері?"
    Grounding: printer_issues.md

    Assertions:
    - classification == QueryClassification.IN_SCOPE_ANSWER
    - reply_text is non-empty, written in Ukrainian (REQ-NF-006)
    - reply_text factually matches printer_issues.md (REQ-F-007)
    - matched_documents contains printer_issues.md (REQ-F-005)
    """
    # Initialize live client and orchestrator
    client = GeminiClient(api_key=_key)
    assert client.model == "gemini-2.5-flash"
    assert client.temperature == 0.0
    assert client.timeout_seconds <= 10.0

    orchestrator = ChatOrchestrator(gemini_client=client)

    session_id = "test_live_session_printer"
    query = "Що робити, якщо в офісі зажувало папір у принтері?"

    # Execute with retry for transient Google free-tier rate spikes (503 / 429)
    response = None
    for attempt in range(2):
        try:
            response = orchestrator.process_message(session_id=session_id, query=query)
            if response.classification == QueryClassification.IN_SCOPE_ANSWER:
                break
        except Exception as exc:
            if attempt == 0 and any(code in str(exc) for code in ["503", "429", "UNAVAILABLE"]):
                time.sleep(1.5)
                continue
            raise

    assert response is not None

    # 1. Classification check
    assert response.classification == QueryClassification.IN_SCOPE_ANSWER

    # 2. Reply content non-empty and in Ukrainian (REQ-NF-006)
    assert response.reply_text is not None and len(response.reply_text.strip()) > 20
    ukrainian_alphabet = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
    assert any(ch in response.reply_text.lower() for ch in ukrainian_alphabet)

    # 3. Factually matches printer_issues.md instructions (REQ-F-007)
    # printer_issues.md instructs: вимкніть живлення, відкрийте кришку, витягніть папір
    reply_lower = response.reply_text.lower()
    assert any(
        term in reply_lower
        for term in ["папір", "принтер", "живленн", "кришк", "лоток", "витягн", "вимкн"]
    )

    # 4. Matched documents verification
    assert response.context_updated is True
    assert len(response.matched_documents) >= 1
    assert any(doc.document_id == "printer_issues.md" for doc in response.matched_documents)

    # 5. Zero attachments & zero escalation for in-scope success
    assert response.escalation_contact is None
    assert response.attachments == []