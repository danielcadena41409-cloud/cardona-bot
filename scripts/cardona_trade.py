#!/usr/bin/env python3
"""Cardona Trade — options order execution and position management."""

import os
import re
import sys
import requests
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

TRADE_URL     = "https://paper-api.alpaca.markets/v2"
DATA_URL      = "https://data.alpaca.markets"
MAX_BUDGET    = 200.0       # max $ per trade
TP_THRESHOLD  = 0.90        # 90% gain → flag take profit
MAX_POSITIONS = 2
MAX_EXP_DAYS  = 14

WATCHLIST = {
    "SPY", "QQQ",                          # index ETFs — 10-pt OTM
    "TSLA", "AAPL", "NVDA", "MSFT",        # mega-cap tech
    "AMZN", "META", "GOOGL", "GLD",        # mega-cap + commodity
}
FIXED_OTM = {"SPY", "QQQ"}                # use flat 10-pt OTM for these two

# OCC option symbol: up to 6 letters + 6-digit date (YYMMDD) + C/P + 8-digit strike
OPTION_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
LINE      = "─" * 70


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


def _hdrs() -> dict:
    key    = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        sys.exit("ERROR: APCA_API_KEY_ID / APCA_API_SECRET_KEY not set in .env")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _trade_get(path: str, params: dict = None) -> object:
    try:
        r = requests.get(f"{TRADE_URL}{path}", headers=_hdrs(), params=params, timeout=15)
    except requests.RequestException as e:
        sys.exit(f"Network error (trade API): {e}")
    if not r.ok:
        sys.exit(f"Trade API {r.status_code}: {r.text[:300]}")
    return r.json()


def _trade_post(path: str, payload: dict) -> dict:
    try:
        r = requests.post(f"{TRADE_URL}{path}", headers=_hdrs(), json=payload, timeout=15)
    except requests.RequestException as e:
        sys.exit(f"Network error (trade API): {e}")
    if not r.ok:
        sys.exit(f"Trade API {r.status_code}: {r.text[:300]}")
    return r.json()


def _trade_delete(path: str) -> dict:
    try:
        r = requests.delete(f"{TRADE_URL}{path}", headers=_hdrs(), timeout=15)
    except requests.RequestException as e:
        sys.exit(f"Network error (trade API): {e}")
    if not r.ok:
        sys.exit(f"Trade API {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def _data_get(path: str, params: dict = None) -> dict:
    try:
        r = requests.get(f"{DATA_URL}{path}", headers=_hdrs(), params=params, timeout=15)
    except requests.RequestException as e:
        sys.exit(f"Network error (data API): {e}")
    if not r.ok:
        sys.exit(f"Data API {r.status_code}: {r.text[:300]}")
    return r.json()


# ── Strike calculation ────────────────────────────────────────────────────────

def calc_strike(symbol: str, price: float, direction: str) -> float:
    """
    Suggested OTM strike for the buy command and signal display.

    SPY / QQQ  : flat 10 points OTM (matches the original Cardona rule)
    All others : 2% OTM, rounded to the nearest $5 strike increment
                 (stock prices vary too widely for a fixed-point offset)
    """
    sym = symbol.upper()
    if sym in FIXED_OTM:
        return price + 10.0 if direction == "call" else price - 10.0
    raw = price * 1.02 if direction == "call" else price * 0.98
    return round(raw / 5) * 5


# ── Core API calls ────────────────────────────────────────────────────────────

def get_clock() -> dict:
    return _trade_get("/clock")


def get_positions() -> list:
    return _trade_get("/positions")


def get_open_orders() -> list:
    return _trade_get("/orders", {"status": "open"})


def get_account() -> dict:
    return _trade_get("/account")


def is_option(symbol: str) -> bool:
    return bool(OPTION_RE.match(symbol))


# ── Options contract search ───────────────────────────────────────────────────

def _fetch_contracts(symbol: str, opt_type: str, exp_target: date) -> list:
    """
    Pull contracts for symbol/type within ±3 days of exp_target
    and within 14 calendar days of today.
    """
    today   = date.today()
    exp_lo  = max(exp_target - timedelta(days=3), today)
    exp_hi  = min(exp_target + timedelta(days=3), today + timedelta(days=MAX_EXP_DAYS))

    params   = {
        "underlying_symbols":  symbol,
        "type":                opt_type,
        "expiration_date_gte": exp_lo.isoformat(),
        "expiration_date_lte": exp_hi.isoformat(),
        "limit":               1000,
    }
    contracts = []
    while True:
        data = _data_get("/v1beta1/options/contracts", params)
        contracts.extend(data.get("option_contracts", []))
        token = data.get("next_page_token")
        if not token:
            break
        params["page_token"] = token

    return contracts


def _get_snapshot(opt_symbol: str) -> dict:
    """Return the live snapshot dict for one option symbol, or {}."""
    data = _data_get("/v1beta1/options/snapshots", {"symbols": opt_symbol})
    return data.get("snapshots", {}).get(opt_symbol, {})


# ── Buy option ────────────────────────────────────────────────────────────────

def buy_option(symbol: str, direction: str, strike: float, expiration: str) -> None:
    """
    Find the best-matching options contract and place a market buy order.

    symbol     : any symbol in WATCHLIST — SPY, QQQ, TSLA, AAPL, NVDA, MSFT,
                 AMZN, META, GOOGL, GLD
    direction  : call or put
    strike     : use calc_strike(symbol, price, direction) for the suggested value
    expiration : YYYY-MM-DD (must be ≤14 days out)
    """
    today    = date.today()
    opt_type = direction.lower()

    if symbol.upper() not in WATCHLIST:
        sys.exit(
            f"ERROR: '{symbol}' is not in the Cardona watchlist.\n"
            f"Valid: {', '.join(sorted(WATCHLIST))}"
        )

    if opt_type not in ("call", "put"):
        sys.exit(f"ERROR: direction must be 'call' or 'put', got '{direction}'")

    try:
        exp_date = date.fromisoformat(expiration)
    except ValueError:
        sys.exit(f"ERROR: bad expiration '{expiration}' — use YYYY-MM-DD")

    days_out = (exp_date - today).days
    if days_out < 0:
        print(f"SKIP: expiration {expiration} is in the past")
        return
    if days_out > MAX_EXP_DAYS:
        print(f"SKIP: {expiration} is {days_out} days out — max {MAX_EXP_DAYS}")
        return

    # Enforce position limit
    positions = get_positions()
    n_opts    = sum(1 for p in positions if is_option(p["symbol"]))
    if n_opts >= MAX_POSITIONS:
        print(f"SKIP: already {n_opts} open options positions (max {MAX_POSITIONS})")
        return

    # Search options chain
    print(f"Searching {symbol} {opt_type} contracts near ${strike:.0f} exp {expiration} ...")
    contracts = _fetch_contracts(symbol, opt_type, exp_date)

    tradable = [c for c in contracts if c.get("tradable", True)]
    pool     = tradable if tradable else contracts

    if not pool:
        print(f"SKIP: no {opt_type} contracts found for {symbol} around {expiration}")
        return

    # Pick best: closest expiration first, then closest strike
    def _rank(c):
        d_exp = abs((date.fromisoformat(c["expiration_date"]) - exp_date).days)
        d_str = abs(float(c["strike_price"]) - strike)
        return (d_exp, d_str)

    best       = min(pool, key=_rank)
    opt_symbol = best["symbol"]
    best_strike = float(best["strike_price"])
    best_exp    = best["expiration_date"]

    print(f"  Contract     : {opt_symbol}")
    print(f"  Strike       : ${best_strike:.2f}  (requested ${strike:.0f})")
    print(f"  Expiration   : {best_exp}  (requested {expiration})")

    # Live price check
    snap = _get_snapshot(opt_symbol)
    ask  = snap.get("latestQuote", {}).get("ap") if snap else None

    if ask and float(ask) > 0:
        ask  = float(ask)
        cost = ask * 100      # 1 contract = 100 shares
        print(f"  Ask price    : ${ask:.2f}")
        print(f"  Est. cost    : ${cost:.0f}  (1 contract × 100)")
        if cost > MAX_BUDGET:
            print(f"SKIP: est. cost ${cost:.0f} exceeds ${MAX_BUDGET:.0f} budget")
            return
    else:
        print("  Ask price    : unavailable — market may be closed")
        print("  Warning      : budget cannot be verified; proceeding with market order")

    # Market-hours advisory
    clock = get_clock()
    if not clock.get("is_open"):
        nxt = clock.get("next_open", "?")[:16]
        print(f"  Market       : CLOSED (next open ~{nxt})")
        print("  Warning      : day orders placed after hours may expire unfilled")

    # Place order
    order  = {"symbol": opt_symbol, "qty": "1", "side": "buy",
               "type": "market", "time_in_force": "day"}
    result = _trade_post("/orders", order)

    print(f"\n  Order ID     : {result.get('id', 'unknown')}")
    print(f"  Status       : {result.get('status', 'unknown').upper()}")
    print(f"  Strategy     : hold to 100% gain — let losers expire (Cardona rules)")


# ── P&L check ─────────────────────────────────────────────────────────────────

def check_options_pnl() -> list:
    """Return enriched list of open options positions with P&L and TP flag."""
    results = []
    for pos in get_positions():
        if not is_option(pos["symbol"]):
            continue
        pl_pct = float(pos.get("unrealized_plpc", 0))   # fraction: 0.90 = 90%
        results.append({
            "symbol":       pos["symbol"],
            "qty":          float(pos.get("qty", 0)),
            "entry":        float(pos.get("avg_entry_price", 0)),
            "current":      float(pos.get("current_price", 0)),
            "pl_pct":       pl_pct,
            "pl_dollar":    float(pos.get("unrealized_pl", 0)),
            "market_value": float(pos.get("market_value", 0)),
            "cost_basis":   float(pos.get("cost_basis", 0)),
            "take_profit":  pl_pct >= TP_THRESHOLD,
        })
    return results


# ── Close position ────────────────────────────────────────────────────────────

def close_position(symbol: str) -> None:
    """Send a market sell-to-close order for the given position symbol."""
    positions = get_positions()
    open_syms = [p["symbol"] for p in positions]

    if symbol not in open_syms:
        print(f"SKIP: no open position for {symbol}")
        if open_syms:
            print(f"Open: {', '.join(open_syms)}")
        return

    print(f"Closing {symbol} ...")
    result = _trade_delete(f"/positions/{symbol}")
    status = result.get("status", "submitted") if result else "submitted"
    print(f"  Status       : {str(status).upper()}")
    print("  Position will fill at next market price")


# ── Formatters ────────────────────────────────────────────────────────────────

def _parse_occ(sym: str) -> dict:
    """Parse OCC option symbol into human-readable parts."""
    m = re.match(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", sym)
    if not m:
        return {}
    return {
        "underlying": m.group(1),
        "expiration": f"20{m.group(2)}-{m.group(3)}-{m.group(4)}",
        "type":       "CALL" if m.group(5) == "C" else "PUT",
        "strike":     int(m.group(6)) / 1000,
    }


def _pl_bar(pct_fraction: float, width: int = 20) -> str:
    """Simple ASCII progress bar for P&L."""
    filled = min(int(pct_fraction * width), width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


# ── CLI commands ──────────────────────────────────────────────────────────────

def cmd_status() -> None:
    clock     = get_clock()
    positions = get_positions()
    orders    = get_open_orders()
    account   = get_account()
    ts        = datetime.now().strftime("%Y-%m-%d %H:%M")

    is_open    = clock.get("is_open", False)
    next_open  = clock.get("next_open", "")[:16]
    next_close = clock.get("next_close", "")[:16]
    equity     = account.get("equity", "?")
    cash       = account.get("cash", "?")

    print(f"\n{LINE}")
    print(f"  CARDONA STATUS  |  {ts} ET")
    print(LINE)

    mkt = "OPEN" if is_open else "CLOSED"
    evt = f"closes {next_close}" if is_open else f"opens {next_open}"
    print(f"\n  Market   : {mkt}  ({evt})")
    if equity != "?":
        print(f"  Equity   : ${float(equity):>12,.2f}")
    if cash != "?":
        print(f"  Cash     : ${float(cash):>12,.2f}")

    n_opts = sum(1 for p in positions if is_option(p["symbol"]))
    print(f"\n  Positions: {len(positions)} total  |  {n_opts} options  "
          f"({MAX_POSITIONS - n_opts} slot(s) available)")

    if positions:
        print()
        for pos in positions:
            sym     = pos["symbol"]
            qty     = pos.get("qty", "?")
            entry   = float(pos.get("avg_entry_price", 0))
            current = float(pos.get("current_price", 0))
            pl_pct  = float(pos.get("unrealized_plpc", 0))
            pl_d    = float(pos.get("unrealized_pl", 0))
            kind    = "OPTION" if is_option(sym) else "STOCK "
            tp_flag = "  *** TAKE PROFIT ***" if pl_pct >= TP_THRESHOLD else ""

            print(f"  ┌ {sym}  [{kind}]{tp_flag}")
            parsed = _parse_occ(sym)
            if parsed:
                print(f"  │ {parsed['underlying']} ${parsed['strike']:.0f} "
                      f"{parsed['type']}  exp {parsed['expiration']}")
            print(f"  │ Qty {qty}  |  Entry ${entry:.2f} → ${current:.2f}")
            bar = _pl_bar(pl_pct)
            print(f"  └ P&L {bar}  {pl_pct*100:+.1f}%  (${pl_d:+.0f})")
            print()
    else:
        print("\n  No open positions\n")

    if orders:
        print(f"  Open Orders ({len(orders)}):")
        for o in orders:
            print(f"    {o['symbol']:25s}  {o['side'].upper():4s}  "
                  f"{o['type']:8s}  {o['status']}")
    else:
        print("  No pending orders")

    print()


def cmd_positions() -> None:
    results = check_options_pnl()
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"\n{LINE}")
    print(f"  OPTIONS P&L  |  {ts} ET")
    print(LINE)

    if not results:
        print("\n  No open options positions.\n")
        return

    for pos in results:
        sym    = pos["symbol"]
        parsed = _parse_occ(sym)
        pct    = pos["pl_pct"] * 100
        tp     = pos["take_profit"]

        print()
        header = f"  {sym}"
        if tp:
            header += "  *** TAKE PROFIT — 90%+ ***"
        print(header)

        if parsed:
            print(f"    {parsed['underlying']} ${parsed['strike']:.0f} "
                  f"{parsed['type']}  exp {parsed['expiration']}")

        bar = _pl_bar(pos["pl_pct"])
        print(f"    Qty     : {pos['qty']:.0f} contract(s)")
        print(f"    Entry   : ${pos['entry']:.2f}  →  Current: ${pos['current']:.2f}")
        print(f"    P&L     : {bar}  {pct:+.1f}%  (${pos['pl_dollar']:+.0f})")
        print(f"    Value   : ${pos['market_value']:.0f}  "
              f"(cost basis ${pos['cost_basis']:.0f})")

        if tp:
            print(f"    ACTION  : CLOSE NOW — take profit target hit (≥90%)")
        elif pos["pl_pct"] >= 0.80:
            print(f"    ACTION  : Watch closely — within 10% of take-profit threshold")
        else:
            print(f"    ACTION  : Hold — target is 100% gain")

    print()


def cmd_buy(symbol: str, direction: str, strike_str: str, expiration: str) -> None:
    print(f"\n{LINE}")
    print(f"  BUY  |  {symbol.upper()} {direction.upper()} "
          f"${strike_str} exp {expiration}")
    print(LINE)
    print()
    try:
        strike = float(strike_str)
    except ValueError:
        sys.exit(f"ERROR: invalid strike '{strike_str}'")
    buy_option(symbol.upper(), direction, strike, expiration)
    print()


def cmd_close(symbol: str) -> None:
    print(f"\n{LINE}")
    print(f"  CLOSE  |  {symbol.upper()}")
    print(LINE)
    print()
    close_position(symbol.upper())
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

USAGE = """\
Cardona Trade — options order execution and position management

Watchlist: SPY QQQ TSLA AAPL NVDA MSFT AMZN META GOOGL GLD

Usage:
  python3 scripts/cardona_trade.py status
  python3 scripts/cardona_trade.py buy SPY  call 750  2026-05-30
  python3 scripts/cardona_trade.py buy QQQ  put  460  2026-05-30
  python3 scripts/cardona_trade.py buy TSLA call 365  2026-05-30
  python3 scripts/cardona_trade.py buy NVDA put  130  2026-05-30
  python3 scripts/cardona_trade.py positions
  python3 scripts/cardona_trade.py close <SYMBOL>

Strike guide:
  SPY / QQQ     ~10 pts OTM  (e.g. SPY at $740 → $750 call or $730 put)
  All others    ~2% OTM rounded to nearest $5
                (e.g. TSLA at $350 → $360 call or $340 put)

Commands:
  status                   Market clock + all positions with P&L
  buy SYM DIR STRIKE EXP   Buy 1 options contract (max $200)
  positions                Options P&L with take-profit flags
  close SYMBOL             Sell-to-close a position
"""


def main() -> None:
    load_env()

    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        cmd_status()
    elif cmd == "positions":
        cmd_positions()
    elif cmd == "buy":
        if len(sys.argv) < 6:
            sys.exit("Usage: cardona_trade.py buy <SYMBOL> <call|put> <STRIKE> <YYYY-MM-DD>")
        cmd_buy(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "close":
        if len(sys.argv) < 3:
            sys.exit("Usage: cardona_trade.py close <SYMBOL>")
        cmd_close(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}\n")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
