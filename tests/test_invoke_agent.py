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
