"""Unit tests for the shared payload downsampler.

The downsampler is the root-cause fix for context-window overflow: metric
tools used to return raw per-round time series (thousands of rows for a week),
which blew the model's context window. downsample_series compresses any
oversized list into summary stats + a decimated series so tool output stays
compact. Purely derived from the fetched rows - still strictly read-only.
"""
import _shared.clients as clients


def _rows(n, agent="A", base=0):
    return [{"agent": agent, "loss": (i % 5) * 0.1 + base, "latency": i} for i in range(n)]


def test_small_list_passes_through_unchanged():
    rows = _rows(3)
    out = clients.downsample_series(rows, max_points=60)
    assert out["sampled"] is False
    assert out["points"] == rows
    assert out["count"] == 3


def test_non_list_is_returned_untouched():
    out = clients.downsample_series({"not": "a list"}, max_points=60)
    assert out["sampled"] is False
    assert out["points"] == {"not": "a list"}


def test_large_flat_list_is_decimated_with_stats():
    rows = _rows(500)
    out = clients.downsample_series(rows, max_points=50)
    assert out["sampled"] is True
    assert out["count"] == 500
    assert len(out["series"]) <= 50
    # stats computed over ALL 500 rows, not the decimated sample
    assert out["stats"]["latency"]["n"] == 500
    assert out["stats"]["latency"]["min"] == 0
    assert out["stats"]["latency"]["max"] == 499
    # p95 is near the top of a 0..499 ramp
    assert 470 <= out["stats"]["latency"]["p95"] <= 499


def test_decimation_preserves_first_and_last_point():
    rows = _rows(500)
    out = clients.downsample_series(rows, max_points=50)
    assert out["series"][0]["latency"] == 0
    assert out["series"][-1]["latency"] == 499


def test_grouped_by_agent_when_multiple_series():
    rows = _rows(300, agent="A") + _rows(300, agent="B", base=1)
    out = clients.downsample_series(rows, group_by="agent", max_points=60)
    assert out["sampled"] is True
    assert out["by"] == "agent"
    assert out["count"] == 600
    names = {g["group"] for g in out["groups"]}
    assert names == {"A", "B"}
    for g in out["groups"]:
        assert g["count"] == 300
        assert g["series"]                       # each group keeps its own series
        assert g["stats"]["latency"]["n"] == 300


def test_group_key_resolves_nested_agent_dict():
    rows = ([{"agent": {"agentName": "edge-1"}, "loss": i * 0.1, "latency": i}
             for i in range(200)]
            + [{"agent": {"agentName": "edge-2"}, "loss": i * 0.1, "latency": i}
               for i in range(200)])
    out = clients.downsample_series(rows, group_by="agent", max_points=40)
    # nested {"agent": {...}} must resolve to a hashable label, not crash
    assert out["sampled"] is True
    assert {g["group"] for g in out["groups"]} == {"edge-1", "edge-2"}


def test_single_group_falls_back_to_flat():
    rows = _rows(300, agent="only")
    out = clients.downsample_series(rows, group_by="agent", max_points=40)
    # one distinct group -> no point grouping; flat series + stats
    assert "groups" not in out
    assert out["sampled"] is True
    assert len(out["series"]) <= 40
