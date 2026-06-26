"""Billing overview across the paid services ko uses — read-only balances + recent usage.

v1: OpenRouter (credit balance + daily/weekly/monthly usage). Each provider is a function that
returns a `Balance` (never raises — a missing key or API error becomes `Balance.error`), so the
overview degrades gracefully like `ko doctor`. Add a provider = add a function to PROVIDERS.

Note: pydantic-ai has no account-billing API (it does per-run token *cost*, not balances), so we
hit each provider's REST API directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


@dataclass
class Balance:
    provider: str
    account: str = ""  # which key/account (provider-specific label)
    remaining: float | None = None  # account credit remaining (USD)
    total: float | None = None  # credits granted/purchased
    used: float | None = None  # total used
    trend: str = ""  # e.g. "today $0.00 · month $0.11"
    error: str = ""  # set instead of the numbers when unavailable


def openrouter() -> Balance:
    """OpenRouter credit balance (/credits) + this key's recent usage (/key)."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return Balance("openrouter", error="OPENROUTER_API_KEY not set")
    headers = {"Authorization": f"Bearer {key}"}
    try:
        with httpx.Client(timeout=10.0, headers=headers) as c:
            credits = c.get("https://openrouter.ai/api/v1/credits").raise_for_status().json()["data"]
            keyinfo = c.get("https://openrouter.ai/api/v1/key").raise_for_status().json().get("data", {})
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return Balance("openrouter", error=str(e))
    total, used = credits.get("total_credits"), credits.get("total_usage")
    remaining = (total - used) if (total is not None and used is not None) else None
    trend = " · ".join(
        f"{label} ${keyinfo[field]:.2f}"
        for label, field in (("today", "usage_daily"), ("week", "usage_weekly"), ("month", "usage_monthly"))
        if keyinfo.get(field) is not None
    )
    # OpenRouter's key API exposes the key label + free-tier flag, but not account email/project.
    account = keyinfo.get("label", "") or ""
    if keyinfo.get("is_free_tier"):
        account = f"{account} (free tier)".strip()
    return Balance("openrouter", account, remaining, total, used, trend)


PROVIDERS = [openrouter]
