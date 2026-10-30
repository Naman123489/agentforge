"""Small tests for the synthetic API."""

from fastapi.testclient import TestClient

from backend.app import app, scores, users


def test_register_login_and_leaderboard() -> None:
    users.clear()
    scores.clear()
    client = TestClient(app)
    assert client.post("/api/register", json={"username": "demo", "password": "fake-password"}).status_code == 200
    assert client.post("/api/login", json={"username": "demo", "password": "fake-password"}).status_code == 200
    assert client.post("/api/scores", json={"username": "demo", "points": 42}).status_code == 200
    assert client.get("/api/leaderboard").json() == [{"username": "demo", "points": 42}]
