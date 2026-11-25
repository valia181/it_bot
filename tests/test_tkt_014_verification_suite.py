"""TKT-014 tests for deterministic control verification and its report contract."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.gemini_client import GeminiClient
from src.core.guardrail import AdversarialGuardrail
from src.core.kb_reader import SandboxedKBReader
from src.core.models import (
    IncomingMessageRequest,
    PayloadType,
    QueryClassification,
    VerificationCaseCategory,
    VerificationRunRequest,
)
from src.core.orchestrator import ChatOrchestrator
from src.core.search_engine import InvertedIndex
from src.core.session_manager import SessionManager
from src.core.verification import format_verification_report, run_verification_suite


@pytest.fixture
def orchestrator_factory():
    """Build real core stacks with only the Gemini network boundary controlled."""
    class RecordingFactory:
        def __init__(self):
            self.created = []

        def __call__(self, *, api_failure=False):
            return self.create(api_failure=api_failure)

        def create(self, *, api_failure=False):
            kb_reader = SandboxedKBReader(
                Path(__file__).resolve().parent.parent / "knowledge_base"
            )
            search_engine = InvertedIndex(kb_reader=kb_reader, worker_threads=2)
            search_engine.build_index()
            session_manager = SessionManager(max_turns=6, ttl_seconds=900)
            guardrail = AdversarialGuardrail(session_manager=session_manager)

            def generate_content(*, model, contents, config):
                if api_failure:
                    raise TimeoutError("simulated external Gemini timeout")
                return SimpleNamespace(
                    text="Перевірте налаштування за інструкцією з бази знань."
                )

            gemini_client = GeminiClient(
                api_key="offline_test_key",
                client=SimpleNamespace(
                    models=SimpleNamespace(generate_content=generate_content)
                ),
            )
            orchestrator = ChatOrchestrator(
                kb_reader=kb_reader,
                search_engine=search_engine,
                session_manager=session_manager,
                guardrail=guardrail,
                gemini_client=gemini_client,
            )
            self.created.append(orchestrator)
            return orchestrator

    return RecordingFactory()


def test_tkt_014_runs_ten_control_questions_and_one_fallback_check(orchestrator_factory):
    """
    TKT-014 | REQ-F-007..REQ-F-013, REQ-NF-006, REQ-NF-008 |
    AC-F-013, AC-NF-006, AC-NF-008:
    Exercise five distinct grounded topics, multi-turn clarification, escalation,
    three security refusals, and the separate API outage check with real core services.
    """
    request = VerificationRunRequest()
    report = run_verification_suite(
        orchestrator_factory=orchestrator_factory,
        request=request,
    )

    assert report.total_cases == 11
    assert report.passed_cases == 11
    assert report.failed_cases == 0
    assert list(report.results[i].case_id for i in range(10)) == list(range(1, 11))
    assert report.results[10].case_id == 11
    assert all(result.passed for result in report.results)
    assert [result.category for result in report.results[:5]] == [
        VerificationCaseCategory.IN_SCOPE
    ] * 5
    assert report.results[5].category == VerificationCaseCategory.AMBIGUOUS_MULTI_TURN
    assert report.results[5].actual_classification == QueryClassification.IN_SCOPE_ANSWER
    assert "Уточнення:" in report.results[5].actual_response
    assert "Уточніть" in report.results[5].actual_response
    assert report.results[6].category == VerificationCaseCategory.OUT_OF_SCOPE
    assert report.results[6].actual_classification == QueryClassification.OUT_OF_SCOPE_ESCALATION
    assert all(
        result.expected_classification == QueryClassification.IN_SCOPE_ANSWER
        and result.actual_classification == QueryClassification.IN_SCOPE_ANSWER
        for result in report.results[:6]
    )
    assert [result.category for result in report.results[7:10]] == [
        VerificationCaseCategory.ADVERSARIAL
    ] * 3
    assert all(
        result.actual_classification == QueryClassification.SECURITY_HARD_REFUSAL
        for result in report.results[7:10]
    )
    assert report.results[10].category == VerificationCaseCategory.API_FALLBACK
    assert report.results[10].actual_classification == QueryClassification.API_FALLBACK_DOCUMENTS
    assert report.executed_at.tzinfo is not None

    normal_orchestrator, fallback_orchestrator = orchestrator_factory.created
    assert normal_orchestrator.session_manager.get_session(
        "verification-case-6"
    ).turn_count == 4
    for case_id in (8, 9, 10):
        stored_turns = normal_orchestrator.session_manager.get_session(
            f"verification-case-{case_id}"
        ).turns
        assert len(stored_turns) == 2
        assert stored_turns[0].content == "Мій корпоративний принтер не друкує."
        assert stored_turns[1].content == "Перевірте підключення принтера."
    assert fallback_orchestrator.session_manager.get_session(
        "verification-fallback"
    ).turn_count == 2


def test_tkt_014_report_matches_openapi_schema_and_formats_pass_fail_summary(
    orchestrator_factory,
):
    """
    TKT-014 | REQ-F-013, REQ-NF-006, REQ-NF-008 |
    AC-F-013, AC-NF-006, AC-NF-008:
    Validate VerificationRunReport/VerificationCaseResult fields and readable verdict output.
    """
    report = run_verification_suite(orchestrator_factory=orchestrator_factory)
    payload = report.model_dump(mode="json")

    assert set(payload) == {
        "total_cases",
        "passed_cases",
        "failed_cases",
        "executed_at",
        "results",
    }
    assert set(payload["results"][0]) == {
        "case_id",
        "category",
        "question",
        "expected_classification",
        "actual_classification",
        "actual_response",
        "passed",
    }
    assert all(result["question"] and result["actual_response"] for result in payload["results"])
    formatted = format_verification_report(report)
    assert "PASS" in formatted
    assert "11/11" in formatted
    assert "API_FALLBACK" in formatted


def test_tkt_014_can_omit_optional_fallback_case(orchestrator_factory):
    """
    TKT-014 | REQ-F-013 | AC-F-013:
    VerificationRunRequest controls whether the optional fallback simulation is included.
    """
    report = run_verification_suite(
        orchestrator_factory=orchestrator_factory,
        request=VerificationRunRequest(include_fallback_simulation=False),
    )

    assert report.total_cases == 10
    assert report.passed_cases == 10
    assert all(result.case_id <= 10 for result in report.results)


def test_tkt_014_message_request_matches_openapi_text_bounds():
    """
    TKT-014 | REQ-F-002 | AC-F-002:
    Keep the core request model aligned with OpenAPI /messages text minLength/maxLength.
    """
    base = {"session_id": "chat-1", "payload_type": PayloadType.TEXT}
    assert IncomingMessageRequest(**base, text="я" * 4000).text == "я" * 4000
    with pytest.raises(ValueError):
        IncomingMessageRequest(**base, text="я" * 4001)
    with pytest.raises(ValueError):
        IncomingMessageRequest(**base, text="")
