# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Hierarchical retriever rerank behavior tests."""

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig, RetrievalConfig


class DummyEmbedResult:
    def __init__(self) -> None:
        self.dense_vector = [1.0]
        self.sparse_vector = {"hello": 1.0}


class DummyEmbedder:
    def prepare_embedding_input(self, text: str) -> str:
        return text

    def embed(self, _query: str, is_query: bool = False) -> DummyEmbedResult:
        return DummyEmbedResult()

    async def embed_async(self, text: str, is_query: bool = False) -> DummyEmbedResult:
        return self.embed(text, is_query=is_query)


class DummyStorage:
    def __init__(self) -> None:
        self.collection_name = "context"
        self.global_search_calls = []
        self.child_search_calls = []

    async def collection_exists_bound(self) -> bool:
        return True

    async def search_global_roots_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.global_search_calls.append(
            {
                "ctx": ctx,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/root-a",
                "abstract": "root A",
                "_score": 0.2,
                "level": 1,
                "context_type": "resource",
            },
            {
                "uri": "viking://resources/root-b",
                "abstract": "root B",
                "_score": 0.8,
                "level": 1,
                "context_type": "resource",
            },
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append(
            {
                "ctx": ctx,
                "parent_uri": parent_uri,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        if parent_uri == "viking://resources":
            return [
                {
                    "uri": "viking://resources/file-a",
                    "abstract": "child A",
                    "_score": 0.2,
                    "level": 2,
                    "context_type": "resource",
                    "category": "doc",
                },
                {
                    "uri": "viking://resources/file-b",
                    "abstract": "child B",
                    "_score": 0.8,
                    "level": 2,
                    "context_type": "resource",
                    "category": "doc",
                },
            ]
        return []


class LevelTwoGlobalStorage(DummyStorage):
    async def search_global_roots_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.global_search_calls.append(
            {
                "ctx": ctx,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 0.2,
                "level": 2,
                "context_type": "resource",
                "category": "doc",
            },
            {
                "uri": "viking://resources/file-b",
                "abstract": "child B",
                "_score": 0.8,
                "level": 2,
                "context_type": "resource",
                "category": "doc",
            },
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append(
            {
                "ctx": ctx,
                "parent_uri": parent_uri,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return []


class DirectChildProxy:
    async def search_children_in_tenant(
        self,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        return [
            {
                "uri": f"{parent_uri}/file-a",
                "abstract": "child A",
                "_score": 0.2,
                "level": 2,
                "context_type": "resource",
            },
            {
                "uri": f"{parent_uri}/file-b",
                "abstract": "child B",
                "_score": 0.8,
                "level": 2,
                "context_type": "resource",
            },
        ]


class FanOutStorage(DummyStorage):
    """Three directories at global search, each with one child.

    Used to prove that per-directory rerank calls collapse into a single
    call per recursion round instead of one call per directory.
    """

    DIR_URIS = (
        "viking://resources/dir-a",
        "viking://resources/dir-b",
        "viking://resources/dir-c",
    )

    async def search_global_roots_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.global_search_calls.append({"limit": limit})
        return [
            {
                "uri": uri,
                "abstract": f"dir {uri[-1]}",
                "_score": score,
                "level": 1,
                "context_type": "resource",
            }
            for uri, score in zip(self.DIR_URIS, (0.3, 0.5, 0.7))
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append({"parent_uri": parent_uri})
        if parent_uri in self.DIR_URIS:
            return [
                {
                    "uri": f"{parent_uri}/file-1",
                    "abstract": f"file under {parent_uri[-1]}",
                    "_score": 0.4,
                    "level": 2,
                    "context_type": "resource",
                    "category": "doc",
                }
            ]
        return []


class FakeRerankClient:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []
        self._cursor = 0

    def rerank_batch(self, query: str, documents: list[str]):
        self.calls.append((query, list(documents)))
        start = self._cursor
        end = start + len(documents)
        self._cursor = end
        return list(self.scores[start:end])


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _query() -> TypedQuery:
    return TypedQuery(query="hello", context_type=ContextType.RESOURCE, intent="")


def _config() -> RerankConfig:
    return RerankConfig(ak="ak", sk="sk", threshold=0.1)


def test_retriever_initializes_rerank_client(monkeypatch):
    fake_client = FakeRerankClient([0.9, 0.1])

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    assert retriever._rerank_client is fake_client


def test_merge_starting_points_prefers_precomputed_rerank_scores():
    # _merge_starting_points no longer reranks by itself (commit 3 merges
    # that call into a single retrieve()-level rerank); it now trusts
    # whatever scores the caller already computed and passed in.
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=None,
    )

    global_results = [
        {
            "uri": "viking://resources/root-a",
            "abstract": "root A",
            "_score": 0.2,
            "level": 1,
        },
        {
            "uri": "viking://resources/root-b",
            "abstract": "root B",
            "_score": 0.8,
            "level": 1,
        },
    ]
    precomputed_scores = {id(global_results[0]): 0.95, id(global_results[1]): 0.05}

    starting_points = retriever._merge_starting_points(
        ["viking://resources"],
        global_results,
        precomputed_scores=precomputed_scores,
    )

    # Precomputed (rerank) scores win over the raw vector `_score` fields.
    assert starting_points[:2] == [
        ("viking://resources/root-a", 0.95),
        ("viking://resources/root-b", 0.05),
    ]


def test_merge_starting_points_falls_back_to_vector_scores_without_precomputed():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=None,
    )

    global_results = [
        {
            "uri": "viking://resources/root-a",
            "abstract": "root A",
            "_score": 0.2,
            "level": 1,
        },
    ]

    starting_points = retriever._merge_starting_points(
        ["viking://resources"],
        global_results,
        precomputed_scores=None,
    )

    assert starting_points[0] == ("viking://resources/root-a", 0.2)


@pytest.mark.asyncio
async def test_retrieve_uses_rerank_scores_in_thinking_mode(monkeypatch):
    fake_client = FakeRerankClient([0.95, 0.05, 0.11, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls[0] == ("hello", ["root A", "root B"])
    assert fake_client.calls[1] == ("hello", ["child A", "child B"])


@pytest.mark.asyncio
async def test_retrieve_reranks_level_two_initial_candidates_in_thinking_mode(monkeypatch):
    fake_client = FakeRerankClient([0.11, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=LevelTwoGlobalStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls == [("hello", ["child A", "child B"])]


@pytest.mark.asyncio
async def test_retrieve_falls_back_to_vector_scores_when_rerank_returns_none(monkeypatch):
    class NoneRerankClient(FakeRerankClient):
        def rerank_batch(self, query: str, documents: list[str]):
            self.calls.append((query, list(documents)))
            return None

    fake_client = NoneRerankClient([])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls


@pytest.mark.asyncio
async def test_quick_mode_skips_rerank(monkeypatch):
    fake_client = FakeRerankClient([0.95, 0.05, 0.05, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.QUICK)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_score_propagation_alpha_uses_configured_weight():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(score_propagation_alpha=1.0),
    )

    candidates = await retriever._recursive_search(
        vector_proxy=DirectChildProxy(),
        query="hello",
        query_vector=None,
        sparse_query_vector=None,
        starting_points=[("viking://resources", 0.4)],
        limit=1,
        mode=RetrieverMode.QUICK,
    )

    assert candidates[0]["uri"] == "viking://resources/file-b"
    assert candidates[0]["_final_score"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_default_retrieval_config_uses_semantic_score_without_hotness(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: pytest.fail("hotness_score should not be called by default"),
    )
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
    )

    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 1.0,
                "level": 2,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert result[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_retrieval_hotness_alpha_blends_when_configured(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: 0.5,
    )
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(hotness_alpha=0.2),
    )

    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 1.0,
                "level": 2,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert result[0].score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_per_round_mode_merges_rerank_calls_across_directories(monkeypatch):
    # 3 global dirs (1 rerank call) fan out to 3 directories with children
    # in the same recursion round (1 more rerank call) = 2 calls total.
    # Before commit 3 this would have been 1 (starting points) + 1 (per
    # directory) * 3 = 4 calls minimum for this fixture.
    fake_client = FakeRerankClient([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=FanOutStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=3, mode=RetrieverMode.THINKING)

    assert len(fake_client.calls) == 2
    assert fake_client.calls[0][1] == ["dir a", "dir b", "dir c"]
    assert sorted(fake_client.calls[1][1]) == sorted(
        ["file under a", "file under b", "file under c"]
    )
    assert len(result.matched_contexts) == 3


@pytest.mark.asyncio
async def test_final_mode_skips_rerank_during_navigation_and_reranks_once(monkeypatch):
    monkeypatch.setattr("openviking.retrieve.hierarchical_retriever.RERANK_MODE", "final")
    # Vector scores rank file-b (0.8) above file-a (0.2); these rerank
    # scores invert that, proving the single final call actually drives
    # the final ranking rather than just being a no-op pass-through.
    fake_client = FakeRerankClient([0.05, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    # Zero rerank calls during navigation (global merge + per-round merge
    # are both skipped in final mode); exactly one call at the very end.
    # Navigation ranked on vector scores puts file-b (0.8) ahead of file-a
    # (0.2), so that is the pool order the final rerank call receives.
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0][1] == ["child B", "child A"]
    # The final rerank call (scores inverted vs. vector order) is what
    # decides the final ranking: file-a (0.95) leads, and file-b (0.05)
    # falls below the 0.1 rerank threshold and is filtered out -- the same
    # fate it would meet under per_round navigation rerank.
    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-a",
    ]


@pytest.mark.asyncio
async def test_convert_to_matched_contexts_returns_empty_relations():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
    )

    result = await retriever._convert_to_matched_contexts(
        [
            {
                "uri": "viking://resources/file-a",
                "abstract": "child A",
                "_score": 1.0,
                "level": 2,
                "context_type": "resource",
            }
        ],
        ctx=_ctx(),
    )

    assert result[0].relations == []
