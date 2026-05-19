#!/usr/bin/env python3
"""Cardona Strategy Scanner — SPY/QQQ options signal detection on 1H bars."""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

SYMBOLS      = ["SPY", "QQQ"]
TIMEFRAME    = "1Hour"
LOOKBACK_DAYS   = 10     # ~70 bars across 7 trading days
LAST_N_BARS     = 20     # window for S/R analysis
DIRECTION_BARS  = 10     # window for trend analysis
SIGNAL_LOOKBACK = 5      # how many recent bars to scan for signals
ROUND_STEP   = 5         # $5 round-number increment
ROUND_RANGE  = 30        # ±$30 around current price
PROXIMITY    = 0.005     # 0.5% tolerance for "near a level"
DATA_URL     = "https://data.alpaca.markets/v2"
LINE         = "─" * 70


# ── Environment ───────────────────────────────────────────────────────────────

def load_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


# ── Alpaca API ────────────────────────────────────────────────────────────────

def fetch_bars(symbol: str) -> list[dict]:
    key    = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        sys.exit("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not found in .env")

    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params  = {
        "timeframe":  TIMEFRAME,
        "start":      start,
        "feed":       "sip",
        "limit":      1000,
        "adjustment": "raw",
    }

    url  = f"{DATA_URL}/stocks/{symbol}/bars"
    bars = []
    while True:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            r.raise_for_status()
        except requests.HTTPError as e:
            sys.exit(f"ERROR {symbol}: {e.response.status_code} — {e.response.text[:200]}")
        except requests.RequestException as e:
            sys.exit(f"ERROR fetching {symbol}: {e}")

        data = r.json()
        bars.extend(data.get("bars", []))
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token

    return bars


# ── Candle pattern detection ──────────────────────────────────────────────────

def is_hammer(bar: dict) -> bool:
    """
    Green candle where body sits in upper third and lower tail ≥ 2× body.
      - close > open  (green)
      - (open - low) / (high - low) ≥ 2/3  (open near top of range)
      - lower_tail ≥ 2 × body
    """
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    if c <= o:
        return False
    rng = h - l
    if rng == 0:
        return False
    body       = c - o
    lower_tail = o - l
    return lower_tail >= 2 * body and (o - l) / rng >= 2 / 3


def is_hanging_man(bar: dict) -> bool:
    """
    Red candle — identical shape to hammer but close < open.
      - close < open  (red)
      - (close - low) / (high - low) ≥ 2/3  (close near top of range)
      - lower_tail ≥ 2 × body
    """
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    if c >= o:
        return False
    rng = h - l
    if rng == 0:
        return False
    body       = o - c
    lower_tail = c - l
    return lower_tail >= 2 * body and (c - l) / rng >= 2 / 3


# ── Support & Resistance ──────────────────────────────────────────────────────

def _dedup(levels: list[float], tol: float = 0.003) -> list[float]:
    """Remove levels that are within tol% of their neighbour (keeps first seen)."""
    out: list[float] = []
    for lvl in levels:
        if not out or abs(lvl - out[-1]) / out[-1] > tol:
            out.append(lvl)
    return out


def find_support(bars: list[dict]) -> list[float]:
    """
    From last 20 bars: collect lows of green candles that had a lower tail.
    A green candle that dipped low then closed higher = price bounced.
    Returns ascending list, deduplicated within 0.3%.
    """
    lows = [
        bar["l"]
        for bar in bars[-LAST_N_BARS:]
        if bar["c"] > bar["o"] and (bar["o"] - bar["l"]) > 0
    ]
    return _dedup(sorted(lows))


def find_resistance(bars: list[dict]) -> list[float]:
    """
    From last 20 bars: collect highs of red candles that had an upper tail.
    A red candle that spiked high then closed lower = price rejected.
    Returns descending list, deduplicated within 0.3%.
    """
    highs = [
        bar["h"]
        for bar in bars[-LAST_N_BARS:]
        if bar["c"] < bar["o"] and (bar["h"] - bar["o"]) > 0
    ]
    return _dedup(sorted(highs, reverse=True))


def round_number_levels(price: float) -> list[float]:
    """Every $5 round number within ±$30 of current price."""
    base = round(price / ROUND_STEP) * ROUND_STEP
    lo   = int(base - ROUND_RANGE)
    hi   = int(base + ROUND_RANGE)
    return [float(x) for x in range(lo, hi + ROUND_STEP, ROUND_STEP)]


# ── Market direction ──────────────────────────────────────────────────────────

def market_trend(bars: list[dict]) -> str:
    """
    Count bar-to-bar transitions in the last 10 bars.
    Uptrend   : >50% of transitions are higher-high AND higher-low.
    Downtrend : >50% of transitions are lower-high  AND lower-low.
    Sideways  : mixed.
    """
    recent = bars[-DIRECTION_BARS:]
    n = len(recent) - 1
    if n < 3:
        return "sideways"

    hh = sum(1 for i in range(1, len(recent)) if recent[i]["h"] > recent[i-1]["h"])
    lh = sum(1 for i in range(1, len(recent)) if recent[i]["h"] < recent[i-1]["h"])
    hl = sum(1 for i in range(1, len(recent)) if recent[i]["l"] > recent[i-1]["l"])
    ll = sum(1 for i in range(1, len(recent)) if recent[i]["l"] < recent[i-1]["l"])

    if hh > n * 0.5 and hl > n * 0.5:
        return "uptrend"
    if lh > n * 0.5 and ll > n * 0.5:
        return "downtrend"
    return "sideways"


# ── Signal detection ──────────────────────────────────────────────────────────

def _near(price: float, level: float) -> bool:
    return abs(price - level) / level <= PROXIMITY


def find_signals(
    bars:       list[dict],
    supports:   list[float],
    resistances: list[float],
) -> list[dict]:
    """
    Scan last SIGNAL_LOOKBACK bars for hammer/hanging man near a level.
    Confirmation = the NEXT bar closing in the expected direction.
    """
    signals: list[dict] = []
    if len(bars) < 2:
        return signals

    start = max(0, len(bars) - SIGNAL_LOOKBACK - 1)
    for i in range(start, len(bars) - 1):
        sig  = bars[i]
        conf = bars[i + 1]

        if is_hammer(sig):
            matched = [s for s in supports if _near(sig["l"], s)]
            if matched:
                lvl = min(matched, key=lambda s: abs(s - sig["l"]))
                signals.append({
                    "type":      "CALL",
                    "pattern":   "Hammer",
                    "time":      sig["t"],
                    "close":     sig["c"],
                    "level":     lvl,
                    "level_tag": "support",
                    "confirmed": conf["c"] > conf["o"],
                    "conf_time": conf["t"],
                })

        if is_hanging_man(sig):
            matched = [r for r in resistances if _near(sig["h"], r)]
            if matched:
                lvl = min(matched, key=lambda r: abs(r - sig["h"]))
                signals.append({
                    "type":      "PUT",
                    "pattern":   "Hanging Man",
                    "time":      sig["t"],
                    "close":     sig["c"],
                    "level":     lvl,
                    "level_tag": "resistance",
                    "confirmed": conf["c"] < conf["o"],
                    "conf_time": conf["t"],
                })

    return signals


# ── Formatting ────────────────────────────────────────────────────────────────

def _bar_line(bar: dict) -> str:
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    color = "GRN" if c >= o else "RED"
    flag  = ""
    if is_hammer(bar):
        flag = "  ▲ HAMMER"
    elif is_hanging_man(bar):
        flag = "  ▼ HANGING MAN"
    return (
        f"  {bar['t'][:16]}  "
        f"O:{o:>8.2f}  H:{h:>8.2f}  L:{l:>8.2f}  C:{c:>8.2f}  [{color}]{flag}"
    )


def _section(title: str) -> None:
    print(f"\n  {title}")
    print(f"  {'·' * (len(title))}")


# ── Command: scan ─────────────────────────────────────────────────────────────

def cmd_scan() -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'═' * 70}")
    print(f"  CARDONA STRATEGY SCANNER  |  {ts} ET")
    print(f"{'═' * 70}")

    for symbol in SYMBOLS:
        bars = fetch_bars(symbol)
        if not bars:
            print(f"\n  {symbol}: no data returned")
            continue

        price = bars[-1]["c"]
        t_str = bars[-1]["t"][:16]
        tr    = market_trend(bars)
        sup   = find_support(bars)
        res   = find_resistance(bars)
        rnds  = round_number_levels(price)

        # Merge round numbers into S/R pools for signal detection
        sup_all = sorted(set(sup) | {r for r in rnds if r < price})
        res_all = sorted((set(res) | {r for r in rnds if r > price}), reverse=True)

        signals = find_signals(bars, sup_all, res_all)

        print(f"\n{LINE}")
        print(f"  {symbol}  |  ${price:.2f} as of {t_str}  |  Trend: {tr.upper()}")
        print(f"  Bars fetched: {len(bars)}")
        print(LINE)

        _section(f"Support (bounced lows — last {LAST_N_BARS} bars)")
        if sup:
            for lvl in sorted(sup):
                pct = (lvl - price) / price * 100
                print(f"    ${lvl:.2f}   ({pct:+.1f}%)")
        else:
            print("    none found")

        _section(f"Resistance (rejected highs — last {LAST_N_BARS} bars)")
        if res:
            for lvl in sorted(res, reverse=True):
                pct = (lvl - price) / price * 100
                print(f"    ${lvl:.2f}   ({pct:+.1f}%)")
        else:
            print("    none found")

        _section(f"Round numbers ($5 increments, ±${ROUND_RANGE})")
        for lvl in sorted(rnds, reverse=True):
            pct  = (lvl - price) / price * 100
            sign = "+" if pct >= 0 else ""
            tag  = "above" if lvl > price else ("below" if lvl < price else "~price")
            print(f"    ${lvl:<8.0f}  ({sign}{pct:.1f}%)  [{tag}]")

        _section("Signals")
        if signals:
            for s in signals:
                status = "CONFIRMED  " if s["confirmed"] else "unconfirmed"
                entry  = s["close"]
                if s["type"] == "CALL":
                    strike = entry + 10
                    action = f"Buy {symbol} ${strike:.0f} CALL  (≤2 wks, $200 max)"
                    warn   = tr != "uptrend"
                    warn_m = f"trend is {tr} — CALL requires uptrend"
                else:
                    strike = entry - 10
                    action = f"Buy {symbol} ${strike:.0f} PUT   (≤2 wks, $200 max)"
                    warn   = tr != "downtrend"
                    warn_m = f"trend is {tr} — PUT requires downtrend"

                print(f"\n    [{status}]  {s['type']} — {s['pattern']}")
                print(f"      Time      {s['time'][:16]}")
                print(f"      Near      {s['level_tag']} ${s['level']:.2f}")
                print(f"      Entry     ${entry:.2f}")
                print(f"      Action    {action}")
                if warn:
                    print(f"      !! WARN   {warn_m}")
        else:
            print(f"    No signals in last {SIGNAL_LOOKBACK} bars.")

    print()


# ── Command: candles ──────────────────────────────────────────────────────────

def cmd_candles(symbol: str) -> None:
    sym  = symbol.upper()
    bars = fetch_bars(sym)

    print(f"\n{LINE}")
    print(f"  LAST 10 ONE-HOUR BARS — {sym}")
    print(LINE)

    if not bars:
        print("  No data returned.")
        return

    print(f"  {'Timestamp':<16}  {'Open':>8}  {'High':>8}  {'Low':>8}  {'Close':>8}  Color")
    print(f"  {'─'*16}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  ─────")
    for bar in bars[-10:]:
        print(_bar_line(bar))

    # Summary stats for the 10 bars
    last10   = bars[-10:]
    hammers  = sum(1 for b in last10 if is_hammer(b))
    hangings = sum(1 for b in last10 if is_hanging_man(b))
    print(f"\n  Bars fetched total : {len(bars)}")
    print(f"  Hammers found      : {hammers}")
    print(f"  Hanging men found  : {hangings}")
    print()


# ── Command: levels ───────────────────────────────────────────────────────────

def cmd_levels(symbol: str) -> None:
    sym  = symbol.upper()
    bars = fetch_bars(sym)

    if not bars:
        print(f"  No data for {sym}.")
        return

    price = bars[-1]["c"]
    sup   = find_support(bars)
    res   = find_resistance(bars)
    rnds  = round_number_levels(price)

    print(f"\n{LINE}")
    print(f"  SUPPORT & RESISTANCE — {sym}  |  Current: ${price:.2f}")
    print(LINE)

    _section("Resistance (rejected highs)")
    if res:
        for lvl in sorted(res, reverse=True):
            pct = (lvl - price) / price * 100
            print(f"    ${lvl:.2f}   ({pct:+.1f}%)")
    else:
        print("    none found")

    print(f"\n  {'─'*30}")
    print(f"  Current price: ${price:.2f}")
    print(f"  {'─'*30}")

    _section("Support (bounced lows)")
    if sup:
        for lvl in sorted(sup, reverse=True):
            pct = (lvl - price) / price * 100
            print(f"    ${lvl:.2f}   ({pct:+.1f}%)")
    else:
        print("    none found")

    _section(f"Round numbers ($5 increments ±${ROUND_RANGE})")
    above = sorted([r for r in rnds if r > price], reverse=True)
    below = sorted([r for r in rnds if r < price], reverse=True)
    for lvl in above:
        pct = (lvl - price) / price * 100
        print(f"    ${lvl:<8.0f}  (+{pct:.1f}%)  [resistance]")
    print(f"    ${price:.2f}      (  0.0%)  [current]")
    for lvl in below:
        pct = (price - lvl) / price * 100
        print(f"    ${lvl:<8.0f}  (-{pct:.1f}%)  [support]")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

USAGE = """\
Cardona Strategy Scanner

Usage:
  python3 scripts/cardona_scanner.py scan
  python3 scripts/cardona_scanner.py candles SPY
  python3 scripts/cardona_scanner.py levels  SPY

Commands:
  scan          Full signal report for SPY and QQQ
  candles SYM   Last 10 one-hour bars with pattern markers
  levels  SYM   Support, resistance, and round-number levels
"""


def main() -> None:
    load_env()

    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "scan":
        cmd_scan()
    elif cmd == "candles":
        if len(sys.argv) < 3:
            sys.exit("Usage: cardona_scanner.py candles <SYMBOL>")
        cmd_candles(sys.argv[2])
    elif cmd == "levels":
        if len(sys.argv) < 3:
            sys.exit("Usage: cardona_scanner.py levels <SYMBOL>")
        cmd_levels(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}\n")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
