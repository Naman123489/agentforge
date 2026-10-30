"""Edition switch: one engine, three ways to run it.

    AF_MODE=local   models on the DGX Spark ($0)           -> config/profiles/spark-lightning.toml
    AF_MODE=web     lighter models on Nebius Token Factory -> config/profiles/web-light.toml
    AF_MODE=demo    scripted fake models, no GPU or credits (UI development, offline rehearsal).
                    The UI labels every demo run as SIMULATED.

AF_ROUTER_CONFIG still overrides the profile (e.g. spark-super.toml).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from engine.router.config import load_dotenv

PROFILES = {
    "local": "config/profiles/spark-lightning.toml",
    "web": "config/profiles/web-light.toml",
    "demo": "config/profiles/spark-lightning.toml",  # any profile works; the client is scripted
}


@dataclass(frozen=True)
class Edition:
    mode: str
    profile: str
    simulated: bool
    max_concurrent_runs: int
    daily_run_quota: int | None  # public website protection
    fixed_prompts_only: bool

    @property
    def label(self) -> str:
        return {"local": "Local · DGX Spark", "web": "Web · Nebius Token Factory",
                "demo": "Demo · SIMULATED models"}[self.mode]


def current() -> Edition:
    load_dotenv()
    mode = os.environ.get("AF_MODE", "local").lower()
    if mode not in PROFILES:
        raise ValueError(f"AF_MODE must be one of {sorted(PROFILES)}, got {mode!r}")
    profile = os.environ.get("AF_ROUTER_CONFIG") or PROFILES[mode]
    if mode == "web":
        return Edition(mode, profile, False, 1, int(os.environ.get("AF_DAILY_RUN_QUOTA", "20")),
                       os.environ.get("AF_FIXED_PROMPTS", "1") != "0")
    return Edition(mode, profile, mode == "demo", 2, None, False)
