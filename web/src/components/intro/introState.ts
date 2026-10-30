// Intro bookkeeping: once-per-visit flag (sessionStorage, guarded) and the WebGL / device capability check.

export const INTRO_KEY = "agentforge:intro-seen";

/** Phase timings in ms. Phases chain off the animations themselves (animationend / transitionend) so a slow device never
 * cuts the sequence short; `brandMax` / `handoffMax` are safety nets for when those events never fire. */
export const TIMING = {
  activate3d: 1750,
  activateFallback: 650,
  stageFade: 900, // computer cross-fades into the brand reveal
  hold: 700, // brand fully revealed -> hand-off
  handoffTail: 600, // black gone -> let the homepage reveal finish
  brandMax: 5200,
  handoffMax: 3200,
  reduced: { activate: 300, hold: 600, handoffTail: 150 },
} as const;

// Survives storage being unavailable (private mode, blocked site data) for the life of the page.
let seenThisLoad = false;
// The `?intro` override applies once per page load, so internal navigation back to `/?intro` does not replay it.
let forceConsumed = false;

export function hasSeenIntro(): boolean {
  if (seenThisLoad) return true;
  try {
    return window.sessionStorage.getItem(INTRO_KEY) === "1";
  } catch {
    return false;
  }
}

export function markIntroSeen(): void {
  seenThisLoad = true;
  forceConsumed = true;
  try {
    window.sessionStorage.setItem(INTRO_KEY, "1");
  } catch {
    /* storage blocked: the module flag still prevents a replay on internal navigation */
  }
}

export function clearIntroSeen(): void {
  seenThisLoad = false;
  try {
    window.sessionStorage.removeItem(INTRO_KEY);
  } catch {
    /* ignore */
  }
}

/** `/?intro` (any value) forces the intro once per page load, e.g. for demos. */
export function introForcedByUrl(): boolean {
  if (forceConsumed) return false;
  try {
    return new URLSearchParams(window.location.search).has("intro");
  } catch {
    return false;
  }
}

/** `?intro=lite` forces the lightweight non-WebGL intro. */
export function liteForcedByUrl(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("intro") === "lite";
  } catch {
    return false;
  }
}

/** True when the device can run the 3D scene: WebGL available and not an obviously weak or data-saving device. */
export function canUse3D(): boolean {
  if (typeof window === "undefined" || typeof WebGLRenderingContext === "undefined") return false;
  const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } };
  if (nav.connection?.saveData) return false;
  if (nav.deviceMemory !== undefined && nav.deviceMemory < 2) return false;
  if (nav.hardwareConcurrency !== undefined && nav.hardwareConcurrency > 0 && nav.hardwareConcurrency < 3) return false;
  try {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl2") ?? c.getContext("webgl");
    if (!gl) return false;
    (gl as WebGLRenderingContext).getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}
