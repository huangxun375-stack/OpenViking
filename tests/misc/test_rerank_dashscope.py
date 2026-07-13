# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for DashScope native rerank client and factory dispatch."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from openviking.models.rerank import DashScopeRerankClient, RerankClient
from openviking.models.rerank.dashscope_rerank import DEFAULT_DASHSCOPE_RERANK_API_BASE
from openviking_cli.utils.config.rerank_config import RerankConfig


class TestDashScopeRerankClient:
    def _make_client(self):
        return DashScopeRerankClient(
            api_key="test-key",
            api_base=DEFAULT_DASHSCOPE_RERANK_API_BASE,
            model_name="qwen3-vl-rerank",
        )

    def test_rerank_batch_success(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.3},
                    {"index": 2, "relevance_score": 0.7},
                ]
            },
            "usage": {"total_tokens": 42},
            "request_id": "req-1",
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.dashscope_rerank.requests.post", return_value=mock_response
        ):
            scores = client.rerank_batch("test query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.3, 0.7]

    def test_rerank_batch_out_of_order_results(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 2, "relevance_score": 0.7},
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.3},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.dashscope_rerank.requests.post", return_value=mock_response
        ):
            scores = client.rerank_batch("test query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.3, 0.7]

    def test_rerank_batch_partial_results_fill_zero(self):
        """top_n-style partial results should fill missing docs with 0.0."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 2, "relevance_score": 0.7},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.dashscope_rerank.requests.post", return_value=mock_response
        ):
            scores = client.rerank_batch("query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.0, 0.7]

    def test_rerank_batch_empty_documents(self):
        client = self._make_client()
        assert client.rerank_batch("query", []) == []

    def test_rerank_batch_api_error_payload_returns_none(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "InvalidApiKey",
            "message": "Invalid API-key provided.",
            "request_id": "fb53c4ec",
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.dashscope_rerank.requests.post", return_value=mock_response
        ):
            assert client.rerank_batch("query", ["doc1"]) is None

    def test_rerank_batch_sends_native_request_body(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "output": {"results": [{"index": 0, "relevance_score": 0.8}]}
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.dashscope_rerank.requests.post", return_value=mock_response
        ) as mock_post:
            client.rerank_batch("my query", ["doc1"])

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["url"] == DEFAULT_DASHSCOPE_RERANK_API_BASE
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-key"
        body = call_kwargs.kwargs["json"]
        assert body["model"] == "qwen3-vl-rerank"
        assert body["input"]["query"] == "my query"
        assert body["input"]["documents"] == ["doc1"]
        assert body["parameters"]["return_documents"] is False
        assert "top_n" not in body["parameters"]

    def test_from_config_customer_shape(self):
        """Match 招行-style config: provider=dashscope + native api_base + qwen3-vl-rerank."""
        config = RerankConfig(
            provider="dashscope",
            api_key="sk-test",
            api_base=(
                "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
            ),
            model="qwen3-vl-rerank",
            threshold=0.1,
        )
        client = DashScopeRerankClient.from_config(config)
        assert isinstance(client, DashScopeRerankClient)
        assert client.api_key == "sk-test"
        assert client.model_name == "qwen3-vl-rerank"
        assert client.api_base.endswith("/text-rerank/text-rerank")

    def test_from_config_default_api_base_and_model(self):
        config = RerankConfig(provider="dashscope", api_key="sk-test")
        client = DashScopeRerankClient.from_config(config)
        assert client.api_base == DEFAULT_DASHSCOPE_RERANK_API_BASE
        assert client.model_name == "qwen3-vl-rerank"


class TestDashScopeFactoryAndConfig:
    def test_factory_dispatches_to_dashscope_client(self):
        config = RerankConfig(
            provider="dashscope",
            api_key="test-key",
            model="qwen3-vl-rerank",
        )
        client = RerankClient.from_config(config)
        assert isinstance(client, DashScopeRerankClient)

    def test_dashscope_requires_api_key(self):
        with pytest.raises(ValidationError):
            RerankConfig(provider="dashscope")

    def test_dashscope_is_available(self):
        config = RerankConfig(provider="dashscope", api_key="key")
        assert config.is_available() is True
        assert config.api_base == DEFAULT_DASHSCOPE_RERANK_API_BASE
