"""Tests for the Semantic Scholar client error-path handling.

We specifically test the cases that previously corrupted output:
- 429 rate-limited -> RATE_LIMITED status (not silent -1 -> treated as 0)
- network error    -> NETWORK_ERROR status
- 5xx              -> HTTP_ERROR status
- OK               -> total + papers preserved

Uses a fake session so no network I/O.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

from roboscout.semantic_scholar import S2Status, SemanticScholarClient


def _fake_response(status_code: int, body=""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 400
    if isinstance(body, dict):
        resp.json.return_value = body
        resp.text = json.dumps(body)
    else:
        resp.json.side_effect = ValueError("not json")
        resp.text = body or ""
    return resp


@pytest.fixture
def client(monkeypatch):
    c = SemanticScholarClient(api_key="test", rate_limit_delay=0)
    # Speed up the retry loop
    monkeypatch.setattr("semantic_scholar.time.sleep", lambda *_: None)
    return c


def test_ok_response_preserves_total_and_papers(client):
    client.session.get = MagicMock(
        return_value=_fake_response(
            200, {"total": 123, "data": [{"title": "A"}, {"title": "B"}]}
        )
    )
    r = client.search_relevance("neural networks", limit=2)
    assert r.ok
    assert r.status is S2Status.OK
    assert r.total == 123
    assert len(r.papers) == 2


def test_rate_limit_eventually_gives_up(client):
    client.session.get = MagicMock(return_value=_fake_response(429, "rate limited"))
    r = client.search_relevance("x")
    assert not r.ok
    assert r.status is S2Status.RATE_LIMITED
    assert r.total == 0
    assert r.papers == []


def test_network_error(client):
    client.session.get = MagicMock(side_effect=requests.ConnectionError("dns"))
    r = client.search_relevance("x")
    assert r.status is S2Status.NETWORK_ERROR
    assert not r.ok


def test_timeout(client):
    client.session.get = MagicMock(side_effect=requests.Timeout("slow"))
    r = client.search_relevance("x")
    assert r.status is S2Status.TIMEOUT


def test_5xx_is_http_error(client):
    client.session.get = MagicMock(return_value=_fake_response(503, "down"))
    r = client.search_relevance("x")
    # 5xx is retryable in our loop; after max retries it should surface.
    assert r.status is S2Status.HTTP_ERROR


def test_legacy_get_top_papers_tuple(client):
    """Back-compat shim: (-1, []) on error, (count, papers) on OK."""
    client.session.get = MagicMock(side_effect=requests.ConnectionError("dns"))
    count, papers = client.get_top_papers("x")
    assert count == -1
    assert papers == []

    client.session.get = MagicMock(
        return_value=_fake_response(200, {"total": 5, "data": [{"title": "A"}]})
    )
    count, papers = client.get_top_papers("x")
    assert count == 5
    assert papers == [{"title": "A"}]
