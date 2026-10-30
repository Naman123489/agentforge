"""Synthetic arcade API used only to test AgentForge deployment staging."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(title="Synthetic Arcade")
SECRET_KEY = "dev-secret-change-me"
users: dict[str, str] = {}
scores: list[dict[str, int | str]] = []


class Credentials(BaseModel):
    username: str
    password: str


class Score(BaseModel):
    username: str
    points: int


@app.post("/api/register")
def register(body: Credentials) -> dict[str, str]:
    if body.username in users:
        raise HTTPException(status_code=409, detail="user exists")
    users[body.username] = body.password
    return {"username": body.username}


@app.post("/api/login")
def login(body: Credentials) -> dict[str, str]:
    if users.get(body.username) != body.password:
        raise HTTPException(status_code=401, detail="invalid login")
    return {"message": "synthetic login accepted"}


@app.post("/api/scores")
def submit_score(body: Score) -> dict[str, int | str]:
    if body.username not in users:
        raise HTTPException(status_code=404, detail="user not found")
    entry = {"username": body.username, "points": body.points}
    scores.append(entry)
    return entry


@app.get("/api/leaderboard")
def leaderboard() -> list[dict[str, int | str]]:
    return sorted(scores, key=lambda item: int(item["points"]), reverse=True)[:10]
