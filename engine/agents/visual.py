"""Visual and interaction checks for a generated app, in a real browser (headless Chrome via Playwright).

serve() starts the app the way the preview does (UI + API on one origin). inspect() visits every route the app links
to at desktop and mobile size, takes full-page screenshots and measures what a user would hit: console and page
errors, failed API calls, horizontal overflow on mobile, empty or unstyled pages, tiny tap targets, images without alt,
and whether its forms actually submit (it registers and logs in when the app has auth). The findings are facts, not
opinions; they feed the Frontend agent's repair loop and the model benchmark.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
SAMPLE = {"email": "visual.check@example.com", "password": "Visual-check-123", "username": "visual_check",
          "name": "Visual Check", "title": "Visual check item", "url": "https://example.com", "number": "3",
          "date": "2026-01-15", "datetime-local": "2026-01-15T09:30", "time": "09:30", "tel": "5550100"}


@dataclass
class PageReport:
    route: str
    viewport: str
    screenshot: str
    text_chars: int
    overflow_x: int
    unstyled: bool
    small_targets: int
    images_without_alt: int
    h1_count: int = 1
    stretched_blocks: int = 0
    edge_text: int = 0
    used_height: float = 1.0
    header_height: int = 0


@dataclass
class VisualReport:
    url: str
    pages: list[PageReport] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    forms_tried: int = 0
    forms_ok: int = 0
    flow_notes: list[str] = field(default_factory=list)

    def defects(self) -> list[str]:
        """Concrete, fixable findings (empty == no defect found by these checks)."""
        out = [f"browser console error: {e}" for e in self.console_errors[:5]]
        out += [f"uncaught page error: {e}" for e in self.page_errors[:5]]
        out += [f"API call failed: {r}" for r in self.failed_requests[:5]]
        for p in self.pages:
            where = f"{p.route} ({p.viewport})"
            if p.unstyled:
                out.append(f"{where}: page renders unstyled (stylesheet missing or not applied)")
            if p.text_chars < 40:
                out.append(f"{where}: page is nearly empty ({p.text_chars} visible characters)")
            if p.viewport == "mobile" and p.overflow_x > 4:
                out.append(f"{where}: content is {p.overflow_x}px wider than the 390px screen (horizontal scrolling)")
            if p.viewport == "mobile" and p.small_targets > 3:
                out.append(f"{where}: {p.small_targets} tap targets are smaller than 32px")
            if p.images_without_alt:
                out.append(f"{where}: {p.images_without_alt} images have no alt text")
            if p.viewport == "mobile" and p.header_height > 90:
                out.append(f"{where}: the header is {p.header_height}px tall on a phone (its contents wrap); move "
                           "secondary items into the AppShell menu")
            if p.h1_count == 0 and p.viewport == "desktop":
                out.append(f"{where}: no page title (h1); wrap the screen in Page or AuthLayout")
            if p.stretched_blocks:
                out.append(f"{where}: {p.stretched_blocks} forms/cards stretch across the whole 1440px screen; "
                           "constrain them (AuthLayout for sign-in, Page/Grid inside AppShell)")
            if p.edge_text > 2:
                out.append(f"{where}: {p.edge_text} text elements touch the screen edge (no page padding; use AppShell)")
            if p.viewport == "desktop" and p.used_height < 0.2 and p.text_chars < 150:
                out.append(f"{where}: content fills only {round(p.used_height * 100)}% of the screen height; "
                           "the screen looks unfinished")
        if self.forms_tried and self.forms_ok < self.forms_tried:
            out.append(f"{self.forms_tried - self.forms_ok} of {self.forms_tried} forms failed when submitted with valid data")
        out += self.flow_notes
        return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(workspace: Path, timeout_s: float = 20.0) -> tuple[subprocess.Popen, str]:
    """Start UI + API like the preview (needs frontend/dist and .agentforge/preview_app.py)."""
    workspace = Path(workspace).resolve()
    if not (workspace / ".agentforge" / "preview_app.py").is_file():
        raise RuntimeError("no .agentforge/preview_app.py: run the frontend checks first")
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")}
    env["AF_DB"] = str(workspace / ".agentforge" / "visual.db")  # never touch the app's own database file
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "preview_app:app", "--app-dir", ".agentforge", "--host",
                             "127.0.0.1", "--port", str(port)], cwd=workspace, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("the app exited while starting: " + (proc.stderr.read() or "")[-600:])
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return proc, f"http://127.0.0.1:{port}"
        except OSError:
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("the app did not answer within the timeout")


_ROUTES_JS = """() => [...new Set([...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))
  .filter(h => h && (h.startsWith('#/') || (h.startsWith('/') && !h.startsWith('//') && !h.startsWith('/api')))))]"""

_MEASURE_JS = """() => {
  const doc = document.documentElement;
  const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
  const ff = getComputedStyle(document.body).fontFamily || '';
  const sheets = [...document.styleSheets].filter(s => { try { return s.cssRules.length > 0 } catch { return true } });
  const targets = [...document.querySelectorAll('a,button,input,select,textarea,[role=button]')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && (r.width < 32 || r.height < 32)
      && getComputedStyle(e).visibility !== 'hidden' && e.type !== 'checkbox' && e.type !== 'radio' });
  const vw = window.innerWidth, vh = window.innerHeight;
  const blocks = [...document.querySelectorAll('form, .card, section, table')].map(e => e.getBoundingClientRect()).filter(r => r.height > 30);
  const stretched = vw >= 1200 ? blocks.filter(r => r.width > 0.85 * vw && r.width > 1000).length : 0;
  const texts = [...document.querySelectorAll('h1,h2,h3,p,label,button,a,td,li')].map(e => e.getBoundingClientRect())
    .filter(r => r.width > 0 && r.height > 0);
  const edge = texts.filter(r => r.left < 6 || r.right > vw - 6).length;
  const bottom = Math.max(0, ...texts.map(r => r.bottom + window.scrollY));
  return { text: text.length, overflow: doc.scrollWidth - doc.clientWidth, unstyled: sheets.length === 0 || /^\\s*"?Times/.test(ff),
           small: targets.length, noalt: [...document.images].filter(i => !i.alt).length,
           h1: document.querySelectorAll('h1').length, hdr: Math.round(document.querySelector('header')?.getBoundingClientRect().height || 0), stretched: stretched, edge: edge, used: Math.min(1, bottom / vh) };
}"""


_SIGNUP = ("sign up", "signup", "register", "create", "get started", "join", "new account")


def _fill_and_submit(page, form, creds: dict | None = None) -> bool:
    """Fill a form with valid-looking values and submit; ok if no page error and no 5xx happened meanwhile."""
    creds = creds or {}
    for el in form.query_selector_all("input, textarea, select"):
        try:
            kind = (el.get_attribute("type") or "text").lower()
            # What the field is for: name, placeholder, aria-label or its visible <label> (ids are often generated).
            name = " ".join(filter(None, (el.get_attribute("name"), el.get_attribute("placeholder"), el.get_attribute("aria-label"),
                                          el.evaluate("e => [...(e.labels || [])].map(l => l.innerText).join(' ')")))).lower()
            if kind in ("hidden", "submit", "button", "file"):
                continue
            if el.evaluate("e => e.tagName") == "SELECT":
                opts = el.query_selector_all("option")
                if len(opts) > 1:
                    el.select_option(index=1)
                continue
            if kind in ("checkbox", "radio"):
                el.check()
                continue
            value = (creds.get("password") if kind == "password" else None) or                 next((v for k, v in creds.items() if k in name or k == kind), None) or                 SAMPLE.get(kind) or next((v for k, v in SAMPLE.items() if k in name), "Visual check")
            if kind == "number" or el.get_attribute("inputmode") == "numeric":
                value = "3"
            el.fill(value)
        except Exception:  # noqa: BLE001 - a field we cannot fill is reported by the submit result
            continue
    button = form.query_selector("button[type=submit], button:not([type]), input[type=submit]")
    if not button:
        return False
    button.click()
    page.wait_for_timeout(900)
    return True


def _sign_in(page, url: str, rep: VisualReport) -> None:
    import secrets as _secrets
    tag = _secrets.token_hex(3)
    creds = {"username": f"visual_{tag}", "email": f"visual_{tag}@example.com", "password": "Visual-check-123",
             "name": "Visual Check"}
    page.goto(url, wait_until="networkidle")
    for el in page.query_selector_all("a, button"):
        try:
            label = (el.inner_text() or "").strip().lower()
        except Exception:  # noqa: BLE001
            continue
        if any(w in label for w in _SIGNUP) and el.is_visible():
            el.click()
            page.wait_for_timeout(500)
            break
    for _ in range(2):  # sign up, then log in if the app asks again
        form = next((f for f in page.query_selector_all("form") if f.query_selector("input[type=password]")), None)
        if not form:
            break
        rep.forms_tried += 1
        before = len(rep.page_errors) + len(rep.failed_requests)
        if _fill_and_submit(page, form, creds) and len(rep.page_errors) + len(rep.failed_requests) == before:
            rep.forms_ok += 1
        page.wait_for_timeout(600)
    page.goto(url, wait_until="networkidle")
    if page.query_selector("input[type=password]"):
        stored = page.evaluate("Object.keys(localStorage).some(k => /token/i.test(k) && localStorage.getItem(k))")
        rep.flow_notes.append(
            "after a reload the app shows sign-in again although a login token is stored: restore the session from "
            "getToken() on start-up (tokens are opaque; never decode or validate them in the browser)" if stored else
            "could not get past the sign-in screen with a freshly registered account")


def inspect(url: str, out_dir: Path, max_routes: int = 6, interact: bool = True) -> VisualReport:
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = VisualReport(url=url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            # 4xx from the API (401 while signed out, 404 for an empty lookup) are expected app behaviour, not defects;
            # JS errors and 5xx are (run_3064a2a02d3e: two repair rounds were spent on a signed-out 401).
            page.on("console", lambda m: rep.console_errors.append(m.text[:300])
                    if m.type == "error" and "status of 4" not in m.text else None)
            page.on("pageerror", lambda e: rep.page_errors.append(str(e)[:300]))
            page.on("response", lambda r: rep.failed_requests.append(f"{r.request.method} {r.url.split('127.0.0.1')[-1]} -> {r.status}")
                    if r.status >= 500 else None)
            page.goto(url, wait_until="networkidle")
            if interact:  # sign up, then log in if still asked, so the signed-in app is what gets inspected
                _sign_in(page, url, rep)
            routes = [""] + [r for r in page.evaluate(_ROUTES_JS) if r not in ("#/", "/", "#")][:max_routes - 1]
            for route in routes:
                target = url + ("/" + route.lstrip("/") if route.startswith("/") else route)
                for vp, (w, h) in VIEWPORTS.items():
                    page.set_viewport_size({"width": w, "height": h})
                    page.goto(target, wait_until="networkidle")
                    page.wait_for_timeout(300)
                    if interact and vp == "desktop":  # exercise this screen's first non-auth form once
                        form = next((f for f in page.query_selector_all("form")
                                     if not f.query_selector("input[type=password]")), None)
                        if form:
                            rep.forms_tried += 1
                            before = len(rep.page_errors) + len(rep.failed_requests)
                            if _fill_and_submit(page, form) and len(rep.page_errors) + len(rep.failed_requests) == before:
                                rep.forms_ok += 1
                            page.wait_for_timeout(300)
                    m = page.evaluate(_MEASURE_JS)
                    name = re.sub(r"[^a-z0-9]+", "-", (route or "home").lower()).strip("-") or "home"
                    shot = out_dir / f"{name}-{vp}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    rep.pages.append(PageReport(route or "/", vp, str(shot), m["text"], m["overflow"], m["unstyled"],
                                                m["small"], m["noalt"], m["h1"], m["stretched"], m["edge"], m["used"], m["hdr"]))
        finally:
            browser.close()
    return rep


def inspect_workspace(workspace: Path, out_dir: Path, **kw) -> VisualReport:
    proc, url = serve(workspace)
    try:
        return inspect(url, out_dir, **kw)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
