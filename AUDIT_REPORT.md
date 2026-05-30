# Cardona Bot — Bug Audit Report

**Date:** 2026-05-30  
**Scope:** `scripts/live_trader.py`, `scripts/cardona_scanner.py`,
`scripts/cardona_trade.py`, `scripts/notify.py`  
**Method:** Full manual code review across all 9 bug categories; all bugs fixed
and verified with `py_compile` before committing.

---

## Bugs Found and Fixed

### BUG-01 — Win/Loss Logging Always Records "LOSS" (CRITICAL)
**File:** `scripts/live_trader.py` — `_append_trade_lesson()`  
**Category:** Profit target bug / math error  
**Severity:** Critical  

**Root cause:**  
```python
# BEFORE (wrong)
result = "WIN" if pl_pct >= 1.0 else "LOSS"
```
`pl_pct` is a decimal fraction (`0.90` = 90%). The take-profit fires at
`TP_THRESHOLD = 0.90`. Because `0.90 >= 1.0` is always False, every
auto-closed trade was written to `lessons.md` as **LOSS**, corrupting cycle
tracking and the EOD self-evaluation answers.

**Fix:**  
```python
result = "WIN" if pl_pct >= TP_THRESHOLD else "LOSS"
```

---

### BUG-02 — EOD Journal Only Sent Once Per Bot Lifetime (HIGH)
**File:** `scripts/live_trader.py` — `BotState`, `run_eod()`, main loop  
**Category:** Crash / reconnect bug  
**Severity:** High  

**Root cause:**  
`eod_sent` was a simple boolean set to `True` after the first EOD send and
never reset. A bot running continuously from Monday through Friday would send
Monday's EOD journal but silently skip Tuesday–Friday.

**Fix:**  
Added `eod_sent_date: date | None` to `BotState`. `run_eod()` now stamps
`state.eod_sent_date = et_now().date()`. The main-loop trigger changed from
`not st.eod_sent` to `st.eod_sent_date != et_now().date()`, firing once per
calendar day.

---

### BUG-03 — Close-Order Failures Are Invisible (HIGH)
**File:** `scripts/live_trader.py` — `_exec_close()`  
**Category:** Stop loss / order placement bug  
**Severity:** High  

**Root cause:**  
`_exec_close()` captured `stdout` from the subprocess but never checked
`returncode` or logged `stderr`. When `cardona_trade.py close` failed (network
error, Alpaca 4xx, etc.) the subprocess exited non-zero and wrote the error to
stderr, which was silently discarded. Additionally, the take-profit block
unconditionally called `del enriched[occ_sym]` regardless of whether the close
succeeded, causing the position to vanish from the display even on failure.

**Fix:**  
`_exec_close()` now returns a `bool`. Added return-code + stderr logging and a
`TimeoutExpired` handler. The take-profit block only removes the position from
`enriched` (and writes the lesson) when the close subprocess exits 0:
```python
if _exec_close(occ_sym, state):
    _append_trade_lesson(meta, pl_pct)
    del enriched[occ_sym]
    ...
# If close failed, keep in enriched so it retries next monitor cycle.
```

---

### BUG-04 — Order Placed When Clock API Fails (CRITICAL)
**File:** `scripts/cardona_trade.py` — `buy_option()`  
**Category:** Order placement bug  
**Severity:** Critical  

**Root cause:**  
`_mins_to_close()` returns `float("inf")` when the Alpaca clock API call
raises `SystemExit` (network failure). The existing guards checked only
`mins < 0` (closed) and `mins <= 30` (near close). `float("inf")` satisfies
neither condition, so the bot would proceed to place a buy order without
knowing whether the market was open.

**Fix:**  
```python
if not (0 < mins < float("inf")):
    print("SKIP [TIME_BLOCK]: market status unknown (clock API error) — skipping to be safe")
    return
```

---

### BUG-05 — Order Placed When Ask Price Is Zero — Budget Bypass (CRITICAL)
**File:** `scripts/cardona_trade.py` — `buy_option()`  
**Category:** Order placement bug / math error  
**Severity:** Critical  

**Root cause:**  
When the options snapshot returned an ask of 0 or null, `buy_option()` logged
a warning, set `ask = 0.0`, then continued. The $200 budget check was skipped
entirely (cost = 0 × 100 = $0). The market order was placed at an unknown cost
that could exceed the $200 limit. Additionally `ask = 0.0` was stored as
`entry_price_estimate`, making all subsequent P&L calculations show 0%.

**Fix:**  
Replaced the `ask = 0.0` branch with an early return:
```python
print("SKIP [NO_PRICE]: ask price unavailable — cannot verify $200 budget limit")
return
```

---

### BUG-06 — Position Monitor Runs on Weekends, Can Try to Auto-Close (HIGH)
**File:** `scripts/live_trader.py` — `past_session()`  
**Category:** Edge case  
**Severity:** High  

**Root cause:**  
`past_session()` checked only `(hour, minute) >= (15, 30)` with no weekday
guard. On Saturday or Sunday after 3:30 PM ET, `past_session()` returned
`True`, causing the main loop to run `run_monitor()`. If any position showed
≥ 90% P&L (from Friday's stale Alpaca data), `_exec_close()` would fire.

**Fix:**  
```python
def past_session() -> bool:
    t = et_now()
    if t.weekday() >= 5:
        return False
    return (t.hour, t.minute) >= SESSION_END
```

---

### BUG-07 — UTC-Based Trade Cutoff Wrong in Winter (EST) (MEDIUM)
**File:** `scripts/cardona_scanner.py` — `_is_after_cutoff()`  
**Category:** Edge case  
**Severity:** Medium  

**Root cause:**  
The cutoff was hard-coded to `19:30 UTC`, which equals `3:30 PM EDT` (UTC-4,
summer only). In winter (EST = UTC-5) `3:30 PM ET` = `20:30 UTC`, so the check
triggered at `19:30 UTC` = `2:30 PM ET`, blocking 1 full trading hour early.

**Fix (applied prior to this audit session, confirmed correct):**  
```python
def _is_after_cutoff() -> bool:
    from zoneinfo import ZoneInfo
    et = datetime.now(ZoneInfo("America/New_York"))
    return (et.hour, et.minute) >= (15, 30)
```

---

### BUG-08 — `error_count` Never Resets; Permanent ERROR Status After 5 Total Errors (MEDIUM)
**File:** `scripts/live_trader.py` — main loop  
**Category:** Crash / reconnect bug  
**Severity:** Medium  

**Root cause:**  
`error_count` was only ever incremented. Five transient errors spread over
multiple days would accumulate to `MAX_RETRIES = 5`, permanently setting
`state.status = "ERROR"`. The bot kept running but the ERROR status was
misleading and would persist even after hours of clean operation.

**Fix:**  
At the end of every clean loop iteration, reset the counter and restore status:
```python
if st.error_count > 0:
    st.error_count = 0
    if st.status == "ERROR":
        st.status = "ACTIVE" if in_session() else "MARKET_CLOSED"
```

---

### BUG-09 — Orphaned / Expired Positions Permanently Block Trade Slots (HIGH)
**File:** `scripts/live_trader.py` — `run_monitor()` ; `scripts/cardona_trade.py` — `close_position()`  
**Category:** Position tracking bug  
**Severity:** High  

**Root cause:**  
If a position expired worthless, was manually closed in the Alpaca UI, or was
never filled (e.g., order rejected), it would remain in `cardona_positions.json`
indefinitely. Every monitor run would find no matching Alpaca entry, silently
skip it (no cleanup), and the 2-position slot remained permanently occupied,
preventing any new auto-trades.

**Fix (two-pronged):**  

In `run_monitor()`: detect positions missing from Alpaca and clean them up:
- If `DTE < 0` (option definitively expired) → unregister + log as LOSS.
- If Alpaca responded successfully but the contract is absent during a live
  session → classify as orphan, unregister + log as LOSS.

In `close_position()`: when Alpaca has no record of the symbol, unregister it
from the registry immediately instead of silently returning.

---

### BUG-10 — Circuit Breaker Leaves Scan Panel Incomplete (LOW)
**File:** `scripts/live_trader.py` — `run_scan()`  
**Category:** Edge case  
**Severity:** Low  

**Root cause:**  
When the API circuit breaker fired (3 consecutive symbol failures), `run_scan`
returned early and set `state.scan_rows` to only the symbols scanned so far.
The UI scan panel would show fewer than 10 rows, making it ambiguous whether
the bot was still active.

**Fix:**  
Before early return, pad remaining symbols as ERROR rows:
```python
scanned = {r["symbol"] for r in rows}
for sym in SYMBOLS:
    if sym not in scanned:
        rows.append({"symbol": sym, "price": 0, "trend": "?",
                     "signal": None, "result": "ERROR: API down"})
```

---

### BUG-11 — HTML Injection in EOD Email Lessons Section (LOW)
**File:** `scripts/notify.py` — `build_html()`  
**Category:** Edge case / security  
**Severity:** Low  

**Root cause:**  
Lesson text from `lessons.md` was inserted directly into HTML without escaping.
Any `<`, `>`, or `&` character in a lesson note would break the email HTML.

**Fix:**  
```python
f'· {_html.escape(ln)}</p>'
```

---

### BUG-12 — `round_number_levels` Could Generate Zero/Negative Levels (LOW)
**File:** `scripts/cardona_scanner.py`, `scripts/notify.py` — `round_number_levels()`  
**Category:** Math error / edge case  
**Severity:** Low  

**Root cause:**  
`lo = int(base - ROUND_RANGE)` could produce 0 or negative values for very low
prices. A zero level would cause division-by-zero in `_near()` and `_dedup()`.
Not reachable with the current watchlist (all symbols well above $30) but a
latent risk.

**Fix:**  
```python
lo = max(ROUND_STEP, int(base - ROUND_RANGE))
```

---

## Additional Notes (No Code Change Required)

### NOTE-A — `_earnings_day()` Is a Safe Stub
`_is_earnings_day()` always returns `False`. This is documented as intentional
pending an earnings-calendar API integration. Current behavior is the safe
default.

### NOTE-B — Infinite Pagination Loop in `fetch_bars`
A perpetual `next_page_token` from Alpaca would cause `fetch_bars()` to loop
forever. With `LOOKBACK_DAYS=10` and `limit=1000` this cannot occur in practice
(≤70 bars fits in one page).

### NOTE-C — `notify.py` `fetch_bars` Has No Retry
Returns `[]` on first failure with no retry. This can produce incomplete EOD
reports on transient errors but has no effect on live trading decisions.

---

## Audit Checklist

| Category | Bugs Found | Status |
|----------|-----------|--------|
| Stale data bugs | 0 | ✅ No live stale-data path found |
| Stop loss execution bugs | BUG-03, BUG-09 | ✅ Fixed |
| Position tracking bugs | BUG-09 | ✅ Fixed |
| Order placement bugs | BUG-04, BUG-05 | ✅ Fixed |
| Crash and reconnect bugs | BUG-02, BUG-08 | ✅ Fixed |
| Profit target bugs | BUG-01, BUG-03 | ✅ Fixed |
| Math errors | BUG-01, BUG-12 | ✅ Fixed |
| Race conditions | 0 | ✅ Scan+monitor are sequential, no threads |
| Edge cases | BUG-06, BUG-07, BUG-10, BUG-11, BUG-12 | ✅ Fixed |

**Total bugs found: 12 (11 fixed in this audit, 1 already fixed prior)**
