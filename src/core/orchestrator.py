"""
Chat Orchestrator for Query Processing, Grounding, Escalation, and Fallback.

Implements TKT-008 | REQ-F-007, REQ-F-008, REQ-F-009, REQ-F-012, REQ-NF-001,
REQ-NF-002, REQ-NF-005, REQ-NF-006, REQ-NF-008.
Acceptance Criteria: AC-F-007, AC-F-008, AC-F-009, AC-F-012, AC-NF-001, AC-NF-002,
AC-NF-005, AC-NF-006, AC-NF-008.
"""

import re
import logging
from typing import List, Optional, Tuple

from src.core.exceptions import SessionNotFoundError
from src.core.gemini_client import GeminiClient
from src.core.guardrail import AdversarialGuardrail
from src.core.kb_reader import SandboxedKBReader
from src.core.models import (
    BotMessageResponse,
    DialogueRole,
    DocumentAttachment,
    IndexBuildStatus,
    QueryClassification,
    RankedDocumentHit,
    SessionResource,
)
from src.core.search_engine import InvertedIndex
from src.core.session_manager import SessionManager

logger = logging.getLogger(__name__)

# Regex patterns indicating vague, broad, or non-specific technical problems
_PATTERNS_VAGUE_QUERY = [
    re.compile(r"(?i)\b(все|нічого|взагалі|щось)\b.*?\b(не\s+працює|зламал[ои]сь|не\s+робить|поламал[ои]сь)\b"),
    re.compile(r"(?i)\b(не\s+працює|зламал[ои]сь|поламал[ои]сь)\b.*?\b(все|нічого|взагалі)\b"),
    re.compile(r"(?i)^\s*(все|нічого|щось)\s+(не\s+працює|зламалось|поламалось)\s*$"),
    re.compile(r"(?i)\b(нічого\s+не\s+працює|все\s+не\s+працює|все\s+зламалось|нічого\s+не\s+робить)\b"),
]

# Keywords indicating concrete symptoms or topics (which must NOT trigger clarification)
_CONCRETE_TOPIC_KEYWORDS = [
    "принтер", "папір", "картридж", "друк", "лоток", "зажува",
    "парол", "відновити", "скинути",
    "пошт", "email", "outlook", "thunderbird", "imap", "smtp", "лист",
    "телефон", "смартфон", "загубив", "сім-карт", "sim",
    "програм", "софт", "встановити", "ліцензі",
    "вайфай", "wifi", "wi-fi", "corp-secure", "guest",
    "vpn", "openvpn", "anyconnect", "з дому", "віддален",
]

# Out-of-scope patterns for topics known to be absent from KB (Scenario 4 / REQ-F-009)
_PATTERNS_OUT_OF_SCOPE = [
    re.compile(r"(?i)\b(1с|1c|бухгалтер[а-яіїє]*)\b"),
]


class ChatOrchestrator:
    """
    Coordinates query security evaluation, sliding session memory, TF-IDF retrieval,
    grounded LLM synthesis, contextual clarification, escalation, and fallback.

    Implements TKT-008 | REQ-F-007, REQ-F-008, REQ-F-009, REQ-F-012, REQ-NF-001..REQ-NF-008
    """

    DEFAULT_ESCALATION_CONTACT: str = "@corp_it_support"
    DEFAULT_ESCALATION_MESSAGE: str = (
        "На жаль, у базі знань немає інформації для вирішення цього питання. "
        "Будь ласка, зверніться до спеціаліста ІТ-підтримки: @corp_it_support."
    )
    DEFAULT_FALLBACK_MESSAGE: str = (
        "Перепрошую, сервіс штучного інтелекту тимчасово недоступний через технічні неполадки або тайм-аут. "
        "До відповіді додано відповідні інструкції з бази знань у вигляді файлів."
    )
    DEFAULT_QUOTA_FALLBACK_MESSAGE: str = (
        "Перепрошую, наразі вичерпано ліміт запитів до сервісу штучного інтелекту. "
        "До відповіді додано відповідні інструкції з бази знань у вигляді файлів."
    )
    CLARIFICATION_NETWORK_MESSAGE: str = (
        "Уточніть, будь ласка, з чим саме виникла проблема з мережею: "
        "з підключенням до офісного Wi-Fi чи з віддаленим доступом через VPN?"
    )
    CLARIFICATION_GENERAL_MESSAGE: str = (
        "Уточніть, будь ласка, що саме не працює: принтер, корпоративна пошта, "
        "пароль до облікового запису або мережа (Wi-Fi чи VPN)?"
    )

    def __init__(
        self,
        kb_reader: Optional[SandboxedKBReader] = None,
        search_engine: Optional[InvertedIndex] = None,
        session_manager: Optional[SessionManager] = None,
        guardrail: Optional[AdversarialGuardrail] = None,
        gemini_client: Optional[GeminiClient] = None,
    ):
        """
        Initialize ChatOrchestrator with required subsystems.

        Implements TKT-008 | REQ-NF-008
        """
        self.kb_reader = kb_reader or SandboxedKBReader()
        self.search_engine = search_engine or InvertedIndex(kb_reader=self.kb_reader)
        if self.search_engine._status != IndexBuildStatus.READY:
            self.search_engine.build_index()
        self.session_manager = session_manager or SessionManager()
        self.guardrail = guardrail or AdversarialGuardrail(session_manager=self.session_manager)

        if gemini_client is not None:
            self.gemini_client = gemini_client
        else:
            self.gemini_client = GeminiClient()

    def _check_ambiguity(
        self,
        query: str,
        session: Optional[SessionResource] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluate if query is genuinely vague or non-specific per REQ-F-008.

        Returns (is_ambiguous, clarification_prompt).
        Implements TKT-008 | REQ-F-008
        """
        query_lower = query.lower()

        # If user is responding after a clarification question, do not re-clarify
        if session and session.turns:
            last_turn = session.turns[-1]
            if (
                getattr(last_turn.role, "value", str(last_turn.role)) == "assistant"
                and any(term in last_turn.content.lower() for term in ["уточніть", "wi-fi", "vpn"])
            ):
                return False, None

        # Check if any concrete topic/symptom is mentioned
        has_concrete_topic = any(kw in query_lower for kw in _CONCRETE_TOPIC_KEYWORDS)
        if has_concrete_topic:
            return False, None

        # Check vague query patterns
        is_vague = any(p.search(query) for p in _PATTERNS_VAGUE_QUERY)
        if not is_vague:
            return False, None

        # Network-specific ambiguity (Wi-Fi vs VPN)
        if "мереж" in query_lower or "інтернет" in query_lower:
            return True, self.CLARIFICATION_NETWORK_MESSAGE

        # General vague problem
        return True, self.CLARIFICATION_GENERAL_MESSAGE

    def process_message(self, session_id: str, query: str) -> BotMessageResponse:
        """
        Process incoming user message through the full business logic pipeline.

        Pipeline stages:
        1. Security Guardrail Evaluation (REQ-F-010, REQ-F-011)
        2. Session Context Retrieval (REQ-F-006)
        3. Ambiguity & Clarification Checking (REQ-F-008)
        4. Out-of-Scope Pre-check (REQ-F-009, Scenario 4)
        5. TF-IDF Knowledge Retrieval (REQ-F-004, REQ-F-005)
        6. Grounded LLM Response / Out-of-Scope Escalation / Fallback (REQ-F-007, REQ-F-009, REQ-F-012)

        Implements TKT-008 | REQ-F-007..REQ-F-012, REQ-NF-001, REQ-NF-002, REQ-NF-005, REQ-NF-006
        :param session_id: User chat identifier.
        :param query: Cleaned user input text.
        :return: Standard BotMessageResponse model.
        """
        # 1. Adversarial Guardrail check (REQ-F-010, REQ-F-011)
        if self.guardrail.is_adversarial(query):
            return self.guardrail.process_query(session_id=session_id, query=query)

        # 2. Session Context Retrieval (REQ-F-006)
        session: Optional[SessionResource] = None
        try:
            session = self.session_manager.get_session(session_id)
        except SessionNotFoundError:
            session = None

        # 3. Balanced Clarification of Ambiguous Queries (REQ-F-008)
        is_ambiguous, clarification_prompt = self._check_ambiguity(query, session)
        if is_ambiguous and clarification_prompt:
            self.session_manager.add_turn(session_id, DialogueRole.USER, query)
            self.session_manager.add_turn(session_id, DialogueRole.ASSISTANT, clarification_prompt)
            return BotMessageResponse(
                session_id=session_id,
                classification=QueryClassification.CLARIFICATION_NEEDED,
                reply_text=clarification_prompt,
                context_updated=True,
                matched_documents=[],
                attachments=[],
                escalation_contact=None,
            )

        # 4. Out-of-Scope query check (REQ-F-009, Scenario 4)
        if any(p.search(query) for p in _PATTERNS_OUT_OF_SCOPE):
            escalation_reply = self.DEFAULT_ESCALATION_MESSAGE
            self.session_manager.add_turn(session_id, DialogueRole.USER, query)
            self.session_manager.add_turn(session_id, DialogueRole.ASSISTANT, escalation_reply)
            return BotMessageResponse(
                session_id=session_id,
                classification=QueryClassification.OUT_OF_SCOPE_ESCALATION,
                reply_text=escalation_reply,
                context_updated=True,
                matched_documents=[],
                attachments=[],
                escalation_contact=self.DEFAULT_ESCALATION_CONTACT,
            )

        # 5. TF-IDF Search (REQ-F-004, REQ-F-005)
        search_response = self.search_engine.search(query)
        if len(search_response.results) == 0 and session and session.turns:
            # Contextual multi-turn follow-up query expansion
            prev_user_queries = [
                t.content for t in session.turns if getattr(t.role, "value", str(t.role)) == "user"
            ]
            if prev_user_queries:
                combined_query = f"{prev_user_queries[-1]} {query}"
                search_response = self.search_engine.search(combined_query)

        # 6. Out-of-Scope Escalation if zero matching documents (REQ-F-009, REQ-F-012 edge case)
        if len(search_response.results) == 0:
            escalation_reply = self.DEFAULT_ESCALATION_MESSAGE
            self.session_manager.add_turn(session_id, DialogueRole.USER, query)
            self.session_manager.add_turn(session_id, DialogueRole.ASSISTANT, escalation_reply)
            return BotMessageResponse(
                session_id=session_id,
                classification=QueryClassification.OUT_OF_SCOPE_ESCALATION,
                reply_text=escalation_reply,
                context_updated=True,
                matched_documents=[],
                attachments=[],
                escalation_contact=self.DEFAULT_ESCALATION_CONTACT,
            )

        # Read contents of retrieved Top-3 documents
        context_docs: List[Tuple[str, str]] = []
        for hit in search_response.results:
            doc_detail = self.kb_reader.read_document(hit.document_id)
            context_docs.append((hit.document_id, doc_detail.content))

        dialogue_history = session.turns if session else []

        # 7. Grounded Gemini LLM Synthesis with Resilient Fallback (REQ-F-007, REQ-F-012)
        try:
            gemini_reply = self.gemini_client.generate_grounded_response(
                query=query,
                context_documents=context_docs,
                dialogue_history=dialogue_history,
            )
            # Ensure string type
            if not isinstance(gemini_reply, str):
                gemini_reply = str(gemini_reply)
        except Exception as exc:
            status_code = getattr(exc, "code", None) or getattr(
                exc, "status_code", None
            )
            logger.error(
                "Gemini generation failed (exception=%s, status_code=%s); "
                "serving knowledge-base fallback.",
                type(exc).__name__,
                status_code,
            )
            # Graceful Fallback on Gemini API Failure (REQ-F-012)
            fallback_attachments: List[DocumentAttachment] = []
            for hit in search_response.results:
                doc_content = next(
                    (content for did, content in context_docs if did == hit.document_id),
                    "",
                )
                fallback_attachments.append(
                    DocumentAttachment(
                        document_id=hit.document_id,
                        title=hit.title,
                        content=doc_content,
                        tfidf_score=hit.tfidf_score,
                    )
                )

            fallback_reply = (
                self.DEFAULT_QUOTA_FALLBACK_MESSAGE
                if status_code == 429
                else self.DEFAULT_FALLBACK_MESSAGE
            )
            self.session_manager.add_turn(session_id, DialogueRole.USER, query)
            self.session_manager.add_turn(session_id, DialogueRole.ASSISTANT, fallback_reply)
            return BotMessageResponse(
                session_id=session_id,
                classification=QueryClassification.API_FALLBACK_DOCUMENTS,
                reply_text=fallback_reply,
                context_updated=True,
                matched_documents=search_response.results,
                attachments=fallback_attachments,
                escalation_contact=None,
            )

        # Check if LLM explicitly escalated
        if self.DEFAULT_ESCALATION_CONTACT in gemini_reply:
            classification = QueryClassification.OUT_OF_SCOPE_ESCALATION
            escalation_contact = self.DEFAULT_ESCALATION_CONTACT
        else:
            classification = QueryClassification.IN_SCOPE_ANSWER
            escalation_contact = None

        self.session_manager.add_turn(session_id, DialogueRole.USER, query)
        self.session_manager.add_turn(session_id, DialogueRole.ASSISTANT, gemini_reply)

        return BotMessageResponse(
            session_id=session_id,
            classification=classification,
            reply_text=gemini_reply,
            context_updated=True,
            matched_documents=search_response.results,
            attachments=[],
            escalation_contact=escalation_contact,
        )