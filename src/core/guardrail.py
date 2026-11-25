"""
Adversarial Query Guardrail and Context Isolation.

Implements TKT-007 | REQ-F-010, REQ-F-011, REQ-NF-006, REQ-NF-008
Provides deterministic detection of credential harvesting, social engineering,
and prompt injection attempts, issuing a Ukrainian Security Hard Refusal
while ensuring zero context pollution in the sliding session window.
"""

import re
from typing import List, Optional

from src.core.models import (
    BotMessageResponse,
    QueryClassification,
)
from src.core.session_manager import SessionManager

# Regex patterns for detecting credential harvesting and unauthorized password requests
_PATTERNS_CREDENTIAL_EXTRACTION = [
    re.compile(r"(?i)\b(скинь|скинути|дай|надай|покажи|скажи|напиши|виведи|передай)\b.*?\b(парол[ьяеі]|паролів|secret|token|api[_-]?key|ключ)"),
    re.compile(r"(?i)\b(парол[ьяеі]|паролів)\s+(директор|керівник|адмін|користувач|сервер|пошт)"),
    re.compile(r"(?i)\b(парол[ьяеі]|паролів)\s+від\s+пошти\s+(директор|керівник|адмін)"),
    re.compile(r"(?i)\b(show|give|reveal|send|tell|print)\b.*?\b(password|credential|secret|token|api[_-]?key)"),
    re.compile(r"(?i)\b(administrator|admin|director|root)\s+password\b"),
    re.compile(r"(?i)\b(паролі\s+всіх\s+користувачів|пароль\s+директора)\b"),
]

# Regex patterns for detecting prompt injection, rule override, and server exfiltration
_PATTERNS_PROMPT_INJECTION = [
    re.compile(r"(?i)\b(ігноруй|ігнорувати|забудь|відкинь|скасуй)\b.*?\b(попередні|правил|інструкці)"),
    re.compile(r"(?i)\b(ignore|disregard|forget|override)\b.*?\b(previous|rule|instruction|system)"),
    re.compile(r"(?i)\b(системний|system)\s*(промпт|prompt)\b"),
    re.compile(r"(?i)\b(jailbreak|dan\s+mode|режим\s+розробника|developer\s+mode)\b"),
    re.compile(r"(?i)\b(список\s+файлів\s+сервера|файлів\s+сервера|файл[уів]?\s*\.env|/etc/passwd|/etc/shadow)\b"),
]

# Safe patterns allowing legitimate self-service password reset inquiries
_SAFE_WHITELIST_TERMS = [
    re.compile(r"(?i)\b(я\s+забув|забув\s+пароль|як\s+відновити|відновлення\s+парол|як\s+змінити\s+пароль|де\s+поміняти\s+пароль)\b"),
]


class AdversarialGuardrail:
    """
    Deterministic security filter evaluating user queries for adversarial intent.

    Implements TKT-007 | REQ-F-010, REQ-F-011, REQ-NF-006, REQ-NF-008
    """

    DEFAULT_SECURITY_REFUSAL: str = (
        "Запит відхилено з міркувань безпеки: надання паролів, конфіденційних даних або зміна "
        "системних інструкцій суворо заборонені корпоративною політикою."
    )

    def __init__(self, session_manager: Optional[SessionManager] = None):
        """
        Initialize the guardrail filter.

        :param session_manager: Optional session manager instance to inspect or protect context.
        """
        self.session_manager = session_manager

    def is_adversarial(self, query: str) -> bool:
        """
        Determine whether an incoming text query is adversarial.

        Implements TKT-007 | REQ-F-010
        :param query: Cleaned user input text.
        :return: True if adversarial pattern matched and not whitelisted, False otherwise.
        """
        if not query:
            return False

        # 1. Prompt injection / rule override check (unconditional block)
        for pattern in _PATTERNS_PROMPT_INJECTION:
            if pattern.search(query):
                return True

        # 2. Credential harvesting / unauthorized password extraction
        for pattern in _PATTERNS_CREDENTIAL_EXTRACTION:
            if pattern.search(query):
                # Ensure legitimate self-service inquiries (forgot_password.md) pass through
                if any(w.search(query) for w in _SAFE_WHITELIST_TERMS):
                    return False
                return True

        return False

    def process_query(self, session_id: str, query: str) -> BotMessageResponse:
        """
        Evaluate a query and generate a Security Hard Refusal if adversarial.

        Crucially:
        - Refusal contains zero credentials, zero attachments, zero escalation contact (@corp_it_support).
        - Session context is NOT updated (REQ-F-011).

        Implements TKT-007 | REQ-F-010, REQ-F-011, REQ-NF-006
        :param session_id: Telegram chat identifier.
        :param query: Incoming query string.
        :return: BotMessageResponse with SECURITY_HARD_REFUSAL.
        """
        # Note: session_manager.add_turn() is explicitly NOT called here (REQ-F-011)
        return BotMessageResponse(
            session_id=session_id,
            classification=QueryClassification.SECURITY_HARD_REFUSAL,
            reply_text=self.DEFAULT_SECURITY_REFUSAL,
            context_updated=False,
            matched_documents=[],
            attachments=[],
            escalation_contact=None,
        )
