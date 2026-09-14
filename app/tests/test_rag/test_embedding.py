from app.models.rag import RAG_EMBEDDING_DIMENSION
from app.rag.embedding import (
    DEFAULT_EMBEDDING_MODEL,
    SentenceTransformerEmbedder,
    create_embedder_from_env,
)
from app.tests.fakes import DeterministicEmbedder


def test_deterministic_embedder_dimension_and_batch() -> None:
    embedder = DeterministicEmbedder()

    query_vector = embedder.embed_query("退款怎么申请")
    document_vectors = embedder.embed_documents(["退款流程", "发货时间"])

    assert embedder.dimension == RAG_EMBEDDING_DIMENSION
    assert len(query_vector) == RAG_EMBEDDING_DIMENSION
    assert len(document_vectors) == 2
    assert all(len(vector) == RAG_EMBEDDING_DIMENSION for vector in document_vectors)
    assert embedder.embed_query("退款怎么申请") == query_vector


def test_create_embedder_from_env_uses_model_name(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "local/test-model")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")

    embedder = create_embedder_from_env()

    assert isinstance(embedder, SentenceTransformerEmbedder)


def test_sentence_transformer_embedder_default_model_name() -> None:
    embedder = SentenceTransformerEmbedder()

    assert DEFAULT_EMBEDDING_MODEL == "BAAI/bge-small-zh-v1.5"
    assert embedder._model_name == DEFAULT_EMBEDDING_MODEL  # noqa: SLF001
