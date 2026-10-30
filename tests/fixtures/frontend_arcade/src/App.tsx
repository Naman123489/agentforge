import { useState } from "react";
import { getToken, setToken } from "./api/generated";
import { Button } from "./components/ui";
import { useHashRoute } from "./lib/useHashRoute";
import AccountPage from "./pages/AccountPage";
import LeaderboardPage from "./pages/LeaderboardPage";
import PlayPage from "./pages/PlayPage";

const NAV = [
  { route: "play", label: "Play" },
  { route: "leaderboard", label: "Leaderboard" },
  { route: "account", label: "Account" },
];

export default function App() {
  const [route, navigate] = useHashRoute("play");
  const [user, setUser] = useState<string | null>(getToken() ? "player" : null);

  const logout = () => {
    setToken(null);
    setUser(null);
    navigate("account");
  };

  return (
    <>
      <header className="topbar">
        <div className="container topbar-inner">
          <strong className="brand">Mini Arcade</strong>
          <nav aria-label="Main">
            {NAV.map((n) => (
              <a key={n.route} href={`#/${n.route}`} aria-current={route === n.route ? "page" : undefined}
                className={route === n.route ? "nav-link active" : "nav-link"}>{n.label}</a>
            ))}
          </nav>
          {user ? <Button variant="ghost" onClick={logout}>Log out</Button> : null}
        </div>
      </header>
      <main className="container">
        {route === "leaderboard" && <LeaderboardPage />}
        {route === "account" && <AccountPage onLoggedIn={(name) => { setUser(name); navigate("play"); }} />}
        {route !== "leaderboard" && route !== "account" && (
          <PlayPage loggedIn={!!user} onNeedLogin={() => navigate("account")} />
        )}
      </main>
    </>
  );
}
