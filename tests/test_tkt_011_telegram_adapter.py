"""
TKT-011 Telegram adapter tests.

The real SessionManager, AdversarialGuardrail, InvertedIndex, GeminiClient,
and ChatOrchestrator are exercised. Fakes are limited to external Telegram
transport methods and the external Gemini API network call.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.gemini_client import GeminiClient
from src.core.guardrail import AdversarialGuardrail
from src.core.kb_reader import SandboxedKBReader
from src.core.models import DialogueRole
from src.core.orchestrator import ChatOrchestrator
from src.core.search_engine import InvertedIndex
from src.core.session_manager import SessionManager
from src.transport.telegram_bot import (
    TelegramBotAdapter,
    TelegramBotConfigurationError,
    load_telegram_bot_token,
    run_telegram_bot,
)


class FakeTelegramBot:
    """Capture outbound Telegram API calls without network access."""

    def __init__(self):
        self.messages = []
        self.documents = []

    async def send_message(self, *, chat_id, text):
        self.messages.append({"chat_id": chat_id, "text": text})

    async def send_document(self, *, chat_id, document, filename):
        self.documents.append(
            {
                "chat_id": chat_id,
                "filename": filename,
                "content": document.read(),
            }
        )


@pytest.fixture
def core_services():
    """Construct real core services with only Gemini's network method stubbed."""
    kb_reader = SandboxedKBReader(Path(__file__).resolve().parent.parent / "knowledge_base")
    search_engine = InvertedIndex(kb_reader=kb_reader, worker_threads=2)
    search_engine.build_index()
    session_manager = SessionManager()
    guardrail = AdversarialGuardrail(session_manager=session_manager)
    api_state = {"calls": [], "exception": None}

    def generate_content(*, model, contents, config):
        api_state["calls"].append({"model": model, "contents": contents, "config": config})
        if api_state["exception"] is not None:
            raise api_state["exception"]
        return SimpleNamespace(
            text="Перевірте налаштування згідно з наданою інструкцією."
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
    return session_manager, search_engine, orchestrator, api_state


@pytest.fixture
def adapter_and_transport(core_services):
    """Pair the real application core with a fake outbound Telegram transport."""
    session_manager, search_engine, orchestrator, api_state = core_services
    telegram_bot = FakeTelegramBot()
    context = SimpleNamespace(bot=telegram_bot)
    adapter = TelegramBotAdapter(
        session_manager=session_manager,
        orchestrator=orchestrator,
    )
    return adapter, context, telegram_bot, session_manager, search_engine, api_state


def make_update(chat_id, *, text=None, **attachments):
    """Create the Telegram message/update attributes used by handlers."""
    message = SimpleNamespace(chat_id=chat_id, text=text, **attachments)
    return SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=chat_id))


@pytest.mark.parametrize("command", ["start", "reset"])
def test_tkt_011_start_and_reset_clear_history_and_send_ukrainian_welcome(
    adapter_and_transport, command
):
    """
    TKT-011 | REQ-F-001 | AC-F-001 | BDD Scenario 1:
    /start and /reset clear the sender's history and invite them to ask an IT question.
    """
    adapter, context, bot, session_manager, _, _ = adapter_and_transport
    chat_id = 81001
    session_manager.add_turn(chat_id.__str__(), DialogueRole.USER, "Не працює принтер")
    session_manager.add_turn(chat_id.__str__(), DialogueRole.ASSISTANT, "Перевірте принтер.")

    asyncio.run(getattr(adapter, f"handle_{command}")(make_update(chat_id), context))

    session = session_manager.get_session(str(chat_id))
    assert session.turn_count == 0
    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == chat_id
    assert any(character in bot.messages[0]["text"] for character in "іїєґ")
    assert any(word in bot.messages[0]["text"].lower() for word in ("іт", "пит", "проблем"))


def test_tkt_011_help_sends_ukrainian_text_hint_without_clearing_session(adapter_and_transport):
    """
    TKT-011 | REQ-F-001 | AC-F-001 | BDD Scenario 1:
    /help gives a text-usage hint and leaves existing session turns unchanged.
    """
    adapter, context, bot, session_manager, _, _ = adapter_and_transport
    session_id = "81002"
    session_manager.add_turn(session_id, DialogueRole.USER, "Не працює принтер")
    session_manager.add_turn(session_id, DialogueRole.ASSISTANT, "Опишіть помилку.")
    turns_before = session_manager.get_session(session_id).turns

    asyncio.run(adapter.handle_help(make_update(int(session_id)), context))

    assert bot.messages[0]["chat_id"] == int(session_id)
    assert any(character in bot.messages[0]["text"] for character in "іїєґ")
    assert any(word in bot.messages[0]["text"].lower() for word in ("текст", "опиш"))
    assert session_manager.get_session(session_id).turns == turns_before


@pytest.mark.parametrize(
    "payload",
    [
        {"photo": [object()]},
        {"sticker": object()},
        {"voice": object()},
        {"video": object()},
        {"audio": object()},
        {"document": object()},
    ],
    ids=["photo", "sticker", "voice", "video", "audio", "document"],
)
def test_tkt_011_non_text_payload_rejected_before_core_processing(adapter_and_transport, payload):
    """
    TKT-011 | REQ-F-002 | AC-F-002 | BDD Scenario 7:
    Reject each supported non-text Telegram payload with the contract's
    NON_TEXT_PAYLOAD_UNSUPPORTED notice before core search or Gemini processing.
    """
    adapter, context, bot, session_manager, _, api_state = adapter_and_transport
    chat_id = 81003

    asyncio.run(adapter.handle_message(make_update(chat_id, **payload), context))

    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == chat_id
    assert any(character in bot.messages[0]["text"] for character in "іїєґ")
    assert "текст" in bot.messages[0]["text"].lower()
    assert api_state["calls"] == []
    assert session_manager.get_active_session_count() == 0


def test_tkt_011_text_is_routed_to_real_orchestrator_and_replied(adapter_and_transport):
    """
    TKT-011 | REQ-F-007..REQ-F-011 | REQ-NF-008 | AC-F-001 | AC-NF-008:
    UTF-8 Ukrainian text is processed by the real core using str(chat_id).
    """
    adapter, context, bot, session_manager, _, api_state = adapter_and_transport
    chat_id = 81004
    query = "Що робити, якщо в офісі зажувало папір у принтері?"

    asyncio.run(adapter.handle_message(make_update(chat_id, text=query), context))

    assert len(bot.messages) == 1
    assert bot.messages[0] == {
        "chat_id": chat_id,
        "text": "Перевірте налаштування згідно з наданою інструкцією.",
    }
    assert len(api_state["calls"]) == 1
    session = session_manager.get_session(str(chat_id))
    assert session.turn_count == 2
    assert session.turns[0].content == query


def test_tkt_011_rejects_text_above_openapi_message_limit(adapter_and_transport):
    """
    TKT-011 | REQ-F-002 | OpenAPI /messages:
    Reject text over the contract's 4000-character maximum before core processing.
    """
    adapter, context, bot, session_manager, _, api_state = adapter_and_transport

    asyncio.run(
        adapter.handle_message(make_update(81006, text="а" * 4001), context)
    )

    assert len(bot.messages) == 1
    assert "4000" in bot.messages[0]["text"]
    assert session_manager.get_active_session_count() == 0
    assert api_state["calls"] == []


def test_tkt_011_api_fallback_sends_notice_and_top_three_documents(adapter_and_transport):
    """
    TKT-011 | REQ-F-012 | AC-F-012 | BDD Scenario 8:
    API_FALLBACK_DOCUMENTS sends the Ukrainian notice and each UTF-8 attachment
    using its original knowledge-base filename.
    """
    adapter, context, bot, _, search_engine, api_state = adapter_and_transport
    api_state["exception"] = TimeoutError("simulated Gemini timeout")
    chat_id = 81005
    query = "Я загубив корпоративний телефон у метро"

    asyncio.run(
        adapter.handle_message(
            make_update(chat_id, text=query),
            context,
        )
    )

    assert bot.messages == [
        {
            "chat_id": chat_id,
            "text": "Перепрошую, сервіс штучного інтелекту тимчасово недоступний "
            "через технічні неполадки або тайм-аут. До відповіді додано відповідні "
            "інструкції з бази знань у вигляді файлів.",
        }
    ]
    expected_hits = search_engine.search(query).results
    assert [item["filename"] for item in bot.documents] == [
        hit.document_id for hit in expected_hits
    ]
    assert 0 < len(bot.documents) <= 3
    assert "lost_phone.md" in [item["filename"] for item in bot.documents]
    assert all(
        item["content"]
        == search_engine.kb_reader.read_document(item["filename"]).content.encode("utf-8")
        for item in bot.documents
    )


def test_tkt_011_loads_telegram_token_from_environment_or_dotenv(monkeypatch, tmp_path):
    """
    TKT-011 | REQ-NF-008:
    Bot configuration loads TELEGRAM_BOT_TOKEN through environment or python-dotenv.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=dotenv_test_token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_telegram_bot_token() == "dotenv_test_token"


def test_tkt_011_prefers_environment_token(monkeypatch):
    """
    TKT-011 | REQ-NF-008:
    Bot configuration accepts TELEGRAM_BOT_TOKEN supplied directly by the environment.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "environment_test_token")

    assert load_telegram_bot_token() == "environment_test_token"


@pytest.mark.parametrize("token", [None, "", "   "], ids=["missing", "empty", "whitespace"])
def test_tkt_011_live_runner_rejects_missing_telegram_token(monkeypatch, token):
    """
    TKT-011 | REQ-NF-008:
    Starting the live runner without TELEGRAM_BOT_TOKEN raises a clear configuration error.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr("src.transport.telegram_bot.load_dotenv", lambda **kwargs: False)
    if token is not None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    with pytest.raises(TelegramBotConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        run_telegram_bot()
