from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
import uuid

from pydantic import BaseModel, Field


class DocumentFormat(str, Enum):
    """Supported instruction file extension in knowledge_base/."""

    MD = "md"
    TXT = "txt"


class IndexBuildStatus(str, Enum):
    """Current availability state of the in-memory inverted index."""

    READY = "READY"
    BUILDING = "BUILDING"


class SessionCommand(str, Enum):
    """Telegram command that triggered session initialization or reset."""

    START = "/start"
    RESET = "/reset"
    HELP = "/help"


class DialogueRole(str, Enum):
    """Participant role in the dialogue turn."""

    USER = "user"
    ASSISTANT = "assistant"


class QueryClassification(str, Enum):
    """Outcome category assigned by the query processing pipeline."""

    IN_SCOPE_ANSWER = "IN_SCOPE_ANSWER"
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    OUT_OF_SCOPE_ESCALATION = "OUT_OF_SCOPE_ESCALATION"
    SECURITY_HARD_REFUSAL = "SECURITY_HARD_REFUSAL"
    API_FALLBACK_DOCUMENTS = "API_FALLBACK_DOCUMENTS"


class PayloadType(str, Enum):
    """Supported message payload type from Telegram per openapi-v2.yaml."""

    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"
    STICKER = "sticker"
    DOCUMENT = "document"
    VIDEO = "video"
    AUDIO = "audio"


class ApiErrorCode(str, Enum):
    """Canonical machine-readable error codes mapped from openapi-v2.yaml."""

    NON_TEXT_PAYLOAD_UNSUPPORTED = "NON_TEXT_PAYLOAD_UNSUPPORTED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PATH_TRAVERSAL_DENIED = "PATH_TRAVERSAL_DENIED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    INDEX_NOT_READY = "INDEX_NOT_READY"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


class VerificationCaseCategory(str, Enum):
    """Control-suite case categories defined by openapi-v2.yaml."""

    IN_SCOPE = "IN_SCOPE"
    AMBIGUOUS_MULTI_TURN = "AMBIGUOUS_MULTI_TURN"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ADVERSARIAL = "ADVERSARIAL"
    API_FALLBACK = "API_FALLBACK"


class VerificationRunRequest(BaseModel):
    """Request options for the offline verification suite (TKT-014)."""

    include_fallback_simulation: bool = Field(
        default=True,
        description="Whether to run the Gemini API failure fallback control.",
    )


class VerificationCaseResult(BaseModel):
    """Single result row in the automated verification report (TKT-014)."""

    case_id: int = Field(..., ge=1, le=11)
    category: VerificationCaseCategory
    question: str
    expected_classification: QueryClassification
    actual_classification: QueryClassification
    actual_response: str
    passed: bool


class VerificationRunReport(BaseModel):
    """Structured pass/fail report matching openapi-v2.yaml."""

    total_cases: int = Field(..., ge=0)
    passed_cases: int = Field(..., ge=0)
    failed_cases: int = Field(..., ge=0)
    executed_at: datetime
    results: List[VerificationCaseResult] = Field(default_factory=list)


class KnowledgeDocumentSummary(BaseModel):
    """
    Summary representation of a single knowledge base document.

    Implements TKT-001 | REQ-NF-001, REQ-NF-007
    """

    document_id: str = Field(..., description="Filename of the instruction inside knowledge_base/")
    title: str = Field(..., description="Document title extracted from the document header")
    format: DocumentFormat = Field(..., description="Document file extension format ('md' or 'txt')")
    token_count: int = Field(..., ge=0, description="Approximated token / word count of the document content")


class KnowledgeDocumentDetail(BaseModel):
    """
    Full text representation of a sandboxed knowledge base document.

    Implements TKT-001 | REQ-NF-001, REQ-NF-007
    """

    document_id: str = Field(..., description="Filename of the instruction inside knowledge_base/")
    title: str = Field(..., description="Document title extracted from the document header")
    format: DocumentFormat = Field(..., description="Document file extension format ('md' or 'txt')")
    content: str = Field(..., description="Raw UTF-8 text content of the instruction file")


class KnowledgeDocumentListResponse(BaseModel):
    """
    Collection response listing all sandboxed knowledge base documents.

    Implements TKT-001 | REQ-NF-001, REQ-NF-007
    """

    total_documents: int = Field(..., ge=0, description="Total number of documents indexed")
    documents: List[KnowledgeDocumentSummary] = Field(..., description="List of document summaries")


class RankedDocumentHit(BaseModel):
    """
    Single document search hit ranked by positive TF-IDF score.

    Implements TKT-003 | REQ-F-005, REQ-NF-001
    """

    document_id: str = Field(..., description="Filename of the instruction inside knowledge_base/")
    title: str = Field(..., description="Document title")
    tfidf_score: float = Field(..., ge=0.0001, description="Positive TF-IDF relevance score (score > 0)")
    matched_stems: List[str] = Field(default_factory=list, description="List of query stems matching this document")


class DocumentAttachment(BaseModel):
    """
    Document attachment metadata and raw content delivered during API fallback.

    Implements TKT-008 | REQ-F-012
    """

    document_id: str = Field(..., description="Filename of the instruction inside knowledge_base/")
    title: str = Field(..., description="Document title")
    content: str = Field(..., description="Full text of the instruction file")
    tfidf_score: float = Field(..., ge=0.0001, description="TF-IDF relevance score")


class SearchQueryRequest(BaseModel):
    """
    Search request payload for retrieving Top-3 matching documents.

    Implements TKT-003 | REQ-F-004, REQ-F-005
    """

    query: str = Field(..., min_length=1, max_length=2000, description="Raw Ukrainian search query")
    top_k: int = Field(default=3, ge=1, le=3, description="Maximum number of top documents to retrieve (capped at 3)")


class SearchQueryResponse(BaseModel):
    """
    Ranked search results containing normalized query stems and Top-3 documents.

    Implements TKT-003 | REQ-F-004, REQ-F-005
    """

    normalized_stems: List[str] = Field(..., description="Stems produced after normalization and stemming")
    results: List[RankedDocumentHit] = Field(default_factory=list, max_length=3, description="Ranked Top-3 documents")


class InvertedIndexStatus(BaseModel):
    """
    Thread-safe operational status of the in-memory inverted index.

    Implements TKT-003 | REQ-F-003, REQ-NF-003, REQ-NF-004
    """

    status: IndexBuildStatus = Field(..., description="Current availability state (READY or BUILDING)")
    indexed_documents: int = Field(..., ge=0, description="Total number of successfully indexed documents")
    unique_stems: int = Field(..., ge=0, description="Total unique normalized stems in the index dictionary")
    build_duration_ms: float = Field(..., ge=0, description="Parallel build time in milliseconds")
    worker_threads: int = Field(..., ge=1, description="Worker thread pool concurrency count")


class DialogueTurn(BaseModel):
    """
    Single dialogue turn in a user session.

    Implements TKT-006 | REQ-F-006, REQ-NF-006
    """

    role: DialogueRole = Field(..., description="Participant role in the dialogue turn ('user' or 'assistant')")
    content: str = Field(..., description="Text content of the turn in Ukrainian")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of the turn")


class SessionInitRequest(BaseModel):
    """
    Request payload to initialize or reset a session.

    Implements TKT-006 | REQ-F-001, REQ-F-006
    """

    session_id: str = Field(..., min_length=1, max_length=64, description="Unique user chat identifier (Telegram chat_id)")
    command: Optional[SessionCommand] = Field(default=None, description="Command triggering session initialization")


class SessionInitResponse(BaseModel):
    """
    Response payload upon session initialization or reset.

    Implements TKT-006 | REQ-F-001, REQ-F-006
    """

    session_id: str = Field(..., description="Unique user chat identifier")
    welcome_message: str = Field(..., description="Greeting message in Ukrainian inviting the user to ask an IT question")
    max_turns: int = Field(default=6, description="Maximum sliding-window capacity in dialogue turns (REQ-F-006)")
    ttl_seconds: int = Field(default=900, description="Session inactivity expiration timeout in seconds (15 minutes)")


class SessionResource(BaseModel):
    """
    Active session context state including turns and remaining TTL.

    Implements TKT-006 | REQ-F-006, REQ-NF-004
    """

    session_id: str = Field(..., description="Unique user chat identifier")
    turns: List[DialogueTurn] = Field(default_factory=list, max_length=6, description="Active sliding context window")
    turn_count: int = Field(..., ge=0, le=6, description="Number of turns currently in memory")
    max_turns: int = Field(default=6, description="Maximum turns allowed")
    remaining_ttl_seconds: int = Field(..., ge=0, le=900, description="Remaining seconds before automatic TTL eviction")


class IncomingMessageRequest(BaseModel):
    """
    Incoming user message request schema strictly matching openapi-v2.yaml /messages.

    Implements TKT-008, TKT-011 | REQ-F-002, REQ-F-006, REQ-F-007
    """

    session_id: str = Field(..., min_length=1, max_length=64, description="Unique user chat identifier")
    payload_type: PayloadType = Field(..., description="Message payload type (only 'text' supported per REQ-F-002)")
    text: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=4000,
        description="Text body of the incoming query",
    )


class BotMessageResponse(BaseModel):
    """
    Standard response delivered by the bot to a user query.

    Implements TKT-007, TKT-008 | REQ-F-007..REQ-F-012, REQ-NF-006
    """

    session_id: str = Field(..., description="User session identifier")
    classification: QueryClassification = Field(..., description="Outcome classification")
    reply_text: str = Field(..., description="Ukrainian response message")
    context_updated: bool = Field(..., description="False when adversarial query is blocked (REQ-F-011)")
    matched_documents: List[RankedDocumentHit] = Field(default_factory=list, max_length=3, description="Top-3 matches")
    attachments: List[DocumentAttachment] = Field(default_factory=list, max_length=3, description="Attachments for fallback")
    escalation_contact: Optional[str] = Field(default=None, description="Escalation handle (@corp_it_support or None)")


class ErrorDetail(BaseModel):
    """Detailed validation or security violation item."""

    field: str
    issue: str
    constraint: Optional[str] = None


class ApiErrorResponse(BaseModel):
    """
    Standardized API error response payload.

    Implements TKT-001 | REQ-NF-001, REQ-NF-006, REQ-NF-008
    """

    error_code: ApiErrorCode
    message: str
    details: List[ErrorDetail] = Field(default_factory=list)
    req_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))