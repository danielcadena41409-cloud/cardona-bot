# Weekly Review Log

_Decisions, rule changes, and strategy approvals. Most recent entry first._

---

## 2026-06-02 — SIDEWAYS Catalyst-Only Mode

**Status:** APPROVED and implemented.

**Decision:** Replace the existing SIDEWAYS behaviour (tighter drift only) with a full hard block plus a narrowly-defined catalyst exception.

### Rules implemented

| Rule | Detail |
|------|--------|
| **1 — Hard block** | All standard directional entries blocked in SIDEWAYS regardless of signal quality |
| **2 — Catalyst exception** | Allowed only when: earnings within 5 days + IV Rank ≤ 45 + not Friday + 6/6 signal + max 1 position + 0.5% portfolio risk cap |
| **3 — Pre-earnings exit** | Any held position with earnings today is force-closed before 3:30 PM ET |
| **4 — No Friday entries** | No new positions in SIDEWAYS on Fridays |
| **5 — Normal resume** | All normal rules restore immediately on BULL_TRENDING or BEAR_TRENDING |

### Files changed
- `scripts/options_research.py` — new module: `earnings_within_days`, `is_earnings_today`, `get_iv_rank`, `update_iv_history`, `check_catalyst_exception`
- `scripts/live_trader.py` — imported `options_research`, added SIDEWAYS position limit (1), catalyst gate in `run_scan`, pre-earnings exit in `run_monitor`
- `CLAUDE.md` — new section: SIDEWAYS CATALYST-ONLY MODE
- `memory/strategy.md` — new section: SIDEWAYS CATALYST-ONLY MODE

### Notes
- IV Rank history starts accumulating from first scan. 30 days of data required before IV Rank is trusted; catalyst trades are blocked until then (conservative).
- Earnings dates from Yahoo Finance calendarEvents API (no additional key required). Cached 1 hour per symbol per process.
- Pre-earnings exit fires for ALL positions (not just catalyst entries) when earnings are today and market is open before 3:30 PM ET. Conservative and correct.
