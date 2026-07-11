# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.retrieve.types import QueryResult
from openviking_cli.utils.config.retrieval_config import RetrievalConfig
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _make_viking_fs(*, enable_intent: bool) -> VikingFS:
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = MagicMock()
    fs.query_embedder = MagicMock(name="embedder")
    fs.rerank_config = None
    fs.retrieval_config = RetrievalConfig(enable_intent=enable_intent)
    fs.vector_store = MagicMock(name="vector_store")
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_intent_test", default=None)
    fs._ensure_access = MagicMock()
    fs._get_vector_store = MagicMock(return_value=fs.vector_store)
    fs._get_embedder = MagicMock(return_value=fs.query_embedder)
    fs._ctx_or_default = MagicMock(return_value=_ctx())
    fs.abstract = AsyncMock(return_value="")
    return fs


def test_retrieval_config_enable_intent_defaults_true():
    cfg = RetrievalConfig()
    assert cfg.enable_intent is True


def test_retrieval_config_enable_intent_can_disable():
    cfg = RetrievalConfig(enable_intent=False)
    assert cfg.enable_intent is False


@pytest.mark.asyncio
async def test_search_skips_intent_and_uses_raw_query_when_disabled(monkeypatch):
    fs = _make_viking_fs(enable_intent=False)
    captured = {}

    class ForbiddenIntentAnalyzer:
        def __init__(self, *args, **kwargs):
            raise AssertionError("intent analysis must not run when disabled")

    class FakeRetriever:
        def __init__(self, storage, embedder, rerank_config, retrieval_config):
            pass

        async def retrieve(self, typed_query, **kwargs):
            captured["typed_query"] = typed_query
            return QueryResult(
                query=typed_query,
                matched_contexts=[],
                searched_directories=typed_query.target_directories,
            )

    monkeypatch.setattr(
        "openviking.retrieve.intent_analyzer.IntentAnalyzer",
        ForbiddenIntentAnalyzer,
    )
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.HierarchicalRetriever",
        FakeRetriever,
    )

    result = await fs.search(
        "raw query",
        target_uri="viking://resources/docs",
        session_info={
            "latest_archive_overview": "previous summary",
            "current_messages": [{"role": "user", "content": "previous turn"}],
        },
        ctx=_ctx(),
    )

    assert result.query_plan is None
    assert captured["typed_query"].query == "raw query"
    assert captured["typed_query"].intent == ""
    assert captured["typed_query"].target_directories == ["viking://resources/docs"]
