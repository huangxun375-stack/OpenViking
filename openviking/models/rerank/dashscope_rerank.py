# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
DashScope native Text Rerank API Client.

Supports Alibaba Cloud DashScope / Model Studio native endpoint:
  POST .../api/v1/services/rerank/text-rerank/text-rerank

Request/response use nested ``input`` / ``output`` wrappers (unlike the
OpenAI-compatible ``/compatible-api/v1/reranks`` path).

Customer configs commonly look like::

    {
      "provider": "dashscope",
      "api_base": "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
      "model": "qwen3-vl-rerank",
      "api_key": "sk-...",
      "threshold": 0.1
    }
"""

import time
from typing import Dict, List, Optional

import requests

from openviking.models.rerank.base import RerankBase
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

DEFAULT_DASHSCOPE_RERANK_API_BASE = (
    "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
)


class DashScopeRerankClient(RerankBase):
    """DashScope native text-rerank client (Bearer auth + nested input/output)."""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model_name: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.extra_headers = extra_headers or {}
        self.provider = "dashscope"

    def rerank_batch(self, query: str, documents: List[str]) -> Optional[List[float]]:
        """
        Batch rerank documents against a query via DashScope native API.

        Returns scores in the same order as ``documents``, or None on failure
        so the caller can fall back to vector scores.
        """
        if not documents:
            return []

        # Native DashScope body. Plain strings are accepted for text-only
        # qwen3-vl-rerank / gte-rerank-v2; omit top_n so all docs are scored.
        req_body = {
            "model": self.model_name,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "return_documents": False,
            },
        }

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            if self.extra_headers:
                headers.update(self.extra_headers)

            max_attempts = 2
            response = None
            for attempt in range(max_attempts):
                response = requests.post(
                    url=self.api_base,
                    headers=headers,
                    json=req_body,
                    timeout=30,
                )
                if response.status_code != 429 or attempt == max_attempts - 1:
                    break
                retry_after_raw = response.headers.get("Retry-After", "")
                try:
                    retry_after = float(retry_after_raw)
                except (TypeError, ValueError):
                    retry_after = 0.0
                delay = min(max(retry_after, 0.5 * (attempt + 1)), 5.0)
                logger.warning(
                    "[DashScopeRerankClient] 429 rate limited, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    max_attempts,
                )
                time.sleep(delay)
            if response.status_code >= 400:
                # Surface provider body (e.g. Arrearage / InvalidParameter) before raise.
                body_preview = (response.text or "")[:500]
                logger.error(
                    "[DashScopeRerankClient] HTTP %s url=%s body=%s",
                    response.status_code,
                    self.api_base,
                    body_preview,
                )
            response.raise_for_status()
            result = response.json()

            # DashScope may return HTTP 200 with an error payload.
            err_code = result.get("code")
            if err_code:
                logger.error(
                    "[DashScopeRerankClient] API error code=%s message=%s request_id=%s",
                    err_code,
                    result.get("message"),
                    result.get("request_id"),
                )
                return None

            self._extract_and_update_token_usage(result, query, documents)

            # Native: output.results[]; also accept flat results for robustness.
            output = result.get("output")
            if isinstance(output, dict):
                results = output.get("results")
            else:
                results = result.get("results")

            if not results:
                logger.warning(f"[DashScopeRerankClient] Unexpected response format: {result}")
                return None

            scores = [0.0] * len(documents)
            seen = 0
            for item in results:
                idx = item.get("index")
                if idx is None or not (0 <= idx < len(documents)):
                    logger.warning(
                        "[DashScopeRerankClient] Out-of-bounds or missing index in result: %s",
                        item,
                    )
                    return None
                scores[idx] = float(item.get("relevance_score", 0.0))
                seen += 1

            if seen != len(documents):
                # top_n or partial responses: missing docs keep 0.0
                logger.debug(
                    "[DashScopeRerankClient] Partial results: expected=%s actual=%s (fill 0.0)",
                    len(documents),
                    seen,
                )

            logger.debug(f"[DashScopeRerankClient] Reranked {len(documents)} documents")
            return scores

        except Exception as e:
            logger.error(f"[DashScopeRerankClient] Rerank failed: {e}")
            return None

    @classmethod
    def from_config(cls, config) -> Optional["DashScopeRerankClient"]:
        """Create DashScopeRerankClient from RerankConfig."""
        if not config or not config.is_available():
            return None
        return cls(
            api_key=config.api_key,
            api_base=config.api_base or DEFAULT_DASHSCOPE_RERANK_API_BASE,
            model_name=config.model or "qwen3-vl-rerank",
            extra_headers=config.extra_headers,
        )
