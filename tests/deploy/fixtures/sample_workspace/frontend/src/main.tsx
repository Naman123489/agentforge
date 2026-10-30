import React from "react";
import { createRoot } from "react-dom/client";

function App() {
  return <main><h1>Synthetic Arcade</h1><p>Fake test build for AgentForge.</p></main>;
}

createRoot(document.getElementById("root")!).render(<App />);
