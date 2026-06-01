#!/usr/bin/env python3
"""
Cardona Options Research
Catalyst detection and IV Rank for the SIDEWAYS catalyst-only entry mode.

Public API
----------
earnings_within_days(symbol, n=5)  -> (bool, date | None)
is_earnings_today(symbol)          -> bool
get_iv_rank(symbol)                -> float | None   (0–100, None = insufficient data)
update_iv_history(symbol, price)   -> None           (call once per scan per symbol)
check_catalyst_exception(symbol, equity) -> (bool, str, dict)
"""

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# ── Paths & constants ──────────────────────────────────────────────────────────
_ROOT            = Path(__file__).parent.parent
_IV_FILE         = _ROOT / "data" / "iv_history.json"
_FIXED_OTM       = {"SPY"}          # 1-point strike step; all others $5
_MIN_IV_DAYS     = 30               # days of history required before IV Rank is trusted
IV_RANK_CAP      = 45.0             # max IV Rank for a catalyst entry
EARN_WINDOW_DAYS = 5                # look-ahead window for earnings
SIDEWAYS_BUDGET_PCT = 0.005         # 0.5 % of portfolio — risk cap in SIDEWAYS

_YAHOO_HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# ── Per-process earnings cache (1-hour TTL) ───────────────────────────────────
_earn_cache: dict[str, tuple[list, float]] = {}
_EARN_TTL = 3600


# ── Earnings detection ─────────────────────────────────────────────────────────

def _yahoo_earnings_dates(symbol: str) -> list[date]:
    """
    Fetch upcoming earnings dates from Yahoo Finance calendarEvents.
    Returns [] on any error (safe default → no catalyst found).
    Results cached 1 hour per symbol per process.
    """
    now = time.monotonic()
    cached = _earn_cache.get(symbol)
    if cached and now - cached[1] < _EARN_TTL:
        return cached[0]

    try:
        r = requests.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
            f"?modules=calendarEvents",
            headers=_YAHOO_HDRS,
            timeout=10,
        )
        if not r.ok:
            return []
        data    = r.json()
        result  = ((data.get("quoteSummary") or {}).get("result") or [])
        if not result:
            return []
        cal     = (result[0].get("calendarEvents") or {}).get("earnings") or {}
        raw     = cal.get("earningsDate") or []
        dates: list[date] = []
        for entry in raw:
            ts = entry.get("raw")
            if ts:
                dates.append(datetime.fromtimestamp(ts, tz=timezone.utc).date())
        _earn_cache[symbol] = (dates, now)
        return dates
    except Exception:
        return []


def earnings_within_days(symbol: str, n: int = EARN_WINDOW_DAYS) -> tuple[bool, date | None]:
    """
    Returns (True, earnings_date) if an earnings event falls within
    [today, today + n days] inclusive.  n=0 checks today only.
    Returns (False, None) if none found or on API error.
    """
    today  = date.today()
    cutoff = today + timedelta(days=n)
    for d in _yahoo_earnings_dates(symbol):
        if today <= d <= cutoff:
            return True, d
    return False, None


def is_earnings_today(symbol: str) -> bool:
    """True if the symbol has an earnings event scheduled for today."""
    found, _ = earnings_within_days(symbol, n=0)
    return found


# ── IV Rank ────────────────────────────────────────────────────────────────────

def _load_iv_history() -> dict:
    try:
        if _IV_FILE.exists():
            return json.loads(_IV_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_iv_history(hist: dict) -> None:
    _IV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _IV_FILE.write_text(json.dumps(hist, indent=2))


def _alpaca_atm_iv(symbol: str, current_price: float | None = None) -> float | None:
    """
    Fetch the implied volatility of the nearest ATM option from Alpaca snapshot.
    Uses the next weekly Friday expiry >= 7 days out, ±2 strikes around ATM,
    both calls and puts.  Returns the IV of the contract closest to ATM price.
    If current_price is supplied it skips the bars API call.
    """
    key    = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not key or not secret:
        return None

    price = current_price
    if price is None:
        try:
            r = requests.get(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                params={"timeframe": "1Hour", "limit": 1, "feed": "sip"},
                timeout=10,
            )
            if not r.ok:
                return None
            bars = r.json().get("bars", [])
            if not bars:
                return None
            price = bars[-1]["c"]
        except Exception:
            return None

    # Next weekly Friday >= 7 days out
    today  = date.today()
    expiry = None
    for offset in range(7, 22):
        d = today + timedelta(days=offset)
        if d.weekday() == 4:
            expiry = d
            break
    if not expiry:
        return None

    yy   = f"{expiry.year % 100:02d}"
    mm   = f"{expiry.month:02d}"
    dd   = f"{expiry.day:02d}"
    step = 1.0 if symbol.upper() in _FIXED_OTM else 5.0
    atm  = round(price / step) * step

    occ_syms = [
        f"{symbol}{yy}{mm}{dd}{cp}{int(round((atm + i * step) * 1000)):08d}"
        for i in range(-2, 3)
        for cp in ("C", "P")
        if (atm + i * step) > 0
    ]
    if not occ_syms:
        return None

    try:
        r = requests.get(
            "https://data.alpaca.markets/v1beta1/options/snapshots",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"symbols": ",".join(occ_syms)},
            timeout=10,
        )
        if not r.ok:
            return None
        snaps = r.json().get("snapshots", {})
        best_iv, best_dist = None, float("inf")
        for occ_sym, snap in snaps.items():
            iv = snap.get("impliedVolatility")
            if not iv or float(iv) <= 0:
                continue
            parsed_strike = int(occ_sym[-8:]) / 1000
            dist = abs(parsed_strike - price)
            if dist < best_dist:
                best_dist = dist
                best_iv   = float(iv)
        return best_iv
    except Exception:
        return None


def update_iv_history(symbol: str, current_price: float | None = None) -> None:
    """
    Fetch current ATM IV for symbol and append to rolling history.
    Called once per scan per symbol (after bar data is available).
    Stores at most one reading per calendar day per symbol.
    """
    iv = _alpaca_atm_iv(symbol, current_price)
    if iv is None or iv <= 0:
        return

    today   = date.today().isoformat()
    hist    = _load_iv_history()
    entries = hist.get(symbol, [])

    if entries and entries[-1]["date"] == today:
        entries[-1]["iv"] = iv          # refresh today's reading
    else:
        entries.append({"date": today, "iv": iv})

    hist[symbol] = entries[-400:]       # rolling ~14-month window
    _save_iv_history(hist)


def get_iv_rank(symbol: str) -> float | None:
    """
    IV Rank = (current_iv − iv_52w_low) / (iv_52w_high − iv_52w_low) × 100.

    Uses up to 365 stored daily readings.  Returns None when fewer than
    _MIN_IV_DAYS readings exist — caller treats None as "IV unknown, skip."
    """
    entries = _load_iv_history().get(symbol, [])
    if len(entries) < _MIN_IV_DAYS:
        return None

    window  = entries[-365:]
    ivs     = [e["iv"] for e in window]
    iv_min  = min(ivs)
    iv_max  = max(ivs)
    current = entries[-1]["iv"]

    if iv_max == iv_min:
        return 50.0

    rank = (current - iv_min) / (iv_max - iv_min) * 100.0
    return max(0.0, min(100.0, rank))


# ── Master catalyst check ──────────────────────────────────────────────────────

def check_catalyst_exception(
    symbol: str,
    equity: float,
) -> tuple[bool, str, dict]:
    """
    Evaluate ALL SIDEWAYS catalyst exception conditions (Rules 1–5).

    Returns:
        (True, "", info_dict)      — all conditions met, trade may proceed
        (False, reason_str, {})   — condition failed; skip with this reason

    The caller (run_scan) is responsible for:
        - Confirming regime == SIDEWAYS
        - Enforcing the 1-position limit (checked before this call)
        - Checking Friday (this function also checks, for defence in depth)
        - Enforcing the 6/6 signal quality (all normal gates already passed)
    """
    from zoneinfo import ZoneInfo
    et = datetime.now(ZoneInfo("America/New_York"))

    # ── Rule 4: No Friday entries ─────────────────────────────────────────────
    if et.weekday() == 4:
        return False, "NO_FRIDAY (SIDEWAYS entries blocked on Fridays)", {}

    # ── Rule 2a: Earnings within 5 calendar days ──────────────────────────────
    has_earn, earn_date = earnings_within_days(symbol, n=EARN_WINDOW_DAYS)
    if not has_earn:
        return False, "NO_CATALYST (no earnings within 5 days)", {}

    # ── Rule 2b: IV Rank ≤ 45 ────────────────────────────────────────────────
    iv_rank = get_iv_rank(symbol)
    if iv_rank is None:
        return (
            False,
            f"NO_IV_DATA (need {_MIN_IV_DAYS}+ days of IV history — "
            f"currently building)",
            {},
        )
    if iv_rank > IV_RANK_CAP:
        return False, f"IV_RANK_HIGH ({iv_rank:.0f} > {IV_RANK_CAP:.0f})", {}

    # ── Rule 2 (budget): 0.5 % portfolio risk cap ─────────────────────────────
    sideways_budget = equity * SIDEWAYS_BUDGET_PCT

    info = {
        "earnings_date":   earn_date.isoformat() if earn_date else "?",
        "iv_rank":         round(iv_rank, 1),
        "sideways_budget": round(sideways_budget, 2),
    }
    return True, "", info
