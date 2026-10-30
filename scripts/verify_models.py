"""Stage-0 check (S0-1, S0-2): verify the three configured models are reachable and usable.

Works against Token Factory (needs NEBIUS_API_KEY, spends a few tokens) or local
vLLM servers on a DGX Spark ($0):

    python scripts/verify_models.py
    AF_ROUTER_CONFIG=config/profiles/spark-lightning.toml SPARK_HOST=<ip> python scripts/verify_models.py
    python scripts/verify_models.py --json out.json

Checks per slot: model id exists in /v1/models, a short call works (latency,
usage reported), json_object and json_schema response formats work. Prints the
supports_* flags to set in config/models.toml. It does NOT verify prices:
confirm those on the Token Factory pricing page / your billing console.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.router import RouterConfig, Slot  # noqa: E402
from engine.router.config import is_banned_model  # noqa: E402
from engine.router.router import _make_client  # noqa: E402

PING = [{"role": "user", "content": "Reply with exactly: OK"}]
JSON_PING = [{"role": "user", "content": 'Return {"ok": true} as JSON.'}]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}


async def check_slot(client, slot: Slot, sc, catalog: set[str]) -> dict:
    r: dict = {"slot": slot.value, "model_id": sc.model_id, "in_catalog": sc.model_id in catalog,
               "chinese_origin": is_banned_model(sc.model_id)}
    if not r["in_catalog"]:
        r["did_you_mean"] = difflib.get_close_matches(sc.model_id, list(catalog), n=3, cutoff=0.3)
        return r
    r["endpoint"] = str(client.base_url)
    # thinking off for these probes: short max_tokens would otherwise be spent on reasoning
    off = {"chat_template_kwargs": {"enable_thinking": False}} if "chat_template_kwargs" in sc.extra_body else {}
    t0 = time.perf_counter()
    try:
        resp = await client.chat.completions.create(model=sc.model_id, messages=PING, max_tokens=16, temperature=0,
                                                    extra_body={**sc.extra_body, **off})
        r["latency_s"] = round(time.perf_counter() - t0, 2)
        r["reply"] = (resp.choices[0].message.content or "")[:40]
        r["usage"] = resp.usage.model_dump() if resp.usage else None
    except Exception as e:  # noqa: BLE001
        r["error"] = f"{type(e).__name__}: {e}"[:300]
        return r
    for flag, fmt in (("supports_json_mode", {"type": "json_object"}),
                      ("supports_json_schema", {"type": "json_schema",
                                                "json_schema": {"name": "Ping", "schema": SCHEMA, "strict": True}})):
        try:
            resp = await client.chat.completions.create(model=sc.model_id, messages=JSON_PING, max_tokens=64,
                                                        temperature=0, response_format=fmt, extra_body=off)
            json.loads(resp.choices[0].message.content or "")
            r[flag] = True
        except Exception as e:  # noqa: BLE001
            r[flag] = False
            r[f"{flag}_error"] = str(e)[:200]
    return r


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the report to this file")
    args = ap.parse_args()

    cfg = RouterConfig.load()
    clients, catalogs, results = {}, {}, []
    for s in Slot:
        ep = cfg.endpoint(s)
        if ep not in clients:
            clients[ep] = _make_client(cfg, s)
            try:
                catalogs[ep] = {m.id async for m in clients[ep].models.list()}
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] cannot reach {ep[0]}: {type(e).__name__}: {e}"[:300])
                catalogs[ep] = set()
        results.append(await check_slot(clients[ep], s, cfg.slot(s), catalogs[ep]))
    catalog = set().union(*catalogs.values())

    ok = True
    for r in results:
        status = "OK" if r.get("in_catalog") and "error" not in r else "FAIL"
        ok &= status == "OK"
        print(f"[{status}] slot {r['slot']}: {r['model_id']}")
        for k in ("latency_s", "supports_json_mode", "supports_json_schema", "did_you_mean", "error"):
            if k in r:
                print(f"         {k}: {r[k]}")
    nvidia = sorted(m for m in catalog if "nemotron" in m.lower())
    print("\nNemotron models in your catalog:", *nvidia or ["(none)"], sep="\n  ")
    print("\nPrices are NOT verified by this script; set price_verified=true only after checking billing.")
    if args.json:
        Path(args.json).write_text(json.dumps({"checked_at": time.time(), "results": results,
                                               "nemotron_catalog": nvidia}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
