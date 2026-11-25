"""
Unit and Security Verification for Sandboxed Knowledge Base Reader.

Implements TKT-001 | REQ-NF-001, REQ-NF-002, REQ-NF-006, REQ-NF-007
Verifies:
- Existence, structure, Ukrainian localization, and zero-secrets compliance across the 7 KB files.
- Sandboxed canonical path resolution confining reads inside knowledge_base/.
- Blocking of path traversal attempts (e.g. '../.env') with PATH_TRAVERSAL_DENIED.
- Blocking of disallowed file extensions (e.g. '.py') with PATH_TRAVERSAL_DENIED.
- Proper raising of DOCUMENT_NOT_FOUND for missing in-scope documents.
"""

from pathlib import Path
import re
import pytest

from src.core.exceptions import DocumentNotFoundError, PathTraversalError
from src.core.kb_reader import SandboxedKBReader
from src.core.models import ApiErrorCode, DocumentFormat

EXPECTED_7_KB_FILES = [
    "lost_phone.md",
    "forgot_password.md",
    "printer_issues.md",
    "vpn_setup.md",
    "wifi_troubleshooting.md",
    "corporate_email.md",
    "software_request.md",
]

SECRET_PATTERNS = [
    re.compile(r"(?i)\bpassword\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bsecret\s*[:=]\s*\S+"),
    re.compile(r"(?i)\btoken\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"password123", re.IGNORECASE),
    re.compile(r"admin123", re.IGNORECASE),
]


@pytest.fixture
def kb_reader() -> SandboxedKBReader:
    """Fixture providing a SandboxedKBReader initialized to the project knowledge base."""
    repo_root = Path(__file__).resolve().parent.parent
    kb_dir = repo_root / "knowledge_base"
    return SandboxedKBReader(kb_dir=kb_dir)


def test_tkt_001_exact_7_files_exist(kb_reader: SandboxedKBReader):
    """
    Verify all 7 expected Ukrainian IT instruction files exist in knowledge_base/.

    Implements TKT-001 | REQ-NF-007 | AC-NF-007
    """
    for expected_file in EXPECTED_7_KB_FILES:
        file_path = kb_reader.kb_dir / expected_file
        assert file_path.exists(), f"Missing required KB file: {expected_file}"
        assert file_path.is_file(), f"KB path is not a file: {expected_file}"


def test_tkt_001_zero_secrets_audit(kb_reader: SandboxedKBReader):
    """
    Audit all 7 knowledge base files to confirm zero real or mock passwords/tokens exist.

    Implements TKT-001 | REQ-NF-002 | AC-NF-002
    """
    for file_name in EXPECTED_7_KB_FILES:
        content = kb_reader.get_document_text(file_name)
        assert len(content.strip()) > 50, f"File {file_name} is too short or empty."

        for pattern in SECRET_PATTERNS:
            match = pattern.search(content)
            assert match is None, (
                f"Security violation in {file_name}: pattern '{pattern.pattern}' matched '{match.group(0)}'."
            )


def test_tkt_001_ukrainian_localization_audit(kb_reader: SandboxedKBReader):
    """
    Confirm all 7 files contain authentic Ukrainian text with Cyrillic characters.

    Implements TKT-001 | REQ-NF-006 | AC-NF-006
    """
    cyrillic_pattern = re.compile(r"[\u0400-\u04FF]")
    for file_name in EXPECTED_7_KB_FILES:
        doc = kb_reader.read_document(file_name)
        assert doc.format == DocumentFormat.MD
        assert len(doc.title) > 0
        assert cyrillic_pattern.search(doc.title), f"Title '{doc.title}' does not contain Ukrainian characters."
        assert cyrillic_pattern.search(doc.content), f"Content of {file_name} does not contain Ukrainian characters."


def test_tkt_001_list_documents(kb_reader: SandboxedKBReader):
    """
    Verify list_documents() correctly lists all 7 files with accurate metadata.

    Implements TKT-001 | REQ-NF-001, REQ-NF-007
    """
    response = kb_reader.list_documents()
    assert response.total_documents >= 7
    doc_ids = [d.document_id for d in response.documents]
    for expected_file in EXPECTED_7_KB_FILES:
        assert expected_file in doc_ids
        doc_summary = next(d for d in response.documents if d.document_id == expected_file)
        assert doc_summary.token_count > 0
        assert doc_summary.format == DocumentFormat.MD
        assert len(doc_summary.title.strip()) > 0


@pytest.mark.parametrize(
    "traversal_attempt",
    [
        "../.env",
        "../../spec/openapi-v2.yaml",
        "../../../etc/passwd",
        r"..\..\..\Windows\win.ini",
        r"..\.env",
        ".env",
        "knowledge_base/../.env",
        "/etc/shadow",
        "C:/Windows/System32/drivers/etc/hosts",
    ],
)
def test_tkt_001_path_traversal_blocking(kb_reader: SandboxedKBReader, traversal_attempt: str):
    """
    Verify that path traversal attempts raise PathTraversalError with PATH_TRAVERSAL_DENIED.

    Implements TKT-001 | REQ-NF-001 | AC-NF-001
    """
    with pytest.raises(PathTraversalError) as exc_info:
        kb_reader.read_document(traversal_attempt)

    err = exc_info.value
    assert err.error_code == ApiErrorCode.PATH_TRAVERSAL_DENIED
    assert err.req_id == "REQ-NF-001"
    # Ensure Ukrainian error message per REQ-NF-006
    assert re.search(r"[\u0400-\u04FF]", err.message)


@pytest.mark.parametrize(
    "disallowed_file",
    [
        "app.py",
        "script.sh",
        "config.json",
        "payload.exe",
        "library.dll",
    ],
)
def test_tkt_001_disallowed_extension_blocking(kb_reader: SandboxedKBReader, disallowed_file: str):
    """
    Verify that attempts to read non-.md/.txt files raise PathTraversalError with PATH_TRAVERSAL_DENIED.

    Implements TKT-001 | REQ-NF-001 | AC-NF-001
    """
    with pytest.raises(PathTraversalError) as exc_info:
        kb_reader.read_document(disallowed_file)

    err = exc_info.value
    assert err.error_code == ApiErrorCode.PATH_TRAVERSAL_DENIED


def test_tkt_001_document_not_found(kb_reader: SandboxedKBReader):
    """
    Verify that querying a missing in-scope document raises DocumentNotFoundError with DOCUMENT_NOT_FOUND.

    Implements TKT-001 | REQ-NF-001
    """
    missing_id = "non_existent_instruction.md"
    with pytest.raises(DocumentNotFoundError) as exc_info:
        kb_reader.read_document(missing_id)

    err = exc_info.value
    assert err.error_code == ApiErrorCode.DOCUMENT_NOT_FOUND
    assert err.document_id == missing_id
    assert re.search(r"[\u0400-\u04FF]", err.message)


def test_tkt_001_empty_document_id_rejected(kb_reader: SandboxedKBReader):
    """
    Verify that empty or whitespace document_id raises PathTraversalError.

    Implements TKT-001 | REQ-NF-001
    """
    with pytest.raises(PathTraversalError) as exc_info:
        kb_reader.read_document("")
    assert exc_info.value.error_code == ApiErrorCode.PATH_TRAVERSAL_DENIED
