"""AgentCore Runtime harness: a Strands agent that answers plain-English
questions about the deployed correlation stack by calling the read-only
AgentCore Gateway tools, with short-term session memory.

Runs as a container on Amazon Bedrock AgentCore Runtime. The entrypoint
receives {"prompt", "sessionId"} and returns {"answer", "sessionId"}.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Mapping

import httpx

DEFAULT_SCOPE = "neat-graph-bedrock-api/invoke"
ACTOR_ID = "operator"

SYSTEM_PROMPT = (
    "You answer questions about meeting-room conditions and Teams call "
    "quality by calling the provided tools. Call records appear ~30 min "
    "after a call ends. When correlating, state correlation is "
    "observational, not causal. Be concise. Use clean, professional "
    "markdown (headings, tables, bold) — do NOT use emojis or decorative "
    "icons anywhere in the answer.\n\n"
    "Visualization: for every answer, first decide the clearest way to "
    "communicate the data — do not default to tables. PROACTIVELY include a "
    "Vega-Lite chart, even when the user did not ask, whenever the answer has "
    "a comparison across categories (counts, inventories, rankings), a metric "
    "over time (a trend), a correlation, or a distribution. If the user "
    "explicitly asks for a chart, graph, plot, or visualization, you MUST "
    "include one. Only omit a chart when the result is a single value, a tiny "
    "lookup, or genuinely non-quantitative. Emit a valid Vega-Lite v5 spec "
    "inside a fenced ```vega-lite code block. Put the data inline under "
    "data.values (never reference an external URL); keep it to ~200 points "
    'and set "width":"container" with a title and axis labels. Do not set a '
    "fixed pixel width or height. ALWAYS accompany a chart with a one- or "
    "two-sentence summary and a markdown data table of the same numbers, so "
    "the answer is useful even if the chart fails. Prefer line for a metric "
    "over time, bar for a comparison across categories, and scatter (point) "
    "for a correlation. Example:\n"
    "```vega-lite\n"
    '{"$schema":"https://vega.github.io/schema/vega-lite/v5.json",'
    '"title":"Temp over time","width":"container",'
    '"data":{"values":[{"t":"2026-07-09T01:00Z","temp":21.1},'
    '{"t":"2026-07-09T02:00Z","temp":21.6}]},"mark":"line",'
    '"encoding":{"x":{"field":"t","type":"temporal","title":"Time"},'
    '"y":{"field":"temp","type":"quantitative","title":"C"}}}\n'
    "```"
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


log = logging.getLogger("agent")

_ROLE_IN = {"USER": "user", "ASSISTANT": "assistant"}


def _extract(event: dict) -> list[tuple[str, str]]:
    """Pull all (role, text) pairs from one memory event payload, in order."""
    out = []
    if not isinstance(event, dict):
        return out
    for item in event.get("payload", []):
        conv = item.get("conversational") if isinstance(item, dict) else None
        if not conv:
            continue
        role = _ROLE_IN.get(str(conv.get("role", "")).upper())
        content = conv.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if role and text is not None:
            out.append((role, text))
    return out


# Prior answers embed huge inline Vega-Lite/JSON chart specs. Replaying those
# verbatim as history was a primary cause of context-window overflow (which in
# turn made the framework trim the user's live question, yielding the "your
# message came through empty" reply). Strip the specs and bound total recall.
_CHART_BLOCK = re.compile(r"```(?:vega-lite|json)\b.*?```", re.DOTALL)
_MAX_RECALL_CHARS = 6000


def _slim_recalled(role: str, text: str) -> str:
    """Drop chart specs from recalled assistant turns; user turns are kept as-is."""
    if role == "assistant":
        return _CHART_BLOCK.sub("[chart omitted from history]", text)
    return text


def _cap_history(messages: list[dict], max_chars: int) -> list[dict]:
    """Keep the most recent turns within a char budget, then ensure the window
    starts on a user turn (Bedrock requires the first message role to be user)."""
    kept: list[dict] = []
    total = 0
    for m in reversed(messages):
        size = len(m["content"][0]["text"])
        if kept and total + size > max_chars:
            break
        kept.append(m)
        total += size
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


def recall_messages(memory_client, memory_id, session_id, max_turns: int = 10,
                    max_chars: int = _MAX_RECALL_CHARS) -> list[dict]:
    if not memory_id:
        return []
    try:
        # NOTE: assumes list_events returns oldest-first; verify ordering at live smoke.
        events = memory_client.list_events(
            memory_id=memory_id, actor_id=ACTOR_ID,
            session_id=session_id, max_results=max_turns * 2)
        messages = []
        for ev in events:
            for role, text in _extract(ev):
                messages.append({"role": role, "content": [{"text": _slim_recalled(role, text)}]})
        return _cap_history(messages, max_chars)
    except Exception as e:  # degrade to stateless
        log.warning("memory recall failed, continuing stateless: %s", e)
        return []


def save_turn(memory_client, memory_id, session_id, user_text, assistant_text) -> None:
    if not memory_id:
        return
    try:
        memory_client.create_event(
            memory_id=memory_id, actor_id=ACTOR_ID, session_id=session_id,
            messages=[(user_text, "USER"), (assistant_text, "ASSISTANT")])
    except Exception as e:  # never block the answer on a memory write
        log.warning("memory save failed, ignoring: %s", e)


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
    try:
        answer_text = str(run(prompt))
    except ContextWindowOverflowException:
        # Last-resort guard: even after the tool-payload caps, a query can pull
        # back more than fits. Return actionable guidance instead of letting the
        # framework silently trim the question and hallucinate an empty prompt.
        log.warning("context overflow for session %s; asking user to narrow scope", session_id)
        answer_text = (
            "Your question pulled back more data than I can analyse in one pass. "
            "Please narrow the scope - for example a shorter time window (such as "
            "a single day), or one specific test, room, or agent - and ask again."
        )

    save_turn(d["memory"], cfg.memory_id, session_id, prompt, answer_text)
    return {"answer": answer_text, "sessionId": session_id}


import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands.types.exceptions import ContextWindowOverflowException
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
