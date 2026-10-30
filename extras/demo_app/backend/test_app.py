import os
import tempfile

os.environ["AF_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402

client = TestClient(app)


def _token(name="alice"):
    r = client.post("/api/register", json={"username": name, "password": "secret123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_register_and_login():
    _token("bob")
    r = client.post("/api/login", json={"username": "bob", "password": "secret123"})
    assert r.status_code == 200 and r.json()["token"]
    assert client.post("/api/login", json={"username": "bob", "password": "wrong"}).status_code == 401
    assert client.post("/api/register", json={"username": "bob", "password": "secret123"}).status_code == 409


def test_scores_require_login():
    assert client.post("/api/scores", json={"game": "snake", "score": 5}).status_code == 401


def test_submit_and_leaderboard():
    t = _token("carol")
    h = {"Authorization": f"Bearer {t}"}
    assert client.post("/api/scores", json={"game": "2048", "score": 512}, headers=h).status_code == 200
    assert client.post("/api/scores", json={"game": "2048", "score": 128}, headers=h).status_code == 200
    assert client.post("/api/scores", json={"game": "chess", "score": 1}, headers=h).status_code == 400
    board = client.get("/api/leaderboard", params={"game": "2048"}).json()
    assert board[0] == {"username": "carol", "game": "2048", "score": 512}
