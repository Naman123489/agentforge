"""engine.agents.visual in a real headless Chrome: known-bad pages produce the right measured defects, a clean
page produces none. Skipped when Playwright or a local Chrome is unavailable."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
from engine.agents.visual import inspect  # noqa: E402

BAD = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;font-family:sans-serif} form{display:block;width:100%;padding:12px 0} input{display:block;width:100%}
.wide{width:900px;height:40px;background:#ccc}
</style></head><body><form><label>Name</label><input name="name"><label>Email</label><input name="email">
<button type="submit">Save</button></form><div class="wide">wide</div>
<p>Some text so the page is not empty, some text so the page is not empty, some text.</p></body></html>"""

GOOD = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{margin:0;font-family:sans-serif;background:#f6f7fb} main{max-width:720px;margin:0 auto;padding:24px}
h1{font-size:28px} p{line-height:1.6} button{min-height:44px;min-width:44px;padding:0 16px}
</style></head><body><main><h1>Contacts</h1><p>Keep everyone you work with in one place. Add a contact, search by name
or company, and mark favourites so they stay at the top of your list.</p><p>Nothing is shared outside your account.</p>
<button type="button">Add contact</button></main></body></html>"""


def _inspect(tmp_path, html: str):
    page = tmp_path / "index.html"
    page.write_text(html, encoding="utf-8")
    try:
        return inspect(page.as_uri(), tmp_path / "shots", interact=False)
    except Exception as e:  # noqa: BLE001 - no Chrome on this machine
        pytest.skip(f"browser unavailable: {e}")


def test_bad_layout_is_measured(tmp_path):
    rep = _inspect(tmp_path, BAD)
    text = "\n".join(rep.defects())
    assert "wider than the 390px screen" in text  # the 900px block on a phone
    assert "stretch across the whole 1440px screen" in text  # the full-width form on desktop
    assert "no page title (h1)" in text
    assert "touch the screen edge" in text
    assert {p.viewport for p in rep.pages} == {"desktop", "mobile"} and all(p.screenshot.endswith(".png") for p in rep.pages)


def test_clean_page_has_no_defects(tmp_path):
    rep = _inspect(tmp_path, GOOD)
    assert rep.defects() == [], rep.defects()
