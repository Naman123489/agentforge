import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
DB = os.environ.get("AF_DB", str(BASE / "app.db"))
GAMES = {"snake", "2048", "memory"}


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


with db() as _con:
    _con.executescript("""
    CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt TEXT NOT NULL, pw TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, username TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS scores (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
                                       game TEXT NOT NULL, score INTEGER NOT NULL);
    """)

app = FastAPI(title="Arcade")


class Credentials(BaseModel):
    username: str
    password: str


class ScoreIn(BaseModel):
    game: str
    score: int


def _hash(salt: str, password: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def _new_token(username: str) -> str:
    token = secrets.token_hex(16)
    with db() as con:
        con.execute("INSERT INTO tokens VALUES (?, ?)", (token, username))
    return token


def _current_user(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "login required")
    with db() as con:
        row = con.execute("SELECT username FROM tokens WHERE token = ?", (authorization[7:],)).fetchone()
    if not row:
        raise HTTPException(401, "invalid token")
    return row["username"]


@app.post("/api/register")
def register(c: Credentials):
    if len(c.username) < 3 or len(c.password) < 6:
        raise HTTPException(400, "username needs 3+ characters and password 6+")
    salt = secrets.token_hex(8)
    try:
        with db() as con:
            con.execute("INSERT INTO users VALUES (?, ?, ?)", (c.username, salt, _hash(salt, c.password)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "username already taken")
    return {"token": _new_token(c.username), "username": c.username}


@app.post("/api/login")
def login(c: Credentials):
    with db() as con:
        row = con.execute("SELECT salt, pw FROM users WHERE username = ?", (c.username,)).fetchone()
    if not row or _hash(row["salt"], c.password) != row["pw"]:
        raise HTTPException(401, "invalid username or password")
    return {"token": _new_token(c.username), "username": c.username}


@app.post("/api/scores")
def submit_score(s: ScoreIn, authorization: str | None = Header(default=None)):
    user = _current_user(authorization)
    if s.game not in GAMES:
        raise HTTPException(400, f"game must be one of {sorted(GAMES)}")
    if s.score < 0:
        raise HTTPException(400, "score must be >= 0")
    with db() as con:
        cur = con.execute("INSERT INTO scores (username, game, score) VALUES (?, ?, ?)", (user, s.game, s.score))
    return {"id": cur.lastrowid, "username": user, "game": s.game, "score": s.score}


@app.get("/api/leaderboard")
def leaderboard(game: str = "snake", limit: int = 10):
    with db() as con:
        rows = con.execute(
            "SELECT username, game, MAX(score) AS score FROM scores WHERE game = ? "
            "GROUP BY username ORDER BY score DESC LIMIT ?", (game, limit)).fetchall()
    return [dict(r) for r in rows]


FRONTEND = BASE.parent / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
