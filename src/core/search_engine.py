"""
Multi-Threaded Inverted Index and TF-IDF Relevance Ranking Engine.

Implements TKT-003 | REQ-F-003, REQ-F-005, REQ-NF-003, REQ-NF-004, REQ-NF-007
Strictly compliant with openapi-v2.yaml /knowledge-base/search and index schemas.
Features:
- Parallel startup scanning of knowledge_base/ via ThreadPoolExecutor.
- Thread-safe concurrency synchronized via threading.RLock.
- Strict TF-IDF scoring formula per REQ-F-005 with Top-3 document selection.
- Codeless KB updates via dynamic rebuilding.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import math
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from src.core.exceptions import KnowledgeBaseError
from src.core.kb_reader import SandboxedKBReader
from src.core.models import (
    ApiErrorCode,
    IndexBuildStatus,
    InvertedIndexStatus,
    RankedDocumentHit,
    SearchQueryResponse,
)
from src.core.text_processing import TextNormalizer, default_normalizer


class InvertedIndex:
    """
    Thread-safe in-memory inverted index and TF-IDF relevance ranking search engine.

    Implements TKT-003 | REQ-F-003, REQ-F-005, REQ-NF-003, REQ-NF-004, REQ-NF-007
    """

    def __init__(
        self,
        kb_reader: SandboxedKBReader,
        normalizer: TextNormalizer = default_normalizer,
        worker_threads: int = 4,
    ):
        """
        Initialize the in-memory inverted index engine.

        :param kb_reader: Sandboxed file reader for knowledge_base/ documents (TKT-001).
        :param normalizer: Text normalizer and Ukrainian stemmer (TKT-002).
        :param worker_threads: ThreadPoolExecutor concurrency worker count.
        """
        self.kb_reader = kb_reader
        self.normalizer = normalizer
        self.worker_threads = max(1, worker_threads)

        # Thread synchronization primitive
        self._lock = threading.RLock()

        # In-memory index data structures
        self._status: IndexBuildStatus = IndexBuildStatus.BUILDING
        self._inverted_index: Dict[str, Dict[str, int]] = defaultdict(dict)  # stem -> {doc_id: term_frequency}
        self._doc_titles: Dict[str, str] = {}  # doc_id -> title
        self._doc_ids: List[str] = []
        self._build_duration_ms: float = 0.0

    def build_index(self) -> InvertedIndexStatus:
        """
        Scan knowledge_base/ concurrently using a ThreadPoolExecutor and build the in-memory inverted index.

        Implements TKT-003 | REQ-F-003, REQ-NF-003, REQ-NF-004, REQ-NF-007
        :return: InvertedIndexStatus snapshot of the built index.
        """
        start_time = time.perf_counter()

        with self._lock:
            self._status = IndexBuildStatus.BUILDING
            doc_ids = self.kb_reader.list_document_ids()

            # Temporary thread-safe build dictionaries
            new_inverted_index: Dict[str, Dict[str, int]] = defaultdict(dict)
            new_doc_titles: Dict[str, str] = {}

            def _process_document(doc_id: str) -> Tuple[str, str, Dict[str, int]]:
                detail = self.kb_reader.read_document(doc_id)
                stems = self.normalizer.tokenize_and_stem(detail.content)
                tf_counts: Dict[str, int] = defaultdict(int)
                for stem in stems:
                    tf_counts[stem] += 1
                return doc_id, detail.title, tf_counts

            # Process documents across the worker thread pool
            with ThreadPoolExecutor(max_workers=self.worker_threads) as executor:
                for doc_id, title, tf_counts in executor.map(_process_document, doc_ids):
                    new_doc_titles[doc_id] = title
                    for stem, count in tf_counts.items():
                        new_inverted_index[stem][doc_id] = count

            # Atomically replace index state
            self._inverted_index = new_inverted_index
            self._doc_titles = new_doc_titles
            self._doc_ids = list(new_doc_titles.keys())
            self._build_duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            self._status = IndexBuildStatus.READY

            return self._get_status_unlocked()

    def search(self, query: str, top_k: int = 3) -> SearchQueryResponse:
        """
        Execute normalized TF-IDF relevance search across indexed documents and return Top-3 documents.

        Formula strictly per REQ-F-005:
            TF-IDF(t, d, D) = TF(t, d) * (log((N + 1) / (DF(t) + 1)) + 1)
            where:
            - N is the total count of indexed documents in knowledge_base/
            - DF(t) is the count of documents containing stem t
            - TF(t, d) is the occurrence count of stem t in document d

        Implements TKT-003 | REQ-F-004, REQ-F-005, REQ-NF-004
        :param query: Raw Ukrainian query string.
        :param top_k: Maximum documents to return (clamped between 1 and 3 per REQ-F-005).
        :return: SearchQueryResponse adhering to openapi-v2.yaml schema.
        :raises KnowledgeBaseError: If index is not yet built.
        """
        with self._lock:
            if self._status != IndexBuildStatus.READY:
                raise KnowledgeBaseError(
                    message="Індекс бази знань ще не готовий.",
                    error_code=ApiErrorCode.INDEX_NOT_READY,
                )

            # Cap top_k between 1 and 3 per REQ-F-005
            top_k = min(max(1, top_k), 3)

            normalized_stems = self.normalizer.tokenize_and_stem(query)
            if not normalized_stems or not self._doc_ids:
                return SearchQueryResponse(normalized_stems=normalized_stems, results=[])

            n_docs = len(self._doc_ids)
            doc_scores: Dict[str, float] = defaultdict(float)
            doc_matched_stems: Dict[str, Set[str]] = defaultdict(set)

            # Compute TF-IDF contributions for each query stem
            for stem in normalized_stems:
                if stem in self._inverted_index:
                    doc_tf_map = self._inverted_index[stem]
                    df = len(doc_tf_map)
                    idf = math.log((n_docs + 1) / (df + 1)) + 1.0

                    for doc_id, tf in doc_tf_map.items():
                        doc_scores[doc_id] += tf * idf
                        doc_matched_stems[doc_id].add(stem)

            # Discard any documents with TF-IDF == 0
            positive_hits = [
                (doc_id, score) for doc_id, score in doc_scores.items() if score > 0
            ]

            # Sort descending by TF-IDF score (with doc_id tie-breaker for deterministic output)
            positive_hits.sort(key=lambda item: (-item[1], item[0]))

            # Take up to top_k documents
            top_hits = positive_hits[:top_k]

            results: List[RankedDocumentHit] = [
                RankedDocumentHit(
                    document_id=doc_id,
                    title=self._doc_titles.get(doc_id, doc_id),
                    tfidf_score=round(score, 4),
                    matched_stems=sorted(list(doc_matched_stems[doc_id])),
                )
                for doc_id, score in top_hits
            ]

            return SearchQueryResponse(
                normalized_stems=normalized_stems,
                results=results,
            )

    def get_status(self) -> InvertedIndexStatus:
        """
        Return the thread-safe operational status of the inverted index.

        Implements TKT-003 | REQ-NF-004
        :return: InvertedIndexStatus snapshot.
        """
        with self._lock:
            return self._get_status_unlocked()

    def _get_status_unlocked(self) -> InvertedIndexStatus:
        """Internal helper returning status while lock is already held."""
        return InvertedIndexStatus(
            status=self._status,
            indexed_documents=len(self._doc_ids),
            unique_stems=len(self._inverted_index),
            build_duration_ms=self._build_duration_ms,
            worker_threads=self.worker_threads,
        )
