"""Router configuration: three model slots, budgets, retry and health settings.

Loaded from config/models.toml, then overridden by environment variables so
deployments can swap models without editing files or agent code:

    AF_MODEL_A / AF_MODEL_B / AF_MODEL_C          model id per slot
    AF_MODEL_<S>_ENABLED                          "0"/"false" disables a slot
    AF_MODEL_<S>_PRICE_IN / _PRICE_OUT            USD per 1M tokens
    AF_MODEL_<S>_BILLING                          paid | prepaid_credits | free_allowance
    AF_MODEL_<S>_FREE_TOKENS                      free allowance size (tokens)
    AF_RUN_TOKEN_LIMIT, AF_RUN_COST_LIMIT_USD, AF_PREMIUM_COST_LIMIT_USD,
    AF_PREMIUM_TOKEN_LIMIT, AF_CREDIT_BALANCE_USD
    NEBIUS_BASE_URL                               overrides provider.base_url
    AF_ROUTER_CONFIG                              which config file to load (e.g. a profile)

A config file may say `extends = "models.toml"` to inherit everything and
override only some keys (used by config/profiles/*). `${VAR}` in a base_url is
expanded from the environment (SPARK_HOST defaults to localhost).
"""

from __future__ import annotations

import os
import re
import tomllib
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.toml"


class Slot(str, Enum):
    """Logical model roles. Ordered from strongest to cheapest."""

    A = "A"  # premium reasoning
    B = "B"  # main coding
    C = "C"  # economy

    @property
    def rank(self) -> int:
        return {"A": 2, "B": 1, "C": 0}[self.value]


Billing = Literal["paid", "prepaid_credits", "free_allowance", "local"]  # local = self-hosted, $0 per token

# Project rule (02_System_Requirements.md section 1): no Chinese-origin models in the roster.
BANNED_MODEL_MARKERS = ("qwen", "deepseek", "glm", "chatglm", "thudm", "zai-org", "kimi", "moonshot",
                        "baichuan", "minimax", "01-ai", "/yi-", "internlm", "hunyuan", "ernie", "baai")


def is_banned_model(model_id: str) -> bool:
    m = model_id.lower()
    return any(k in m for k in BANNED_MODEL_MARKERS)


class SlotConfig(BaseModel):
    model_id: str
    enabled: bool = True
    context_window: int = 32768
    max_output_tokens: int = 8192
    reasoning_overhead: float = Field(1.0, ge=1.0)  # billed output = answer tokens x this (thinking modes)
    reasoning: float = Field(0.5, ge=0, le=1)
    coding: float = Field(0.5, ge=0, le=1)
    billing: Billing = "paid"
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None
    price_verified: bool = False
    price_source: str = ""
    free_allowance_tokens: int | None = None
    supports_json_schema: bool = False
    supports_json_mode: bool = True
    timeout_s: float = 120.0
    temperature: float = 0.2
    top_p: float | None = None
    extra_body: dict = Field(default_factory=dict)  # provider-specific knobs (e.g. reasoning toggle)
    base_url: str | None = None  # per-slot endpoint; defaults to provider.base_url
    api_key_env: str | None = None  # per-slot key; defaults to provider.api_key_env

    @model_validator(mode="after")
    def _free_needs_allowance(self) -> "SlotConfig":
        if self.billing == "free_allowance" and not self.free_allowance_tokens:
            raise ValueError(
                "billing='free_allowance' requires free_allowance_tokens; "
                "never assume a model is free without a confirmed allowance"
            )
        return self

    @property
    def has_price(self) -> bool:
        return self.price_input_per_1m is not None and self.price_output_per_1m is not None

    def expected_output(self, answer_tokens: int) -> int:
        """Billed output tokens for an answer of this size, including reasoning, capped by max_output_tokens."""
        return min(int(answer_tokens * self.reasoning_overhead), self.max_output_tokens)

    def list_price(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.billing == "local":
            return 0.0
        if not self.has_price:
            return None
        return (input_tokens * self.price_input_per_1m + output_tokens * self.price_output_per_1m) / 1e6


class ProviderConfig(BaseModel):
    base_url: str = "https://api.tokenfactory.nebius.com/v1"
    api_key_env: str = "NEBIUS_API_KEY"
    requires_api_key: bool = True  # false for local vLLM servers started without --api-key


class AccountConfig(BaseModel):
    credit_balance_usd: float | None = None
    credit_low_fraction: float = 0.30


class BudgetConfig(BaseModel):
    run_token_limit: int = 2_000_000
    run_cost_limit_usd: float | None = 5.0
    premium_cost_limit_usd: float | None = 2.0
    premium_token_limit: int = 400_000
    premium_reserve_fraction: float = 0.25
    low_budget_fraction: float = 0.20


class PolicyConfig(BaseModel):
    escalate_to_premium_after_failures: int = 2
    # Per-agent override of the B->A threshold, e.g. {"frontend": 3}: type errors are cheap for B to repair.
    escalate_to_premium_after_failures_by_agent: dict[str, int] = Field(default_factory=dict)
    promote_economy_after_failures: int = 1
    downstream_failure_cost_usd: dict[str, float] = Field(
        default_factory=lambda: {"low": 0.0, "medium": 0.02, "high": 0.10, "critical": 0.40}
    )


class RetryConfig(BaseModel):
    max_attempts_per_call: int = 4
    same_slot_transient_retries: int = 1
    backoff_base_s: float = 1.0
    backoff_max_s: float = 20.0
    max_rate_limit_wait_s: float = 15.0
    max_structured_repairs: int = 1


class HealthConfig(BaseModel):
    failure_threshold: int = 3
    cooldown_s: float = 60.0
    slow_latency_s: float = 45.0  # kept for old configs; slowness is judged per output token below
    slow_s_per_1k_tokens: float = 30.0  # seconds per 1,000 generated tokens above which a slot is "slow"
    default_rate_limit_cooldown_s: float = 20.0


class RouterConfig(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    account: AccountConfig = Field(default_factory=AccountConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    slots: dict[Slot, SlotConfig]

    @model_validator(mode="after")
    def _exactly_three_slots(self) -> "RouterConfig":
        if set(self.slots) != set(Slot):
            raise ValueError(f"exactly three slots A, B, C are required; got {sorted(s.value for s in self.slots)}")
        for s, sc in self.slots.items():
            if is_banned_model(sc.model_id):
                raise ValueError(f"slot {s.value}: {sc.model_id} is a Chinese-origin model, excluded by project rule")
        return self

    def slot(self, s: Slot) -> SlotConfig:
        return self.slots[s]

    def endpoint(self, s: Slot) -> tuple[str, str]:
        """(base_url, api_key_env) for a slot."""
        sc = self.slots[s]
        return sc.base_url or self.provider.base_url, sc.api_key_env or self.provider.api_key_env

    @classmethod
    def load(cls, path: str | Path | None = None, env: dict[str, str] | None = None) -> "RouterConfig":
        if env is None:
            load_dotenv()
            env = os.environ
        path = Path(path or env.get("AF_ROUTER_CONFIG") or DEFAULT_CONFIG_PATH)
        if not path.is_absolute() and not path.exists():
            path = DEFAULT_CONFIG_PATH.parents[1] / path  # relative to the project root
        raw = _load_toml(path)
        _apply_env(raw, env)
        return cls.model_validate(raw)


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=value lines from the project .env into os.environ (existing variables win)."""
    path = path or DEFAULT_CONFIG_PATH.parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    parent = raw.pop("extends", None)
    if parent:
        base = _load_toml((path.parent / parent).resolve())
        _deep_merge(base, raw)
        raw = base
    return raw


def _deep_merge(base: dict, over: dict) -> None:
    for k, v in over.items():
        if isinstance(v, dict) and v and isinstance(base.get(k), dict):  # an explicit {} clears the inherited table
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _expand(value: str, env) -> str:
    defaults = {"SPARK_HOST": "localhost"}
    return re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1)) or defaults.get(m.group(1), m.group(0)), value)


def _apply_env(raw: dict, env) -> None:
    def num(key: str, cast=float):
        return cast(env[key]) if env.get(key) not in (None, "") else None

    if env.get("NEBIUS_BASE_URL"):
        raw.setdefault("provider", {})["base_url"] = env["NEBIUS_BASE_URL"]
    prov = raw.setdefault("provider", {})
    if "base_url" in prov:
        prov["base_url"] = _expand(prov["base_url"], env)
    for s in ("A", "B", "C"):
        slot = raw.setdefault("slots", {}).setdefault(s, {})
        if slot.get("base_url"):
            slot["base_url"] = _expand(slot["base_url"], env)
        if env.get(f"AF_MODEL_{s}"):
            slot["model_id"] = env[f"AF_MODEL_{s}"]
        if env.get(f"AF_MODEL_{s}_ENABLED"):
            slot["enabled"] = env[f"AF_MODEL_{s}_ENABLED"].lower() not in ("0", "false", "no")
        if env.get(f"AF_MODEL_{s}_BILLING"):
            slot["billing"] = env[f"AF_MODEL_{s}_BILLING"]
        for suffix, field, cast in (
            ("PRICE_IN", "price_input_per_1m", float),
            ("PRICE_OUT", "price_output_per_1m", float),
            ("FREE_TOKENS", "free_allowance_tokens", int),
        ):
            v = num(f"AF_MODEL_{s}_{suffix}", cast)
            if v is not None:
                slot[field] = v
    budget = raw.setdefault("budget", {})
    for key, field, cast in (
        ("AF_RUN_TOKEN_LIMIT", "run_token_limit", int),
        ("AF_RUN_COST_LIMIT_USD", "run_cost_limit_usd", float),
        ("AF_PREMIUM_COST_LIMIT_USD", "premium_cost_limit_usd", float),
        ("AF_PREMIUM_TOKEN_LIMIT", "premium_token_limit", int),
    ):
        v = num(key, cast)
        if v is not None:
            budget[field] = v
    v = num("AF_CREDIT_BALANCE_USD")
    if v is not None:
        raw.setdefault("account", {})["credit_balance_usd"] = v
