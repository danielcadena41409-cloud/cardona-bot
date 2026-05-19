#!/usr/bin/env python3
"""Cardona Notify — Rich EOD journal email via SendGrid."""

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

SYMBOLS      = ["SPY", "QQQ", "TSLA", "AAPL", "NVDA", "MSFT", "AMZN", "META", "GOOGL", "GLD"]
FIXED_OTM    = {"SPY", "QQQ"}
TIMEFRAME    = "1Hour"
LOOKBACK_DAYS   = 10
LAST_N_BARS     = 20
DIRECTION_BARS  = 10
SIGNAL_LOOKBACK = 5
ROUND_STEP   = 5
ROUND_RANGE  = 30
PROXIMITY    = 0.005


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
        params = {
            "status":    "all",
            "after":     f"{today}T00:00:00Z",
            "limit":     100,
            "direction": "desc",
        }
        r = requests.get(f"{TRADE_URL}/orders", headers=_alpaca_hdrs(),
                         params=params, timeout=15)
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
    body       = c - o
    lower_tail = o - l
    return lower_tail >= 2 * body and (o - l) / rng >= 2 / 3


def is_hanging_man(bar: dict) -> bool:
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    if c >= o:
        return False
    rng = h - l
    if rng == 0:
        return False
    body       = o - c
    lower_tail = c - l
    return lower_tail >= 2 * body and (c - l) / rng >= 2 / 3


# ── S/R and trend ──────────────────────────────────────────────────────────────

def _dedup(levels: list, tol: float = 0.003) -> list:
    out: list = []
    for lvl in levels:
        if not out or abs(lvl - out[-1]) / out[-1] > tol:
            out.append(lvl)
    return out


def find_support(bars: list) -> list:
    lows = [
        bar["l"] for bar in bars[-LAST_N_BARS:]
        if bar["c"] > bar["o"] and (bar["o"] - bar["l"]) > 0
    ]
    return _dedup(sorted(lows))


def find_resistance(bars: list) -> list:
    highs = [
        bar["h"] for bar in bars[-LAST_N_BARS:]
        if bar["c"] < bar["o"] and (bar["h"] - bar["o"]) > 0
    ]
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
        sig  = bars[i]
        conf = bars[i + 1]
        if is_hammer(sig):
            matched = [s for s in supports if _near(sig["l"], s)]
            if matched:
                lvl = min(matched, key=lambda s: abs(s - sig["l"]))
                signals.append({
                    "type":       "CALL",
                    "pattern":    "Hammer",
                    "time":       sig["t"],
                    "close":      sig["c"],
                    "conf_close": conf["c"],
                    "level":      lvl,
                    "level_tag":  "support",
                    "confirmed":  conf["c"] > conf["o"],
                    "conf_time":  conf["t"],
                })
        if is_hanging_man(sig):
            matched = [r for r in resistances if _near(sig["h"], r)]
            if matched:
                lvl = min(matched, key=lambda r: abs(r - sig["h"]))
                signals.append({
                    "type":       "PUT",
                    "pattern":    "Hanging Man",
                    "time":       sig["t"],
                    "close":      sig["c"],
                    "conf_close": conf["c"],
                    "level":      lvl,
                    "level_tag":  "resistance",
                    "confirmed":  conf["c"] < conf["o"],
                    "conf_time":  conf["t"],
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
            "symbol":         symbol,
            "price":          price,
            "trend":          trend,
            "support":        sup,
            "resistance":     res,
            "top_support":    top_sup,
            "top_resistance": top_res,
            "signals":        signals,
            "last_bar_time":  bars[-1]["t"],
        })
        sig_label = f"{len(signals)} signal(s)" if signals else "no signals"
        print(f" ${price:.2f} {trend} {sig_label}")
    return results


# ── OCC parser ─────────────────────────────────────────────────────────────────

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


# ── Memory files ───────────────────────────────────────────────────────────────

def read_lessons(n: int = 5) -> list:
    path = Path(__file__).parent.parent / "memory" / "lessons.md"
    if not path.exists():
        return []
    lines = [
        ln.strip() for ln in path.read_text().splitlines()
        if ln.strip()
        and not ln.startswith("#")
        and not ln.startswith("_")
        and ln.strip() != "---"
    ]
    return lines[-n:]


def read_cycles_text() -> str:
    path = Path(__file__).parent.parent / "memory" / "cycles.md"
    return path.read_text() if path.exists() else ""


# ── HTML helpers ───────────────────────────────────────────────────────────────

TABLE_OPEN  = ('<table style="width:100%;border-collapse:collapse;background:#111;'
               'border-radius:6px;overflow:hidden;margin-bottom:24px">')
TABLE_CLOSE = "</table>"

def _h2(title: str) -> str:
    return (f'<h2 style="color:#00aaff;border-bottom:1px solid #1e3a5a;'
            f'padding-bottom:8px;margin-top:40px;font-size:17px">{title}</h2>')


def _h3(title: str) -> str:
    return (f'<h3 style="color:#88bbee;font-size:12px;text-transform:uppercase;'
            f'letter-spacing:1px;margin-top:24px;margin-bottom:8px">{title}</h3>')


def _th(val: str) -> str:
    return (f'<th style="padding:8px 12px;text-align:left;color:#555;font-size:11px;'
            f'text-transform:uppercase;background:#0a0a0a">{val}</th>')


def _td(val, color: str = "") -> str:
    style = "padding:8px 12px;border-bottom:1px solid #1a1a1a"
    if color:
        style += f";color:{color}"
    return f'<td style="{style}">{val}</td>'


def _check(cond: bool) -> str:
    if cond:
        return '<span style="color:#00cc55;font-weight:bold">✓</span>'
    return '<span style="color:#dd3300;font-weight:bold">✗</span>'


def _trend_badge(trend: str) -> str:
    cfg = {
        "uptrend":   ("#00cc55", "▲ UPTREND"),
        "downtrend": ("#dd3300", "▼ DOWNTREND"),
        "sideways":  ("#888800", "↔ SIDEWAYS"),
    }
    color, label = cfg.get(trend, ("#888", trend.upper()))
    return f'<span style="color:{color};font-weight:bold">{label}</span>'


def _pct_color(pct: float) -> str:
    if pct >= 90:  return "#00dd55"
    if pct >= 50:  return "#88cc00"
    if pct >= 0:   return "#cccc00"
    return "#dd3300"


def _thead(*headers) -> str:
    return f"<thead><tr>{''.join(_th(h) for h in headers)}</tr></thead>"


# ── HTML builder ───────────────────────────────────────────────────────────────

def build_html(today: str, positions: list, account: dict,
               scan_results: list, orders: list, lessons: list) -> str:

    ts      = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    equity  = account.get("equity")
    cash    = account.get("cash")
    opts    = [p for p in positions if OPTION_RE.match(p["symbol"])]

    # Collect all signals with their symbol
    all_signals: list = []
    for sr in scan_results:
        for s in sr.get("signals", []):
            s = dict(s)
            s["symbol"] = sr["symbol"]
            s["trend"]  = sr.get("trend", "sideways")
            all_signals.append(s)

    confirmed_sigs  = [s for s in all_signals if s["confirmed"]]
    option_orders   = [o for o in orders if OPTION_RE.match(o.get("symbol", ""))]
    filled_orders   = [o for o in option_orders if o.get("status") == "filled"]

    # ── Page header ────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html>
<body style="background:#0d0d0d;color:#e0e0e0;font-family:monospace,monospace;
             padding:24px;margin:0">
<div style="max-width:860px;margin:0 auto">

<h1 style="color:#ffffff;margin-bottom:4px;font-size:24px;letter-spacing:-0.5px">
  Options Journal &nbsp;—&nbsp; {today}
</h1>
<p style="color:#444;font-size:12px;margin-top:0">
  Generated {ts} &nbsp;·&nbsp; Cardona Strategy Bot &nbsp;·&nbsp; Paper Trading
</p>
<hr style="border:none;border-top:2px solid #1a2a3a;margin:16px 0 28px">

"""

    # ── Session Summary ────────────────────────────────────────────────────────
    html += _h2("Session Summary")
    equity_str = f"${float(equity):,.2f}" if equity else "N/A"
    html += TABLE_OPEN + _thead("Metric", "Value") + "<tbody>"
    for label, val in [
        ("Symbols scanned",       len([r for r in scan_results if "error" not in r])),
        ("Signals detected",      len(all_signals)),
        ("Confirmed signals",     len(confirmed_sigs)),
        ("Auto-trades fired",     len(filled_orders)),
        ("Positions open",        len(opts)),
        ("Auto-trade slots free", f"{max(0, 2 - len(opts))} / 2"),
        ("Account equity",        equity_str),
    ]:
        html += f"<tr>{_td(label, '#888')}{_td(f'<b>{val}</b>')}</tr>"
    html += f"</tbody>{TABLE_CLOSE}"

    # ── Watchlist Scan ─────────────────────────────────────────────────────────
    html += _h2("Watchlist Scan — EOD State")
    html += TABLE_OPEN + _thead("Symbol", "Price", "Trend", "Top Support", "Top Resistance", "Signal") + "<tbody>"
    for sr in scan_results:
        if "error" in sr:
            no_data = '<span style="color:#555">no data</span>'
            html += (f"<tr>{_td('<b>' + sr['symbol'] + '</b>')}"
                     f"{_td('—')}{_td('ERROR')}{_td('—')}{_td('—')}"
                     f"{_td(no_data)}</tr>")
            continue
        top_sup = " / ".join(f"${s:.2f}" for s in sr["top_support"]) or "—"
        top_res = " / ".join(f"${r:.2f}" for r in sr["top_resistance"]) or "—"
        sigs    = sr["signals"]
        if not sigs:
            sig_html = '<span style="color:#444">No signal</span>'
        else:
            parts = []
            for s in sigs:
                col  = "#00cc55" if s["confirmed"] else "#888800"
                stat = "CONFIRMED" if s["confirmed"] else "watching"
                parts.append(f'<span style="color:{col}">{s["type"]} {stat}</span>')
            sig_html = " &nbsp;/&nbsp; ".join(parts)
        sym_bold  = f"<b>{sr['symbol']}</b>"
        price_str = f"${sr['price']:.2f}"
        html += (f"<tr>"
                 + _td(sym_bold)
                 + _td(price_str)
                 + _td(_trend_badge(sr["trend"]))
                 + _td(top_sup, "#88cc88")
                 + _td(top_res, "#cc8888")
                 + _td(sig_html)
                 + "</tr>")
    html += f"</tbody>{TABLE_CLOSE}"

    # ── Signals Detected ───────────────────────────────────────────────────────
    html += _h2(f"Signals Detected ({len(all_signals)})")

    if not all_signals:
        html += '<p style="color:#555;margin-bottom:24px">No hammer or hanging man signals in any symbol today.</p>'
    else:
        for s in all_signals:
            sym       = s["symbol"]
            sig_type  = s["type"]
            pattern   = s["pattern"]
            trend     = s["trend"]
            n_opts    = len(opts)

            trend_ok = (sig_type == "CALL" and trend == "uptrend") or \
                       (sig_type == "PUT"  and trend == "downtrend")
            slot_ok  = n_opts < 2
            conf_ok  = s["confirmed"]
            level    = s["level"]
            conf_price = s["conf_close"]

            if sig_type == "CALL":
                drift = (conf_price - level) / level
            else:
                drift = (level - conf_price) / level
            chase_ok = drift <= PROXIMITY

            strike = _suggested_strike(sym, s["close"], sig_type.lower())

            if conf_ok and trend_ok and slot_ok and chase_ok:
                result_color = "#00cc55"
                result_text  = "FIRED — AUTO-TRADE EXECUTED"
            elif not conf_ok:
                result_color = "#888800"
                result_text  = "WATCHING — no confirmation candle yet"
            elif not trend_ok:
                need = "UPTREND" if sig_type == "CALL" else "DOWNTREND"
                result_color = "#dd3300"
                result_text  = f"BLOCKED — trend is {trend.upper()}, {sig_type} requires {need}"
            elif not slot_ok:
                result_color = "#dd3300"
                result_text  = "BLOCKED — position limit reached (2/2)"
            elif not chase_ok:
                result_color = "#dd3300"
                result_text  = (f"BLOCKED — price drifted {drift * 100:.2f}% past level "
                                f"(max {PROXIMITY * 100:.1f}%)")
            else:
                result_color = "#888800"
                result_text  = "WATCHING"

            border_color = "#00cc55" if "FIRED" in result_text else \
                           ("#dd3300" if "BLOCKED" in result_text else "#888800")
            html += (f'<div style="background:#111;border-left:3px solid {border_color};'
                     f'padding:16px;border-radius:4px;margin-bottom:16px">')
            html += (f'<p style="margin:0 0 4px;font-size:15px;font-weight:bold;color:#cde">'
                     f'{sym} — {sig_type} Signal ({pattern} at ${level:.2f} {s["level_tag"]})</p>')
            html += (f'<p style="margin:0 0 12px;color:#555;font-size:11px">'
                     f'Pattern bar: {s["time"][:16]} &nbsp;·&nbsp; '
                     f'Confirmation bar: {s["conf_time"][:16]} &nbsp;·&nbsp; '
                     f'Suggested strike: ${strike:.0f}</p>')

            need_trend = "UPTREND" if sig_type == "CALL" else "DOWNTREND"
            dir_ok_str = f"Trend aligned — {sig_type} needs {need_trend} (got {trend.upper()})"
            slot_str   = f"Position slot available ({n_opts}/2 open)"
            chase_str  = f"No-chase: within 0.5% of level (drift {drift * 100:.2f}%)"
            conf_dir   = "green" if sig_type == "CALL" else "red"

            html += TABLE_OPEN + _thead("Rule", "Result") + "<tbody>"
            for rule_text, cond in [
                (dir_ok_str,                              trend_ok),
                (slot_str,                                slot_ok),
                (f"S/R level identified (${level:.2f} {s['level_tag']})", True),
                (f"{pattern} pattern at level",           True),
                (f"Confirmation candle ({conf_dir})",     conf_ok),
                (chase_str,                               chase_ok),
            ]:
                html += f"<tr>{_td(rule_text)}{_td(_check(cond))}</tr>"

            html += (f'<tr><td colspan="2" style="padding:10px 12px;font-weight:bold;'
                     f'color:{result_color};border-top:1px solid #2a2a2a">'
                     f'{result_text}</td></tr>')
            html += f"</tbody>{TABLE_CLOSE}"
            html += "</div>"

    # ── Auto-Trade Log ─────────────────────────────────────────────────────────
    html += _h2("Auto-Trade Log")
    if not option_orders:
        html += '<p style="color:#555;margin-bottom:24px">No option orders placed today.</p>'
    else:
        html += TABLE_OPEN + _thead("Time (UTC)", "Symbol", "Side", "Qty", "Type", "Status", "Fill") + "<tbody>"
        for o in option_orders:
            t      = o.get("submitted_at", "")[:16]
            sym    = o.get("symbol", "")
            parsed = _parse_occ(sym)
            desc   = (f"{parsed['underlying']} ${parsed['strike']:.0f} {parsed['type']}"
                      if parsed else sym)
            side   = o.get("side", "").upper()
            qty    = o.get("qty", "1")
            otype  = o.get("order_type", "market").upper()
            status = o.get("status", "").upper()
            fill   = o.get("filled_avg_price")
            fill_s = f"${float(fill):.2f}" if fill else "—"
            scol   = ("#00cc55" if status == "FILLED"
                      else "#dd3300" if status in ("CANCELED", "REJECTED")
                      else "#888")
            status_cell = _td(f'<span style="color:{scol}">{status}</span>')
            html += ("<tr>"
                     + _td(t, "#666")
                     + _td(f"<b>{desc}</b>")
                     + _td(side)
                     + _td(qty)
                     + _td(otype)
                     + status_cell
                     + _td(fill_s)
                     + "</tr>")
        html += f"</tbody>{TABLE_CLOSE}"

    # ── Open Positions ─────────────────────────────────────────────────────────
    html += _h2(f"Open Positions ({len(opts)})")
    if not opts:
        html += '<p style="color:#555;margin-bottom:24px">No open options positions.</p>'
    else:
        html += TABLE_OPEN + _thead("Underlying", "Type", "Strike", "Expiry", "DTE",
                                     "Entry", "Current", "P&amp;L", "Status") + "<tbody>"
        for pos in opts:
            sym    = pos["symbol"]
            pct    = float(pos.get("unrealized_plpc", 0)) * 100
            pl_d   = float(pos.get("unrealized_pl", 0))
            entry  = float(pos.get("avg_entry_price", 0))
            cur    = float(pos.get("current_price", 0))
            parsed = _parse_occ(sym)
            under  = parsed.get("underlying", sym)
            strike = f"${parsed['strike']:.0f}" if parsed else "—"
            exp    = parsed.get("expiration", "—")
            dte    = _dte(exp)
            otype  = parsed.get("type", "—")

            pct_c = _pct_color(pct)
            if pct >= TP_THRESHOLD * 100:
                status_s = '<span style="color:#00dd55;font-weight:bold">★ TAKE PROFIT</span>'
            elif dte <= 2:
                status_s = '<span style="color:#dd8800;font-weight:bold">⚠ EXPIRY SOON</span>'
            elif pct >= 80:
                status_s = '<span style="color:#88cc00">Watch closely</span>'
            else:
                status_s = '<span style="color:#555">Holding</span>'

            dte_color = "#dd8800" if dte <= 2 else "#888"
            pl_cell   = _td(f'<span style="color:{pct_c};font-weight:bold">{pct:+.1f}% (${pl_d:+.0f})</span>')
            html += ("<tr>"
                     + _td(f"<b>{under}</b>")
                     + _td(otype)
                     + _td(strike)
                     + _td(exp, "#888")
                     + _td(dte, dte_color)
                     + _td(f"${entry:.2f}")
                     + _td(f"${cur:.2f}")
                     + pl_cell
                     + _td(status_s)
                     + "</tr>")
        html += f"</tbody>{TABLE_CLOSE}"

    # ── End of Day Snapshot ────────────────────────────────────────────────────
    html += _h2("End of Day Snapshot")
    equity_val = float(equity) if equity else 0
    cash_val   = float(cash)   if cash   else 0
    unrl_pl    = sum(float(p.get("unrealized_pl", 0)) for p in opts)

    html += TABLE_OPEN + _thead("Metric", "Value") + "<tbody>"
    for label, val in [
        ("Account equity",           f"${equity_val:,.2f}" if equity else "N/A"),
        ("Cash available",           f"${cash_val:,.2f}"   if cash   else "N/A"),
        ("Open options positions",   len(opts)),
        ("Unrealized P&L (options)", f'<span style="color:{_pct_color(unrl_pl)}">'
                                     f'${unrl_pl:+,.2f}</span>'),
        ("Signals seen today",       len(all_signals)),
        ("Confirmed signals",        len(confirmed_sigs)),
        ("Auto-trades fired",        len(filled_orders)),
        ("Option orders total",      len(option_orders)),
    ]:
        html += f"<tr>{_td(label, '#888')}{_td(val)}</tr>"
    html += f"</tbody>{TABLE_CLOSE}"

    # ── EOD Reflection ─────────────────────────────────────────────────────────
    html += _h2("End of Day Reflection")

    # Active rules
    html += _h3("Strategy Rules")
    html += TABLE_OPEN + _thead("Rule", "Status") + "<tbody>"
    for rule in [
        "1-hour candles only — no other timeframe",
        "Max 2 options positions open at any time",
        "Max $200 per trade — enforced at order time",
        "No trades after 3:30 PM ET",
        "No trade when trend is SIDEWAYS",
        "No-chase: skip if confirmation close >0.5% past S/R level",
        "Exit at 90%+ gain — auto_monitor() closes automatically",
        "Stop loss: none — let options expire (intentional)",
    ]:
        html += f"<tr>{_td(rule)}{_td(_check(True))}</tr>"
    html += f"</tbody>{TABLE_CLOSE}"

    # Lessons
    html += _h3("Recent Lessons")
    if lessons:
        items = "".join(
            f'<li style="margin:6px 0;color:#bbb;line-height:1.5">{ln}</li>'
            for ln in lessons
        )
        html += f'<ul style="padding-left:20px;margin-bottom:24px">{items}</ul>'
    else:
        html += ('<p style="color:#555;margin-bottom:24px">'
                 'No lessons recorded yet. Write one after each closed trade.</p>')

    # Cycle status
    html += _h3("Cycle Tracking")
    html += (f'<p style="color:#888;margin-bottom:8px">'
             f'Cycle 1 in progress. Target: 6–7 wins per 10 trades. '
             f'A win = option closed at 100%+ gain. A loss = expired worthless.</p>')

    # Human action required
    tp_list = [p for p in opts if float(p.get("unrealized_plpc", 0)) >= TP_THRESHOLD]
    exp_warn = [p for p in opts if _dte(_parse_occ(p["symbol"]).get("expiration", "9999-12-31")) <= 2]

    html += _h3("Human Action Required")
    if tp_list or exp_warn:
        html += '<div style="background:#1a0a0a;border:1px solid #dd3300;border-radius:4px;padding:14px;margin-bottom:24px">'
        if tp_list:
            syms = ", ".join(
                (_parse_occ(p["symbol"]).get("underlying") or p["symbol"])
                for p in tp_list
            )
            html += (f'<p style="color:#dd3300;font-weight:bold;margin:0 0 8px">'
                     f'★ TAKE PROFIT — {syms} at 90%+ but still open.<br>'
                     f'Run: <code>python3 scripts/cardona_trade.py monitor</code></p>')
        if exp_warn:
            syms = ", ".join(
                (_parse_occ(p["symbol"]).get("underlying") or p["symbol"])
                for p in exp_warn
            )
            html += (f'<p style="color:#dd8800;font-weight:bold;margin:0">'
                     f'⚠ EXPIRY WARNING — {syms} expire within 2 trading days. '
                     f'Review and decide whether to close manually.</p>')
        html += '</div>'
    else:
        html += ('<p style="color:#00cc55;margin-bottom:24px">'
                 'NONE — the bot is handling everything autonomously.</p>')

    html += """
<hr style="border:none;border-top:1px solid #1a1a1a;margin-top:36px">
<p style="color:#333;font-size:11px;margin-bottom:0">
  Cardona Strategy Bot &nbsp;·&nbsp; Paper Trading Account &nbsp;·&nbsp; """ + today + """<br>
  All positions are simulated. Not financial advice.
</p>
</div>
</body>
</html>"""

    return html


# ── Plain text fallback ────────────────────────────────────────────────────────

def build_text(today: str, positions: list, scan_results: list,
               orders: list, lessons: list) -> str:
    opts = [p for p in positions if OPTION_RE.match(p["symbol"])]
    all_signals = []
    for sr in scan_results:
        for s in sr.get("signals", []):
            s = dict(s)
            s["symbol"] = sr["symbol"]
            all_signals.append(s)

    lines = [
        f"CARDONA STRATEGY BOT — OPTIONS JOURNAL — {today}",
        "=" * 62, "",
    ]

    lines += [f"WATCHLIST SCAN ({len(scan_results)} symbols)"]
    arrows = {"uptrend": "▲", "downtrend": "▼", "sideways": "↔"}
    for sr in scan_results:
        if "error" in sr:
            lines.append(f"  {sr['symbol']:6}  ERROR")
            continue
        arrow   = arrows.get(sr["trend"], "?")
        sig_str = f"{len(sr['signals'])} signal(s)" if sr["signals"] else "no signals"
        lines.append(f"  {sr['symbol']:6}  ${sr['price']:.2f}  "
                     f"{arrow} {sr['trend'].upper():<12}  {sig_str}")

    lines += ["", f"SIGNALS ({len(all_signals)} detected)"]
    for s in all_signals:
        status = "CONFIRMED" if s["confirmed"] else "watching"
        lines.append(f"  {s['symbol']} {s['type']} — {s['pattern']} — {status}")
    if not all_signals:
        lines.append("  None")

    option_orders = [o for o in orders if OPTION_RE.match(o.get("symbol", ""))]
    filled = [o for o in option_orders if o.get("status") == "filled"]
    lines += ["", f"AUTO-TRADES FIRED ({len(filled)})"]
    for o in filled:
        parsed = _parse_occ(o.get("symbol", ""))
        desc   = (f"{parsed['underlying']} ${parsed['strike']:.0f} {parsed['type']}"
                  if parsed else o.get("symbol", "?"))
        fill   = o.get("filled_avg_price")
        fill_s = f"${float(fill):.2f}" if fill else "pending"
        lines.append(f"  {o.get('side','').upper()} {desc}  fill {fill_s}")
    if not filled:
        lines.append("  None")

    lines += ["", f"OPEN POSITIONS ({len(opts)})"]
    for pos in opts:
        sym    = pos["symbol"]
        pct    = float(pos.get("unrealized_plpc", 0)) * 100
        pl_d   = float(pos.get("unrealized_pl", 0))
        parsed = _parse_occ(sym)
        desc   = (f"{parsed['underlying']} ${parsed['strike']:.0f} {parsed['type']} "
                  f"exp {parsed['expiration']}" if parsed else sym)
        tp = "  *** TAKE PROFIT ***" if pct >= TP_THRESHOLD * 100 else ""
        lines.append(f"  {desc}  {pct:+.1f}% (${pl_d:+.0f}){tp}")
    if not opts:
        lines.append("  No open positions")

    lines += ["", "RECENT LESSONS"]
    for ln in lessons:
        lines.append(f"  • {ln}")
    if not lessons:
        lines.append("  None yet — write one after each closed trade")

    lines += ["", "─" * 62, "Cardona Strategy Bot — Paper Trading"]
    return "\n".join(lines)


# ── SendGrid delivery ──────────────────────────────────────────────────────────

def send_email(subject: str, text_body: str, html_body: str) -> bool:
    api_key  = os.environ.get("SENDGRID_API_KEY")
    to_email = os.environ.get("NOTIFY_EMAIL")

    if not api_key:
        print("ERROR: SENDGRID_API_KEY not set in .env")
        return False
    if not to_email:
        print("ERROR: NOTIFY_EMAIL not set in .env")
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from":    {"email": to_email, "name": "Cardona Strategy Bot"},
        "subject": subject,
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

    print(f"Cardona Notify — {today}")

    print("Fetching account data...", end="", flush=True)
    positions = get_positions()
    account   = get_account()
    orders    = get_orders_today(today)
    print(" done")

    print("Running EOD scan:")
    scan_results = scan_all()

    lessons = read_lessons()

    opts    = [p for p in positions if OPTION_RE.match(p["symbol"])]
    all_sigs = []
    for sr in scan_results:
        for s in sr.get("signals", []):
            s = dict(s)
            s["symbol"] = sr["symbol"]
            all_sigs.append(s)

    confirmed_cnt = sum(1 for s in all_sigs if s["confirmed"])
    tp_cnt        = sum(1 for p in opts if float(p.get("unrealized_plpc", 0)) >= TP_THRESHOLD)

    tags = []
    if tp_cnt:         tags.append(f"{tp_cnt} TAKE PROFIT")
    if confirmed_cnt:  tags.append(f"{confirmed_cnt} signal(s)")
    tag_str = " | " + " | ".join(tags) if tags else ""
    subject = f"Cardona EOD {today} — {len(opts)} position(s) open{tag_str}"

    html_body = build_html(today, positions, account, scan_results, orders, lessons)
    text_body = build_text(today, positions, scan_results, orders, lessons)

    if test_mode:
        print(f"\nSubject: {subject}")
        print("[--test mode: email not sent]\n")
        print(text_body)
        return

    send_email(subject, text_body, html_body)


if __name__ == "__main__":
    main()
