import { useState } from "react";
import { api, type GetLeaderboardQuery } from "../api/generated";
import { Alert, Button, Card, EmptyState, Spinner } from "../components/ui";
import { useAsync } from "../lib/useAsync";

const GAMES: GetLeaderboardQuery["game"][] = ["snake", "2048", "memory"];

export default function LeaderboardPage() {
  const [game, setGame] = useState<GetLeaderboardQuery["game"]>("snake");
  const board = useAsync(() => api.getLeaderboard({ game, limit: 10 }), game);

  return (
    <Card title="Leaderboard" actions={
      <div className="row" role="group" aria-label="Game">
        {GAMES.map((g) => (
          <Button key={g} variant={g === game ? "primary" : "secondary"} aria-pressed={g === game} onClick={() => setGame(g)}>{g}</Button>
        ))}
      </div>
    }>
      {board.loading && <Spinner label="Loading scores" />}
      {board.error && (
        <Alert>{board.error.message} <Button variant="ghost" onClick={board.reload}>Try again</Button></Alert>
      )}
      {!board.loading && !board.error && (board.data?.length ? (
        <ol className="board">
          {board.data.map((row, i) => (
            <li key={`${row.username}-${i}`}><span>{row.username}</span><strong>{row.score}</strong></li>
          ))}
        </ol>
      ) : <EmptyState title="No scores yet">Be the first to post a score for {game}.</EmptyState>)}
    </Card>
  );
}
