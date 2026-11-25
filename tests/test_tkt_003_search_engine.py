"""
Unit, Formula, Schema, and Concurrency Verification for Search Engine and Inverted Index.

Implements TKT-003 | REQ-F-003, REQ-F-005, REQ-NF-003, REQ-NF-004, REQ-NF-007
Acceptance Criteria: AC-F-003, AC-F-005, AC-NF-003, AC-NF-004, AC-NF-007
Contracts: openapi-v2.yaml InvertedIndexStatus, SearchQueryRequest, SearchQueryResponse, RankedDocumentHit
"""

import math
from pathlib import Path
import threading
from typing import List
import pytest

from src.core.kb_reader import SandboxedKBReader
from src.core.models import (
    IndexBuildStatus,
    InvertedIndexStatus,
    RankedDocumentHit,
    SearchQueryResponse,
)
from src.core.search_engine import InvertedIndex
from src.core.text_processing import TextNormalizer


@pytest.fixture
def repo_kb_reader() -> SandboxedKBReader:
    """Fixture providing SandboxedKBReader for the project knowledge base."""
    repo_root = Path(__file__).resolve().parent.parent
    return SandboxedKBReader(kb_dir=repo_root / "knowledge_base")


@pytest.fixture
def search_engine(repo_kb_reader: SandboxedKBReader) -> InvertedIndex:
    """Fixture providing a built InvertedIndex on the repository knowledge base."""
    index = InvertedIndex(kb_reader=repo_kb_reader, worker_threads=4)
    index.build_index()
    return index


def test_tkt_003_multithreaded_startup_indexing(search_engine: InvertedIndex):
    """
    Verify AC-F-003: ThreadPoolExecutor startup indexing scans all 7 KB files and builds in-memory inverted index.

    Implements TKT-003 | REQ-F-003 | AC-F-003
    """
    status: InvertedIndexStatus = search_engine.get_status()
    assert status.status == IndexBuildStatus.READY
    assert status.indexed_documents == 7
    assert status.unique_stems > 100
    assert status.worker_threads == 4
    assert status.build_duration_ms >= 0


def test_tkt_003_exact_tfidf_formula(search_engine: InvertedIndex, repo_kb_reader: SandboxedKBReader):
    """
    Verify REQ-F-005: Exact TF-IDF score formula:
    TF-IDF(t, d, D) = TF(t, d) * (log((N + 1) / (DF(t) + 1)) + 1)

    Implements TKT-003 | REQ-F-005
    """
    normalizer = TextNormalizer()
    term = "принтер"
    stem = normalizer.stemmer.stem(term)

    # Document frequency DF(t): count how many of the 7 documents contain stem
    n_docs = 7
    doc_hits = {}
    for doc_summary in repo_kb_reader.list_documents().documents:
        content = repo_kb_reader.get_document_text(doc_summary.document_id)
        stems = normalizer.tokenize_and_stem(content)
        tf = stems.count(stem)
        if tf > 0:
            doc_hits[doc_summary.document_id] = tf

    df = len(doc_hits)
    assert df > 0, "Stem 'принтер' must exist in at least one document"

    expected_idf = math.log((n_docs + 1) / (df + 1)) + 1

    # Search for single term
    response: SearchQueryResponse = search_engine.search(query=term, top_k=3)
    assert len(response.results) > 0

    top_hit = response.results[0]
    expected_tf = doc_hits[top_hit.document_id]
    expected_score = round(expected_tf * expected_idf, 4)

    assert math.isclose(top_hit.tfidf_score, expected_score, rel_tol=1e-3)


def test_tkt_003_zero_score_filtering_and_top3_limit(search_engine: InvertedIndex):
    """
    Verify AC-F-005: Exclude TF-IDF == 0 documents and cap results at at most 3 sorted descending.

    Implements TKT-003 | REQ-F-005 | AC-F-005
    """
    # 1. Zero match query returns empty results
    zero_res = search_engine.search(query="невідомесловоякогонемаєвбазізнань12345", top_k=3)
    assert zero_res.results == []

    # 2. General query with many matches returns at most 3
    broad_res = search_engine.search(query="налаштувати пароль або принтер чи телефон", top_k=3)
    assert len(broad_res.results) <= 3
    assert len(broad_res.results) > 0

    # Verify descending sort order and all scores > 0
    scores = [hit.tfidf_score for hit in broad_res.results]
    assert all(s > 0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_tkt_003_schema_compliance_openapi(search_engine: InvertedIndex):
    """
    Verify openapi-v2.yaml schema compliance for SearchQueryResponse and RankedDocumentHit.

    Implements TKT-003 | REQ-F-005
    """
    res = search_engine.search(query="загубив робочий телефон у метро", top_k=3)
    assert isinstance(res, SearchQueryResponse)
    assert res.normalized_stems == ["загуб", "робоч", "телефон", "метр"]
    assert len(res.results) >= 1

    for hit in res.results:
        assert isinstance(hit, RankedDocumentHit)
        assert hit.document_id.endswith((".md", ".txt"))
        assert len(hit.title) > 0
        assert hit.tfidf_score >= 0.0001
        assert isinstance(hit.matched_stems, list)
        assert len(hit.matched_stems) > 0


def test_tkt_003_thread_safety_rlock(search_engine: InvertedIndex):
    """
    Verify AC-NF-004: Thread safety under concurrent load using threading.RLock.
    Executes 10 concurrent threads querying the index simultaneously with zero race conditions.

    Implements TKT-003 | REQ-NF-004 | AC-NF-004
    """
    queries = [
        "не працює принтер",
        "забув пароль",
        "налаштування vpn",
        "як підключити wifi",
        "корпоративна пошта outlook",
        "втрата телефону",
        "встановлення програм",
    ]

    exceptions: List[Exception] = []

    def worker(query_text: str):
        try:
            for _ in range(15):
                res = search_engine.search(query_text, top_k=3)
                assert len(res.results) > 0
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker, args=(queries[i % len(queries)],)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(exceptions) == 0, f"Concurrent query exceptions occurred: {exceptions}"


def test_tkt_003_dynamic_kb_pickup(tmp_path: Path):
    """
    Verify AC-NF-007: Adding a new .md instruction file and rebuilding/restarting
    indexes retrieves the new topic without any Python source code edits.

    Implements TKT-003 | REQ-NF-007 | AC-NF-007
    """
    # Create isolated dynamic knowledge base
    kb_folder = tmp_path / "dynamic_kb"
    kb_folder.mkdir()
    (kb_folder / "test_doc.md").write_text("# Базовий документ\nІнструкція про комп'ютери.", encoding="utf-8")

    reader = SandboxedKBReader(kb_dir=kb_folder)
    dynamic_index = InvertedIndex(kb_reader=reader, worker_threads=2)
    dynamic_index.build_index()

    status1 = dynamic_index.get_status()
    assert status1.indexed_documents == 1

    # Add brand new document dynamically
    (kb_folder / "new_monitor_issue.md").write_text(
        "# Проблема з монітором\nЯк налаштувати монітор та кабель HDMI.", encoding="utf-8"
    )

    # Rebuild index (simulating restart)
    dynamic_index.build_index()
    status2 = dynamic_index.get_status()
    assert status2.indexed_documents == 2

    # Query newly added topic
    res = dynamic_index.search("проблема з монітором та hdmi")
    assert len(res.results) > 0
    assert res.results[0].document_id == "new_monitor_issue.md"


def test_tkt_003_in_scope_control_queries(search_engine: InvertedIndex):
    """
    Verify that canonical control questions rank their expected source instruction #1.

    Implements TKT-003 | REQ-F-005, REQ-F-007
    """
    # 1. Printer
    res_printer = search_engine.search("Що робити, якщо в офісі зажувало папір у принтері?")
    assert res_printer.results[0].document_id == "printer_issues.md"

    # 2. Lost phone
    res_phone = search_engine.search("Я загубив корпоративний телефон у метро")
    assert res_phone.results[0].document_id == "lost_phone.md"

    # 3. VPN
    res_vpn = search_engine.search("Не можу підключитися з дому через VPN")
    assert res_vpn.results[0].document_id == "vpn_setup.md"

    # 4. Wi-Fi
    res_wifi = search_engine.search("Не працює офісний Wi-Fi Corp-Secure")
    assert res_wifi.results[0].document_id == "wifi_troubleshooting.md"

    # 5. Password
    res_pw = search_engine.search("Забув пароль від корпоративного облікового запису")
    assert res_pw.results[0].document_id == "forgot_password.md"
