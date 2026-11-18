"""Document retrieval over extracted text: chunking with page references, BM25, local semantic search, hybrid fusion, comparison and evaluation."""
from .chunker import CHUNKER_VERSION, Chunk, chunk_document
from .corpus import MODES, Corpus, DocIndex
from .service import INDEX_VERSION, RetrievalService

__all__ = ["CHUNKER_VERSION", "Chunk", "Corpus", "DocIndex", "INDEX_VERSION", "MODES", "RetrievalService", "chunk_document"]
