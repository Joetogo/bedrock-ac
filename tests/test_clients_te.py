import pytest
import _shared.clients as clients


def test_te_get_sends_bearer_and_returns_payload(monkeypatch):
    monkeypatch.setenv("THOUSANDEYES_SECRET_ARN", "arn:te")
    monkeypatch.setattr(clients, "get_secret", lambda a: {"bearer_token": "TOK"})
    captured = {}

    def fake_http(method, url, headers, body=None, timeout=20):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        return (200, {"tests": []})

    monkeypatch.setattr(clients, "_http", fake_http)
    out = clients.te_get("/tests", {"aid": "9"})
    assert captured["method"] == "GET"
    assert captured["url"].startswith("https://api.thousandeyes.com/v7/tests")
    assert "aid=9" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer TOK"
    assert out == {"tests": []}


def test_te_get_raises_on_error_status(monkeypatch):
    monkeypatch.setenv("THOUSANDEYES_SECRET_ARN", "arn:te")
    monkeypatch.setattr(clients, "get_secret", lambda a: {"bearer_token": "TOK"})
    monkeypatch.setattr(clients, "_http", lambda *a, **k: (403, {"error": "nope"}))
    with pytest.raises(RuntimeError):
        clients.te_get("/tests")
