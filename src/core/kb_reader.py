"""
Sandboxed Read-Only Knowledge Base File Reader.

Implements TKT-001 | REQ-NF-001, REQ-NF-002, REQ-NF-006, REQ-NF-007
Enforces strict file-system confinement to knowledge_base/ using canonical path resolution
and blocks all path traversal attempts (such as '../.env' or '.py' files).
"""

from pathlib import Path
import re
from typing import List, Union

from src.core.exceptions import DocumentNotFoundError, PathTraversalError
from src.core.models import (
    DocumentFormat,
    ErrorDetail,
    KnowledgeDocumentDetail,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentSummary,
)

# Allowed filename pattern per openapi-v2.yaml (/knowledge-base/documents/{document_id})
_ALLOWED_FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+\.(md|txt)$")
_ALLOWED_EXTENSIONS = {".md", ".txt"}


class SandboxedKBReader:
    """
    Sandboxed file reader providing secure, read-only access strictly confined
    within the designated knowledge_base/ directory.

    Implements TKT-001 | REQ-NF-001, REQ-NF-007
    """

    def __init__(self, kb_dir: Union[str, Path] = "knowledge_base"):
        """
        Initialize the sandboxed reader with a canonicalized base directory.

        :param kb_dir: Path to the knowledge base root directory.
        :raises FileNotFoundError: If the directory does not exist or is not a directory.
        """
        self.kb_dir = Path(kb_dir).resolve()
        if not self.kb_dir.exists() or not self.kb_dir.is_dir():
            raise FileNotFoundError(
                f"Knowledge base directory does not exist or is not a directory: {self.kb_dir}"
            )

    def resolve_and_validate_path(self, document_id: Union[str, Path]) -> Path:
        """
        Resolve document_id against knowledge_base/ and enforce security boundary.

        Blocks:
        - Path traversal sequences (e.g., '../', '..\\')
        - Non-whitelisted file extensions (e.g., '.py', '.env')
        - Hidden files (starting with '.')
        - Absolute paths resolving outside the sandbox root
        - References to the sandbox directory itself

        Implements TKT-001 | REQ-NF-001
        :param document_id: Target document filename or path string.
        :return: Validated canonical Path strictly inside self.kb_dir.
        :raises PathTraversalError: If boundary or format checks fail.
        """
        if not document_id:
            raise PathTraversalError(
                message="Доступ заборонено: ідентифікатор документа не може бути порожнім.",
                details=[
                    ErrorDetail(
                        field="document_id",
                        issue="Empty document_id provided.",
                        constraint="len(document_id) > 0",
                    )
                ],
            )

        doc_str = str(document_id).strip()

        # Reject path traversal tokens and illegal extensions immediately
        if ".." in doc_str or doc_str.startswith("/") or doc_str.startswith("\\"):
            raise PathTraversalError(
                message=f"Доступ заборонено: виявлено спробу виходу за межі каталогу бази знань ('{doc_str}').",
                details=[
                    ErrorDetail(
                        field="document_id",
                        issue=f"Path traversal sequence detected in '{doc_str}'.",
                        constraint="path inside knowledge_base/",
                    )
                ],
            )

        # Check regex pattern for valid document filename
        if not _ALLOWED_FILENAME_PATTERN.match(doc_str):
            raise PathTraversalError(
                message=f"Доступ заборонено: неприпустиме ім'я файлу або розширення ('{doc_str}'). Дозволені лише .md та .txt.",
                details=[
                    ErrorDetail(
                        field="document_id",
                        issue=f"Filename '{doc_str}' does not conform to pattern ^[a-zA-Z0-9_-]+\\.(md|txt)$",
                        constraint="pattern == '^[a-zA-Z0-9_-]+\\.(md|txt)$'",
                    )
                ],
            )

        candidate = (self.kb_dir / doc_str).resolve()

        # Canonical path confinement check via is_relative_to
        try:
            is_inside = candidate.is_relative_to(self.kb_dir)
        except AttributeError:
            try:
                candidate.relative_to(self.kb_dir)
                is_inside = True
            except ValueError:
                is_inside = False

        if not is_inside or candidate == self.kb_dir:
            raise PathTraversalError(
                message=f"Доступ заборонено: шлях '{doc_str}' виходить за межі кореневої директорії бази знань.",
                details=[
                    ErrorDetail(
                        field="document_id",
                        issue=f"Resolved path '{candidate}' is outside '{self.kb_dir}'.",
                        constraint="canonical path must reside within knowledge_base/",
                    )
                ],
            )

        # Confirm extension is strictly .md or .txt
        if candidate.suffix.lower() not in _ALLOWED_EXTENSIONS:
            raise PathTraversalError(
                message=f"Доступ заборонено: розширення '{candidate.suffix}' не дозволено.",
                details=[
                    ErrorDetail(
                        field="document_id",
                        issue=f"Disallowed extension '{candidate.suffix}'.",
                        constraint="extension in {'.md', '.txt'}",
                    )
                ],
            )

        return candidate

    def read_document(self, document_id: Union[str, Path]) -> KnowledgeDocumentDetail:
        """
        Securely read a single document from knowledge_base/ with UTF-8 decoding.

        Implements TKT-001 | REQ-NF-001, REQ-NF-006, REQ-NF-007
        :param document_id: Target document filename.
        :return: KnowledgeDocumentDetail populated with metadata and content.
        :raises PathTraversalError: If path resolves outside the sandbox.
        :raises DocumentNotFoundError: If the file does not exist inside the sandbox.
        """
        canonical_path = self.resolve_and_validate_path(document_id)

        if not canonical_path.exists() or not canonical_path.is_file():
            raise DocumentNotFoundError(document_id=str(document_id))

        content = canonical_path.read_text(encoding="utf-8")
        title = self._extract_title(content, canonical_path.stem)
        doc_format = (
            DocumentFormat.MD
            if canonical_path.suffix.lower() == ".md"
            else DocumentFormat.TXT
        )

        return KnowledgeDocumentDetail(
            document_id=canonical_path.name,
            title=title,
            format=doc_format,
            content=content,
        )

    def get_document_text(self, document_id: Union[str, Path]) -> str:
        """
        Return the raw text content of a document.

        Implements TKT-001 | REQ-NF-001
        :param document_id: Target document filename.
        :return: UTF-8 decoded string content.
        """
        return self.read_document(document_id).content

    def list_document_ids(self) -> List[str]:
        """
        Enumerate all valid document filenames inside knowledge_base/ without loading bodies.

        Implements TKT-001, TKT-003 | REQ-NF-001, REQ-NF-003, REQ-NF-007
        :return: List of sorted document filename strings.
        """
        return [
            file_path.name
            for file_path in sorted(self.kb_dir.iterdir(), key=lambda p: p.name.lower())
            if file_path.is_file() and file_path.suffix.lower() in _ALLOWED_EXTENSIONS
        ]

    def list_documents(self) -> KnowledgeDocumentListResponse:
        """
        Enumerate all valid .md and .txt instruction files inside knowledge_base/.

        Implements TKT-001 | REQ-NF-001, REQ-NF-007
        :return: KnowledgeDocumentListResponse with summaries of all available files.
        """
        summaries: List[KnowledgeDocumentSummary] = []
        for doc_id in self.list_document_ids():
            file_path = self.kb_dir / doc_id
            content = file_path.read_text(encoding="utf-8")
            title = self._extract_title(content, file_path.stem)
            token_count = len(content.split())
            doc_format = (
                DocumentFormat.MD
                if file_path.suffix.lower() == ".md"
                else DocumentFormat.TXT
            )
            summaries.append(
                KnowledgeDocumentSummary(
                    document_id=file_path.name,
                    title=title,
                    format=doc_format,
                    token_count=token_count,
                )
            )

        return KnowledgeDocumentListResponse(
            total_documents=len(summaries),
            documents=summaries,
        )

    @staticmethod
    def _extract_title(content: str, fallback_stem: str) -> str:
        """
        Extract the first Markdown H1 heading (# Title) or fall back to file stem.

        :param content: Raw document text.
        :param fallback_stem: Stem of the filename.
        :return: Clean title string.
        """
        for line in content.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("# "):
                title = line_stripped[2:].strip()
                if title:
                    return title
        return fallback_stem.replace("_", " ").capitalize()
