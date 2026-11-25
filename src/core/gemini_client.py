"""
Google Gemini API Client for Grounded Response Generation.

Implements TKT-008 | REQ-NF-001 (Tier 2: LLM isolation), REQ-NF-002 (temperature=0, zero secrets),
REQ-NF-005 (timeout <= 10s), REQ-NF-006 (Ukrainian localization), REQ-NF-008 (Decoupled architecture).
Acceptance Criteria: AC-NF-001, AC-NF-002, AC-NF-005, AC-NF-008.
"""

import os
from typing import Any, List, Optional, Tuple

import dotenv
from google.genai import Client, types

from src.core.exceptions import GeminiConfigurationError
from src.core.models import DialogueTurn


class GeminiClient:
    """
    Client for Google Gemini API enforcing zero-temperature deterministic generation,
    maximum 10-second request timeout, zero external tools, and exclusive context isolation.

    Implements TKT-008 | REQ-NF-001, REQ-NF-002, REQ-NF-005, REQ-NF-008
    """

    DEFAULT_MODEL: str = "gemini-2.5-flash"
    DEFAULT_TEMPERATURE: float = 0.0
    DEFAULT_TIMEOUT_SECONDS: float = 10.0

    SYSTEM_GROUNDING_INSTRUCTION: str = (
        "Ви — ввічливий та компетентний корпоративний ІТ-помічник служби підтримки.\n"
        "Відповідайте виключно українською мовою лаконічно, чітко та структуровано (за пунктами).\n"
        "СУВОРЕ ПРАВИЛО: Відповідайте виключно на основі наданих нижче документів із бази знань.\n"
        "Категорично заборонено вигадувати факти, інструкції чи посилання, яких немає в документах.\n"
        "Якщо в наданих документах немає відповіді на запитання користувача, чесно повідомте, "
        "що інформація відсутня, і порадьте звернутися до спеціаліста підтримки: @corp_it_support.\n"
        "Ніколи не запитуйте та не розголошуйте паролі, токени чи секретні дані."
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: Optional[Any] = None,
    ):
        """
        Initialize the Gemini API client.

        :param api_key: Gemini API key. If None, loaded from environment or .env via python-dotenv.
        :param model: Target Gemini model identifier (default: gemini-2.5-flash).
        :param timeout_seconds: Maximum request timeout in seconds (default: 10.0s per REQ-NF-005).
        :param client: Optional pre-configured or mocked google.genai.Client instance for testing.
        :raises GeminiConfigurationError: If GEMINI_API_KEY is missing or invalid.
        """
        if api_key is None:
            dotenv.load_dotenv()
            api_key = os.environ.get("GEMINI_API_KEY")

        if not api_key or not api_key.strip() or api_key.strip() == "your_gemini_api_key_here":
            raise GeminiConfigurationError(
                "GEMINI_API_KEY is not configured or missing from environment/.env. "
                "Please configure a valid GEMINI_API_KEY to enable AI orchestration."
            )

        self.api_key: str = api_key.strip()
        self.model: str = model
        self.temperature: float = self.DEFAULT_TEMPERATURE
        self.timeout_seconds: float = timeout_seconds

        if client is not None:
            self._client = client
        else:
            self._client = Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
            )

    def generate_grounded_response(
        self,
        query: str,
        context_documents: List[Tuple[str, str]],
        dialogue_history: Optional[List[DialogueTurn]] = None,
    ) -> str:
        """
        Invoke Gemini API passing strictly the Top-3 context documents and query.

        Enforces temperature = 0, zero tools, timeout <= 10s, and returns stripped response text.

        Implements TKT-008 | REQ-F-007, REQ-NF-001, REQ-NF-002, REQ-NF-005, REQ-NF-006
        :param query: User query string.
        :param context_documents: List of (document_id, text_content) tuples for Top-3 documents.
        :param dialogue_history: Optional sliding window of previous dialogue turns.
        :return: Synthesized grounded response text in Ukrainian.
        """
        # Format Top-3 context documents strictly per REQ-NF-001 Tier 2
        docs_parts: List[str] = []
        for doc_id, doc_content in context_documents:
            docs_parts.append(f"--- Документ: {doc_id} ---\n{doc_content}\n")
        docs_block = "\n".join(docs_parts)

        # Format session dialogue history if available
        history_block = ""
        if dialogue_history:
            turns_parts: List[str] = []
            for turn in dialogue_history:
                role_val = getattr(turn.role, "value", str(turn.role))
                role_label = "Користувач" if role_val == "user" else "Помічник"
                turns_parts.append(f"{role_label}: {turn.content}")
            history_block = "Історія попереднього діалогу:\n" + "\n".join(turns_parts) + "\n\n"

        prompt = (
            f"{self.SYSTEM_GROUNDING_INSTRUCTION}\n\n"
            f"Документи бази знань (Top-3 Documents):\n"
            f"{docs_block}\n"
            f"{history_block}"
            f"Запит користувача: {query}\n\n"
            f"Відповідь:"
        )

        config = types.GenerateContentConfig(
            temperature=self.temperature,
            tools=[],
            http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
        )

        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        return (getattr(response, "text", "") or "").strip()