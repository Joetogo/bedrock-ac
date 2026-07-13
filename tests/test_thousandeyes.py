import types
from thousandeyes import handler as te


def _ctx(tool):
    return types.SimpleNamespace(
        client_context=types.SimpleNamespace(
            custom={"bedrockAgentCoreToolName": f"neat-graph-bedrock-thousandeyes___{tool}"}))


def test_list_tests_alerts(monkeypatch):
    def fake(path, params=None):
        if path == "/tests":
            return {"tests": [{"testId": 1, "testName": "voip"}]}
        return {"alerts": [{"alertId": 7, "active": True}]}
    monkeypatch.setattr(te, "te_get", fake)
    out = te.handler({"fromDateTime": "2026-07-01T00:00:00Z",
                      "toDateTime": "2026-07-01T01:00:00Z"}, _ctx("te_list_tests_alerts"))
    assert out["ok"]
    assert out["data"]["tests"][0]["testName"] == "voip"
    assert out["data"]["alerts"][0]["alertId"] == 7


def test_network_requires_test_id(monkeypatch):
    monkeypatch.setattr(te, "te_get", lambda *a, **k: {})
    out = te.handler({}, _ctx("te_network_results"))
    assert not out["ok"] and "test_id" in out["error"]


def test_voice_results_uses_rtp_server_path_and_startdate(monkeypatch):
    seen = {}

    def fake(path, params=None):
        seen["path"] = path
        seen["params"] = params
        return {"results": [{"mos": 4.1, "jitter": 3, "loss": 0.0}]}
    monkeypatch.setattr(te, "te_get", fake)
    out = te.handler({"test_id": "2", "fromDateTime": "2026-07-01T00:00:00Z",
                      "toDateTime": "2026-07-01T01:00:00Z"}, _ctx("te_voice_results"))
    assert out["ok"]
    assert seen["path"] == "/test-results/2/rtp-server"
    assert seen["params"] == {"startDate": "2026-07-01T00:00:00Z",
                              "endDate": "2026-07-01T01:00:00Z"}
    # small result sets pass through untouched under the "points" key
    assert out["data"]["results"]["sampled"] is False
    assert out["data"]["results"]["points"][0]["mos"] == 4.1


def test_network_results_downsamples_large_series_per_agent(monkeypatch):
    # a week of per-round rows across two agents must not reach the model raw
    rows = ([{"agentName": "edge-1", "roundId": i, "loss": 0.0, "jitter": i % 7}
             for i in range(2000)]
            + [{"agentName": "edge-2", "roundId": i, "loss": 0.0, "jitter": i % 5}
               for i in range(2000)])
    monkeypatch.setattr(te, "te_get", lambda *a, **k: {"results": rows})
    out = te.handler({"test_id": "9", "fromDateTime": "2026-07-02T00:00:00Z",
                      "toDateTime": "2026-07-09T00:00:00Z"}, _ctx("te_network_results"))
    assert out["ok"]
    res = out["data"]["results"]
    assert res["sampled"] is True and res["count"] == 4000
    assert {g["group"] for g in res["groups"]} == {"edge-1", "edge-2"}
    # each agent's returned series is tiny compared to its 2000 raw rows
    assert all(len(g["series"]) <= res["points_per_group"] for g in res["groups"])


def test_path_visualization_path(monkeypatch):
    seen = {}

    def fake(path, params=None):
        seen["path"] = path
        return {"results": []}
    monkeypatch.setattr(te, "te_get", fake)
    out = te.handler({"test_id": "5"}, _ctx("te_path_visualization"))
    assert out["ok"]
    assert seen["path"] == "/test-results/5/path-vis"


def test_unknown_tool():
    out = te.handler({}, _ctx("nope"))
    assert not out["ok"]
