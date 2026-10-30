import { useState, type FormEvent } from "react";
import { api, ApiError, type PostScoresRequest } from "../api/generated";
import { Alert, Button, Card, Field } from "../components/ui";

const GAMES: PostScoresRequest["game"][] = ["snake", "2048", "memory"];

export default function PlayPage({ loggedIn, onNeedLogin }: { loggedIn: boolean; onNeedLogin: () => void }) {
  const [game, setGame] = useState<PostScoresRequest["game"]>("snake");
  const [score, setScore] = useState("");
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = Number(score);
    if (!Number.isInteger(value) || value < 0) {
      setMessage({ tone: "error", text: "Enter a whole number of 0 or more." });
      return;
    }
    setPending(true);
    setMessage(null);
    try {
      const saved = await api.postScores({ game, score: value });
      setMessage({ tone: "success", text: `Saved ${saved.score} points for ${saved.game}.` });
      setScore("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onNeedLogin();
      setMessage({ tone: "error", text: err instanceof ApiError ? err.message : "Could not save the score." });
    } finally {
      setPending(false);
    }
  };

  return (
    <Card title="Submit a score">
      {!loggedIn && <Alert tone="info">Log in to save scores to the leaderboard.</Alert>}
      <form onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor="game">Game</label>
          <select id="game" value={game} onChange={(e) => setGame(e.target.value as PostScoresRequest["game"])}>
            {GAMES.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </div>
        <Field label="Score" inputMode="numeric" value={score} onChange={(e) => setScore(e.target.value)} />
        {message && <Alert tone={message.tone}>{message.text}</Alert>}
        <Button type="submit" loading={pending} disabled={!loggedIn}>Save score</Button>
      </form>
    </Card>
  );
}
