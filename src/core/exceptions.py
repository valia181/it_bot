"""
Exceptions for Knowledge Base and Security Confinement.

Implements TKT-001, TKT-006 | REQ-NF-001, REQ-NF-006, REQ-NF-007, REQ-F-006
Maps internal exceptions to canonical openapi-v2.yaml ApiErrorCode values.
"""

from typing import List, Optional
from src.core.models import ApiErrorCode, ApiErrorResponse, ErrorDetail


class KnowledgeBaseError(Exception):
    """
    Base exception for all Knowledge Base access and validation errors.

    Implements TKT-001 | REQ-NF-001
    """

    def __init__(
        self,
        message: str,
        error_code: ApiErrorCode = ApiErrorCode.INTERNAL_SERVER_ERROR,
        req_id: str = "REQ-NF-001",
        details: Optional[List[ErrorDetail]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.req_id = req_id
        self.details = details or []

    def to_api_error_response(self) -> ApiErrorResponse:
        """Serialize exception to standard ApiErrorResponse model."""
        return ApiErrorResponse(
            error_code=self.error_code,
            message=self.message,
            details=self.details,
            req_id=self.req_id,
        )


class PathTraversalError(KnowledgeBaseError):
    """
    Raised when an attempt to escape knowledge_base/ sandbox is detected.

    Implements TKT-001 | REQ-NF-001, REQ-NF-006
    """

    def __init__(
        self,
        message: str = "Доступ заборонено: спроба виходу за межі дозволеної директорії бази знань.",
        details: Optional[List[ErrorDetail]] = None,
    ):
        super().__init__(
            message=message,
            error_code=ApiErrorCode.PATH_TRAVERSAL_DENIED,
            req_id="REQ-NF-001",
            details=details or [],
        )


class DocumentNotFoundError(KnowledgeBaseError):
    """
    Raised when the requested document does not exist inside knowledge_base/.

    Implements TKT-001 | REQ-NF-001, REQ-NF-006
    """

    def __init__(
        self,
        document_id: str,
        message: Optional[str] = None,
        details: Optional[List[ErrorDetail]] = None,
    ):
        if message is None:
            message = f"Запитуваний документ '{document_id}' не знайдено в базі знань."
        super().__init__(
            message=message,
            error_code=ApiErrorCode.DOCUMENT_NOT_FOUND,
            req_id="REQ-NF-001",
            details=details or [
                ErrorDetail(
                    field="document_id",
                    issue=f"Файл '{document_id}' відсутній.",
                    constraint="is_file() == True",
                )
            ],
        )
        self.document_id = document_id


class SessionNotFoundError(KnowledgeBaseError):
    """
    Raised when the requested session does not exist or has expired via TTL.

    Implements TKT-006 | REQ-F-006, REQ-NF-006
    """

    def __init__(
        self,
        session_id: str,
        message: Optional[str] = None,
        details: Optional[List[ErrorDetail]] = None,
    ):
        if message is None:
            message = f"Сесію '{session_id}' не знайдено або її термін дії вичерпано."
        super().__init__(
            message=message,
            error_code=ApiErrorCode.SESSION_NOT_FOUND,
            req_id="REQ-F-006",
            details=details or [
                ErrorDetail(
                    field="session_id",
                    issue=f"Session '{session_id}' was not found or expired via TTL.",
                    constraint="session exists and remaining_ttl > 0",
                )
            ],
        )
        self.session_id = session_id
