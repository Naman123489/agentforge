import { lazy, Suspense } from "react";
import { createBrowserRouter, Link, Navigate, RouterProvider } from "react-router";
import { AppShell } from "./components/shell/AppShell";
import { IntroExperience } from "./components/intro/IntroExperience";
import { EmptyState, Loading } from "./components/ui/States";
import LandingPage from "./pages/LandingPage";

const CreatePage = lazy(() => import("./pages/CreatePage"));
const ObserverPage = lazy(() => import("./pages/ObserverPage"));
const RunsPage = lazy(() => import("./pages/RunsPage"));
const RunScopedPage = lazy(() => import("./pages/RunScopedPage"));
const InsightsPage = lazy(() => import("./pages/InsightsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

const lazyEl = (el: React.ReactNode) => <Suspense fallback={<Loading />}>{el}</Suspense>;

// Journey: landing (/) -> create (/new) -> live observer (/runs/:id) -> preview tab -> Platform Insights (/insights).
export const routes = [
  { path: "/", element: <IntroExperience><LandingPage /></IntroExperience> },
  {
    element: <AppShell />,
    children: [
      { path: "new", element: lazyEl(<CreatePage />) },
      { path: "runs", element: lazyEl(<RunsPage />) },
      { path: "runs/:runId", element: lazyEl(<ObserverPage />) },
      { path: "insights", element: lazyEl(<InsightsPage />) },
      { path: "settings", element: lazyEl(<SettingsPage />) },
      // Older links into run-scoped views resolve to the current run's tab.
      { path: "observer", element: lazyEl(<RunScopedPage tab="activity" title="Live observer" />) },
      { path: "files", element: lazyEl(<RunScopedPage tab="files" title="Files" />) },
      { path: "reviews", element: lazyEl(<RunScopedPage tab="review" title="Reviews" />) },
      { path: "usage", element: lazyEl(<RunScopedPage tab="usage" title="Models & usage" />) },
      { path: "checkpoints", element: lazyEl(<RunScopedPage tab="checkpoints" title="Checkpoints" />) },
      { path: "models", element: <Navigate replace to="/insights?tab=models" /> },
      { path: "*", element: <EmptyState title="Page not found" action={<Link to="/new" className="text-sm font-medium text-nv-700 hover:underline">Start a project</Link>} /> },
    ],
  },
];

export default function App() {
  return <RouterProvider router={createBrowserRouter(routes)} />;
}
