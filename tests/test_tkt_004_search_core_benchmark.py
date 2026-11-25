"""
Verification Suite for TKT-004: Unit, Security, and Performance Benchmark Testing of the Search Core.

Implements TKT-004 | REQ-F-003, REQ-F-004, REQ-F-005, REQ-NF-001, REQ-NF-002, REQ-NF-003, REQ-NF-004, REQ-NF-007
Acceptance Criteria: AC-F-003, AC-F-004, AC-F-005, AC-NF-001, AC-NF-002, AC-NF-003, AC-NF-004, AC-NF-007
BDD Scenario: Scenario 2 (Grounded In-Scope Retrieval with Inflected Word Forms)
Provides formal verification for GATE-1 (TKT-005).
"""

from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
import re
import threading
import time
from typing import List
import pytest

from src.core.exceptions import PathTraversalError
from src.core.kb_reader import SandboxedKBReader
from src.core.models import ApiErrorCode, IndexBuildStatus, InvertedIndexStatus, SearchQueryResponse
from src.core.search_engine import InvertedIndex
from src.core.text_processing import UkrainianStemmer, normalize_query_stems


@pytest.fixture
def repo_root() -> Path:
    """Fixture providing repository root path."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def kb_reader(repo_root: Path) -> SandboxedKBReader:
    """Fixture providing SandboxedKBReader on repo knowledge_base/."""
    return SandboxedKBReader(kb_dir=repo_root / "knowledge_base")


@pytest.fixture
def search_engine(kb_reader: SandboxedKBReader) -> InvertedIndex:
    """Fixture providing initialized and built InvertedIndex."""
    engine = InvertedIndex(kb_reader=kb_reader, worker_threads=4)
    engine.build_index()
    return engine


def test_tkt_004_indexing_benchmark_1000_files(tmp_path: Path):
    """
    Verify AC-NF-003 / REQ-NF-003: Multi-threaded startup indexing of 1,000 synthetic .md
    files via ThreadPoolExecutor completes strictly in < 2.0 seconds.

    Implements TKT-004 | REQ-NF-003 | AC-NF-003
    """
    synth_kb = tmp_path / "synthetic_1000_kb"
    synth_kb.mkdir()

    # Generate 1,000 synthetic markdown files
    for i in range(1000):
        content = (
            f"# Інструкція {i}\n"
            f"Опис процедури налаштування робочого місця номер {i}. "
            f"Перевірка мережевого принтера, підключення до корпоративного vpn та wifi."
        )
        (synth_kb / f"doc_{i:04d}.md").write_text(content, encoding="utf-8")

    reader = SandboxedKBReader(kb_dir=synth_kb)
    engine = InvertedIndex(kb_reader=reader, worker_threads=8)

    start_time = time.perf_counter()
    status: InvertedIndexStatus = engine.build_index()
    elapsed = time.perf_counter() - start_time

    assert status.status == IndexBuildStatus.READY
    assert status.indexed_documents == 1000
    assert status.unique_stems > 0
    # Strict 2.0 second benchmark assertion per REQ-NF-003
    assert elapsed < 2.0, f"Indexing 1,000 files took {elapsed:.3f}s (must be < 2.0s)"
    assert status.build_duration_ms < 2000.0


def test_tkt_004_20_thread_concurrency_stress(search_engine: InvertedIndex):
    """
    Verify AC-NF-004 / REQ-NF-004: 20 concurrent worker threads querying and updating/rebuilding
    the index under threading.RLock complete with zero race condition exceptions or corruptions.

    Implements TKT-004 | REQ-NF-004 | AC-NF-004
    """
    queries = [
        "зажувало папір у принтері",
        "загубив телефон у метро",
        "як підключити vpn",
        "проблеми з wifi corp-secure",
        "відновлення пароля корпоративної пошти",
        "запит на встановлення програмного забезпечення",
        "невідомий запит без відповіді",
    ]

    exceptions: List[Exception] = []

    def reader_worker(worker_id: int):
        try:
            for cycle in range(20):
                query = queries[(worker_id + cycle) % len(queries)]
                response = search_engine.search(query=query, top_k=3)
                assert isinstance(response, SearchQueryResponse)
                assert len(response.results) <= 3
                # Verify scores are sorted descending
                scores = [r.tfidf_score for r in response.results]
                assert scores == sorted(scores, reverse=True)
        except Exception as e:
            exceptions.append(e)

    def rebuilder_worker():
        try:
            for _ in range(5):
                time.sleep(0.01)
                status = search_engine.build_index()
                assert status.indexed_documents == 7
        except Exception as e:
            exceptions.append(e)

    # 18 reader threads + 2 concurrent rebuilder threads = 20 concurrent threads
    threads: List[threading.Thread] = []
    for i in range(18):
        threads.append(threading.Thread(target=reader_worker, args=(i,)))
    for _ in range(2):
        threads.append(threading.Thread(target=rebuilder_worker))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(exceptions) == 0, f"Concurrency stress test produced {len(exceptions)} exceptions: {exceptions}"


def test_tkt_004_bdd_scenario_2_grounded_in_scope_retrieval(search_engine: InvertedIndex):
    """
    Verify BDD Scenario 2 (SRS Section 7.3):
    Given printer_issues.md is indexed in knowledge_base/
    When the user sends 'Що робити, якщо в офісі зажувало папір у принтері?'
    Then the search engine stems the query, ranks printer_issues.md in Top-3 (TF-IDF > 0).

    Implements TKT-004 | REQ-F-004, REQ-F-005 | BDD Scenario 2
    """
    query = "Що робити, якщо в офісі зажувало папір у принтері?"
    response = search_engine.search(query=query, top_k=3)

    assert len(response.results) > 0
    top_hit = response.results[0]
    assert top_hit.document_id == "printer_issues.md"
    assert top_hit.tfidf_score > 0
    assert "принтер" in top_hit.matched_stems
    assert "папір" in top_hit.matched_stems


def test_tkt_004_path_traversal_blocking_ac_nf_001(kb_reader: SandboxedKBReader):
    """
    Verify AC-NF-001 / REQ-NF-001: Path traversal attempts raise PATH_TRAVERSAL_DENIED.

    Implements TKT-004 | REQ-NF-001 | AC-NF-001
    """
    traversal_paths = [
        "../.env",
        "../../spec/openapi-v2.yaml",
        "../../spec/software_requirements_specification_srs.md",
        "knowledge_base/../.env",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "src/core/search_engine.py",
    ]
    for bad_path in traversal_paths:
        with pytest.raises(PathTraversalError) as exc_info:
            kb_reader.read_document(bad_path)
        assert exc_info.value.error_code == ApiErrorCode.PATH_TRAVERSAL_DENIED


def test_tkt_004_zero_secrets_kb_audit_ac_nf_002(kb_reader: SandboxedKBReader):
    """
    Verify AC-NF-002 / REQ-NF-002: Audit all 7 KB files for zero passwords or tokens.

    Implements TKT-004 | REQ-NF-002 | AC-NF-002
    """
    forbidden_patterns = [
        re.compile(r"(?i)\bpassword\s*[:=]\s*\S+"),
        re.compile(r"(?i)\btoken\s*[:=]\s*\S+"),
        re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*\S+"),
        re.compile(r"(?i)\bsecret\s*[:=]\s*\S+"),
    ]
    doc_ids = kb_reader.list_document_ids()
    assert len(doc_ids) == 7
    for doc_id in doc_ids:
        text = kb_reader.get_document_text(doc_id)
        for pattern in forbidden_patterns:
            assert pattern.search(text) is None, f"Secret pattern found in {doc_id}"


def test_tkt_004_stemming_equivalence_ac_f_004():
    """
    Verify AC-F-004 / REQ-F-004: Inflected word forms map to identical stem.

    Implements TKT-004 | REQ-F-004 | AC-F-004
    """
    assert UkrainianStemmer.stem("принтер") == UkrainianStemmer.stem("принтера")
    assert UkrainianStemmer.stem("принтер") == UkrainianStemmer.stem("принтером")
    assert UkrainianStemmer.stem("принтер") == UkrainianStemmer.stem("принтері")
    assert UkrainianStemmer.stem("принтер") == UkrainianStemmer.stem("принтерів")


def test_tkt_004_top3_ranking_and_zero_score_ac_f_005(search_engine: InvertedIndex):
    """
    Verify AC-F-005 / REQ-F-005: Results return 0 to 3 documents sorted descending by TF-IDF,
    excluding any document with score == 0.

    Implements TKT-004 | REQ-F-005 | AC-F-005
    """
    # Query with zero matching terms yields exactly 0 results
    res_zero = search_engine.search("синхрофазотрон квантовий колайдер бозон")
    assert len(res_zero.results) == 0

    # Query with matching terms yields at most 3 documents
    res_valid = search_engine.search("налаштування корпоративної пошти outlook та пароль")
    assert 0 < len(res_valid.results) <= 3
    assert all(r.tfidf_score > 0 for r in res_valid.results)
    scores = [r.tfidf_score for r in res_valid.results]
    assert scores == sorted(scores, reverse=True)


def test_tkt_004_dynamic_kb_hot_pickup_ac_nf_007(tmp_path: Path):
    """
    Verify AC-NF-007 / REQ-NF-007: Adding a new .md file and rebuilding picks up
    the new topic without any Python source code edits.

    Implements TKT-004 | REQ-NF-007 | AC-NF-007
    """
    kb_dir = tmp_path / "dynamic_test_kb"
    kb_dir.mkdir()
    (kb_dir / "doc1.md").write_text("# Перша інструкція\nОпис принтера.", encoding="utf-8")

    reader = SandboxedKBReader(kb_dir=kb_dir)
    engine = InvertedIndex(kb_reader=reader, worker_threads=2)
    engine.build_index()

    assert engine.get_status().indexed_documents == 1

    # Add second file dynamically
    (kb_dir / "doc2.md").write_text("# Друга інструкція\nОпис нового сканера.", encoding="utf-8")
    engine.build_index()

    assert engine.get_status().indexed_documents == 2
    res = engine.search("новий сканер")
    assert len(res.results) > 0
    assert res.results[0].document_id == "doc2.md"
