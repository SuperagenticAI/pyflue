"""Search implementations for PyFlue MCP tools."""

from __future__ import annotations

import math
from typing import Any

try:
    from pyflue.mcp import MCPToolMatch
except ImportError:
    MCPToolMatch = Any


class BM25Search:
    """BM25 ranking algorithm implementation."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: dict[str, int] = {}
        self.avgdl: float = 0
        self.doc_lengths: list[int] = []
        self.doc_term_freqs: list[dict[str, int]] = []

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into terms."""
        text = text.lower()
        text = text.replace("_", " ").replace("-", " ")
        return [w for w in text.split() if w.strip()]

    def index(self, documents: list[MCPToolMatch]) -> BM25Search:
        """Index a list of tool matches for BM25 search."""
        self.doc_term_freqs = []
        self.doc_lengths = []
        self.doc_freqs = {}

        for doc in documents:
            text = f"{doc.name} {doc.description} {doc.server}"
            tokens = self._tokenize(text)
            tf = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + +1

            self.doc_term_freqs.append(tf)
            self.doc_lengths.append(len(tokens))

            for token in tf:
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

        self.avgdl = sum(self.doc_lengths) / len(self.doc_lengths) if self.doc_lengths else 0
        return self

    def score(self, query: str, doc_idx: int) -> float:
        """Calculate BM25 score for a query against a document."""
        query_terms = self._tokenize(query)
        if not query_terms:
            return 0.0

        tf = self.doc_term_freqs[doc_idx]
        doc_len = self.doc_lengths[doc_idx]
        N = len(self.doc_term_freqs)

        score = 0.0
        for term in query_terms:
            df = self.doc_freqs.get(term, 0)
            if df == 0:
                continue

            tf_term = tf.get(term, 0)
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1)

            numerator = tf_term * (self.k1 + 1)
            denominator = tf_term + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)

            score += idf * numerator / denominator

        return score

    @staticmethod
    def search(
        documents: list[MCPToolMatch],
        query: str,
        limit: int = 10,
    ) -> list[MCPToolMatch]:
        """Search documents using BM25."""
        if not documents:
            return []

        index = BM25Search().index(documents)
        scores = []

        for idx, _doc in enumerate(documents):
            score = index.score(query, idx)
            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:limit]:
            doc = documents[idx]
            doc.score = score
            results.append(doc)

        return results


class SemanticSearch:
    """Semantic search using sentence embeddings."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model = None
        self._model_name = model_name
        self._initialized = False
        self._documents: list[MCPToolMatch] = []
        self._embeddings: list[list[float]] = []

    def _init(self) -> None:
        """Lazy initialization of the embedding model."""
        if self._initialized:
            return

        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            self._initialized = True
        except ImportError as err:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            ) from err

    def index(self, documents: list[MCPToolMatch]) -> SemanticSearch:
        """Index documents for semantic search."""
        self._init()

        self._documents = documents
        texts = [f"{doc.name} {doc.description}" for doc in documents]
        self._embeddings = self._model.encode(texts, convert_to_numpy=True).tolist()

        return self

    @staticmethod
    def search(
        documents: list[MCPToolMatch],
        query: str,
        limit: int = 10,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> list[MCPToolMatch]:
        """Search documents using semantic similarity."""
        searcher = SemanticSearch(model_name=model_name)
        searcher.index(documents)

        query_embedding = searcher._model.encode([query], convert_to_numpy=True)[0]

        scores = []
        for idx, doc_embedding in enumerate(searcher._embeddings):
            score = _cosine_similarity(query_embedding, doc_embedding)
            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:limit]:
            doc = documents[idx]
            doc.score = score
            results.append(doc)

        return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    dot_product = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)