# Cardona Bot — Bug Audit Report

**Date:** 2026-05-30
**Scope:** `scripts/live_trader.py` and all files it imports
         (`cardona_scanner.py`, `cardona_trade.py`, `notify.py`)
**Status:** All 12 bugs found and fixed. Committed to main.

---

## Summary

| # | Severity | Category | File | Description |
|---|----------|----------|------|-------------|
| 01 | CRITICAL | Math Error | `live_trader.py` | WIN threshold used 1.0 — every auto-close logged as LOSS |
| 02 | CRITICAL | Crash/Reconnect | `live_trader.py` | EOD journal never resent after day 1 of a multi-day run |
| 03 | CRITICAL | Order Placement | `live_trader.py` | `_exec_close` swallowed errors; lesson logged on failed close |
| 04 | CRITICAL | Order Placement | `cardona_trade.py` | Buy proceeded when clock API returned unknown market status |
| 05 | CRITICAL | Order Placement | `cardona_trade.py` | Buy proceeded with ask=0, bypassing $200 budget check |
| 06 | CRITICAL | Stale Data | `live_trader.py` | `past_session()` had no weekday guard — monitor ran on weekends |
| 07 | CRITICAL | Edge Case | `cardona_scanner.py` | `_is_after_cutoff()` fired 1h early in winter (EST vs EDT) |
| 08 | CRITICAL | Crash/Reconnect | `live_trader.py` | Error count never reset — permanent ERROR after 5 transient failures |
| 09 | CRITICAL | Position Tracking | `live_trader.py` + `cardona_trade.py` | Expired/orphaned positions never cleaned from registry |
| 10 | IMPORTANT | Edge Case/Display | `live_trader.py` | Circuit breaker left up to 7 symbols missing from scan panel |
| 11 | MINOR | HTML Injection | `notify.py` | Lesson text inserted raw into HTML email without escaping |
| 12 | IMPORTANT | Math/Division by Zero | `cardona_scanner.py` + `notify.py` | `round_number_levels` could generate 0/negative levels |

---

## Bug Details

---

### BUG-01 — CRITICAL — WIN threshold wrong in `_append_trade_lesson`
**File:** `live_trader.py:_append_trade_lesson`
**Category:** Math Error

**Problem:**
```python
result = "WIN" if pl_pct >= 1.0 else "LOSS"
```
`pl_pct` is the Alpaca `unrealized_plpc` fraction (e.g., 0.90 for 90% gain). The strategy
auto-closes at 90% gain (`TP_THRESHOLD = 0.90`). Since 0.90 < 1.0, every take-profit closure
was recorded as a "LOSS" in `lessons.md`. The cycle tracker, EOD self-evaluation, and win rate
statistics were all wrong.

**Fix:**
```python
result = "WIN" if pl_pct >= TP_THRESHOLD else "LOSS"
```

---

### BUG-02 — CRITICAL — EOD journal only sent once in a multi-day session
**File:** `live_trader.py`
**Category:** Crash/Reconnect

**Problem:** `st.eod_sent` was a plain bool, never reset. In a long-running session that spans
multiple trading days (the bot is designed to run continuously), `eod_sent` would be `True` after
the first EOD journal at 4:15 PM on day 1. The check `not st.eod_sent` would block the EOD journal
from firing on every subsequent day, meaning days 2+ had no EOD email.

**Fix:** Replaced `eod_sent: bool` with `eod_sent_date: date | None`. The gate now checks
`st.eod_sent_date != et_now().date()` instead of `not st.eod_sent`, resetting automatically at
midnight when the date changes.

```python
# Before
if in_eod_window() and not st.eod_sent:
    run_eod(st)

# After
if in_eod_window() and st.eod_sent_date != et_now().date():
    run_eod(st)
```

---

### BUG-03 — CRITICAL — `_exec_close` swallowed errors; lesson logged on failed close
**File:** `live_trader.py:_exec_close` and `run_monitor`
**Category:** Order Placement / Profit Target

**Problem (A):** `_exec_close` returned `None`. `run_monitor` unconditionally called
`_append_trade_lesson` and `del enriched[occ_sym]` regardless of close success.
If the API rejected the close or the subprocess crashed, the lesson would record a
false "closed" event while the position still existed on Alpaca and in `cardona_positions.json`.

**Problem (B):** Close failures were logged at "WARN" level, not "ERROR".
`subprocess.TimeoutExpired` fell into the bare `except Exception` without a specific message.

**Fix:** Changed return type to `bool`. Lesson and deletion only happen on `True`.
Added explicit `TimeoutExpired` handler. Failures logged at ERROR level.

```python
def _exec_close(occ_sym: str, state: BotState) -> bool:
    ...
    if r.returncode != 0:
        state.log(f"  ERR: {r.stderr.strip()[:200]}", "ERROR")
        return False
    return True

# In run_monitor:
if _exec_close(occ_sym, state):
    _append_trade_lesson(meta, pl_pct)
    del enriched[occ_sym]
```

---

### BUG-04 — CRITICAL — `buy_option` placed orders when clock API status was unknown
**File:** `cardona_trade.py:buy_option`
**Category:** Order Placement

**Problem:** `_mins_to_close()` returns `float("inf")` when the Alpaca clock API fails.
The original check was:
```python
if mins < 0:
    return  # closed
if mins <= 30:
    return  # last 30 min
```
`float("inf")` fails both conditions, so the buy proceeded with market status unknown.
Orders placed during a closed market (or on a holiday) would be rejected by Alpaca but still
consume a position slot in the registry (since `_register_position` was called after `_trade_post`).

**Fix:**
```python
if not (0 < mins < float("inf")):
    print("SKIP [TIME_BLOCK]: market status unknown (clock API error) — skipping to be safe")
    return
```

---

### BUG-05 — CRITICAL — `buy_option` bypassed $200 budget check when ask was unavailable
**File:** `cardona_trade.py:buy_option`
**Category:** Order Placement

**Problem:** When the options snapshot returned no ask price (`ask = None` or `ask = 0`),
the old code set `ask = 0.0` and printed a warning but still proceeded:
```python
else:
    print("  Warning: budget cannot be verified; proceeding with market order")
    ask = 0.0
```
A market order was then placed with no price validation. The actual fill could be any amount,
violating the $200 maximum per trade rule. The position was also registered with
`entry_price_estimate = 0.0`, making all P&L calculations wrong for that position.

**Fix:**
```python
else:
    print("SKIP [NO_PRICE]: ask price unavailable — cannot verify $200 budget limit")
    return
```

---

### BUG-06 — CRITICAL — `past_session()` had no weekday guard — monitor ran on weekends
**File:** `live_trader.py:past_session`
**Category:** Stale Data

**Problem:**
```python
def past_session() -> bool:
    t = et_now()
    return (t.hour, t.minute) >= SESSION_END
```
On Saturdays and Sundays, any time after 3:30 PM ET returned `True`. The main loop then ran
`run_monitor()` every 5 minutes all weekend. The monitor called `_safe_positions()` (API cost)
and `_safe_snapshot()` per position with stale Friday prices — the bid price of an expired option
would be 0, potentially triggering incorrect P&L calculations or false orphan cleanups.

**Fix:**
```python
def past_session() -> bool:
    t = et_now()
    if t.weekday() >= 5:
        return False
    return (t.hour, t.minute) >= SESSION_END
```

---

### BUG-07 — IMPORTANT — `_is_after_cutoff()` fired 1 hour early in winter (EST)
**File:** `cardona_scanner.py:_is_after_cutoff`
**Category:** Edge Case / Wrong Time Gate

**Problem:** The function used a hardcoded UTC comparison:
```python
return utc.hour > 19 or (utc.hour == 19 and utc.minute >= 30)
# "3:30 PM EDT = 19:30 UTC"
```
Correct in summer (EDT = UTC−4). In winter (EST = UTC−5), 3:30 PM ET = 20:30 UTC.
The cutoff fired at 2:30 PM ET all winter, blocking the last hour of valid trading.
Used by the standalone scanner (`cmd_scan`). `live_trader.py` was not affected (it
already used `ZoneInfo("America/New_York")` for its own time gates).

**Fix:**
```python
now_et = datetime.now(ZoneInfo("America/New_York"))
return (now_et.hour, now_et.minute) >= (15, 30)
```

---

### BUG-08 — CRITICAL — Error count never reset; permanent ERROR after 5 transient failures
**File:** `live_trader.py` main loop
**Category:** Crash/Reconnect

**Problem:** `st.error_count` incremented on every exception but was never decremented or reset
on a successful iteration. `MAX_RETRIES = 5`. Five transient API timeouts spread over a single
trading day permanently set `st.status = "ERROR"`, misleading the operator.

**Fix:** Reset at the end of each clean loop iteration:
```python
if st.error_count > 0:
    st.error_count = 0
    if st.status == "ERROR":
        st.status = "ACTIVE" if in_session() else "MARKET_CLOSED"
```
ERROR status now only persists through a genuine crash loop (5+ back-to-back exceptions).

---

### BUG-09 — CRITICAL — Expired/orphaned positions never removed from registry
**Files:** `live_trader.py:run_monitor`, `cardona_trade.py:close_position`
**Category:** Position Tracking

**Problem:** Three separate code paths all failed to clean up `cardona_positions.json`:

1. **Expired worthless (DTE < 0):** `run_monitor` found `ap = None`, fell through to snapshot
   (bid=0, P&L=−100%), TP check failed (−1.0 < 0.90), and nothing was cleaned up.
2. **Externally closed (DTE ≥ 0, not on Alpaca):** Same as above — no cleanup.
3. **`close_position` early return:** When called for a symbol not found on Alpaca, the function
   returned early WITHOUT calling `_unregister_position`.

In all three cases, positions accumulated in `cardona_positions.json` indefinitely. With a 2-position
cap, two expired positions permanently halted all new trading.

**Fix — `run_monitor`:** Added orphan/expiry detection at the top of each loop iteration:
```python
if ap is None:
    if exp_parseable and dte < 0:
        # Definitively expired — always clean up
        _ct._unregister_position(occ_sym)
        _append_trade_lesson(meta, -1.0)
        continue
    if alpaca_map and in_session():
        # API is working and position is gone — externally closed
        _ct._unregister_position(occ_sym)
        _append_trade_lesson(meta, -1.0)
        continue
```

**Fix — `close_position`:**
```python
if symbol not in open_syms:
    print(f"SKIP: no open position for {symbol} — removing from registry")
    _unregister_position(symbol)   # ← added
    return
```

---

### BUG-10 — IMPORTANT — Circuit breaker left scan panel incomplete (< 10 rows)
**File:** `live_trader.py:run_scan`
**Category:** Edge Case / Display

**Problem:** When the API circuit breaker triggered after 3 consecutive fetch failures,
`state.scan_rows = rows` captured only the symbols attempted (3 error rows). The remaining
7 symbols were never added. The scan panel displayed fewer than 10 rows with no explanation
for the missing symbols.

**Fix:** Before returning from the circuit breaker, pad with explicit error rows:
```python
scanned = {r["symbol"] for r in rows}
for sym in SYMBOLS:
    if sym not in scanned:
        rows.append({"symbol": sym, "price": 0, "trend": "?",
                     "signal": None, "result": "ERROR: API down"})
state.scan_rows = rows
```

---

### BUG-11 — MINOR — Lesson text injected raw into HTML email
**File:** `notify.py:build_html`
**Category:** HTML Injection

**Problem:** Lesson lines from `memory/lessons.md` were inserted directly:
```python
content += f'· {ln}</p>'
```
Any `<`, `>`, `&` or `"` in a lesson would corrupt the email layout.

**Fix:**
```python
import html as _html
...
content += f'· {_html.escape(ln)}</p>'
```

---

### BUG-12 — IMPORTANT — `round_number_levels` generated zero/negative strike levels
**Files:** `cardona_scanner.py`, `notify.py`
**Category:** Math Error / Division by Zero

**Problem:**
```python
lo = int(base - ROUND_RANGE)  # ROUND_RANGE = 30
```
For any price ≤ $30, `lo` could be 0 or negative. `_dedup` divides by `out[-1]` and `_near`
divides by `level`, both producing `ZeroDivisionError` if a 0-level entered the S/R pool.
Current watchlist prices (min ~$180) avoid this, but the code was one new symbol away from
crashing.

**Fix:**
```python
lo = max(ROUND_STEP, int(base - ROUND_RANGE))  # never generate 0 or negative levels
```

---

## Bugs Investigated — Not Fixed (by design or non-issue)

| | Description | Verdict |
|-|-------------|---------|
| A | `fetch_bars` calls `sys.exit()` on HTTP errors | Safe — `_safe_bars` explicitly catches `SystemExit`. |
| B | Market orders for options (no limit orders) | Design choice per Cardona strategy. |
| C | No market holiday detection in `in_session()` | `buy_option` uses Alpaca clock to block orders. Scanning on holidays wastes API calls but causes no harm. |
| D | `_next_expiry` last-resort fallback may return non-Friday | Mathematically unreachable with a 5–14 day window. `_find_contract` would return NO_CONTRACT safely. |
| E | `_safe_bars` has no total-fetch timeout | ≈70 bars max, ~2 pages, well within 15-min scan interval. |
| F | Cycle tracker (`cycles.md`) not auto-updated | By design — CLAUDE.md specifies manual tracking. |
