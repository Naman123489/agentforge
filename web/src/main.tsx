import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { DEMO_MODE } from "./api/config";
import { ToastProvider } from "./components/ui/Toast";
import { queryClient } from "./state/queryClient";
import "./styles/index.css";

async function boot() {
  // Demo mode (npm run dev:demo) swaps the HTTP transport for a clearly labelled in-browser mock.
  // The import is behind the build-time flag, so the mock is not shipped in normal builds.
  if (DEMO_MODE) {
    const { installDemo } = await import("./demo/mockApi");
    installDemo();
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <App />
        </ToastProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}

void boot();
