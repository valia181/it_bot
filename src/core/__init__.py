"""
Core Business Logic and Knowledge Retrieval Package.

Implements TKT-001, TKT-002, TKT-003, TKT-006, TKT-007, TKT-008.
Traceability: REQ-NF-001, REQ-NF-002, REQ-NF-007, REQ-NF-008
"""

from src.core.exceptions import (
    DocumentNotFoundError,
    GeminiConfigurationError,
    KnowledgeBaseError,
    PathTraversalError,
    SessionNotFoundError,
)
from src.core.gemini_client import GeminiClient
from src.core.guardrail import AdversarialGuardrail
from src.core.kb_reader import SandboxedKBReader
from src.core.orchestrator import ChatOrchestrator
from src.core.search_engine import InvertedIndex
from src.core.session_manager import SessionManager

__all__ = [
    "AdversarialGuardrail",
    "ChatOrchestrator",
    "DocumentNotFoundError",
    "GeminiClient",
    "GeminiConfigurationError",
    "InvertedIndex",
    "KnowledgeBaseError",
    "PathTraversalError",
    "SandboxedKBReader",
    "SessionManager",
    "SessionNotFoundError",
]