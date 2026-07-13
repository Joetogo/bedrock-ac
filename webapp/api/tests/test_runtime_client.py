import json

import agentcore_client as rc


def test_build_payload_shape():
    assert json.loads(rc.build_payload("hi", "s1")) == {"prompt": "hi", "sessionId": "s1"}


def test_parse_response_answer():
    raw = json.dumps({"answer": "42", "sessionId": "s1"}).encode()
    assert rc.parse_response(raw) == "42"


def test_parse_response_surfaces_error():
    raw = json.dumps({"error": "boom"}).encode()
    assert "boom" in rc.parse_response(raw)


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeClient:
    def __init__(self, answer: str):
        self._answer = answer
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": _FakeBody(json.dumps({"answer": self._answer}).encode())}


def test_invoke_runtime_passes_session_and_returns_answer():
    client = _FakeClient("six rooms")
    out = rc.invoke_runtime(
        "list rooms", "sess-0123456789012345678901234567890",
        runtime_arn="arn:runtime", region="us-east-1", client=client)
    assert out == "six rooms"
    assert client.calls[0]["agentRuntimeArn"] == "arn:runtime"
    assert client.calls[0]["runtimeSessionId"] == "sess-0123456789012345678901234567890"
    assert json.loads(client.calls[0]["payload"]) == {
        "prompt": "list rooms", "sessionId": "sess-0123456789012345678901234567890"}
