import types
import _shared.clients as clients
from neat_sense import handler as neat
from graph_calls import handler as graph
from correlate import handler as corr


def _ctx(tool):
    cc = types.SimpleNamespace(custom={"bedrockAgentCoreToolName": f"t___{tool}"})
    return types.SimpleNamespace(client_context=types.SimpleNamespace(custom=cc.custom))


def test_neat_list_rooms(monkeypatch):
    monkeypatch.setattr(clients, "neat_get",
        lambda p, params=None: {"rooms": [{"id": "r1", "name": "Room A", "endpointIds": ["e1"]}]})
    # neat.handler imports neat_get into its own namespace
    monkeypatch.setattr(neat, "neat_get", clients.neat_get)
    out = neat.handler({}, _ctx("neat_list_rooms"))
    assert out["ok"] and out["data"]["count"] == 1
    assert out["data"]["rooms"][0]["name"] == "Room A"


def test_neat_room_sensors_requires_id(monkeypatch):
    monkeypatch.setattr(neat, "neat_get", lambda *a, **k: {})
    out = neat.handler({}, _ctx("neat_room_sensors"))
    assert not out["ok"] and "room_id" in out["error"]


def test_neat_room_sensors_downsamples_long_series(monkeypatch):
    readings = [{"co2": 400 + i, "temperature": 21.0, "peopleCount": i % 8}
                for i in range(1500)]
    monkeypatch.setattr(neat, "neat_get", lambda *a, **k: {"readings": readings})
    out = neat.handler({"room_id": "r1", "fromDateTime": "a", "toDateTime": "b"},
                       _ctx("neat_room_sensors"))
    assert out["ok"]
    m = out["data"]["metrics"]
    assert m["sampled"] is True and m["count"] == 1500
    assert len(m["series"]) <= 60
    assert m["stats"]["co2"]["n"] == 1500


def test_graph_list_caps_record_count(monkeypatch):
    value = [{"id": f"c{i}", "startDateTime": "t", "endDateTime": "u"}
             for i in range(400)]
    monkeypatch.setattr(graph, "graph_get", lambda p, params=None: {"value": value})
    out = graph.handler({}, _ctx("graph_list_call_records"))
    assert out["ok"]
    assert out["data"]["count"] == 400                 # exact total preserved
    assert out["data"]["truncated"] is True
    assert len(out["data"]["records"]) == graph._MAX_RECORDS


def test_graph_list_defaults_window_when_missing(monkeypatch):
    seen = {}
    monkeypatch.setattr(graph, "graph_get",
                        lambda p, params=None: seen.update(params=params) or {"value": []})
    out = graph.handler({}, _ctx("graph_list_call_records"))
    assert out["ok"] and out["data"]["count"] == 0
    assert "startDateTime ge" in seen["params"]["$filter"]    # a valid window was synthesized


def test_graph_list_clamps_out_of_range_window(monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(graph, "graph_get", lambda p, params=None: {"value": []})
    out = graph.handler(
        {"fromDateTime": "2000-01-01T00:00:00Z", "toDateTime": "2999-01-01T00:00:00Z"},
        _ctx("graph_list_call_records"))
    assert out["ok"]
    now = datetime.now(timezone.utc)
    frm = graph._parse_iso(out["data"]["window"]["from"])
    to = graph._parse_iso(out["data"]["window"]["to"])
    assert to <= now + timedelta(seconds=5)                  # clamped: not in the future
    assert now - frm <= timedelta(days=30)                   # clamped: within the last 30 days
    assert frm <= to


def test_graph_call_quality(monkeypatch):
    sample = {"startDateTime": "x", "endDateTime": "y", "sessions": [
        {"caller": {"identity": {"user": {"displayName": "Joe"}}},
         "segments": [{"media": [{"label": "audio", "streams": [
             {"averageJitter": 5, "maxJitter": 9, "averagePacketLossRate": 0.01,
              "averageRoundTripTime": 40, "audioCodec": "opus"}]}]}]}]}
    monkeypatch.setattr(graph, "graph_get", lambda p, params=None: sample)
    out = graph.handler({"call_id": "c1"}, _ctx("graph_call_quality"))
    assert out["ok"] and out["data"]["stream_count"] == 1
    assert out["data"]["media_streams"][0]["avg_jitter_ms"] == 5


def test_correlate(monkeypatch):
    def fake_neat(path, params=None):
        if path.endswith("/sensors"):
            return {"readings": [{"co2": 800, "temperature": 22, "humidity": 45,
                                  "peopleCount": 6, "voc": 120}]}
        return {"name": "Room A"}
    def fake_graph(path, params=None):
        if path.endswith("/callRecords"):
            return {"value": [{"id": "c1", "startDateTime": "t", "subject": "Room A sync"}]}
        return {"sessions": [{"segments": [{"media": [{"streams": [
            {"averageJitter": 7, "averagePacketLossRate": 0.02,
             "averageRoundTripTime": 55}]}]}]}]}
    monkeypatch.setattr(corr, "neat_get", fake_neat)
    monkeypatch.setattr(corr, "graph_get", fake_graph)
    out = corr.handler({"room_id": "r1", "fromDateTime": "a", "toDateTime": "b"}, None)
    assert out["ok"]
    assert out["data"]["environment"]["avg_co2"] == 800
    assert out["data"]["calls_matched"] == 1
    assert out["data"]["calls"][0]["avg_jitter_ms"] == 7
