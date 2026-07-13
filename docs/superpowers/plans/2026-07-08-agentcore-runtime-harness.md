# AgentCore Runtime Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host the existing `ask.py` Bedrock Converse tool-use loop as an Amazon Bedrock AgentCore Runtime agent (Strands SDK) wired to the deployed AgentCore Gateway MCP, so the operator queries the stack in plain English with no local scripts.

**Architecture:** A containerized Strands `Agent` runs on AgentCore Runtime behind a `BedrockAgentCoreApp` entrypoint. Per invocation it mints a Cognito client-credentials token (creds from Secrets Manager), opens a Strands `MCPClient` to the existing gateway, hands the gateway's read-only tools to the Agent, and answers. AgentCore Memory (short-term event store, keyed by `sessionId`) seeds prior turns for multi-turn recall; a memory failure degrades to stateless.

**Tech Stack:** Python 3.12, `strands-agents`, `bedrock-agentcore` (runtime + memory), `bedrock-agentcore-starter-toolkit` (deploy-time containerize/launch), `boto3`, `httpx`, `mcp`. Region us-east-1.

## Global Constraints

- **Region:** us-east-1 (matches gateway, Cognito, Lambdas, secrets). Copy verbatim into every script default.
- **Read-only preserved by construction:** the agent adds NO new tools; it consumes only the existing gateway tools (`neat_* / graph_* / te_* / correlate_*`), all GET-based. No write/delete path is in scope.
- **Upstream creds never in the image/env:** the client→gateway Cognito `client_id`/`client_secret` are read at runtime from Secrets Manager secret `neat-graph-bedrock/gateway-client` (JSON `{"client_id":..., "client_secret":...}`). Only the secret *name/arn* is passed via env.
- **OAuth scope default:** `neat-graph-bedrock-api/invoke` (same as `scripts/agent.py`).
- **System prompt is reused verbatim** from `scripts/ask.py` (room conditions + Teams call quality + ThousandEyes; call records lag ~30 min; correlation is observational, not causal; be concise).
- **Stack outputs are the source of truth:** `MCP_URL` ← gateway URL (from `deploy_gateway.py` output / `.env`), `TOKEN_URL` ← stack output `TokenUrl`, client id ← stack output `AppClientId`, client secret ← `cognito-idp describe-user-pool-client`.
- **Resolved open items (from the spec):**
  1. **Entrypoint contract:** payload `{"prompt": str, "sessionId": str}`, **buffered** (non-streaming) response `{"answer": str, "sessionId": str}`.
  2. **`gateway-client` secret is created by `deploy_runtime.py`** (not SAM), auto-populated from stack outputs + Cognito. Container build/launch uses the **`agentcore` starter-toolkit CLI**; `deploy_runtime.py` owns Memory + secret + execution-role + env config so AgentCore-adjacent resources stay together.
  3. **Memory surface:** short-term **event store** (`create_event` / `list_events`) keyed by `actorId="operator"` + `sessionId`. No summarization strategy.

---

## File Structure

- `agent/__init__.py` — marks the package (empty).
- `agent/agent.py` — the hosted agent: config loading, token minting, memory wrappers, Strands wiring, and the `BedrockAgentCoreApp` entrypoint. One responsibility: answer one question with session memory over the gateway tools.
- `agent/requirements.txt` — runtime container deps only.
- `agent/Dockerfile` — container image for the runtime (python:3.12-slim base).
- `scripts/deploy_runtime.py` — boto3: create/reuse AgentCore Memory, create/populate the `gateway-client` secret, create/reuse the runtime execution role, emit `agent/.env.runtime` for the toolkit, print the `agentcore configure/launch` commands and an example invoke.
- `scripts/invoke_agent.py` — thin CLI wrapper over `bedrock-agentcore invoke-agent-runtime`.
- `scripts/requirements-deploy.txt` — deploy-time deps (`bedrock-agentcore-starter-toolkit`, `boto3`).
- `tests/test_agent_helpers.py` — unit tests for the pure/mocked helpers in `agent/agent.py`.
- `tests/test_invoke_agent.py` — unit tests for `invoke_agent.py` arg/response parsing.

**Note on TDD scope:** the Strands/MCP/Runtime glue is integration code against external SDKs and is validated by live smoke tests (Tasks 5–8), not unit tests. The pure, mockable seams (config, token, memory formatting, response parsing) are unit-tested first (Tasks 2–4, 7). Task 1 pins the exact SDK method surface so later tasks build on verified names, not memory.

---

### Task 1: Scaffold the `agent/` package and pin the SDK contract

**Files:**
- Create: `agent/__init__.py`
- Create: `agent/requirements.txt`
- Create: `scripts/requirements-deploy.txt`
- Create: `agent/_sdk_contract.md` (short notes file capturing verified signatures)

**Interfaces:**
- Produces: a working local venv with the SDKs importable, and `agent/_sdk_contract.md` recording the exact names later tasks depend on: `BedrockAgentCoreApp().entrypoint`, `BedrockAgentCoreApp().run`, `strands.Agent`, `strands.models.BedrockModel`, `strands.tools.mcp.MCPClient` (+ `.list_tools_sync()`), `bedrock_agentcore.memory.MemoryClient` (+ create/list event method names).

- [ ] **Step 1: Create the package marker and requirements files**

`agent/__init__.py`:
```python
```
(empty file)

`agent/requirements.txt`:
```
strands-agents>=0.1
bedrock-agentcore>=0.1
boto3>=1.35
httpx>=0.27
mcp>=1.2
```

`scripts/requirements-deploy.txt`:
```
bedrock-agentcore-starter-toolkit>=0.1
boto3>=1.35
```

- [ ] **Step 2: Install into a local venv**

Run:
```bash
python -m venv .venv-agent
. .venv-agent/Scripts/activate   # Windows Git Bash; use .venv-agent/bin/activate on POSIX
pip install -r agent/requirements.txt -r scripts/requirements-deploy.txt
```
Expected: all packages resolve and install without error.

- [ ] **Step 3: Probe the SDK surface**

Run:
```bash
python -c "
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
app = BedrockAgentCoreApp()
print('entrypoint', hasattr(app, 'entrypoint'), 'run', hasattr(app, 'run'))
print('MemoryClient methods:', [m for m in dir(MemoryClient) if not m.startswith('_')])
print('MCPClient methods:', [m for m in dir(MCPClient) if not m.startswith('_')])
"
```
Expected: prints `entrypoint True run True`, and method lists that include an event-create and event-list method on `MemoryClient` (e.g. `create_event`, `list_events`) and `list_tools_sync` on `MCPClient`.

- [ ] **Step 4: Record the verified contract**

Create `agent/_sdk_contract.md` capturing the EXACT method names printed in Step 3 (memory create-event, memory list-events, memory create-resource, MCP list-tools). Later tasks reference this file; if a name differs from what this plan assumes (`create_event`, `list_events`, `create_memory_and_wait`), use the name from this file and keep the wrapper functions in `agent/agent.py` as the single place that touches these SDK calls.

- [ ] **Step 5: Commit**

```bash
git add agent/__init__.py agent/requirements.txt scripts/requirements-deploy.txt agent/_sdk_contract.md
git commit -m "chore: scaffold agent package and pin AgentCore/Strands SDK contract"
```

---

### Task 2: Config loading and gateway token minting (TDD)

**Files:**
- Create: `agent/agent.py`
- Create: `tests/test_agent_helpers.py`

**Interfaces:**
- Produces:
  - `load_config(env: Mapping[str, str] = os.environ) -> Config` where `Config` is a dataclass with fields `mcp_url: str`, `token_url: str`, `scope: str`, `region: str`, `gateway_secret_id: str`, `memory_id: str | None`, `model_id: str | None`. Missing required var raises `RuntimeError` naming the var.
  - `get_gateway_token(cfg: Config, secrets_client, http_post=httpx.post) -> str` — reads `{"client_id","client_secret"}` from `cfg.gateway_secret_id` via `secrets_client.get_secret_value`, posts client-credentials to `cfg.token_url`, returns `access_token`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_helpers.py`:
```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_agent_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (no `agent.agent` yet).

- [ ] **Step 3: Write the minimal implementation**

`agent/agent.py`:
```python
"""AgentCore Runtime harness: a Strands agent that answers plain-English
questions about the deployed correlation stack by calling the read-only
AgentCore Gateway tools, with short-term session memory.

Runs as a container on Amazon Bedrock AgentCore Runtime. The entrypoint
receives {"prompt", "sessionId"} and returns {"answer", "sessionId"}.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

import httpx

DEFAULT_SCOPE = "neat-graph-bedrock-api/invoke"
ACTOR_ID = "operator"

SYSTEM_PROMPT = (
    "You answer questions about meeting-room conditions and Teams call "
    "quality by calling the provided tools. Call records appear ~30 min "
    "after a call ends. When correlating, state correlation is "
    "observational, not causal. Be concise."
)


@dataclass
class Config:
    mcp_url: str
    token_url: str
    scope: str
    region: str
    gateway_secret_id: str
    memory_id: str | None
    model_id: str | None


def _require(env: Mapping[str, str], key: str) -> str:
    val = env.get(key)
    if not val:
        raise RuntimeError(f"missing required env var: {key}")
    return val


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    return Config(
        mcp_url=_require(env, "MCP_URL"),
        token_url=_require(env, "TOKEN_URL"),
        scope=env.get("OAUTH_SCOPE", DEFAULT_SCOPE),
        region=_require(env, "AWS_REGION"),
        gateway_secret_id=_require(env, "GATEWAY_CLIENT_SECRET_ARN"),
        memory_id=env.get("MEMORY_ID") or None,
        model_id=env.get("MODEL_ID") or None,
    )


def get_gateway_token(cfg: Config, secrets_client, http_post=httpx.post) -> str:
    sec = json.loads(secrets_client.get_secret_value(
        SecretId=cfg.gateway_secret_id)["SecretString"])
    cid, csecret = sec["client_id"], sec["client_secret"]
    r = http_post(
        cfg.token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": csecret,
            "scope": cfg.scope,
        },
        auth=(cid, csecret),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_agent_helpers.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add agent/agent.py tests/test_agent_helpers.py
git commit -m "feat: agent config loading and gateway token minting"
```

---

### Task 3: Memory recall/save wrappers (TDD)

**Files:**
- Modify: `agent/agent.py`
- Modify: `tests/test_agent_helpers.py`

**Interfaces:**
- Consumes: `Config`, `ACTOR_ID` from Task 2.
- Produces:
  - `recall_messages(memory_client, memory_id: str | None, session_id: str, max_turns: int = 10) -> list[dict]` — returns prior turns as Bedrock/Strands messages `[{"role","content":[{"text"}]}]`, oldest first. Returns `[]` if `memory_id` is falsy or on any exception (degrade to stateless).
  - `save_turn(memory_client, memory_id: str | None, session_id: str, user_text: str, assistant_text: str) -> None` — persists the two-message turn; swallows exceptions (log-and-continue). No-op if `memory_id` is falsy.

**Note:** these two functions are the ONLY code that calls `MemoryClient` event methods. Use the exact method names recorded in `agent/_sdk_contract.md` (this plan assumes `list_events` and `create_event`); if they differ, change them here only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_helpers.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_agent_helpers.py -k "recall or save_turn" -v`
Expected: FAIL — `AttributeError: module 'agent.agent' has no attribute 'recall_messages'`.

- [ ] **Step 3: Implement**

Append to `agent/agent.py`:
```python
import logging

log = logging.getLogger("agent")

_ROLE_IN = {"USER": "user", "ASSISTANT": "assistant"}


def _extract(event: dict) -> tuple[str, str] | None:
    """Pull (role, text) from one memory event payload item; None if unusable."""
    for item in event.get("payload", []):
        conv = item.get("conversational")
        if not conv:
            continue
        role = _ROLE_IN.get(conv.get("role", "").upper())
        text = (conv.get("content") or {}).get("text")
        if role and text is not None:
            return role, text
    return None


def recall_messages(memory_client, memory_id, session_id, max_turns: int = 10) -> list[dict]:
    if not memory_id:
        return []
    try:
        events = memory_client.list_events(
            memory_id=memory_id, actor_id=ACTOR_ID,
            session_id=session_id, max_results=max_turns * 2)
    except Exception as e:  # degrade to stateless
        log.warning("memory recall failed, continuing stateless: %s", e)
        return []
    messages = []
    for ev in events:
        got = _extract(ev)
        if got:
            role, text = got
            messages.append({"role": role, "content": [{"text": text}]})
    return messages


def save_turn(memory_client, memory_id, session_id, user_text, assistant_text) -> None:
    if not memory_id:
        return
    try:
        memory_client.create_event(
            memory_id=memory_id, actor_id=ACTOR_ID, session_id=session_id,
            messages=[(user_text, "USER"), (assistant_text, "ASSISTANT")])
    except Exception as e:  # never block the answer on a memory write
        log.warning("memory save failed, ignoring: %s", e)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_agent_helpers.py -v`
Expected: PASS (all tests, including Task 2's).

- [ ] **Step 5: Commit**

```bash
git add agent/agent.py tests/test_agent_helpers.py
git commit -m "feat: session memory recall/save wrappers with stateless degradation"
```

---

### Task 4: Model resolution + the `answer()` orchestration (TDD)

**Files:**
- Modify: `agent/agent.py`
- Modify: `tests/test_agent_helpers.py`

**Interfaces:**
- Consumes: `Config`, `get_gateway_token`, `recall_messages`, `save_turn`, `SYSTEM_PROMPT` from Tasks 2–3.
- Produces:
  - `resolve_model(cfg: Config, bedrock_client) -> str` — returns `cfg.model_id` if set, else auto-selects a Claude inference profile (prefer newest Sonnet), raising `RuntimeError` if none. Mirrors `scripts/ask.py::resolve_model`.
  - `answer(payload: dict, *, deps=None) -> dict` — validates `payload["prompt"]`/`payload["sessionId"]`, runs the agent seeded with recalled messages, saves the turn, returns `{"answer": str, "sessionId": str}`. `deps` is an injectable dict of clients/factories for testing; when `None`, real clients are built via `_build_deps()` (Task 5). The agent runner is `deps["run_agent"](model, tools, system_prompt, messages) -> callable(prompt) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_helpers.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_agent_helpers.py -k "resolve_model or answer" -v`
Expected: FAIL — `resolve_model` / `answer` not defined.

- [ ] **Step 3: Implement**

Append to `agent/agent.py`:
```python
_MODEL_PREFS = ("claude-sonnet-4-6", "claude-sonnet-4", "claude-sonnet", "claude")


def resolve_model(cfg: Config, bedrock_client) -> str:
    if cfg.model_id:
        return cfg.model_id
    profiles = bedrock_client.list_inference_profiles().get(
        "inferenceProfileSummaries", [])
    ids = [p["inferenceProfileId"] for p in profiles]
    for pref in _MODEL_PREFS:
        for pid in ids:
            if pref in pid:
                return pid
    raise RuntimeError(
        "no Claude inference profile found in this account/region; "
        "enable model access or set MODEL_ID")


def answer(payload: dict, *, deps=None) -> dict:
    prompt = payload.get("prompt")
    session_id = payload.get("sessionId")
    if not prompt:
        raise ValueError("payload missing required field: prompt")
    if not session_id:
        raise ValueError("payload missing required field: sessionId")

    d = deps if deps is not None else _build_deps()
    cfg = d["cfg"]
    prior = recall_messages(d["memory"], cfg.memory_id, session_id)

    run = d["run_agent"](d["model"], d["tools"], SYSTEM_PROMPT, prior)
    answer_text = str(run(prompt))

    save_turn(d["memory"], cfg.memory_id, session_id, prompt, answer_text)
    return {"answer": answer_text, "sessionId": session_id}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_agent_helpers.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add agent/agent.py tests/test_agent_helpers.py
git commit -m "feat: model resolution and answer() orchestration with injectable deps"
```

---

### Task 5: Real deps builder, Strands wiring, and the Runtime entrypoint

**Files:**
- Modify: `agent/agent.py`
- Create: `agent/Dockerfile`

**Interfaces:**
- Consumes: everything from Tasks 2–4.
- Produces: `_build_deps() -> dict` (real boto3/Strands/MCP clients) and module-level `app` / `invoke` entrypoint. `run_agent(model, _tools, system_prompt, messages)` opens the MCP client, lists tools, builds `Agent(model=BedrockModel(...), tools=..., system_prompt=..., messages=prior)`, and returns a callable that runs the prompt and returns text.

This task is validated by a **local live smoke test** against the deployed gateway (Step 3), not a unit test — it wires external SDKs.

- [ ] **Step 1: Implement the real deps builder + entrypoint**

Append to `agent/agent.py`:
```python
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client


def _make_run_agent(cfg: Config, token: str):
    """Return run_agent(model, tools_ignored, system_prompt, messages)->callable.

    Opens a fresh MCP session per prompt (tokens are short-lived), lists the
    gateway tools, and runs a Strands Agent seeded with prior-turn messages.
    """
    headers = {"Authorization": f"Bearer {token}"}

    def run_agent(model, _tools, system_prompt, messages):
        def _run(prompt):
            mcp = MCPClient(lambda: streamablehttp_client(cfg.mcp_url, headers=headers))
            with mcp:
                tools = mcp.list_tools_sync()
                agent = Agent(
                    model=BedrockModel(model_id=model, region_name=cfg.region),
                    tools=tools,
                    system_prompt=system_prompt,
                    messages=list(messages),
                )
                return str(agent(prompt))
        return _run

    return run_agent


def _build_deps() -> dict:
    cfg = load_config()
    secrets = boto3.client("secretsmanager", region_name=cfg.region)
    bedrock = boto3.client("bedrock", region_name=cfg.region)
    token = get_gateway_token(cfg, secrets)
    model = resolve_model(cfg, bedrock)
    memory = MemoryClient(region_name=cfg.region)
    return {
        "cfg": cfg,
        "model": model,
        "memory": memory,
        "tools": None,          # discovered per-prompt inside run_agent
        "run_agent": _make_run_agent(cfg, token),
    }


app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    try:
        return answer(payload)
    except Exception as e:               # return a clean error, not a stack trace
        log.exception("invoke failed")
        return {"error": str(e), "sessionId": payload.get("sessionId")}


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 2: Write the Dockerfile**

`agent/Dockerfile`:
```dockerfile
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/ ./agent/
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "agent.agent"]
```

- [ ] **Step 3: Local live smoke test against the deployed gateway**

Run `answer()` directly (bypassing the HTTP server) to confirm tool discovery + a real Neat answer. **Requires the `gateway-client` secret to exist — do Task 6 Step 2 first if it doesn't.**

Run:
```bash
export MCP_URL="<gateway MCP url>"
export TOKEN_URL="<stack output TokenUrl>"
export AWS_REGION="us-east-1"
export GATEWAY_CLIENT_SECRET_ARN="neat-graph-bedrock/gateway-client"
export OAUTH_SCOPE="neat-graph-bedrock-api/invoke"
# MEMORY_ID intentionally unset -> stateless, isolates the tool path
python -c "
from agent.agent import answer
print(answer({'prompt': 'List the Neat rooms.', 'sessionId': 'smoke-1'}))
"
```
Expected: prints `{'answer': '... 6 rooms ...', 'sessionId': 'smoke-1'}`. Read-only check: the run only ever calls `neat_*/graph_*/te_*/correlate_*` tools.

- [ ] **Step 4: Commit**

```bash
git add agent/agent.py agent/Dockerfile
git commit -m "feat: Strands+MCP wiring and AgentCore Runtime entrypoint"
```

---

### Task 6: `deploy_runtime.py` — Memory, gateway-client secret, exec role, launch config

**Files:**
- Create: `scripts/deploy_runtime.py`

**Interfaces:**
- Consumes: SAM stack outputs (`TokenUrl`, `AppClientId`, `UserPoolId`), the gateway MCP URL (arg), the Cognito app-client secret (via `describe_user_pool_client`).
- Produces (all idempotent): the populated `gateway-client` secret (ARN), an AgentCore **Memory** resource (id), a runtime **execution role** (ARN) with least-privilege policy, and `agent/.env.runtime` for the toolkit; prints the `agentcore configure`/`launch` and example invoke commands.

This is orchestration validated by running it (Step 2), not a unit test.

- [ ] **Step 1: Write the script**

`scripts/deploy_runtime.py`:
```python
#!/usr/bin/env python3
"""Provision the AgentCore Runtime harness' AWS-side resources (idempotent).

Mirrors scripts/deploy_gateway.py conventions: reads SAM stack outputs, then
uses boto3 to create/reuse the gateway-client secret, an AgentCore Memory
resource, and a runtime execution role. Container build + runtime creation is
done by the `agentcore` starter-toolkit CLI using the emitted agent/.env.runtime.

  python scripts/deploy_runtime.py --stack neat-graph-bedrock --region us-east-1 \
      --mcp-url https://...gateway.../mcp

Requires: pip install -r scripts/requirements-deploy.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3

SECRET_NAME = "neat-graph-bedrock/gateway-client"
MEMORY_NAME = "neatGraphBedrockSessionMemory"


def stack_outputs(cf, stack: str) -> dict[str, str]:
    out = cf.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return {o["OutputKey"]: o["OutputValue"] for o in out}


def ensure_gateway_client_secret(sm, cognito, user_pool_id, client_id) -> str:
    desc = cognito.describe_user_pool_client(
        UserPoolId=user_pool_id, ClientId=client_id)["UserPoolClient"]
    client_secret = desc.get("ClientSecret")
    if not client_secret:
        sys.exit("app client has no secret; regenerate the Cognito app client "
                 "with a secret enabled.")
    payload = json.dumps({"client_id": client_id, "client_secret": client_secret})
    try:
        sm.create_secret(Name=SECRET_NAME, SecretString=payload)
        print(f"created secret {SECRET_NAME}")
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=SECRET_NAME, SecretString=payload)
        print(f"updated secret {SECRET_NAME}")
    return sm.describe_secret(SecretId=SECRET_NAME)["ARN"]


def ensure_memory(region) -> str:
    from bedrock_agentcore.memory import MemoryClient
    mc = MemoryClient(region_name=region)
    for m in mc.list_memories():
        if m.get("name") == MEMORY_NAME or str(m.get("id", "")).startswith(MEMORY_NAME):
            mid = m.get("id") or m.get("memoryId")
            print(f"reusing memory {mid}")
            return mid
    created = mc.create_memory_and_wait(
        name=MEMORY_NAME,
        description="Short-term per-session conversation memory for the "
                    "neat-graph-bedrock runtime agent.",
        strategies=[],                 # short-term event store only
        event_expiry_days=7,
    )
    mid = created.get("id") or created.get("memoryId")
    print(f"created memory {mid}")
    return mid


def ensure_exec_role(iam, region, account_id, secret_arn, memory_id) -> str:
    role_name = "neat-graph-bedrock-runtime-exec"
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
             "Resource": secret_arn},
            {"Effect": "Allow", "Action": [
                "bedrock:Converse", "bedrock:InvokeModel",
                "bedrock:ListInferenceProfiles"], "Resource": "*"},
            {"Effect": "Allow", "Action": [
                "bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetMemory", "bedrock-agentcore:RetrieveMemoryRecords"],
             "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/{memory_id}"},
            {"Effect": "Allow", "Action": [
                "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "*"},
        ],
    }
    try:
        iam.create_role(RoleName=role_name,
                        AssumeRolePolicyDocument=json.dumps(trust),
                        Description="Execution role for neat-graph-bedrock AgentCore Runtime")
        print(f"created role {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=role_name,
                                      PolicyDocument=json.dumps(trust))
        print(f"reusing role {role_name}")
    iam.put_role_policy(RoleName=role_name, PolicyName="runtime-inline",
                        PolicyDocument=json.dumps(policy))
    return iam.get_role(RoleName=role_name)["Role"]["Arn"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--mcp-url", required=True, help="gateway MCP URL")
    args = ap.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    sm = boto3.client("secretsmanager", region_name=args.region)
    cognito = boto3.client("cognito-idp", region_name=args.region)
    iam = boto3.client("iam")
    account_id = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]

    o = stack_outputs(cf, args.stack)
    secret_arn = ensure_gateway_client_secret(
        sm, cognito, o["UserPoolId"], o["AppClientId"])
    memory_id = ensure_memory(args.region)
    role_arn = ensure_exec_role(iam, args.region, account_id, secret_arn, memory_id)

    env_lines = [
        f"MCP_URL={args.mcp_url}",
        f"TOKEN_URL={o['TokenUrl']}",
        "OAUTH_SCOPE=neat-graph-bedrock-api/invoke",
        f"AWS_REGION={args.region}",
        f"GATEWAY_CLIENT_SECRET_ARN={secret_arn}",
        f"MEMORY_ID={memory_id}",
    ]
    env_path = Path(__file__).resolve().parent.parent / "agent" / ".env.runtime"
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"\nwrote {env_path}")

    print("\nExecution role ARN:", role_arn)
    print("\nNext: build + launch the container with the starter toolkit:")
    print("  agentcore configure --entrypoint agent/agent.py \\")
    print(f"      --execution-role {role_arn} \\")
    print("      --env-file agent/.env.runtime --region", args.region)
    print("  agentcore launch")
    print("\nThen invoke:")
    print('  python scripts/invoke_agent.py "List the Neat rooms." --session demo-1')
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it (live)**

Run:
```bash
python scripts/deploy_runtime.py --stack neat-graph-bedrock --region us-east-1 \
    --mcp-url "<gateway MCP url>"
```
Expected: prints created/reused for secret, memory, role; writes `agent/.env.runtime`; prints the `agentcore configure/launch` commands. Confirm the secret now holds real `client_id`/`client_secret` (this unblocks Task 5 Step 3 if it hadn't run yet).

- [ ] **Step 3: Commit**

```bash
git add scripts/deploy_runtime.py
git commit -m "feat: deploy_runtime.py provisions memory, gateway-client secret, exec role"
```

---

### Task 7: `invoke_agent.py` CLI wrapper (TDD + live)

**Files:**
- Create: `scripts/invoke_agent.py`
- Create: `tests/test_invoke_agent.py`

**Interfaces:**
- Produces:
  - `build_payload(prompt: str, session: str) -> bytes` — JSON-encodes `{"prompt","sessionId"}`.
  - `parse_response(raw: bytes) -> str` — extracts `answer` (or surfaces `error`) from the runtime response body.
  - `main()` — parses `prompt` + `--session` (+ `--runtime-arn`/`--region`), calls `bedrock-agentcore invoke_agent_runtime`, prints the answer.

- [ ] **Step 1: Write the failing tests**

`tests/test_invoke_agent.py`:
```python
import json
import importlib.util
from pathlib import Path

# Load scripts/invoke_agent.py without requiring a scripts package.
_spec = importlib.util.spec_from_file_location(
    "invoke_agent",
    Path(__file__).resolve().parent.parent / "scripts" / "invoke_agent.py")
I = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(I)


def test_build_payload_shape():
    assert json.loads(I.build_payload("hi", "s1")) == {"prompt": "hi", "sessionId": "s1"}


def test_parse_response_answer():
    raw = json.dumps({"answer": "42", "sessionId": "s1"}).encode()
    assert I.parse_response(raw) == "42"


def test_parse_response_surfaces_error():
    raw = json.dumps({"error": "boom", "sessionId": "s1"}).encode()
    assert "boom" in I.parse_response(raw)
```
(The `importlib` loader matches the repo's existing `sys.path`-free test style and avoids adding a `scripts/__init__.py`. If `tests/conftest.py` already puts `scripts/` on `sys.path`, a plain `from invoke_agent import ...` is equivalent — either is fine.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_invoke_agent.py -v`
Expected: FAIL — file `scripts/invoke_agent.py` does not exist yet.

- [ ] **Step 3: Implement**

`scripts/invoke_agent.py`:
```python
#!/usr/bin/env python3
"""Invoke the deployed AgentCore Runtime agent from the CLI.

  python scripts/invoke_agent.py "List the Neat rooms." --session demo-1

Runtime ARN is read from --runtime-arn or the AGENT_RUNTIME_ARN env var
(printed by `agentcore launch`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import boto3


def build_payload(prompt: str, session: str) -> bytes:
    return json.dumps({"prompt": prompt, "sessionId": session}).encode()


def parse_response(raw: bytes) -> str:
    data = json.loads(raw.decode() or "{}")
    if "error" in data and "answer" not in data:
        return f"[agent error] {data['error']}"
    return data.get("answer", raw.decode())


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows cp1252 + emoji
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--session", default="cli-default")
    ap.add_argument("--runtime-arn", default=os.environ.get("AGENT_RUNTIME_ARN"))
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()
    if not args.runtime_arn:
        sys.exit("set --runtime-arn or AGENT_RUNTIME_ARN (from `agentcore launch`)")

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=args.runtime_arn,
        runtimeSessionId=args.session,
        payload=build_payload(args.prompt, args.session),
    )
    body = resp["response"].read() if hasattr(resp["response"], "read") else resp["response"]
    print(parse_response(body if isinstance(body, bytes) else str(body).encode()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_invoke_agent.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/invoke_agent.py tests/test_invoke_agent.py
git commit -m "feat: invoke_agent.py CLI wrapper for the runtime agent"
```

---

### Task 8: Deploy, end-to-end verification, and docs

**Files:**
- Modify: `README.md`
- Modify: `brain/00-hub.md`, `brain/phases.md`

**Interfaces:** consumes the deployed runtime from Tasks 5–7.

- [ ] **Step 1: Build and launch the container**

Run:
```bash
agentcore configure --entrypoint agent/agent.py \
    --execution-role <role ARN from deploy_runtime.py> \
    --env-file agent/.env.runtime --region us-east-1
agentcore launch
```
Expected: CodeBuild → ECR → runtime created; prints the runtime ARN. Export it: `export AGENT_RUNTIME_ARN=<arn>`.

- [ ] **Step 2: E2E test 1 — Neat answer**

Run: `python scripts/invoke_agent.py "List the Neat rooms." --session e2e-1`
Expected: the 6 rooms are listed.

- [ ] **Step 3: E2E test 2 — memory-dependent follow-up (same session)**

Run: `python scripts/invoke_agent.py "Now the CO2 and people count for that first room." --session e2e-1`
Expected: resolves "that first room" from the prior turn (memory recall working) and returns telemetry.

- [ ] **Step 4: E2E test 3 — ThousandEyes**

Run: `python scripts/invoke_agent.py "List a few ThousandEyes tests." --session e2e-2`
Expected: returns real tests.

- [ ] **Step 5: Read-only assertion**

From the Task 5 Step 3 smoke output (or by inspecting the discovered tool list), confirm the tool set contains ONLY `neat_* / graph_* / te_* / correlate_*` — no create/update/delete verbs.

- [ ] **Step 6: Update docs**

In `README.md` add a "Hosted agent (AgentCore Runtime)" section: prerequisites (`pip install -r scripts/requirements-deploy.txt`), `deploy_runtime.py`, `agentcore configure/launch`, and `invoke_agent.py` usage. In `brain/phases.md` mark the Runtime harness phase done; in `brain/00-hub.md` flip the Runtime harness line under Live status to ✅ with the runtime ARN.

- [ ] **Step 7: Commit**

```bash
git add README.md brain/00-hub.md brain/phases.md
git commit -m "docs: document and record the deployed AgentCore Runtime harness"
```

---

## Self-Review

**Spec coverage:**
- Goal (host ask.py loop as Runtime agent, plain English, no local scripts) → Tasks 4–5, 7–8. ✓
- `agent/agent.py` (BedrockAgentCoreApp entrypoint, Cognito token, MCPClient, tools, Memory, system prompt, returns text) → Tasks 2–5. ✓
- `agent/requirements.txt` → Task 1. ✓
- `scripts/deploy_runtime.py` (Memory + Runtime + exec role, idempotent) → Task 6 (+ container via toolkit in Task 8). ✓
- `scripts/invoke_agent.py` → Task 7. ✓
- Auth & read-only guarantee (SigV4 operator→runtime is the default inbound; secret-based gateway creds; read-only by construction) → Global Constraints + Task 6 role + Task 8 Step 5. ✓
- Error handling (clean token/gateway errors; memory degrades to stateless; model resolution fallback) → Task 3 (degrade), Task 4 (resolve raise), Task 5 (entrypoint try/except). ✓
- Data flow (recall → agent loop → save → return) → Task 4 `answer()`. ✓
- Testing (local pre-deploy, post-deploy invoke ×3, read-only assertion) → Tasks 5, 8. ✓
- Region us-east-1 → Global Constraints + script defaults. ✓
- Open items resolved (entrypoint contract; secret via script; memory event surface) → Global Constraints. ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output. The only deliberate "verify against installed SDK" is Task 1, which produces a concrete recorded contract that downstream memory-wrapper tasks (3, 6) reference — the assumed names (`create_event`, `list_events`, `create_memory_and_wait`, `list_tools_sync`) are stated explicitly so a mismatch is a one-line change in a known place.

**Type consistency:** `Config` fields are used identically across Tasks 2–6. `answer(payload, *, deps)` signature matches its callers (entrypoint Task 5, tests Task 4). `run_agent(model, tools, system_prompt, messages) -> callable(prompt) -> str` is defined identically in Task 4's stub and Task 5's implementation. `recall_messages`/`save_turn` signatures match between Task 3 and their use in Task 4. Secret shape `{client_id, client_secret}` is consistent between `get_gateway_token` (Task 2) and `ensure_gateway_client_secret` (Task 6). `MEMORY_NAME`/`SECRET_NAME` constants are single-sourced in `deploy_runtime.py`.

**Known risk (flagged):** exact `MemoryClient` method names and AgentCore Runtime container conventions vary by SDK version. Task 1 pins them before any dependent code is written, and all memory SDK calls are isolated to two wrappers (`recall_messages`/`save_turn`) plus `ensure_memory`, so drift is contained to known places.
