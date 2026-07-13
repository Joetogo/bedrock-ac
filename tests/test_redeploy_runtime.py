"""Unit tests for the pure helpers in scripts/redeploy_runtime.py.

The boto3 calls run against live AWS (operator/CI); these tests pin the
logic that decides what gets sent: config parsing/selection, the
schema-preserving update payload, and status reading.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import redeploy_runtime as R  # noqa: E402


def test_parse_env_file_ignores_blanks_and_comments():
    text = "MCP_URL=https://gw/mcp\n\n# a comment\nAWS_REGION=us-east-1\nBAD LINE\n"
    assert R.parse_env_file(text) == {
        "MCP_URL": "https://gw/mcp",
        "AWS_REGION": "us-east-1",
    }


def test_select_config_keeps_only_known_nonempty_keys():
    parsed = {
        "MCP_URL": "https://gw/mcp",
        "OAUTH_SCOPE": "",              # empty -> dropped
        "UNRELATED": "x",              # not a config key -> dropped
        "MEMORY_ID": "mem-1",
    }
    assert R.select_config(parsed) == {"MCP_URL": "https://gw/mcp", "MEMORY_ID": "mem-1"}


def test_build_update_kwargs_preserves_current_schema_and_injects_env():
    current = {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": "ecr/img:1"}},
        "roleArn": "arn:aws:iam::1:role/exec",
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "status": "READY",
    }
    env = {"MCP_URL": "https://gw/mcp"}
    kw = R.build_update_kwargs(current, "rt-1", env)
    assert kw["agentRuntimeId"] == "rt-1"
    assert kw["environmentVariables"] == env
    # image/role/network/protocol are echoed back unchanged
    assert kw["agentRuntimeArtifact"] == current["agentRuntimeArtifact"]
    assert kw["roleArn"] == current["roleArn"]
    assert kw["networkConfiguration"] == current["networkConfiguration"]
    assert kw["protocolConfiguration"] == current["protocolConfiguration"]
    # status is not part of an update payload
    assert "status" not in kw


def test_build_update_kwargs_tolerates_nested_agentRuntime_wrapper():
    current = {"agentRuntime": {
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": "ecr/img:1"}},
        "roleArn": "arn:aws:iam::1:role/exec",
    }}
    kw = R.build_update_kwargs(current, "rt-1", {"MEMORY_ID": "m"})
    assert kw["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"] == "ecr/img:1"
    assert kw["roleArn"] == "arn:aws:iam::1:role/exec"


def test_status_reads_top_level_and_nested():
    assert R._status({"status": "ready"}) == "READY"
    assert R._status({"agentRuntime": {"status": "Updating"}}) == "UPDATING"
    assert R._status({}) == ""


def test_run_launch_aborts_on_nonzero_exit():
    class _Res:
        returncode = 2

    with pytest.raises(SystemExit):
        R.run_launch(runner=lambda cmd: _Res())


def test_run_launch_passes_launch_subcommand_and_extra_args():
    seen = {}

    class _Res:
        returncode = 0

    def fake_runner(cmd):
        seen["cmd"] = cmd
        return _Res()

    R.run_launch(["--auto-update-on-conflict"], runner=fake_runner)
    assert "launch" in seen["cmd"]
    assert seen["cmd"][-1] == "--auto-update-on-conflict"
