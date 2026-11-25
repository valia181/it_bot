"""
Domain Models and Schema Contracts.

Implements TKT-001, TKT-003 | REQ-NF-001, REQ-F-003, REQ-F-005, REQ-NF-007, REQ-NF-008
Strictly aligned with openapi-v2.yaml schemas and components.
"""

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


class ApiErrorCode(str, Enum):
    """Canonical machine-readable error codes mapped from openapi-v2.yaml."""

    NON_TEXT_PAYLOAD_UNSUPPORTED = "NON_TEXT_PAYLOAD_UNSUPPORTED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PATH_TRAVERSAL_DENIED = "PATH_TRAVERSAL_DENIED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    INDEX_NOT_READY = "INDEX_NOT_READY"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


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
