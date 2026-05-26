#!/usr/bin/env python3
"""
Cardona Live Trader
Autonomous 1H options scanner running 7:30 AM – 3:30 PM ET.
Scans every 15 min · monitors positions every 5 min · EOD journal at 4:15 PM ET.

Usage:
    python3 scripts/live_trader.py
    python3 scripts/live_trader.py --no-screen   # flat output (tmux / nohup)
"""

import json
import os
import re
import subprocess
import sys
import time
import traceback
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import requests
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.rule import Rule
except ImportError as e:
    print(f"Missing dependency: {e}\nRun: pip install rich requests")
    sys.exit(1)

# ── Shared modules ────────────────────────────────────────────────────────────
_SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS))
import cardona_scanner as _cs   # noqa: E402
import cardona_trade   as _ct   # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
ET            = ZoneInfo("America/New_York")
BOT_NAME      = "CARDONA LIVE TRADER"
VERSION       = "1.0"
SYMBOLS       = list(_cs.SYMBOLS)
FIXED_OTM     = set(_cs.FIXED_OTM)
SCAN_MIN      = 15           # minutes between full scans
MON_MIN       = 5            # minutes between position checks
SESSION_START = (7, 30)      # 7:30 AM ET — first scan
SESSION_END   = (15, 30)     # 3:30 PM ET — no new entries after
EOD_HOUR      = 16
EOD_MIN       = 15           # 4:15 PM ET — EOD journal
MAX_RETRIES   = 5
RETRY_WAITS   = [5, 15, 30, 60, 120]
MAX_POSITIONS = 2
MAX_BUDGET    = 200.0
TP_THRESHOLD  = 0.90
PROXIMITY     = _cs.PROXIMITY           # 0.005 = 0.5%
_ROOT         = _SCRIPTS.parent
POS_FILE      = _ROOT / "data"   / "cardona_positions.json"
MEM_DIR       = _ROOT / "memory"
LOG_DIR       = _ROOT / "logs"
REGIME_FILE   = Path.home() / "trading-agent" / "data" / "regime.json"

SCREEN_MODE   = "--no-screen" not in sys.argv

# ── ET time helpers ───────────────────────────────────────────────────────────

def et_now() -> datetime:
    return datetime.now(ET)


def in_session() -> bool:
    t = et_now()
    if t.weekday() >= 5:
        return False
    hm = (t.hour, t.minute)
    return SESSION_START <= hm < SESSION_END


def past_session() -> bool:
    t = et_now()
    return (t.hour, t.minute) >= SESSION_END


def in_eod_window() -> bool:
    t = et_now()
    if t.weekday() >= 5:
        return False
    return t.hour == EOD_HOUR and EOD_MIN <= t.minute < EOD_MIN + 10


# ── File logger ───────────────────────────────────────────────────────────────

def _flog(msg: str, level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = et_now().strftime("%Y-%m-%d %H:%M:%S ET")
    try:
        with open(LOG_DIR / "live_trader.log", "a") as fh:
            fh.write(f"[{ts}] [{level:5s}]  {msg}\n")
    except Exception:
        pass


# ── Bot state ─────────────────────────────────────────────────────────────────

class BotState:
    """All mutable bot state. Updated only inside methods; read by renderer."""

    def __init__(self):
        self.account      = {"equity": 0.0, "cash": 0.0, "last_equity": 0.0}
        self.regime       = _cs._read_regime()
        self.scan_rows    = []           # list[ScanRow] — last scan results
        self.last_scan    = None         # datetime of last completed scan
        self.next_scan    = None         # datetime of next scheduled scan
        self.last_mon     = None         # datetime of last monitor run
        self.positions    = {}           # {occ_sym: enriched dict}
        self.cycle        = _read_cycles()
        self.activity     = deque(maxlen=20)
        self.error_count  = 0
        self.status       = "STARTING"
        self.eod_sent     = False
        self.start_time   = et_now()

    def log(self, msg: str, level: str = "INFO") -> None:
        ts = et_now().strftime("%H:%M:%S")
        self.activity.appendleft({"ts": ts, "msg": msg, "level": level})
        _flog(msg, level)


# ── Cycle tracker helper ──────────────────────────────────────────────────────

def _read_cycles() -> dict:
    path = MEM_DIR / "cycles.md"
    if not path.exists():
        return {"cycle": 1, "trades": 0, "wins": 0, "losses": 0}
    text = path.read_text()
    num  = 1
    for ln in text.splitlines():
        m = re.match(r"##\s+Cycle\s+(\d+)", ln)
        if m:
            num = int(m.group(1))
    rows = re.findall(
        r"\|\s*\d{4}-\d{2}-\d{2}\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|",
        text,
    )
    wins   = sum(1 for r in rows if re.search(r"\bW(in)?\b", r, re.I))
    losses = sum(1 for r in rows if re.search(r"\bL(oss)?\b", r, re.I))
    return {"cycle": num, "trades": len(rows), "wins": wins, "losses": losses}


def _append_trade_lesson(meta: dict, pl_pct: float) -> None:
    """Write closed trade to lessons.md so it's visible in EOD journal."""
    path   = MEM_DIR / "lessons.md"
    today  = date.today().isoformat()
    result = "WIN" if pl_pct >= 1.0 else "LOSS"
    under  = meta.get("underlying", "?")
    typ    = meta.get("type", "?")
    strike = meta.get("strike", 0)
    expiry = meta.get("expiry", "?")
    entry  = meta.get("entry_price_estimate", 0)
    exit_p = entry * (1 + pl_pct) if entry else 0
    pl_d   = (exit_p - entry) * 100
    sign   = "+" if pl_pct >= 0 else ""
    with open(path, "a") as fh:
        fh.write(
            f"\n---\n**{today}** — {under} ${strike:.0f} {typ} exp {expiry}  "
            f"Entry: ${entry:.2f} | Exit: ${exit_p:.2f} | "
            f"P&L: {sign}{pl_pct*100:.0f}% (${pl_d:+.0f}) | **{result}**\n"
        )


# ── Safe API wrappers ─────────────────────────────────────────────────────────

def _safe_bars(symbol: str) -> list:
    for i, wait in enumerate(RETRY_WAITS):
        try:
            return _cs.fetch_bars(symbol)
        except (SystemExit, Exception) as e:
            if i < MAX_RETRIES - 1:
                time.sleep(wait)
    return []


def _safe_positions() -> list:
    for i, wait in enumerate(RETRY_WAITS):
        try:
            return _ct.get_positions()
        except (SystemExit, Exception):
            if i < MAX_RETRIES - 1:
                time.sleep(wait)
    return []


def _safe_account() -> dict:
    for i, wait in enumerate(RETRY_WAITS):
        try:
            return _ct.get_account()
        except (SystemExit, Exception):
            if i < MAX_RETRIES - 1:
                time.sleep(wait)
    return {}


def _safe_snapshot(occ_sym: str) -> dict:
    """Fetch single-symbol option snapshot via direct HTTP (no sys.exit path)."""
    try:
        key    = os.environ.get("APCA_API_KEY_ID", "")
        secret = os.environ.get("APCA_API_SECRET_KEY", "")
        r = requests.get(
            "https://data.alpaca.markets/v1beta1/options/snapshots",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"symbols": occ_sym},
            timeout=10,
        )
        if r.ok:
            return r.json().get("snapshots", {}).get(occ_sym, {})
    except Exception:
        pass
    return {}


# ── Scan loop ─────────────────────────────────────────────────────────────────

def run_scan(state: BotState) -> None:
    """Scan all 10 symbols and auto-trade when all conditions pass."""
    regime_data    = _cs._read_regime()
    regime         = regime_data.get("current_regime", "SIDEWAYS")
    drift_limit    = 0.003 if regime == "SIDEWAYS" else PROXIMITY
    state.regime   = regime_data

    if regime == "HIGH_VOLATILITY":
        state.log("Scan — HIGH_VOLATILITY: visibility only, no entries", "WARN")

    rows = []
    for symbol in SYMBOLS:
        bars = _safe_bars(symbol)
        if not bars:
            rows.append({"symbol": symbol, "price": 0, "trend": "?",
                         "signal": None, "result": "ERROR: no data"})
            state.log(f"{symbol}: no bar data", "WARN")
            continue

        price       = bars[-1]["c"]
        trend       = _cs.market_trend(bars)
        sup         = _cs.find_support(bars)
        res         = _cs.find_resistance(bars)
        rnds        = _cs.round_number_levels(price)
        sup_all     = sorted(set(sup) | {r for r in rnds if r < price})
        res_all     = sorted((set(res) | {r for r in rnds if r > price}), reverse=True)
        signals     = _cs.find_signals(bars, sup_all, res_all)
        latest_time = bars[-1]["t"]

        # Regime preference filtering
        if regime in ("BULL_TRENDING", "BEAR_TRENDING"):
            preferred = "CALL" if regime == "BULL_TRENDING" else "PUT"
            has_c = any(s["type"] == "CALL" for s in signals)
            has_p = any(s["type"] == "PUT"  for s in signals)
            if has_c and has_p:
                signals = [s for s in signals if s["type"] == preferred]

        best_signal  = None
        result_str   = "no signal"

        for s in signals:
            direction = s["type"].lower()
            strike    = _cs._suggested_strike(symbol, s["close"], direction)

            if not s["confirmed"]:
                best_signal = s
                result_str  = f"{s['type']} {s['pattern']} — unconfirmed"
                continue

            # Freshness
            if s["conf_time"] != latest_time:
                best_signal = s
                result_str  = "SKIP [STALE_SIGNAL]"
                continue

            # Trend alignment
            trend_ok = (direction == "call" and trend == "uptrend") or \
                       (direction == "put"  and trend == "downtrend")
            if not trend_ok:
                best_signal = s
                result_str  = f"SKIP [TREND_MISMATCH] ({trend})"
                continue

            # Regime block
            if regime == "HIGH_VOLATILITY":
                best_signal = s
                result_str  = "SKIP [REGIME_BLOCK]"
                continue

            # Drift / no-chase
            cp    = s["conf_close"]
            lvl   = s["level"]
            drift = (cp - lvl) / lvl if direction == "call" else (lvl - cp) / lvl
            if drift > drift_limit:
                best_signal = s
                result_str  = f"SKIP [DRIFT_EXCEEDED] {drift*100:.2f}% > {drift_limit*100:.1f}%"
                continue

            # Time gate
            now_hm = (et_now().hour, et_now().minute)
            if now_hm >= SESSION_END:
                best_signal = s
                result_str  = "SKIP [TIME_BLOCK] past 3:30 PM"
                continue

            # Earnings
            if _cs._is_earnings_day(symbol):
                best_signal = s
                result_str  = "SKIP [EARNINGS_BLOCK]"
                continue

            # Position limit (read fresh each iteration)
            n_pos = len(_ct._load_cardona_positions())
            if n_pos >= MAX_POSITIONS:
                best_signal = s
                result_str  = f"SKIP [POSITION_LIMIT] {n_pos}/2"
                continue

            # Contract availability
            expiry = _cs._next_expiry()
            if not _cs._has_option_contracts(symbol, direction, strike, expiry):
                best_signal = s
                result_str  = "SKIP [NO_CONTRACT]"
                continue

            # ── All conditions met — fire ─────────────────────────────────
            best_signal = s
            result_str  = f"FIRING {s['type']} ${strike:.0f} exp {expiry}"
            state.log(
                f"AUTO-TRADE: {symbol} {s['type']} ${strike:.0f} exp {expiry}", "TRADE"
            )
            _exec_buy(symbol, direction, strike, expiry, state)
            break   # one trade per symbol per scan

        rows.append({
            "symbol": symbol, "price": price,
            "trend":  trend,  "signal": best_signal,
            "result": result_str,
        })

    state.scan_rows = rows
    state.last_scan = et_now()
    state.next_scan = et_now() + timedelta(minutes=SCAN_MIN)
    n_sig = sum(1 for r in rows if r["signal"] is not None)
    state.log(f"Scan complete — {n_sig} signal(s), {len(SYMBOLS)} symbols")


# ── Trade execution (subprocess) ──────────────────────────────────────────────

def _exec_buy(symbol: str, direction: str, strike: float,
              expiry: str, state: BotState) -> None:
    cmd = [sys.executable, str(_SCRIPTS / "cardona_trade.py"),
           "buy", symbol, direction, f"{strike:.0f}", expiry]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        for ln in r.stdout.strip().splitlines():
            if ln.strip():
                state.log(f"  {ln.strip()}")
        if r.returncode != 0 and r.stderr.strip():
            state.log(f"  ERR: {r.stderr.strip()[:120]}", "WARN")
    except subprocess.TimeoutExpired:
        state.log(f"Buy timeout: {symbol} {direction}", "ERROR")
    except Exception as e:
        state.log(f"Buy error: {e}", "ERROR")


def _exec_close(occ_sym: str, state: BotState) -> None:
    cmd = [sys.executable, str(_SCRIPTS / "cardona_trade.py"), "close", occ_sym]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        for ln in r.stdout.strip().splitlines():
            if ln.strip():
                state.log(f"  {ln.strip()}")
    except Exception as e:
        state.log(f"Close error {occ_sym}: {e}", "ERROR")


# ── Position monitor ──────────────────────────────────────────────────────────

def run_monitor(state: BotState) -> None:
    """Check all open Cardona positions; auto-close any at ≥90% gain."""
    cardona_pos = _ct._load_cardona_positions()
    if not cardona_pos:
        state.positions = {}
        state.last_mon  = et_now()
        return

    alpaca_map = {p["symbol"]: p for p in _safe_positions()}
    enriched   = {}
    closed_cnt = 0

    for occ_sym, meta in list(cardona_pos.items()):
        parsed  = _ct._parse_occ(occ_sym)
        ap      = alpaca_map.get(occ_sym)

        if ap:
            pl_pct  = float(ap.get("unrealized_plpc", 0))
            pl_d    = float(ap.get("unrealized_pl",   0))
            entry   = float(ap.get("avg_entry_price",  meta.get("entry_price_estimate", 0)))
            current = float(ap.get("current_price",    0))
        else:
            # Market closed or position filled; try snapshot
            snap    = _safe_snapshot(occ_sym)
            bid     = float(snap.get("latestQuote", {}).get("bp", 0)) if snap else 0
            entry   = float(meta.get("entry_price_estimate", 0))
            current = bid if bid else 0
            pl_pct  = ((current - entry) / entry) if entry else 0
            pl_d    = (current - entry) * 100 if entry else 0

        exp_str = parsed.get("expiration", meta.get("expiry", "?"))
        try:
            dte = (date.fromisoformat(exp_str) - date.today()).days
        except Exception:
            dte = -1

        enriched[occ_sym] = {
            **meta,
            "pl_pct":  pl_pct,
            "pl_d":    pl_d,
            "entry":   entry,
            "current": current,
            "dte":     dte,
            "parsed":  parsed,
        }

        # Auto-close at take-profit threshold
        if pl_pct >= TP_THRESHOLD:
            under = (f"{parsed.get('underlying','?')} "
                     f"${parsed.get('strike',0):.0f} {parsed.get('type','?')}")
            state.log(f"TAKE PROFIT: {under} at {pl_pct*100:+.1f}% — closing", "TRADE")
            _exec_close(occ_sym, state)
            _append_trade_lesson(meta, pl_pct)
            del enriched[occ_sym]
            closed_cnt += 1
            state.cycle = _read_cycles()

        elif dte == 0:
            state.log(f"WARNING: {occ_sym} expires today — letting expire per strategy", "WARN")
        elif dte == 1:
            state.log(f"WARNING: {occ_sym} expires tomorrow — DTE 1", "WARN")

    state.positions = enriched
    state.last_mon  = et_now()
    if closed_cnt:
        state.log(f"Monitor: {closed_cnt} position(s) auto-closed at take profit")


# ── Account refresh ───────────────────────────────────────────────────────────

def refresh_account(state: BotState) -> None:
    acct = _safe_account()
    if acct:
        state.account = {
            "equity":      float(acct.get("equity",      0)),
            "cash":        float(acct.get("cash",        0)),
            "last_equity": float(acct.get("last_equity", 0)),
        }


# ── EOD journal ───────────────────────────────────────────────────────────────

def run_eod(state: BotState) -> None:
    state.log("EOD: running notify.py", "INFO")
    state.status = "EOD"
    try:
        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "notify.py")],
            capture_output=True, text=True, timeout=120,
        )
        for ln in r.stdout.strip().splitlines():
            if ln.strip():
                state.log(f"  {ln.strip()}")
        state.eod_sent = True
        state.status   = "EOD_COMPLETE"
        state.log("EOD journal sent")
    except Exception as e:
        state.log(f"EOD error: {e}", "ERROR")


# ── Rich display ──────────────────────────────────────────────────────────────

def _regime_text(code: str) -> Text:
    cfg = {
        "BULL_TRENDING":   ("bright_green", "▲ BULL"),
        "BEAR_TRENDING":   ("bright_red",   "▼ BEAR"),
        "HIGH_VOLATILITY": ("yellow",       "⚡ HIGH VOL"),
        "SIDEWAYS":        ("yellow",       "↔ SIDEWAYS"),
    }
    color, label = cfg.get(code, ("dim", code))
    return Text(label, style=f"bold {color}")


def _trend_text(t: str) -> Text:
    cfg = {
        "uptrend":   ("bright_green", "▲ UP  "),
        "downtrend": ("bright_red",   "▼ DOWN"),
        "sideways":  ("yellow",       "↔ SIDE"),
    }
    color, label = cfg.get(t, ("dim", t.upper()[:6]))
    return Text(label, style=color)


def _pl_text(pct: float) -> Text:
    s = f"{pct*100:+.1f}%"
    if pct >= TP_THRESHOLD:
        return Text(f"★ {s}", style="bold bright_green")
    if pct >= 0.50:
        return Text(s, style="bright_green")
    if pct >= 0:
        return Text(s, style="yellow")
    return Text(s, style="bright_red")


def _header_panel(state: BotState) -> Panel:
    now    = et_now()
    regime = state.regime.get("current_regime", "SIDEWAYS")
    tmrw   = state.regime.get("tomorrow_forecast", {}).get("most_likely", "?")
    eq     = state.account["equity"]
    cash   = state.account["cash"]
    leq    = state.account["last_equity"]
    day_pl = eq - leq
    sign   = "+" if day_pl >= 0 else ""
    cyc    = state.cycle

    # Countdown to next scan — MM:SS when close, HH:MM ET when far
    if state.next_scan:
        secs = max(0, int((state.next_scan - now).total_seconds()))
        if secs == 0:
            nxt = "NOW"
        elif secs <= 3600:
            nxt = f"{secs//60:02d}:{secs%60:02d}"
        else:
            nxt = state.next_scan.strftime("%H:%M ET")
    else:
        nxt = "—"

    # Progress bar (10 boxes, filled = trades done)
    done   = min(cyc["trades"], 10)
    bar    = "█" * done + "░" * (10 - done)

    status_color = {
        "ACTIVE":        "bold bright_green",
        "MARKET_CLOSED": "dim",
        "EOD":           "bold cyan",
        "EOD_COMPLETE":  "bold cyan",
        "ERROR":         "bold bright_red",
        "STARTING":      "bold yellow",
    }.get(state.status, "white")

    win_pct  = cyc["wins"] / cyc["trades"] * 100 if cyc["trades"] else 0
    rate_str = f"  {win_pct:.0f}% win" if cyc["trades"] else ""

    t = Text()
    t.append(f"  {BOT_NAME}  v{VERSION}", style="bold white")
    t.append(f"  │  {now.strftime('%H:%M:%S ET')}  │  {now.strftime('%b %-d, %Y')}\n",
             style="dim")
    t.append(f"  Status: ", style="dim")
    t.append(state.status, style=status_color)
    t.append(f"  │  Regime: ")
    t.append_text(_regime_text(regime))
    t.append(f"  Tomorrow: ")
    t.append_text(_regime_text(tmrw))
    t.append("\n")
    t.append(f"  Eq: ${eq:>11,.0f}", style="white")
    t.append(f"  Cash: ${cash:>10,.0f}", style="dim")
    t.append(f"  P&L: {sign}${abs(day_pl):,.0f}",
             style="bright_green" if day_pl >= 0 else "bright_red")
    t.append(f"  │  Next scan: {nxt}\n", style="cyan")
    t.append(f"  Cycle {cyc['cycle']}: [{bar}]  ", style="dim")
    t.append(f"{cyc['trades']}/10  ", style="white")
    t.append(f"{cyc['wins']}W ", style="bright_green")
    t.append(f"{cyc['losses']}L", style="bright_red")
    t.append(rate_str, style="dim")

    return Panel(t, border_style="blue", box=box.HEAVY, padding=(0, 0))


def _positions_panel(state: BotState) -> Panel:
    n     = len(state.positions)
    title = f"[bold]POSITIONS  {n}/{MAX_POSITIONS} slots used[/bold]"
    border = "bright_green" if n < MAX_POSITIONS else "yellow"

    if not state.positions:
        body = Text("\n  No open positions — 2 slots available\n", style="dim")
        return Panel(body, title=title, border_style=border,
                     box=box.ROUNDED, padding=(0, 0))

    tbl = Table(
        "Contract", "Type", "Strike", "Expiry", "DTE",
        "Entry", "Current", "P&L", "Status",
        box=box.SIMPLE_HEAD, header_style="bold dim",
        show_header=True, expand=True, padding=(0, 1),
    )
    for occ_sym, p in state.positions.items():
        parsed   = p.get("parsed", {})
        under    = parsed.get("underlying", "?")
        typ      = parsed.get("type", "?")
        strike   = parsed.get("strike", 0)
        exp      = parsed.get("expiration", p.get("expiry", "?"))
        dte      = p.get("dte", -1)
        pl_pct   = p.get("pl_pct", 0)
        dte_t    = Text(str(dte) if dte >= 0 else "?",
                        style="bold yellow" if dte <= 2 else "dim")
        if pl_pct >= TP_THRESHOLD:
            status_t = Text("★ TAKE PROFIT", style="bold bright_green")
        elif dte <= 1:
            status_t = Text("⚠ EXPIRY SOON", style="bold yellow")
        else:
            status_t = Text("Holding", style="dim")
        tbl.add_row(
            Text(under, style="white"),
            Text(typ, style="bright_green" if typ == "CALL" else "bright_red"),
            Text(f"${strike:.0f}"),
            Text(exp),
            dte_t,
            Text(f"${p.get('entry',0):.2f}"),
            Text(f"${p.get('current',0):.2f}"),
            _pl_text(pl_pct),
            status_t,
        )

    return Panel(tbl, title=title, border_style=border,
                 box=box.ROUNDED, padding=(0, 0))


def _scan_panel(state: BotState) -> Panel:
    ts  = state.last_scan.strftime("%H:%M ET") if state.last_scan else "pending"
    nxt = state.next_scan.strftime("%H:%M ET") if state.next_scan else "—"

    tbl = Table(
        "Symbol", "Price", "Trend", "Signal", "Result",
        box=box.SIMPLE_HEAD, header_style="bold dim",
        show_header=True, expand=True, padding=(0, 1),
    )
    if not state.scan_rows:
        tbl.add_row(Text("Waiting for first scan…", style="dim"), "", "", "", "")
    else:
        for row in state.scan_rows:
            sym    = row["symbol"]
            price  = row.get("price", 0)
            trend  = row.get("trend", "?")
            sig    = row.get("signal")
            result = row.get("result", "—")

            if sig:
                typ   = sig.get("type", "")
                pat   = sig.get("pattern", "")[:4]
                conf  = "✓" if sig.get("confirmed") else "?"
                sig_t = Text(f"{typ} {pat} {conf}",
                             style="bright_green" if typ == "CALL" else "bright_red")
            else:
                sig_t = Text("—", style="dim")

            if "FIRING" in result:
                res_t = Text(result, style="bold bright_green")
            elif "SKIP" in result:
                res_t = Text(result, style="yellow")
            elif "ERROR" in result:
                res_t = Text(result, style="bright_red")
            elif "unconfirmed" in result:
                res_t = Text(result, style="yellow")
            else:
                res_t = Text(result, style="dim")

            tbl.add_row(
                Text(sym, style="white"),
                Text(f"${price:.2f}" if price else "—"),
                _trend_text(trend),
                sig_t,
                res_t,
            )

    return Panel(
        tbl,
        title=f"[bold]LAST SCAN: {ts}   │   NEXT: {nxt}[/bold]",
        border_style="blue", box=box.ROUNDED, padding=(0, 0),
    )


def _log_panel(state: BotState) -> Panel:
    t = Text()
    entries = list(state.activity)[:12]
    if not entries:
        t.append("  No activity yet", style="dim")
    for e in entries:
        lvl_style = {
            "TRADE": "bold bright_green",
            "ERROR": "bright_red",
            "WARN":  "yellow",
            "INFO":  "dim",
        }.get(e["level"], "dim")
        t.append(f"  [{e['ts']}]  ", style="dim")
        t.append(f"{e['msg']}\n", style=lvl_style)

    return Panel(t, title="[bold]ACTIVITY LOG[/bold]",
                 border_style="dim", box=box.ROUNDED, padding=(0, 0))


def _render(state: BotState) -> Group:
    return Group(
        _header_panel(state),
        _positions_panel(state),
        _scan_panel(state),
        _log_panel(state),
    )


# ── Main bot ──────────────────────────────────────────────────────────────────

class CardonaLiveTrader:

    def __init__(self):
        _cs.load_env()           # load .env into os.environ
        self.state   = BotState()
        self.console = Console()

    def _startup(self) -> None:
        st = self.state
        c  = self.console
        c.clear()
        c.rule(f"[bold cyan]{BOT_NAME}[/bold cyan]")
        c.print(f"  [dim]v{VERSION}[/dim]   "
                f"[white]{et_now().strftime('%A, %B %-d, %Y')}[/white]   "
                f"[cyan]{et_now().strftime('%H:%M ET')}[/cyan]")
        c.print()

        c.print("  [dim]Reading regime…[/dim]", end="")
        regime = st.regime.get("current_regime", "SIDEWAYS")
        tmrw   = st.regime.get("tomorrow_forecast", {}).get("most_likely", "?")
        c.print(f"  {regime}  →  tomorrow: {tmrw}")

        c.print("  [dim]Fetching account…[/dim]", end="")
        refresh_account(st)
        eq = st.account["equity"]
        c.print(f"  [white]${eq:,.2f}[/white]")

        n_pos = len(_ct._load_cardona_positions())
        c.print(f"  [dim]Positions:[/dim]  {n_pos} open  "
                f"({MAX_POSITIONS - n_pos} slot(s) available)")

        if n_pos:
            c.print("  [dim]Checking P&L on open positions…[/dim]")
            run_monitor(st)

        st.cycle = _read_cycles()
        cyc = st.cycle
        c.print(f"  [dim]Cycle:[/dim]  {cyc['cycle']} — "
                f"{cyc['trades']}/10 trades  {cyc['wins']}W / {cyc['losses']}L")

        c.print()
        c.rule("[bold bright_green]LIVE CARDONA SESSION ACTIVE[/bold bright_green]")
        c.print()
        time.sleep(1)

        st.status = "ACTIVE" if in_session() else "MARKET_CLOSED"
        st.log("Bot started", "INFO")

        # Schedule first scan at startup (immediately if in session, else at SESSION_START)
        now = et_now()
        if in_session():
            st.next_scan = now
        else:
            # Schedule for next SESSION_START
            target = now.replace(hour=SESSION_START[0], minute=SESSION_START[1],
                                 second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            st.next_scan = target

    def run(self) -> None:
        self._startup()
        st = self.state

        kwargs = {"auto_refresh": False, "screen": SCREEN_MODE}
        with Live(_render(st), **kwargs) as live:
            while True:
                try:
                    now = et_now()

                    # ── EOD journal (4:15 PM, once) ──────────────────────
                    if in_eod_window() and not st.eod_sent:
                        run_eod(st)

                    # ── Market hours logic ───────────────────────────────
                    if in_session():
                        st.status = "ACTIVE"

                        # Scan trigger (every SCAN_MIN minutes)
                        if st.next_scan and now >= st.next_scan:
                            run_scan(st)
                            refresh_account(st)
                            # next_scan already set inside run_scan()

                        # Monitor trigger (every MON_MIN minutes)
                        if (st.last_mon is None or
                                (now - st.last_mon).total_seconds() >= MON_MIN * 60):
                            run_monitor(st)
                            st.cycle = _read_cycles()

                    else:
                        if past_session():
                            st.status = "MARKET_CLOSED"
                            # Still run monitor in case positions need attention
                            if (st.last_mon is None or
                                    (now - st.last_mon).total_seconds() >= MON_MIN * 60):
                                run_monitor(st)
                        else:
                            # Pre-market
                            st.status = "MARKET_CLOSED"

                    live.update(_render(st))
                    time.sleep(30)

                except KeyboardInterrupt:
                    st.log("Keyboard interrupt — shutting down")
                    break
                except Exception as e:
                    st.error_count += 1
                    msg = f"Loop error ({st.error_count}/{MAX_RETRIES}): {e}"
                    st.log(msg, "ERROR")
                    _flog(f"TRACEBACK:\n{traceback.format_exc()}", "ERROR")
                    if st.error_count >= MAX_RETRIES:
                        st.status = "ERROR"
                        st.log("Max errors reached — check live_trader.log", "ERROR")
                    try:
                        live.update(_render(st))
                    except Exception:
                        pass
                    time.sleep(30)

        # ── Clean shutdown ────────────────────────────────────────────────
        self.console.rule("[bold]CARDONA SESSION ENDED[/bold]")
        uptime = et_now() - st.start_time
        h, rem = divmod(int(uptime.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        cyc    = st.cycle
        self.console.print(
            f"  Uptime  : {h}h {m}m {s}s\n"
            f"  Trades  : {cyc['trades']} placed  "
            f"({cyc['wins']}W / {cyc['losses']}L)\n"
            f"  EOD sent: {st.eod_sent}"
        )
        st.log("Bot shutdown complete")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    CardonaLiveTrader().run()


if __name__ == "__main__":
    main()
