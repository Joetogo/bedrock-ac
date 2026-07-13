import json
import pytest
from agent import agent as A


REQUIRED_ENV = {
    "MCP_URL": "https://gw.example/mcp",
    "TOKEN_URL": "https://cognito.example/oauth2/token",
    "GATEWAY_CLIENT_SECRET_ARN": "neat-graph-bedrock/gateway-client",
    "AWS_REGION": "us-east-1",
}


def test_load_config_reads_required_and_defaults():
    cfg = A.load_config(REQUIRED_ENV)
    assert cfg.mcp_url == "https://gw.example/mcp"
    assert cfg.region == "us-east-1"
    assert cfg.scope == "neat-graph-bedrock-api/invoke"   # default
    assert cfg.memory_id is None
    assert cfg.model_id is None


def test_load_config_missing_required_raises_named():
    env = dict(REQUIRED_ENV)
    del env["MCP_URL"]
    with pytest.raises(RuntimeError, match="MCP_URL"):
        A.load_config(env)


class _FakeSecrets:
    def get_secret_value(self, SecretId):
        assert SecretId == "neat-graph-bedrock/gateway-client"
        return {"SecretString": json.dumps(
            {"client_id": "cid", "client_secret": "csecret"})}


def test_get_gateway_token_posts_client_credentials():
    cfg = A.load_config(REQUIRED_ENV)
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "TOK"}

    def fake_post(url, data=None, auth=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["auth"] = auth
        return _Resp()

    tok = A.get_gateway_token(cfg, _FakeSecrets(), http_post=fake_post)
    assert tok == "TOK"
    assert captured["url"] == cfg.token_url
    assert captured["data"]["grant_type"] == "client_credentials"
    assert captured["data"]["client_id"] == "cid"
    assert captured["auth"] == ("cid", "csecret")


class _FakeMemoryOK:
    def __init__(self):
        self.created = []

    def list_events(self, memory_id, actor_id, session_id, max_results):
        assert memory_id == "mem-1"
        assert actor_id == A.ACTOR_ID
        assert session_id == "s1"
        # two prior messages, API-native shape from the event payload
        return [
            {"payload": [{"conversational": {"role": "USER", "content": {"text": "hi"}}}]},
            {"payload": [{"conversational": {"role": "ASSISTANT", "content": {"text": "hello"}}}]},
        ]

    def create_event(self, memory_id, actor_id, session_id, messages):
        self.created.append((memory_id, actor_id, session_id, messages))


def test_recall_messages_maps_to_bedrock_shape():
    msgs = A.recall_messages(_FakeMemoryOK(), "mem-1", "s1")
    assert msgs == [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [{"text": "hello"}]},
    ]


def test_recall_messages_stateless_when_no_memory_id():
    assert A.recall_messages(_FakeMemoryOK(), None, "s1") == []


def test_recall_messages_degrades_on_error():
    class _Boom:
        def list_events(self, **k): raise RuntimeError("down")
    assert A.recall_messages(_Boom(), "mem-1", "s1") == []


def test_save_turn_writes_two_messages():
    mem = _FakeMemoryOK()
    A.save_turn(mem, "mem-1", "s1", "q", "a")
    assert len(mem.created) == 1
    _, actor, sess, messages = mem.created[0]
    assert actor == A.ACTOR_ID and sess == "s1"
    assert messages == [("q", "USER"), ("a", "ASSISTANT")]


def test_save_turn_noop_without_memory_id():
    mem = _FakeMemoryOK()
    A.save_turn(mem, None, "s1", "q", "a")
    assert mem.created == []


def test_recall_messages_degrades_on_malformed_payload():
    """Malformed event payload that breaks extraction should return [] gracefully."""
    class _MalformedEvents:
        def list_events(self, **k):
            # Return a malformed event that will fail extraction
            return [{"payload": "not-a-list"}]

    msgs = A.recall_messages(_MalformedEvents(), "mem-1", "s1")
    assert msgs == []


def test_save_turn_swallows_create_event_error():
    """create_event errors should be swallowed, not propagate."""
    class _CreateEventBoom:
        def create_event(self, **k):
            raise RuntimeError("memory service down")

    result = A.save_turn(_CreateEventBoom(), "mem-1", "s1", "q", "a")
    assert result is None  # should not raise


def test_recall_messages_extracts_both_messages_from_one_event():
    class _Mem:
        def list_events(self, memory_id, actor_id, session_id, max_results):
            return [{"payload": [
                {"conversational": {"role": "USER", "content": {"text": "q1"}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": "a1"}}},
            ]}]
    assert A.recall_messages(_Mem(), "mem-1", "s1") == [
        {"role": "user", "content": [{"text": "q1"}]},
        {"role": "assistant", "content": [{"text": "a1"}]},
    ]


class _MemWithChart:
    def list_events(self, memory_id, actor_id, session_id, max_results):
        big = "Here is the trend:\n```vega-lite\n{\"data\":\"HUGE...\"}\n```\nDone."
        return [{"payload": [
            {"conversational": {"role": "USER", "content": {"text": "show trend"}}},
            {"conversational": {"role": "ASSISTANT", "content": {"text": big}}},
        ]}]


def test_recall_strips_chart_specs_from_assistant_history():
    msgs = A.recall_messages(_MemWithChart(), "mem-1", "s1")
    asst = msgs[1]["content"][0]["text"]
    assert "vega-lite" not in asst              # spec removed
    assert "chart omitted from history" in asst  # replaced with a marker
    assert msgs[0]["content"][0]["text"] == "show trend"  # user turn untouched


def test_recall_caps_history_and_starts_on_user():
    class _MemBig:
        def list_events(self, memory_id, actor_id, session_id, max_results):
            evs = []
            for i in range(8):
                evs.append({"payload": [
                    {"conversational": {"role": "USER", "content": {"text": f"q{i}"}}},
                    {"conversational": {"role": "ASSISTANT",
                                        "content": {"text": "a" * 2000}}},
                ]})
            return evs
    msgs = A.recall_messages(_MemBig(), "mem-1", "s1", max_chars=5000)
    total = sum(len(m["content"][0]["text"]) for m in msgs)
    assert total <= 5000 + 2000        # bounded (last message can straddle budget)
    assert msgs[0]["role"] == "user"   # window starts on a user turn


def test_answer_returns_guidance_on_context_overflow():
    from strands.types.exceptions import ContextWindowOverflowException

    class _Mem:
        def __init__(self): self.created = []
        def list_events(self, **k): return []
        def create_event(self, **k): self.created.append(k)

    def run_agent(model, tools, system_prompt, messages):
        def _raise(_prompt):
            raise ContextWindowOverflowException("still too big")
        return _raise

    deps = {
        "cfg": A.load_config(dict(REQUIRED_ENV, MEMORY_ID="mem-1")),
        "model": "stub.model", "memory": _Mem(), "tools": [],
        "run_agent": run_agent,
    }
    out = A.answer({"prompt": "everything for the whole month", "sessionId": "s1"}, deps=deps)
    assert "narrow" in out["answer"].lower()
    assert out["sessionId"] == "s1"


def test_resolve_model_prefers_pinned():
    cfg = A.load_config(dict(REQUIRED_ENV, MODEL_ID="pinned.model"))
    assert A.resolve_model(cfg, bedrock_client=None) == "pinned.model"


def test_resolve_model_autoselects_sonnet():
    cfg = A.load_config(REQUIRED_ENV)

    class _BR:
        def list_inference_profiles(self):
            return {"inferenceProfileSummaries": [
                {"inferenceProfileId": "us.anthropic.claude-3-haiku"},
                {"inferenceProfileId": "us.anthropic.claude-sonnet-4-6"},
            ]}
    assert "sonnet" in A.resolve_model(cfg, _BR())


def test_resolve_model_raises_when_none():
    cfg = A.load_config(REQUIRED_ENV)

    class _BR:
        def list_inference_profiles(self):
            return {"inferenceProfileSummaries": []}
    with pytest.raises(RuntimeError, match="no Claude inference profile"):
        A.resolve_model(cfg, _BR())


def _stub_deps(capture):
    """deps bundle wired to fakes so answer() runs with no AWS/MCP.
    capture: dict the agent factory writes the seen messages into."""
    class _Mem:
        def __init__(self): self.created = []
        def list_events(self, **k):
            return [{"payload": [{"conversational":
                    {"role": "USER", "content": {"text": "earlier"}}}]}]
        def create_event(self, **k):
            self.created.append((k["memory_id"], k["actor_id"],
                                 k["session_id"], k["messages"]))

    def run_agent(model, tools, system_prompt, messages):
        capture["messages"] = messages
        return lambda prompt: "STUB ANSWER"

    return {
        "cfg": A.load_config(dict(REQUIRED_ENV, MEMORY_ID="mem-1")),
        "model": "stub.model",
        "memory": _Mem(),
        "tools": [],
        "run_agent": run_agent,
    }


def test_answer_validates_payload():
    with pytest.raises(ValueError, match="prompt"):
        A.answer({"sessionId": "s1"}, deps=_stub_deps({}))


def test_answer_runs_agent_seeds_memory_and_saves():
    capture = {}
    deps = _stub_deps(capture)
    out = A.answer({"prompt": "list rooms", "sessionId": "s1"}, deps=deps)
    assert out == {"answer": "STUB ANSWER", "sessionId": "s1"}
    # prior turn recalled and handed to the agent
    assert capture["messages"] == [
        {"role": "user", "content": [{"text": "earlier"}]}]
    # the new turn was persisted
    assert deps["memory"].created[0][3] == [
        ("list rooms", "USER"), ("STUB ANSWER", "ASSISTANT")]


def test_system_prompt_has_visualization_guidance():
    p = A.SYSTEM_PROMPT.lower()
    assert "vega-lite" in p                        # names the format
    assert "```vega-lite" in A.SYSTEM_PROMPT        # tells it to use a fenced block
    assert "inline" in p                            # inline data only
    assert "table" in p                             # a data table must accompany the chart
    assert "container" in p                         # width: container guidance
    assert "must" in p                              # explicit chart request is mandatory
    assert "emoji" in p                             # no-emoji instruction present
    assert "proactively" in p                       # charts unprompted when data warrants
