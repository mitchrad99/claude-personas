"""Smoke tests — every UI tab's backing route returns 200 and basic JSON shape."""
from unittest.mock import MagicMock, patch


# ── HTML shell ────────────────────────────────────────────────────────────────

def test_index_returns_html(client, auth):
    resp = client.get("/", headers=auth)
    assert resp.status_code == 200
    assert b"AAO CRM" in resp.data


# ── Contacts tab ──────────────────────────────────────────────────────────────

def test_contacts_tab(client, auth):
    resp = client.get("/api/contacts", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


# ── Funders tab ───────────────────────────────────────────────────────────────

def test_funders_tab(client, auth):
    resp = client.get("/api/funders", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


# ── Tasks tab ─────────────────────────────────────────────────────────────────

def test_tasks_tab(client, auth):
    resp = client.get("/api/tasks", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


# ── DC Orgs tab ───────────────────────────────────────────────────────────────

def test_dc_orgs_tab(client, auth):
    resp = client.get("/api/dc_orgs", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


# ── Opportunities tab ─────────────────────────────────────────────────────────

def test_opportunities_tab(client, auth):
    resp = client.get("/api/opportunities", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


# ── Inbox tab ─────────────────────────────────────────────────────────────────

def test_inbox_tab(client, auth):
    resp = client.get("/api/inbox", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_task_recommendations(client, auth):
    resp = client.get("/api/task_recommendations", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


# ── Summary (header pills) ────────────────────────────────────────────────────

def test_summary_shape(client, auth):
    resp = client.get("/api/summary", headers=auth)
    assert resp.status_code == 200
    d = resp.get_json()
    assert "tasks_due_this_week" in d
    assert "overdue_tasks" in d
    assert "hot_contacts" in d
    assert "pending_inbox" in d


# ── Chat tab ──────────────────────────────────────────────────────────────────

def test_chat_reset(client, auth):
    mock_engine = MagicMock()
    with patch("app.get_chat_engine", return_value=mock_engine):
        resp = client.post("/api/chat/reset", headers=auth, json={})
    assert resp.status_code == 200


def test_chat_message_mocked(client, auth):
    mock_engine = MagicMock()
    mock_engine.chat.return_value = ("Got it.", [])
    with patch("app.get_chat_engine", return_value=mock_engine):
        resp = client.post("/api/chat", headers=auth, json={"message": "Hello"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["response"] == "Got it."
    assert d["changes"] == []


def test_chat_message_with_changes(client, auth):
    mock_engine = MagicMock()
    mock_engine.chat.return_value = (
        "Logged meeting with Alice.",
        [{"type": "contact", "action": "updated", "id": 1, "name": "Alice"}],
    )
    with patch("app.get_chat_engine", return_value=mock_engine):
        resp = client.post("/api/chat", headers=auth, json={"message": "Met Alice today"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert len(d["changes"]) == 1
    assert d["changes"][0]["action"] == "updated"


# ── auth guard (single route representative) ──────────────────────────────────

def test_all_routes_require_auth(client):
    routes = [
        "/api/contacts", "/api/funders", "/api/tasks",
        "/api/dc_orgs", "/api/opportunities", "/api/inbox",
        "/api/summary",
    ]
    for route in routes:
        assert client.get(route).status_code == 401, f"{route} should require auth"
