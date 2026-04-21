import hashlib
import json
import sys
from pathlib import Path


def _add_benchmark_package_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    benchmark_root = repo_root / "benchmark" / "work_memory"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)


def test_user_turn_posts_to_openclaw_responses_api():
    _add_benchmark_package_to_path()

    from src.openclaw_client import OpenClawBenchmarkClient

    openclaw_http = _FakeSession(
        [
            _FakeResponse(
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ack"}],
                        }
                    ]
                }
            )
        ]
    )
    ov_http = _FakeSession()

    client = OpenClawBenchmarkClient(
        openclaw_base_url="http://gateway",
        openclaw_token="gateway-token",
        ov_base_url="http://ov",
        ov_api_key="ov-key",
        agent_id="bench-agent",
        user="wm-user",
        session_key="agent:bench:test",
        openclaw_http=openclaw_http,
        ov_http=ov_http,
    )

    client.apply_turn({"role": "user", "content": "remember alpha"})

    assert len(openclaw_http.calls) == 1
    call = openclaw_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://gateway/v1/responses"
    assert call["kwargs"]["headers"]["Authorization"] == "Bearer gateway-token"
    assert call["kwargs"]["headers"]["X-OpenClaw-Agent-ID"] == "bench-agent"
    assert call["kwargs"]["headers"]["X-OpenClaw-Session-Key"] == "agent:bench:test"
    assert call["kwargs"]["json"]["model"] == "openclaw"
    assert call["kwargs"]["json"]["input"] == "remember alpha"
    assert call["kwargs"]["json"]["user"] == "wm-user"


def test_tool_turn_posts_to_ov_messages_endpoint():
    _add_benchmark_package_to_path()

    from src.openclaw_client import OpenClawBenchmarkClient

    openclaw_http = _FakeSession()
    ov_http = _FakeSession([_FakeResponse({"status": "ok", "result": {"session_id": "abc"}})])

    client = OpenClawBenchmarkClient(
        openclaw_base_url="http://gateway",
        openclaw_token="gateway-token",
        ov_base_url="http://ov",
        ov_api_key="ov-key",
        agent_id="bench-agent",
        session_key="agent:bench:test",
        openclaw_http=openclaw_http,
        ov_http=ov_http,
    )

    client.apply_turn(
        {
            "role": "assistant",
            "tool_name": "read_config",
            "tool_input": {"path": "config/app.yaml"},
            "tool_output": {"current_env": "staging"},
        }
    )

    expected_session_id = hashlib.sha256(b"agent:bench:test").hexdigest()
    assert len(ov_http.calls) == 1
    call = ov_http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"http://ov/api/v1/sessions/{expected_session_id}/messages"
    payload = call["kwargs"]["json"]
    assert payload["role"] == "assistant"
    assert payload["parts"][0]["type"] == "tool"
    assert payload["parts"][0]["tool_name"] == "read_config"
    assert payload["parts"][0]["tool_input"] == {"path": "config/app.yaml"}
    assert json.loads(payload["parts"][0]["tool_output"]) == {"current_env": "staging"}
    assert payload["parts"][0]["tool_status"] == "completed"


def test_commit_session_posts_keep_recent_count_to_ov():
    _add_benchmark_package_to_path()

    from src.openclaw_client import OpenClawBenchmarkClient

    ov_http = _FakeSession(
        [_FakeResponse({"status": "ok", "result": {"status": "accepted", "task_id": "task-123"}})]
    )

    client = OpenClawBenchmarkClient(
        openclaw_base_url="http://gateway",
        openclaw_token="gateway-token",
        ov_base_url="http://ov",
        ov_api_key="ov-key",
        agent_id="bench-agent",
        session_id="123e4567-e89b-12d3-a456-426614174000",
        keep_recent_count=7,
        openclaw_http=_FakeSession(),
        ov_http=ov_http,
    )

    result = client.commit_session()

    assert result["task_id"] == "task-123"
    assert ov_http.calls[0]["url"] == (
        "http://ov/api/v1/sessions/123e4567-e89b-12d3-a456-426614174000/commit"
    )
    assert ov_http.calls[0]["kwargs"]["json"] == {"keep_recent_count": 7}

