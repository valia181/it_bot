"""
Domain Models and Schema Contracts.

Implements TKT-001 | REQ-NF-001, REQ-NF-007, REQ-NF-008
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
