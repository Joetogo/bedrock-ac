#!/usr/bin/env python3
"""Non-interactive AgentCore Runtime redeploy — bakes env onto the runtime
resource so it is permanently operational with no local launch / no --env
juggling. Runs headless (boto3 only, no `agentcore` toolkit console), so it
works from a laptop or CI.

Config lives in SSM Parameter Store (cloud), NOT a local .env. The runtime's
own execution role reads the gateway client SECRET from Secrets Manager at
run time; only its non-secret ARN is stored here.

Typical use:

  # 1) one-time: move config into SSM (from the existing env file or flags)
  python scripts/redeploy_runtime.py --seed-from-env agent/.env.runtime

  # 2) any time: bake the SSM config onto the runtime resource + wait READY
  python scripts/redeploy_runtime.py

  # 2b) shipped new agent code? rebuild the image AND bake env in one command:
  python scripts/redeploy_runtime.py --build

The default (step 2) reads the runtime's CURRENT container image / role /
network / protocol via get_agent_runtime and re-submits them UNCHANGED with
environmentVariables injected — it does not rebuild the image. Ship new agent
code with the toolkit build once, then this keeps env persisted forever.

--build (step 2b) runs `agentcore launch` first (which rebuilds the image but
DROPS env), then immediately re-bakes env — so a single command leaves the
runtime on new code AND correctly configured, with no manual env juggling.

Requires: pip install -r scripts/requirements-deploy.txt
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# The six config keys the agent's load_config() requires/uses. All are
# non-secret (URLs, an ARN, an id); the client secret itself never appears here.
CONFIG_KEYS = (
    "MCP_URL",
    "TOKEN_URL",
    "OAUTH_SCOPE",
    "AWS_REGION",
    "GATEWAY_CLIENT_SECRET_ARN",
    "MEMORY_ID",
)

DEFAULT_RUNTIME_ID = "<runtime-id>"
DEFAULT_SSM_PREFIX = "/neat-graph-bedrock/runtime/"
READY_STATES = {"READY", "ACTIVE"}
FAILED_SUFFIX = "FAILED"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines (ignoring blanks and # comments) into a dict."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def select_config(source: dict[str, str]) -> dict[str, str]:
    """Keep only the recognised CONFIG_KEYS that have a non-empty value."""
    return {k: source[k] for k in CONFIG_KEYS if source.get(k)}


def _status(resp: dict) -> str:
    """Read the runtime status from a get/update response (shape-tolerant)."""
    inner = resp.get("agentRuntime", resp)
    return str(inner.get("status", "")).upper()


def _runtime_field(resp: dict, key: str):
    """Pull a field from a get_agent_runtime response, top-level or nested."""
    inner = resp.get("agentRuntime", resp)
    return inner.get(key)


def seed_ssm(ssm, prefix: str, config: dict[str, str]) -> None:
    for key, val in config.items():
        name = prefix + key
        ssm.put_parameter(Name=name, Value=val, Type="String", Overwrite=True)
        print(f"  put {name}")


def load_ssm(ssm, prefix: str) -> dict[str, str]:
    """Read all CONFIG_KEYS from SSM under prefix."""
    out: dict[str, str] = {}
    missing: list[str] = []
    for key in CONFIG_KEYS:
        name = prefix + key
        try:
            out[key] = ssm.get_parameter(Name=name)["Parameter"]["Value"]
        except ssm.exceptions.ParameterNotFound:
            missing.append(key)
    # OAUTH_SCOPE is the only optional key (load_config defaults it).
    required_missing = [k for k in missing if k != "OAUTH_SCOPE"]
    if required_missing:
        sys.exit(
            f"missing SSM params under {prefix}: {', '.join(required_missing)}\n"
            f"seed them first: python scripts/redeploy_runtime.py "
            f"--seed-from-env agent/.env.runtime"
        )
    return out


def build_update_kwargs(current: dict, runtime_id: str, env: dict[str, str]) -> dict:
    """Re-submit the runtime's current image/role/network with env injected.

    Only environmentVariables changes; everything else is echoed back exactly
    as get_agent_runtime returned it, so we never guess the control-plane
    schema. Missing expected fields fail loud rather than silently dropping.
    """
    kwargs: dict = {"agentRuntimeId": runtime_id, "environmentVariables": env}
    passthrough = {
        "agentRuntimeArtifact": "agentRuntimeArtifact",
        "roleArn": "roleArn",
        "networkConfiguration": "networkConfiguration",
        "protocolConfiguration": "protocolConfiguration",
    }
    for src_key, dst_key in passthrough.items():
        val = _runtime_field(current, src_key)
        if val is not None:
            kwargs[dst_key] = val
    if "agentRuntimeArtifact" not in kwargs:
        sys.exit(
            "get_agent_runtime returned no agentRuntimeArtifact; cannot update "
            "without an image reference. Response keys: "
            + ", ".join(sorted(current.get("agentRuntime", current).keys()))
        )
    return kwargs


def _agentcore_cmd() -> str:
    """Locate the `agentcore` toolkit CLI — prefer the one beside the running
    interpreter (so `python scripts/redeploy_runtime.py` finds the venv's copy
    even when the venv isn't activated), else fall back to PATH."""
    import shutil
    exe_dir = Path(sys.executable).resolve().parent
    for name in ("agentcore.exe", "agentcore"):
        cand = exe_dir / name
        if cand.exists():
            return str(cand)
    return shutil.which("agentcore") or "agentcore"


def run_launch(extra_args: list[str] | None = None, runner=None) -> None:
    """Rebuild + push the container image via `agentcore launch`, headless.

    `agentcore launch` reads the already-written .bedrock_agentcore.yaml, builds
    the image and points the runtime at it — but it does NOT persist
    environmentVariables (that is exactly the drift the bake step after this
    fixes). stdio is inherited so it runs in the caller's real console; a
    non-zero exit aborts before we touch env.
    """
    import subprocess
    cmd = [_agentcore_cmd(), "launch", *(extra_args or [])]
    print(f"[build] running: {' '.join(cmd)}")
    runner = runner or subprocess.run
    result = runner(cmd)
    code = getattr(result, "returncode", 0)
    if code != 0:
        sys.exit(f"`agentcore launch` failed (exit {code}); env NOT baked. "
                 f"Fix the build error and re-run.")
    print("[build] image rebuilt + pushed; baking env onto the runtime...")


def wait_ready(client, runtime_id: str, timeout_s: int = 600, poll_s: int = 10) -> str:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        state = _status(client.get_agent_runtime(agentRuntimeId=runtime_id))
        if state != last:
            print(f"  status: {state}")
            last = state
        if state in READY_STATES:
            return state
        if state.endswith(FAILED_SUFFIX):
            sys.exit(f"runtime update failed (status {state})")
        time.sleep(poll_s)
    sys.exit(f"timed out waiting for runtime to become READY (last: {last})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--runtime-id", default=DEFAULT_RUNTIME_ID,
                    help="AgentCore Runtime id (agent_id from .bedrock_agentcore.yaml)")
    ap.add_argument("--ssm-prefix", default=DEFAULT_SSM_PREFIX)
    ap.add_argument("--seed-from-env", metavar="FILE",
                    help="push KEY=VALUE lines from FILE into SSM, then exit")
    ap.add_argument("--build", action="store_true",
                    help="rebuild+push the image via `agentcore launch` first, "
                         "then bake env — folds both steps into one command")
    ap.add_argument("--launch-arg", action="append", default=[], metavar="ARG",
                    help="extra arg passed through to `agentcore launch` (repeatable)")
    args = ap.parse_args(argv)

    import boto3

    if args.seed_from_env:
        text = open(args.seed_from_env, encoding="utf-8").read()
        config = select_config(parse_env_file(text))
        if not config:
            sys.exit(f"no recognised config keys found in {args.seed_from_env}")
        ssm = boto3.client("ssm", region_name=args.region)
        print(f"seeding {len(config)} params into SSM under {args.ssm_prefix}")
        seed_ssm(ssm, args.ssm_prefix, config)
        print("done. now run without --seed-from-env to bake env onto the runtime.")
        return 0

    if args.build:
        run_launch(args.launch_arg)

    ssm = boto3.client("ssm", region_name=args.region)
    env = load_ssm(ssm, args.ssm_prefix)
    print(f"loaded {len(env)} config values from SSM {args.ssm_prefix}")

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    current = client.get_agent_runtime(agentRuntimeId=args.runtime_id)
    print(f"current runtime status: {_status(current) or '(unknown)'}")

    kwargs = build_update_kwargs(current, args.runtime_id, env)
    print(f"updating runtime {args.runtime_id} with {len(env)} env vars "
          f"(keys: {', '.join(sorted(env))})")
    client.update_agent_runtime(**kwargs)

    state = wait_ready(client, args.runtime_id)
    print(f"\nruntime is {state} — env baked on the resource. "
          f"It will serve every web-console invocation with no further action.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
