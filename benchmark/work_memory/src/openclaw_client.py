from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

import requests


OPENVIKING_OV_SESSION_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
WINDOWS_BAD_SESSION_SEGMENT = re.compile(r'[:<>"/\\|?*]')


def openclaw_session_to_ov_storage_id(
    session_id: str | None,
    session_key: str | None,
) -> str:
    sid = session_id.strip() if isinstance(session_id, str) else ""
    key = session_key.strip() if isinstance(session_key, str) else ""

    if sid and OPENVIKING_OV_SESSION_UUID.fullmatch(sid):
        return sid.lower()
    if key:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()
    if sid:
        if WINDOWS_BAD_SESSION_SEGMENT.search(sid):
            return hashlib.sha256(f"openclaw-session:{sid}".encode("utf-8")).hexdigest()
        return sid
    raise ValueError("need sessionId or sessionKey for OV session path")


def extract_response_text(response_json: dict[str, Any]) -> str:
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return str(content.get("text", ""))
    for item in response_json.get("output", []):
        if "text" in item:
            return str(item["text"])
        for content in item.get("content", []):
            if "text" in content:
                return str(content["text"])
    return ""


class OpenClawBenchmarkClient:
    def __init__(
        self,
        *,
        openclaw_base_url: str,
        openclaw_token: str,
        ov_base_url: str,
        ov_api_key: str,
        agent_id: str,
        user: str | None = None,
        session_key: str | None = None,
        session_id: str | None = None,
        keep_recent_count: int = 10,
        openclaw_model: str = "openclaw",
        openclaw_http: Any | None = None,
        ov_http: Any | None = None,
    ):
        self.openclaw_base_url = openclaw_base_url.rstrip("/")
        self.openclaw_token = openclaw_token
        self.ov_base_url = ov_base_url.rstrip("/")
        self.ov_api_key = ov_api_key
        self.agent_id = agent_id
        self.user = user
        self.session_key = session_key or f"wm-bench:{uuid4()}"
        self.session_id = session_id
        self.keep_recent_count = keep_recent_count
        self.openclaw_model = openclaw_model
        self.openclaw_http = openclaw_http or requests.Session()
        self.ov_http = ov_http or requests.Session()
        self.ov_session_id = openclaw_session_to_ov_storage_id(self.session_id, self.session_key)

    def apply_turn(self, turn: dict[str, Any]) -> str:
        role = str(turn.get("role", "")).strip()
        content = turn.get("content")

        if role == "user" and isinstance(content, str):
            return self._send_response(content)

        return self._inject_turn(turn)

    def ask(self, query: str) -> str:
        return self._send_response(query)

    def commit_session(self, keep_recent_count: int | None = None) -> dict[str, Any]:
        effective_keep_recent_count = (
            self.keep_recent_count if keep_recent_count is None else keep_recent_count
        )
        payload: dict[str, Any] = {}
        if effective_keep_recent_count > 0:
            payload["keep_recent_count"] = int(effective_keep_recent_count)
        return self._ov_request(
            "POST",
            f"/api/v1/sessions/{self.ov_session_id}/commit",
            json=payload,
        )

    def compact_session(self) -> dict[str, Any]:
        return self.commit_session(keep_recent_count=0)

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._ov_request("GET", f"/api/v1/tasks/{task_id}")

    def get_session_context(self, token_budget: int = 128_000) -> dict[str, Any]:
        return self._ov_request(
            "GET",
            f"/api/v1/sessions/{self.ov_session_id}/context?token_budget={int(token_budget)}",
        )

    def _send_response(self, message: str) -> str:
        payload: dict[str, Any] = {
            "model": self.openclaw_model,
            "input": message,
            "stream": False,
        }
        if self.user:
            payload["user"] = self.user
        response_json = self._openclaw_request("POST", "/v1/responses", json=payload)
        return extract_response_text(response_json)

    def _inject_turn(self, turn: dict[str, Any]) -> str:
        role = str(turn.get("role", "assistant") or "assistant")
        parts: list[dict[str, Any]] = []

        content = turn.get("content")
        if isinstance(content, str) and content.strip():
            parts.append({"type": "text", "text": content})

        if turn.get("tool_name"):
            tool_output = turn.get("tool_output")
            if isinstance(tool_output, str):
                tool_output_text = tool_output
            else:
                tool_output_text = json.dumps(tool_output, ensure_ascii=False)
            parts.append(
                {
                    "type": "tool",
                    "tool_name": turn["tool_name"],
                    "tool_input": turn.get("tool_input") or {},
                    "tool_output": tool_output_text,
                    "tool_status": turn.get("tool_status") or "completed",
                }
            )

        if not parts:
            raise ValueError(f"turn cannot be injected without content or tool payload: {turn!r}")

        self._ov_request(
            "POST",
            f"/api/v1/sessions/{self.ov_session_id}/messages",
            json={"role": role, "parts": parts},
        )
        return ""

    def _openclaw_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.openclaw_token}"
        headers["Content-Type"] = "application/json"
        headers["X-OpenClaw-Agent-ID"] = self.agent_id
        headers["X-OpenClaw-Session-Key"] = self.session_key

        response = self.openclaw_http.request(
            method,
            f"{self.openclaw_base_url}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", 600),
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def _ov_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["X-API-Key"] = self.ov_api_key
        headers["Content-Type"] = "application/json"
        headers["X-OpenViking-Agent"] = self.agent_id

        response = self.ov_http.request(
            method,
            f"{self.ov_base_url}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", 300),
            **kwargs,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "error":
            error = payload.get("error", {})
            message = error.get("message") or "unknown error"
            raise RuntimeError(f"OpenViking request failed: {message}")
        return payload.get("result", payload)
