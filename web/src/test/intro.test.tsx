import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IntroExperience } from "../components/intro/IntroExperience";
import { clearIntroSeen, INTRO_KEY, markIntroSeen, TIMING } from "../components/intro/introState";
import LandingPage from "../pages/LandingPage";
import { mockApi, renderApp } from "./utils";

const renderHome = () => renderApp(<IntroExperience><LandingPage /></IntroExperience>);
const phase = () => document.querySelector("[data-intro-phase]")?.getAttribute("data-intro-phase");
const runIntro = () => {
  act(() => { vi.advanceTimersByTime(TIMING.activateFallback); });
  act(() => { vi.advanceTimersByTime(TIMING.brandMax); });
  act(() => { vi.advanceTimersByTime(TIMING.handoffMax); });
};

describe("Intro experience", () => {
  beforeEach(() => {
    clearIntroSeen();
    mockApi({});
    vi.spyOn(window, "scrollTo").mockImplementation(() => {}); // jsdom does not implement scrolling
  });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("shows only the computer before the click: no homepage, no brand name", () => {
    renderHome();
    expect(screen.getByText("Click to enter")).toBeInTheDocument();
    // jsdom has no WebGL, so the lightweight fallback is used.
    expect(screen.getByTestId("intro-fallback")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(document.body.textContent).not.toMatch(/agentforge/i);
    expect(phase()).toBe("idle");
  });

  it("triggers exactly once on click, reveals the brand, then hands off to the homepage", () => {
    vi.useFakeTimers();
    renderHome();
    const target = screen.getByRole("button", { name: "Enter site" });
    fireEvent.click(target);
    fireEvent.click(target);
    expect(phase()).toBe("activating");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();

    act(() => { vi.advanceTimersByTime(TIMING.activateFallback); });
    expect(phase()).toBe("brand");
    expect(screen.getByText("AGENTFORGE")).toBeInTheDocument();
    // The homepage is mounted underneath the black stage but inert until the hand-off.
    expect(document.querySelector(".intro-home")).toHaveAttribute("inert");

    act(() => { vi.advanceTimersByTime(TIMING.brandMax); });
    expect(phase()).toBe("handoff");
    expect(document.querySelector(".intro-home")).not.toHaveAttribute("inert");

    act(() => { vi.advanceTimersByTime(TIMING.handoffMax); });
    expect(phase()).toBe("done");
    expect(screen.queryByTestId("intro-overlay")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Your idea. A team of AI engineers. One working product.");
    expect(screen.getByRole("link", { name: /Start Building/ })).toHaveAttribute("href", "/new");
    expect(sessionStorage.getItem(INTRO_KEY)).toBe("1");
  });

  it("starts from the keyboard (Enter) with focus already on the computer", async () => {
    renderHome();
    expect(screen.getByRole("button", { name: "Enter site" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(phase()).toBe("activating");
  });

  it("is skipped for the rest of the visit once seen", () => {
    markIntroSeen();
    renderHome();
    expect(phase()).toBe("done");
    expect(screen.queryByTestId("intro-overlay")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("still works when sessionStorage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.useFakeTimers();
    const first = renderHome();
    expect(phase()).toBe("idle");
    fireEvent.click(screen.getByRole("button", { name: "Enter site" }));
    runIntro();
    expect(phase()).toBe("done");
    first.unmount();
    renderHome(); // internal navigation back home: the in-memory flag prevents a replay
    expect(phase()).toBe("done");
  });

  it("can be replayed from the footer", () => {
    markIntroSeen();
    renderHome();
    fireEvent.click(screen.getByRole("button", { name: "Replay intro" }));
    expect(phase()).toBe("idle");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(sessionStorage.getItem(INTRO_KEY)).toBeNull();
  });
});
