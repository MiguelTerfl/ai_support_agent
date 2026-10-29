import pytest

from app.confirm import InvalidToken, UsedTokens, sign_action, verify_action
from app.db import fts_query, vector_literal
from app.rag.chunking import split_markdown
from app.rag.retriever import Retriever
from app.types import Chunk

DOC = """# Example policy

## Deadlines
First paragraph about the deadline. It has several sentences. Here is one more sentence.

Second paragraph about the deadline with more text to force the chunk to split and to check the overlap between consecutive fragments.

## Costs
The cost is $99 MXN.
"""


def test_chunks_respect_headings_and_prefix():
    chunks = split_markdown(DOC, "example.md", chunk_chars=120, overlap=30)
    assert {c.heading for c in chunks} == {"Deadlines", "Costs"}
    assert all(c.content.startswith("Example policy › ") for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert len(chunks) >= 3  # the "Deadlines" section was split


def test_chunks_never_exceed_limit_by_much():
    long = "# T\n\n## S\n" + ("A long test sentence. " * 200)
    chunks = split_markdown(long, "x.md", chunk_chars=300, overlap=50)
    assert len(chunks) > 5
    assert all(len(c.content) <= 300 + 60 for c in chunks)  # limit + prefix


def test_fts_query_and_vector_literal():
    assert fts_query("How many days do I have to return?") == "how | many | days | have | return"
    assert fts_query("a b") == ""
    assert vector_literal([0.5, -1]) == "[0.5000000,-1.0000000]"


class _Store:
    async def hybrid_search(self, embedding, text, k):
        return [Chunk(1, "a.md", "h", "high", 0.8), Chunk(2, "b.md", "h", "low", 0.2)]


class _Emb:
    async def embed_query(self, text):
        return [0.0]


async def test_retriever_relevance_gate():
    res = await Retriever(_Store(), _Emb(), min_score=0.45).search("x")
    assert [c.source for c in res.chunks] == ["a.md"] and res.best_score == 0.8
    res = await Retriever(_Store(), _Emb(), min_score=0.9).search("x")
    assert res.chunks == [] and res.best_score == 0.8


def test_confirm_roundtrip_tamper_expiry():
    token = sign_action("s", "create_ticket", {"subject": "x"}, ttl=60, now=1000)
    tool, args, sig, exp, lang = verify_action("s", token, now=1010)
    assert (tool, args, exp, lang) == ("create_ticket", {"subject": "x"}, 1060, "en")
    assert verify_action("s", sign_action("s", "create_ticket", {}, 60, now=1000, lang="es"), now=1010).lang == "es"

    with pytest.raises(InvalidToken):
        verify_action("other-key", token, now=1010)
    with pytest.raises(InvalidToken):
        verify_action("s", token, now=2000)  # expired
    body, s = token.split(".")
    forged = sign_action("s", "create_ticket", {"subject": "other"}, 60, now=1000).split(".")[0] + "." + s
    with pytest.raises(InvalidToken):
        verify_action("s", forged, now=1010)  # signature of different content
    with pytest.raises(InvalidToken):
        verify_action("s", "garbage", now=1010)


def test_used_tokens_replay_and_release():
    u = UsedTokens()
    assert u.consume("sig", 100, now=10)
    assert not u.consume("sig", 100, now=11)
    u.release("sig")
    assert u.consume("sig", 100, now=12)
    assert u.consume("sig2", 100, now=500)  # expired entries are cleaned up
