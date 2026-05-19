#!/usr/bin/env python3
"""Cardona Notify — EOD journal email via SendGrid."""

import json
import os
import re
import sys
import requests
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

TRADE_URL    = "https://paper-api.alpaca.markets/v2"
DATA_URL     = "https://data.alpaca.markets/v2"
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
OPTION_RE    = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
TP_THRESHOLD = 0.90

SYMBOLS   = ["SPY", "QQQ", "TSLA", "AAPL", "NVDA", "MSFT", "AMZN", "META", "GOOGL", "GLD"]
FIXED_OTM = {"SPY", "QQQ"}
WATCHLIST = set(SYMBOLS)

TIMEFRAME    = "1Hour"
LOOKBACK_DAYS   = 10
LAST_N_BARS     = 20
DIRECTION_BARS  = 10
SIGNAL_LOOKBACK = 5
ROUND_STEP   = 5
ROUND_RANGE  = 30
PROXIMITY    = 0.005

# ── Colors ─────────────────────────────────────────────────────────────────────
BG     = "#080c14"
CARD   = "#0d1117"
BORDER = "#1a2332"
TEXT   = "#c9d1d9"
DIM    = "#484f58"
GREEN  = "#00ff88"
RED    = "#ff4d4d"
YELLOW = "#ffd60a"
WHITE  = "#ffffff"


# ── Environment ────────────────────────────────────────────────────────────────

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


def _alpaca_hdrs() -> dict:
    return {
        "APCA-API-KEY-ID":     os.environ.get("APCA_API_KEY_ID", ""),
        "APCA-API-SECRET-KEY": os.environ.get("APCA_API_SECRET_KEY", ""),
    }


# ── Alpaca API ─────────────────────────────────────────────────────────────────

def get_positions() -> list:
    try:
        r = requests.get(f"{TRADE_URL}/positions", headers=_alpaca_hdrs(), timeout=15)
        return r.json() if r.ok else []
    except Exception:
        return []


def get_account() -> dict:
    try:
        r = requests.get(f"{TRADE_URL}/account", headers=_alpaca_hdrs(), timeout=15)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def get_orders_today(today: str) -> list:
    try:
        r = requests.get(
            f"{TRADE_URL}/orders",
            headers=_alpaca_hdrs(),
            params={"status": "all", "after": f"{today}T00:00:00Z",
                    "limit": 100, "direction": "desc"},
            timeout=15,
        )
        return r.json() if r.ok else []
    except Exception:
        return []


def fetch_bars(symbol: str) -> list:
    key    = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")
    start  = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params  = {"timeframe": TIMEFRAME, "start": start,
                "feed": "sip", "limit": 1000, "adjustment": "raw"}
    url, bars = f"{DATA_URL}/stocks/{symbol}/bars", []
    while True:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if not r.ok:
                return []
        except Exception:
            return []
        data = r.json()
        bars.extend(data.get("bars", []))
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    return bars


# ── Pattern detection ──────────────────────────────────────────────────────────

def is_hammer(bar: dict) -> bool:
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    if c <= o:
        return False
    rng = h - l
    if rng == 0:
        return False
    body = c - o
    return (o - l) >= 2 * body and (o - l) / rng >= 2 / 3


def is_hanging_man(bar: dict) -> bool:
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    if c >= o:
        return False
    rng = h - l
    if rng == 0:
        return False
    body = o - c
    return (c - l) >= 2 * body and (c - l) / rng >= 2 / 3


def _dedup(levels: list, tol: float = 0.003) -> list:
    out: list = []
    for lvl in levels:
        if not out or abs(lvl - out[-1]) / out[-1] > tol:
            out.append(lvl)
    return out


def find_support(bars: list) -> list:
    lows = [b["l"] for b in bars[-LAST_N_BARS:]
            if b["c"] > b["o"] and (b["o"] - b["l"]) > 0]
    return _dedup(sorted(lows))


def find_resistance(bars: list) -> list:
    highs = [b["h"] for b in bars[-LAST_N_BARS:]
             if b["c"] < b["o"] and (b["h"] - b["o"]) > 0]
    return _dedup(sorted(highs, reverse=True))


def round_number_levels(price: float) -> list:
    base = round(price / ROUND_STEP) * ROUND_STEP
    lo   = int(base - ROUND_RANGE)
    hi   = int(base + ROUND_RANGE)
    return [float(x) for x in range(lo, hi + ROUND_STEP, ROUND_STEP)]


def market_trend(bars: list) -> str:
    recent = bars[-DIRECTION_BARS:]
    n      = len(recent) - 1
    if n < 3:
        return "sideways"
    hh = sum(1 for i in range(1, len(recent)) if recent[i]["h"] > recent[i - 1]["h"])
    lh = sum(1 for i in range(1, len(recent)) if recent[i]["h"] < recent[i - 1]["h"])
    hl = sum(1 for i in range(1, len(recent)) if recent[i]["l"] > recent[i - 1]["l"])
    ll = sum(1 for i in range(1, len(recent)) if recent[i]["l"] < recent[i - 1]["l"])
    if hh > n * 0.5 and hl > n * 0.5:
        return "uptrend"
    if lh > n * 0.5 and ll > n * 0.5:
        return "downtrend"
    return "sideways"


def _near(price: float, level: float) -> bool:
    return abs(price - level) / level <= PROXIMITY


def find_signals(bars: list, supports: list, resistances: list) -> list:
    signals: list = []
    if len(bars) < 2:
        return signals
    start = max(0, len(bars) - SIGNAL_LOOKBACK - 1)
    for i in range(start, len(bars) - 1):
        sig, conf = bars[i], bars[i + 1]
        if is_hammer(sig):
            matched = [s for s in supports if _near(sig["l"], s)]
            if matched:
                lvl = min(matched, key=lambda s: abs(s - sig["l"]))
                signals.append({
                    "type": "CALL", "pattern": "Hammer",
                    "time": sig["t"], "close": sig["c"],
                    "conf_close": conf["c"], "level": lvl,
                    "level_tag": "support",
                    "confirmed": conf["c"] > conf["o"],
                    "conf_time": conf["t"],
                })
        if is_hanging_man(sig):
            matched = [r for r in resistances if _near(sig["h"], r)]
            if matched:
                lvl = min(matched, key=lambda r: abs(r - sig["h"]))
                signals.append({
                    "type": "PUT", "pattern": "Hanging Man",
                    "time": sig["t"], "close": sig["c"],
                    "conf_close": conf["c"], "level": lvl,
                    "level_tag": "resistance",
                    "confirmed": conf["c"] < conf["o"],
                    "conf_time": conf["t"],
                })
    return signals


def _suggested_strike(symbol: str, price: float, direction: str) -> float:
    if symbol in FIXED_OTM:
        return price + 10 if direction == "call" else price - 10
    raw = price * 1.02 if direction == "call" else price * 0.98
    return round(raw / 5) * 5


# ── Full scan ──────────────────────────────────────────────────────────────────

def scan_all() -> list:
    results = []
    for symbol in SYMBOLS:
        print(f"  {symbol}...", end="", flush=True)
        bars = fetch_bars(symbol)
        if not bars:
            results.append({"symbol": symbol, "error": "no data"})
            print(" no data")
            continue
        price   = bars[-1]["c"]
        trend   = market_trend(bars)
        sup     = find_support(bars)
        res     = find_resistance(bars)
        rnds    = round_number_levels(price)
        sup_all = sorted(set(sup) | {r for r in rnds if r < price})
        res_all = sorted((set(res) | {r for r in rnds if r > price}), reverse=True)
        signals = find_signals(bars, sup_all, res_all)
        top_sup = [s for s in sorted(sup_all, reverse=True) if s < price][:2]
        top_res = [r for r in res_all if r > price][:2]
        results.append({
            "symbol": symbol, "price": price, "trend": trend,
            "top_support": top_sup, "top_resistance": top_res,
            "signals": signals, "last_bar_time": bars[-1]["t"],
        })
        sig_label = f"{len(signals)} signal(s)" if signals else "no signals"
        print(f" ${price:.2f} {trend} {sig_label}")
    return results


# ── OCC / position helpers ─────────────────────────────────────────────────────

def _parse_occ(sym: str) -> dict:
    m = re.match(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", sym)
    if not m:
        return {}
    return {
        "underlying": m.group(1),
        "expiration": f"20{m.group(2)}-{m.group(3)}-{m.group(4)}",
        "type":       "CALL" if m.group(5) == "C" else "PUT",
        "strike":     int(m.group(6)) / 1000,
    }


def _dte(expiration: str) -> int:
    try:
        return (date.fromisoformat(expiration) - date.today()).days
    except Exception:
        return -1


def load_cardona_positions() -> dict:
    path = Path(__file__).parent.parent / "data" / "cardona_positions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ── Memory files ───────────────────────────────────────────────────────────────

def read_lessons(n: int = 3) -> list:
    path = Path(__file__).parent.parent / "memory" / "lessons.md"
    if not path.exists():
        return []
    lines = [
        ln.strip() for ln in path.read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
        and not ln.startswith("_") and ln.strip() != "---"
    ]
    return lines[-n:]


def read_cycles_summary() -> dict:
    """Return {cycle_num, trades_done, wins, losses} from cycles.md."""
    path = Path(__file__).parent.parent / "memory" / "cycles.md"
    if not path.exists():
        return {"cycle": 1, "trades": 0, "wins": 0, "losses": 0}
    text = path.read_text()
    # Find the last cycle heading
    cycle_num = 1
    for line in text.splitlines():
        m = re.match(r"##\s+Cycle\s+(\d+)", line)
        if m:
            cycle_num = int(m.group(1))
    # Count filled trade rows — a filled row has a date in the Date column.
    # Only examine the Result column (last column before the final |).
    trade_rows = re.findall(
        r"\|\s*\d{4}-\d{2}-\d{2}\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|",
        text,
    )
    trades  = len(trade_rows)
    wins    = sum(1 for r in trade_rows if re.search(r"\bW(in)?\b", r, re.I))
    losses  = sum(1 for r in trade_rows if re.search(r"\bL(oss)?\b", r, re.I))
    return {"cycle": cycle_num, "trades": trades,
            "wins": wins, "losses": losses}


# ── Self-evaluation generator ──────────────────────────────────────────────────

def _generate_self_eval(scan_results: list, cardona_positions: dict,
                        today: str) -> dict:
    """Return {q1, q2, q3} as plain strings for inclusion in email."""

    all_signals = []
    for sr in scan_results:
        for s in sr.get("signals", []):
            s = dict(s)
            s["symbol"] = sr["symbol"]
            s["trend"]  = sr.get("trend", "sideways")
            all_signals.append(s)

    # Q1 — what was seen
    if not all_signals:
        q1 = ("No hammer or hanging man patterns were detected on any of the "
               "10 symbols today. All candles failed the body/tail geometry "
               "requirements for a valid pattern.")
    else:
        parts = []
        for s in all_signals:
            status = "confirmed" if s["confirmed"] else "unconfirmed"
            parts.append(
                f"{s['symbol']} {s['type']} ({s['pattern']}, {status}, "
                f"near ${s['level']:.2f} {s['level_tag']})"
            )
        q1 = "Signals detected: " + " · ".join(parts) + "."

    # Q2 — missed / blocked analysis
    confirmed = [s for s in all_signals if s["confirmed"]]
    if not confirmed:
        watching = [s for s in all_signals if not s["confirmed"]]
        if watching:
            syms = ", ".join(s["symbol"] for s in watching)
            q2 = (f"No confirmed signals to evaluate. {syms} showed a pattern "
                  f"but the confirmation candle has not closed yet — still watching.")
        else:
            q2 = "No confirmed signals today. Nothing was missed; nothing qualified."
    else:
        missed_parts = []
        fired_parts  = []
        n_positions  = len(cardona_positions)
        for s in confirmed:
            sym       = s["symbol"]
            sig_type  = s["type"]
            trend     = s["trend"]
            trend_ok  = (sig_type == "CALL" and trend == "uptrend") or \
                        (sig_type == "PUT"  and trend == "downtrend")
            slot_ok   = n_positions < 2
            conf_price = s["conf_close"]
            level      = s["level"]
            drift      = ((conf_price - level) / level if sig_type == "CALL"
                          else (level - conf_price) / level)
            chase_ok   = drift <= PROXIMITY

            if trend_ok and slot_ok and chase_ok:
                fired_parts.append(f"{sym} {sig_type}")
            elif not trend_ok:
                need = "uptrend" if sig_type == "CALL" else "downtrend"
                missed_parts.append(
                    f"{sym} {sig_type} blocked — trend is {trend}, "
                    f"{sig_type.lower()} needs {need}"
                )
            elif not slot_ok:
                missed_parts.append(
                    f"{sym} {sig_type} blocked — position limit reached ({n_positions}/2)"
                )
            elif not chase_ok:
                missed_parts.append(
                    f"{sym} {sig_type} blocked — price drifted "
                    f"{drift*100:.2f}% past level (max 0.5%)"
                )

        lines = []
        if fired_parts:
            lines.append("Fired: " + ", ".join(fired_parts) + ".")
        if missed_parts:
            lines.append("Missed: " + " · ".join(missed_parts) + ".")
        if not lines:
            lines.append("No confirmed signals fired or missed today.")
        q2 = " ".join(lines)

    # Q3 — what to watch tomorrow
    watching = [s for s in all_signals if not s["confirmed"]]
    watch_parts = []
    for s in watching:
        conf_dir = "green" if s["type"] == "CALL" else "red"
        watch_parts.append(
            f"{s['symbol']} {s['type']}: if next bar closes {conf_dir}, "
            f"signal confirms near ${s['level']:.2f}"
        )
    trend_parts = []
    for sr in scan_results:
        if "error" not in sr and sr["trend"] == "sideways":
            top_sup = sr["top_support"][:1]
            top_res = sr["top_resistance"][:1]
            if top_sup and top_res:
                trend_parts.append(
                    f"{sr['symbol']} (sideways — watch ${top_sup[0]:.2f} support "
                    f"/ ${top_res[0]:.2f} resistance)"
                )
    lines3 = []
    if watch_parts:
        lines3.append("Unconfirmed signals to monitor: " + " · ".join(watch_parts) + ".")
    if trend_parts:
        lines3.append("Sideways symbols — wait for trend to resolve: "
                      + ", ".join(trend_parts[:3]) + ".")
    lines3.append(
        "SPY and QQQ trend direction at the open sets the session bias. "
        "Calls only in uptrend, puts only in downtrend, sit out sideways."
    )
    q3 = " ".join(lines3)

    return {"q1": q1, "q2": q2, "q3": q3}


# ── HTML helpers ───────────────────────────────────────────────────────────────

_FONT = ("'IBM Plex Mono', 'Courier New', Courier, monospace")

def _s(rules: dict) -> str:
    """Dict to inline style string."""
    return ";".join(f"{k}:{v}" for k, v in rules.items())


def _card(content: str, extra_style: str = "") -> str:
    style = (f"background:{CARD};border:1px solid {BORDER};"
             f"border-radius:6px;padding:20px 24px;"
             f"margin-bottom:20px;{extra_style}")
    return f'<div style="{style}">{content}</div>'


def _section_label(text: str) -> str:
    style = (f"color:{GREEN};font-size:10px;letter-spacing:2px;"
             f"text-transform:uppercase;margin:0 0 14px;padding:0;"
             f"font-family:{_FONT}")
    return f'<p style="{style}">{text}</p>'


def _table(headers: list, rows: list) -> str:
    th_style = (f"color:{DIM};font-size:11px;letter-spacing:1px;"
                f"text-transform:uppercase;padding:8px 12px;"
                f"border-bottom:1px solid {BORDER};text-align:left;"
                f"font-family:{_FONT};font-weight:400")
    td_style  = (f"padding:8px 12px;border-bottom:1px solid {BORDER};"
                 f"font-size:13px;color:{TEXT};font-family:{_FONT};vertical-align:top")
    last_td   = (f"padding:8px 12px;font-size:13px;color:{TEXT};"
                 f"font-family:{_FONT};vertical-align:top")
    table_s   = ("width:100%;border-collapse:collapse;"
                 f"background:{CARD};border-radius:4px;overflow:hidden")

    head = "".join(f'<th style="{th_style}">{h}</th>' for h in headers)
    body = ""
    for row in rows:
        cells = ""
        for i, cell in enumerate(row):
            s = last_td if i == len(row) - 1 else td_style
            cells += f'<td style="{s}">{cell}</td>'
        body += f"<tr>{cells}</tr>"
    return (f'<table style="{table_s}">'
            f"<thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _val(text, color: str = WHITE) -> str:
    return f'<span style="color:{color};font-weight:500">{text}</span>'


def _dim(text: str) -> str:
    return f'<span style="color:{DIM}">{text}</span>'


def _trend_label(trend: str) -> str:
    cfg = {
        "uptrend":   (GREEN,  "▲ UPTREND"),
        "downtrend": (RED,    "▼ DOWNTREND"),
        "sideways":  (YELLOW, "↔ SIDEWAYS"),
    }
    color, label = cfg.get(trend, (DIM, trend.upper()))
    return f'<span style="color:{color};font-weight:500">{label}</span>'


def _pct_color(pct: float) -> str:
    if pct >= 90:  return GREEN
    if pct >= 50:  return "#88ff44"
    if pct >= 0:   return YELLOW
    return RED


# ── HTML email builder ─────────────────────────────────────────────────────────

def build_html(today: str, positions: list, account: dict,
               scan_results: list, orders: list,
               cardona_positions: dict, lessons: list) -> str:

    ts           = datetime.now().strftime("%I:%M %p ET").lstrip("0")
    today_label  = date.today().strftime("%A, %B %-d, %Y")
    equity       = account.get("equity")
    cash         = account.get("cash")
    equity_f     = float(equity) if equity else 0.0
    cash_f       = float(cash)   if cash   else 0.0
    last_equity  = float(account.get("last_equity", equity_f))
    day_pl       = equity_f - last_equity
    day_pl_pct   = (day_pl / last_equity * 100) if last_equity else 0.0

    # Cardona-scoped options positions from Alpaca
    cardona_syms = set(cardona_positions.keys())
    opts = [p for p in positions
            if OPTION_RE.match(p["symbol"]) and p["symbol"] in cardona_syms]

    # Signals
    all_signals: list = []
    for sr in scan_results:
        for s in sr.get("signals", []):
            s = dict(s)
            s["symbol"] = sr["symbol"]
            s["trend"]  = sr.get("trend", "sideways")
            all_signals.append(s)

    # Cardona trades placed today (entries with entry_date == today)
    trades_today = {sym: meta for sym, meta in cardona_positions.items()
                    if meta.get("entry_date") == today}

    # Self-evaluation
    self_eval = _generate_self_eval(scan_results, cardona_positions, today)

    # Cycle data
    cycle = read_cycles_summary()
    trades_remaining = max(0, 10 - cycle["trades"])

    # ── Day P&L color
    pl_color = GREEN if day_pl >= 0 else RED
    pl_sign  = "+" if day_pl >= 0 else ""

    # ── base body style
    body_style = (f"background:{BG};margin:0;padding:0;"
                  f"font-family:{_FONT};color:{TEXT};-webkit-font-smoothing:antialiased")

    # ── wrapper
    wrap_style = "max-width:680px;margin:0 auto;padding:24px 16px"

    html_parts = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<title>Cardona Bot Journal</title>
</head>
<body style="{body_style}">
<div style="{wrap_style}">"""]

    # ── HEADER ────────────────────────────────────────────────────────────────
    header_style = (f"border-bottom:2px solid {BORDER};padding-bottom:20px;"
                    "margin-bottom:24px")
    badge_style  = (f"display:inline-block;background:{GREEN};color:#000;"
                    "font-size:9px;letter-spacing:2px;text-transform:uppercase;"
                    "padding:3px 8px;border-radius:3px;margin-bottom:10px;"
                    f"font-family:{_FONT};font-weight:600")
    title_style  = (f"color:{WHITE};font-size:24px;font-weight:600;"
                    f"margin:0 0 4px;font-family:{_FONT}")
    sub_style    = (f"color:{DIM};font-size:12px;margin:0;"
                    f"font-family:{_FONT}")

    equity_str = f"${equity_f:,.2f}" if equity else "N/A"
    pl_str     = f"{pl_sign}${abs(day_pl):,.2f} ({pl_sign}{day_pl_pct:.2f}%)"

    html_parts.append(f"""
<div style="{header_style}">
  <div style="{badge_style}">PAPER TRADING</div>
  <h1 style="{title_style}">Cardona Bot Journal</h1>
  <p style="{sub_style}">{today_label} &nbsp;·&nbsp; Generated {ts}</p>
</div>""")

    # ── STAT STRIP (equity / cash / day P&L / slots) ──────────────────────────
    stat_cell = (f"background:{CARD};border:1px solid {BORDER};border-radius:6px;"
                 "padding:14px 18px;text-align:center;width:25%")
    stat_label = (f"color:{DIM};font-size:9px;letter-spacing:2px;"
                  f"text-transform:uppercase;margin:0 0 6px;font-family:{_FONT}")
    stat_val   = (f"color:{WHITE};font-size:16px;font-weight:600;"
                  f"margin:0;font-family:{_FONT}")
    n_slots = max(0, 2 - len(opts))

    html_parts.append(f"""
<table style="width:100%;border-collapse:collapse;margin-bottom:24px">
<tr>
  <td style="{stat_cell}">
    <p style="{stat_label}">Equity</p>
    <p style="{stat_val}">{equity_str}</p>
  </td>
  <td style="width:8px"></td>
  <td style="{stat_cell}">
    <p style="{stat_label}">Cash</p>
    <p style="{stat_val}">${cash_f:,.0f}</p>
  </td>
  <td style="width:8px"></td>
  <td style="{stat_cell}">
    <p style="{stat_label}">Day P&amp;L</p>
    <p style="{stat_val};color:{pl_color}">{pl_str}</p>
  </td>
  <td style="width:8px"></td>
  <td style="{stat_cell}">
    <p style="{stat_label}">Slots Free</p>
    <p style="{stat_val};color:{GREEN if n_slots > 0 else RED}">{n_slots} / 2</p>
  </td>
</tr>
</table>""")

    # ── SECTION 1: Account Snapshot ────────────────────────────────────────────
    acc_rows = [
        ("Portfolio Value",    _val(equity_str)),
        ("Cash Available",     _val(f"${cash_f:,.2f}")),
        ("Cardona Positions",  _val(f"{len(opts)} open")),
        ("Auto-Trade Slots",   _val(f"{n_slots} / 2 free",
                                    GREEN if n_slots > 0 else YELLOW)),
        ("Day P&amp;L",        _val(pl_str, pl_color)),
        ("Paper Account",      _dim("Alpaca paper trading — not real money")),
    ]
    content = _section_label("01 — Account Snapshot")
    content += _table(["Field", "Value"], acc_rows)
    html_parts.append(_card(content))

    # ── SECTION 2: Signals Today ───────────────────────────────────────────────
    sig_rows = []
    for sr in scan_results:
        if "error" in sr:
            sig_rows.append([_val(sr["symbol"]), _dim("—"), _dim("error"), _dim("no data")])
            continue
        sigs  = sr["signals"]
        trend = _trend_label(sr["trend"])
        if not sigs:
            sig_rows.append([_val(sr["symbol"]),
                              f'<span style="color:{DIM};font-size:12px">'
                              f'${sr["price"]:.2f}</span>',
                              trend,
                              _dim("—"), _dim("No signal")])
        else:
            for s in sigs:
                stat_color = GREEN if s["confirmed"] else YELLOW
                status     = "CONFIRMED" if s["confirmed"] else "Watching"
                sig_rows.append([
                    _val(sr["symbol"]),
                    f'<span style="color:{DIM};font-size:12px">'
                    f'${sr["price"]:.2f}</span>',
                    trend,
                    _val(f'{s["type"]} — {s["pattern"]}',
                         GREEN if s["type"] == "CALL" else RED),
                    f'<span style="color:{stat_color};font-weight:500">'
                    f'{status}</span>',
                ])

    content  = _section_label("02 — Signals Today")
    content += f'<p style="color:{DIM};font-size:11px;margin:0 0 12px">10 symbols scanned on 1-hour bars</p>'
    content += _table(["Symbol", "Price", "Trend", "Signal", "Status"], sig_rows)
    html_parts.append(_card(content))

    # ── SECTION 3: Trades Placed ───────────────────────────────────────────────
    content = _section_label("03 — Trades Placed")
    if not trades_today:
        content += f'<p style="color:{DIM};font-size:13px;margin:0">No Cardona entries today.</p>'
    else:
        trade_rows = []
        for occ_sym, meta in trades_today.items():
            parsed = _parse_occ(occ_sym)
            under  = parsed.get("underlying", occ_sym)
            typ    = parsed.get("type", "?")
            strike = f'${parsed.get("strike", 0):.0f}' if parsed else "?"
            exp    = meta.get("expiry", "?")
            entry_est = meta.get("entry_price_estimate", 0)
            cost_est  = entry_est * 100 if entry_est else 0
            trade_rows.append([
                _val(under),
                _val(typ, GREEN if typ == "CALL" else RED),
                _val(strike),
                _val(exp),
                _val(f"${entry_est:.2f}" if entry_est else "?"),
                _val(f"${cost_est:.0f}" if cost_est else "?"),
            ])
        content += _table(
            ["Symbol", "Direction", "Strike", "Expiry", "Premium", "Cost"],
            trade_rows,
        )
    html_parts.append(_card(content))

    # ── SECTION 4: Open Positions ─────────────────────────────────────────────
    content = _section_label("04 — Open Positions")
    if not opts:
        n_cardona = len(cardona_positions)
        if n_cardona == 0:
            content += f'<p style="color:{DIM};font-size:13px;margin:0">No open Cardona positions. 2 slots available.</p>'
        else:
            content += (f'<p style="color:{YELLOW};font-size:13px;margin:0">'
                        f'{n_cardona} position(s) in registry but not found on Alpaca '
                        f'(may be closed or pending fill).</p>')
    else:
        pos_rows = []
        for pos in opts:
            sym    = pos["symbol"]
            pct    = float(pos.get("unrealized_plpc", 0)) * 100
            pl_d   = float(pos.get("unrealized_pl", 0))
            entry  = float(pos.get("avg_entry_price", 0))
            cur    = float(pos.get("current_price", 0))
            parsed = _parse_occ(sym)
            under  = parsed.get("underlying", sym)
            typ    = parsed.get("type", "?")
            exp    = parsed.get("expiration", "?")
            dte    = _dte(exp)
            pctc   = _pct_color(pct)
            if pct >= TP_THRESHOLD * 100:
                stat = f'<span style="color:{GREEN};font-weight:600">★ TAKE PROFIT</span>'
            elif dte <= 2:
                stat = f'<span style="color:{YELLOW};font-weight:600">⚠ EXPIRY SOON</span>'
            else:
                stat = f'<span style="color:{DIM}">Holding</span>'
            pos_rows.append([
                _val(f"{under} {typ}"),
                _val(f'${parsed.get("strike", 0):.0f}') if parsed else _dim("?"),
                _val(exp),
                f'<span style="color:{YELLOW if dte <= 2 else DIM}">{dte}d</span>',
                _val(f"${entry:.2f}"),
                _val(f"${cur:.2f}"),
                f'<span style="color:{pctc};font-weight:600">{pct:+.1f}% (${pl_d:+.0f})</span>',
                stat,
            ])
        content += _table(
            ["Contract", "Strike", "Expiry", "DTE", "Entry", "Current", "P&amp;L", "Status"],
            pos_rows,
        )
    html_parts.append(_card(content))

    # ── SECTION 5: Cycle Tracking ──────────────────────────────────────────────
    cycle_rows = [
        ("Current cycle",    _val(f"Cycle {cycle['cycle']}")),
        ("Trades completed", _val(f"{cycle['trades']} / 10")),
        ("Wins / Losses",    _val(f"{cycle['wins']}W / {cycle['losses']}L")),
        ("Trades remaining", _val(str(trades_remaining))),
        ("Target",           _dim("6 – 7 wins per 10 trades")),
        ("Exit rule",        _dim("Close at 100%+ gain · let losers expire")),
    ]
    content  = _section_label("05 — Cycle Tracking")
    content += _table(["Metric", "Value"], cycle_rows)

    # Progress bar
    filled   = min(cycle["trades"], 10)
    bar_html = ""
    for i in range(10):
        if i < filled:
            color = GREEN if i < cycle["wins"] else RED
        else:
            color = BORDER
        bar_html += (f'<span style="display:inline-block;width:44px;height:10px;'
                     f'background:{color};border-radius:2px;margin-right:4px"></span>')
    content += (f'<div style="margin-top:14px">'
                f'<p style="color:{DIM};font-size:10px;letter-spacing:1px;'
                f'text-transform:uppercase;margin:0 0 8px">Trade progress</p>'
                f'{bar_html}</div>')
    html_parts.append(_card(content))

    # ── SECTION 6: Self Evaluation ─────────────────────────────────────────────
    q_label = (f"color:{GREEN};font-size:11px;letter-spacing:1px;"
               f"text-transform:uppercase;margin:0 0 6px;font-family:{_FONT}")
    a_style = (f"color:{TEXT};font-size:13px;line-height:1.6;"
               f"margin:0 0 18px;font-family:{_FONT}")

    content = _section_label("06 — Self Evaluation")
    for q_num, q_text, answer in [
        ("Q1", "What signals were seen today?",         self_eval["q1"]),
        ("Q2", "Were any signals missed and why?",      self_eval["q2"]),
        ("Q3", "What to watch tomorrow?",               self_eval["q3"]),
    ]:
        content += (f'<p style="{q_label}">{q_num} — {q_text}</p>'
                    f'<p style="{a_style}">{answer}</p>')

    # Append recent lessons if any
    if lessons:
        content += (f'<p style="{q_label}">Recent lessons</p>')
        for ln in lessons:
            content += (f'<p style="color:{DIM};font-size:12px;'
                        f'line-height:1.5;margin:0 0 6px;font-family:{_FONT}">'
                        f'· {ln}</p>')
    html_parts.append(_card(content))

    # ── FOOTER ────────────────────────────────────────────────────────────────
    footer_style = (f"color:{DIM};font-size:11px;text-align:center;"
                    f"line-height:1.6;margin-top:12px;font-family:{_FONT}")
    html_parts.append(f"""
<p style="{footer_style}">
  Cardona Strategy Bot &nbsp;·&nbsp; Paper trading account &nbsp;·&nbsp; {today}<br>
  All positions are simulated. Not financial advice.<br>
  <a href="mailto:{os.environ.get('NOTIFY_EMAIL','')}"
     style="color:{DIM}">Unsubscribe</a>
</p>
</div>
</body>
</html>""")

    return "\n".join(html_parts)


# ── Plain text fallback ────────────────────────────────────────────────────────

def build_text(today: str, positions: list, account: dict,
               scan_results: list, cardona_positions: dict,
               lessons: list) -> str:
    equity  = account.get("equity")
    cash    = account.get("cash")
    equity_f = float(equity) if equity else 0.0
    cash_f   = float(cash) if cash else 0.0
    last_eq  = float(account.get("last_equity", equity_f))
    day_pl   = equity_f - last_eq
    pl_sign  = "+" if day_pl >= 0 else ""

    cardona_syms = set(cardona_positions.keys())
    opts = [p for p in positions
            if OPTION_RE.match(p["symbol"]) and p["symbol"] in cardona_syms]

    today_label = date.today().strftime("%A, %B %-d, %Y")
    lines = [
        f"CARDONA BOT JOURNAL — {today_label}",
        "=" * 62, "",
        f"Equity: ${equity_f:,.2f}  |  Cash: ${cash_f:,.2f}  |  "
        f"Day P&L: {pl_sign}${abs(day_pl):,.2f}", "",
    ]

    # Signals
    lines += ["SIGNALS TODAY (10 symbols scanned)", ""]
    arrows = {"uptrend": "▲", "downtrend": "▼", "sideways": "↔"}
    for sr in scan_results:
        if "error" in sr:
            lines.append(f"  {sr['symbol']:6}  ERROR")
            continue
        arrow   = arrows.get(sr["trend"], "?")
        sigs    = sr["signals"]
        sig_str = (", ".join(f"{s['type']} {'CONFIRMED' if s['confirmed'] else 'watching'}"
                             for s in sigs)
                   if sigs else "no signals")
        lines.append(f"  {sr['symbol']:6}  ${sr['price']:.2f}  "
                     f"{arrow} {sr['trend'].upper():<12}  {sig_str}")

    # Trades placed
    trades_today = {sym: meta for sym, meta in cardona_positions.items()
                    if meta.get("entry_date") == today}
    lines += ["", f"TRADES PLACED ({len(trades_today)})"]
    for occ_sym, meta in trades_today.items():
        parsed = _parse_occ(occ_sym)
        under  = parsed.get("underlying", occ_sym)
        typ    = parsed.get("type", "?")
        strike = f'${parsed.get("strike", 0):.0f}' if parsed else "?"
        exp    = meta.get("expiry", "?")
        lines.append(f"  {under} {typ} {strike} exp {exp}")
    if not trades_today:
        lines.append("  None")

    # Open positions
    lines += ["", f"OPEN POSITIONS ({len(opts)})"]
    for pos in opts:
        sym    = pos["symbol"]
        pct    = float(pos.get("unrealized_plpc", 0)) * 100
        pl_d   = float(pos.get("unrealized_pl", 0))
        parsed = _parse_occ(sym)
        desc   = (f"{parsed['underlying']} ${parsed['strike']:.0f} "
                  f"{parsed['type']} exp {parsed['expiration']}" if parsed else sym)
        tp_tag = "  *** TAKE PROFIT ***" if pct >= TP_THRESHOLD * 100 else ""
        lines.append(f"  {desc}  {pct:+.1f}% (${pl_d:+.0f}){tp_tag}")
    if not opts:
        lines.append("  None (2 slots available)")

    # Self eval
    se = _generate_self_eval(scan_results, cardona_positions, today)
    cycle = read_cycles_summary()
    lines += ["", f"CYCLE {cycle['cycle']} — {cycle['trades']}/10 trades  "
              f"({cycle['wins']}W / {cycle['losses']}L)"]
    lines += ["", "SELF EVALUATION",
              "", "Q1 — What signals were seen?", f"  {se['q1']}",
              "", "Q2 — Were any signals missed?", f"  {se['q2']}",
              "", "Q3 — What to watch tomorrow?", f"  {se['q3']}"]

    if lessons:
        lines += ["", "RECENT LESSONS"]
        for ln in lessons:
            lines.append(f"  · {ln}")

    lines += ["", "─" * 62,
              "Cardona Strategy Bot — Paper Trading Account — Not financial advice"]
    return "\n".join(lines)


# ── SendGrid delivery ──────────────────────────────────────────────────────────

def send_email(subject: str, text_body: str, html_body: str) -> bool:
    api_key   = os.environ.get("SENDGRID_API_KEY")
    to_email  = os.environ.get("NOTIFY_EMAIL")

    if not api_key:
        print("ERROR: SENDGRID_API_KEY not set in .env")
        return False
    if not to_email:
        print("ERROR: NOTIFY_EMAIL not set in .env")
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from":    {"email": to_email, "name": "Cardona Bot"},
        "reply_to": {"email": to_email, "name": "Cardona Bot"},
        "subject": subject,
        "headers": {
            "List-Unsubscribe": f"<mailto:{to_email}?subject=unsubscribe>",
            "X-Mailer": "Cardona Strategy Bot",
        },
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html",  "value": html_body},
        ],
    }
    hdrs = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        r = requests.post(SENDGRID_URL, json=payload, headers=hdrs, timeout=20)
    except requests.RequestException as e:
        print(f"ERROR sending email: {e}")
        return False

    if r.status_code == 202:
        print(f"Email sent → {to_email}")
        return True

    print(f"SendGrid error {r.status_code}: {r.text[:200]}")
    return False


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    load_env()

    today     = date.today().isoformat()
    test_mode = "--test" in sys.argv or "--dry-run" in sys.argv

    # Format subject: "Cardona Bot Journal — May 19, 2026"
    today_label = date.today().strftime("%B %-d, %Y")

    print(f"Cardona Notify — {today}")

    print("Fetching account data...", end="", flush=True)
    positions         = get_positions()
    account           = get_account()
    orders            = get_orders_today(today)
    cardona_positions = load_cardona_positions()
    print(" done")

    print("Running EOD scan:")
    scan_results = scan_all()

    lessons = read_lessons()

    # Scoped opts for subject line
    cardona_syms = set(cardona_positions.keys())
    opts    = [p for p in positions
               if OPTION_RE.match(p["symbol"]) and p["symbol"] in cardona_syms]
    tp_cnt  = sum(1 for p in opts
                  if float(p.get("unrealized_plpc", 0)) >= TP_THRESHOLD)

    tags = []
    if tp_cnt:
        tags.append(f"{tp_cnt} TAKE PROFIT")

    all_sigs   = [s for sr in scan_results for s in sr.get("signals", [])]
    conf_cnt   = sum(1 for s in all_sigs if s.get("confirmed"))
    if conf_cnt:
        tags.append(f"{conf_cnt} signal" + ("s" if conf_cnt > 1 else ""))

    tag_str = (" — " + " · ".join(tags)) if tags else ""
    subject = f"Cardona Bot Journal — {today_label}{tag_str}"

    html_body = build_html(today, positions, account, scan_results,
                           orders, cardona_positions, lessons)
    text_body = build_text(today, positions, account, scan_results,
                           cardona_positions, lessons)

    if test_mode:
        print(f"\nSubject: {subject}")
        print("[--test mode: email not sent]\n")
        print(text_body)
        return

    send_email(subject, text_body, html_body)


if __name__ == "__main__":
    main()
