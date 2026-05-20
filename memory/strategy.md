# Cardona Strategy — Complete Rules Reference

_Read this file at the start of every session. It is the single source of truth for all trading decisions._

---

## What This Bot Does

**Fully autonomous paper trading bot.** It scans all 10 watchlist symbols every hour, detects hammer and hanging man patterns at key S/R levels, verifies trend alignment and confirmation, then places trades and closes winners automatically — no human approval required. The human role is limited to reviewing the EOD journal.

---

## AUTONOMOUS OPERATION

The bot enforces these rules in code before every trade:

| Rule | Detail |
|------|--------|
| Confirmed signal required | Pattern + confirmation candle + trend alignment — all three must be true |
| No-chase rule | Skip if confirmation close is >0.5% past the S/R level |
| Market hours only | No trades when market is closed or within 30 min of close (3:30 PM ET) |
| No earnings trades | Skip if symbol has earnings today (requires calendar API) |
| Max 2 positions | Checked before every entry — hard block if at limit |
| Max $200 / trade | Verified via live ask price before order submission |
| Auto-close at 90% | `auto_monitor()` runs hourly and closes positions at 90%+ gain |
| No manual approval | Trades execute immediately when all conditions are met |

---

## INSTRUMENTS

| Field | Rule |
|-------|------|
| Watchlist | SPY, QQQ, TSLA, AAPL, NVDA, MSFT, AMZN, META, GOOGL, GLD |
| Asset class | Options (calls and puts) on all 10 symbols |
| Expiration | Weekly options, maximum 2 weeks to expiration |
| Strike — SPY / QQQ | Approximately **10 points OTM** from current price |
| Strike — all others | Approximately **2% OTM**, rounded to nearest $5 strike |
| Contracts | 1 contract per trade |
| Account | Paper trading only |

**Strike examples:**
- SPY at $740 → $750 call / $730 put (10 pts)
- TSLA at $350 → $360 call / $340 put (2% ≈ $7, rounded to $5 increment)
- NVDA at $130 → $135 call / $125 put (2% ≈ $2.60, rounded to $5 increment)
- GLD at $310 → $320 call / $305 put (2% ≈ $6.20, rounded to $5 increment)

---

## CANDLE TIMEFRAME

- **1-hour candles exclusively** for all pattern detection, S/R identification, and trend analysis
- No other timeframe is used for any decision

---

## MARKET DIRECTION — CHECK BEFORE EVERY TRADE

Analyze the last 10 one-hour candles. Count bar-to-bar transitions:

| Condition | Direction | Allowed Trades |
|-----------|-----------|----------------|
| >50% of transitions are higher-high AND higher-low | **UPTREND** | Calls only |
| >50% of transitions are lower-high AND lower-low | **DOWNTREND** | Puts only |
| Mixed / neither qualifies | **SIDEWAYS** | **No trade — wait** |

**Sideways = skip the scan entirely. Do not look for setups when trend is unclear.**

---

## SUPPORT & RESISTANCE IDENTIFICATION

Look at the **last 20 one-hour candles**:

**Support level:**
- Find candles where: close > open (green) AND the candle has a lower tail (open > low)
- The low of that candle is a support level
- Price "bounced" — it touched the low but closed higher

**Resistance level:**
- Find candles where: close < open (red) AND the candle has an upper tail (high > open)
- The high of that candle is a resistance level
- Price "rejected" — it touched the high but closed lower

**Round numbers (always active):**
- Every $5 increment near current price counts as S/R (e.g., $540, $545, $550)
- Valid within ±$30 of current price
- Round numbers below current price = additional support
- Round numbers above current price = additional resistance

**Proximity rule:** A candle is "at or near" a level if within **0.5%** of that level.

---

## CALL ENTRY — COMPLETE CHECKLIST

All 5 conditions must be true before entering:

1. **Trend is UPTREND** on the 1-hour chart (higher highs + higher lows)
2. **Fewer than 2 options positions** currently open
3. **Key support level identified** from the last 20 candles or a $5 round number
4. **Hammer candle formed AT or NEAR support** (within 0.5%):
   - Candle is GREEN (close > open)
   - Body sits in the **upper third** of the total candle range
     - `(open − low) / (high − low) ≥ 0.667`
   - Lower tail is **at least 2× the body size**
     - `(open − low) ≥ 2 × (close − open)`
5. **Next 1-hour candle closed GREEN** (bullish confirmation)

**Then:** Buy a call option approximately 10 points above current price, expiring within 2 weeks, maximum $200.

_Example: SPY at $740 → buy $750 call expiring within 14 days_

---

## PUT ENTRY — COMPLETE CHECKLIST

All 5 conditions must be true before entering:

1. **Trend is DOWNTREND** on the 1-hour chart (lower highs + lower lows)
2. **Fewer than 2 options positions** currently open
3. **Key resistance level identified** from the last 20 candles or a $5 round number
4. **Hanging man candle formed AT or NEAR resistance** (within 0.5%):
   - Candle is RED (close < open)
   - Body sits in the **upper third** of the total candle range
     - `(close − low) / (high − low) ≥ 0.667`
   - Lower tail is **at least 2× the body size**
     - `(close − low) ≥ 2 × (open − close)`
5. **Next 1-hour candle closed RED** (bearish confirmation)

**Then:** Buy a put option approximately 10 points below current price, expiring within 2 weeks, maximum $200.

_Example: SPY at $740 → buy $730 put expiring within 14 days_

---

## POSITION SIZING

- **Fixed $200 maximum per trade** — enforced by `cardona_trade.py`
- If the ask price × 100 > $200, skip the trade and log the reason
- Never risk more than $200 on a single contract

---

## EXIT RULES

| Condition | Action |
|-----------|--------|
| Option up **100% or more** | **EXIT immediately** — full take profit |
| Option up **90–99%** | **Flag as TAKE PROFIT** — close now, don't wait for 100% |
| Option up **80–89%** | Watch closely — within 10% of target |
| Option **losing at any level** | **HOLD — do not cut early** |
| Option at expiration | Let expire — do not exercise |

**The stop loss is zero. This is intentional per the Cardona strategy.** The strategy expects ~6–7 wins out of 10. The losers expire worthless. The winners double the money. Net result is positive if win rate holds.

**Check every open position every hour** during market hours (10 AM – 3 PM ET).

---

## POSITION LIMITS

- **Maximum 2 open options positions at any time**
- Never open a new trade if 2 positions are already open
- Non-options positions (stocks, ETFs) do not count against this limit

---

## DAILY SCHEDULE

| Time ET | Action |
|---------|--------|
| 9:30 AM | Morning scan — assess trend, map S/R, check for opening signals |
| 10:00 AM | Hourly scan — new signals + position P&L check |
| 11:00 AM | Hourly scan — new signals + position P&L check |
| 12:00 PM | Hourly scan — new signals + position P&L check |
| 1:00 PM | Hourly scan — new signals + position P&L check |
| 2:00 PM | Hourly scan — new signals + position P&L check |
| 3:00 PM | Hourly scan — **last entry window** — new signals + P&L check |
| 4:15 PM | EOD journal — log trades, update lessons, send email digest |

**No new trades after 3:00 PM ET.** Monitor existing positions but do not open new ones.

---

## CYCLE TRACKING

- Every **10 trades** = 1 cycle
- **Target: 6 or 7 wins per cycle**
- A "win" = option closed at 100%+ gain
- A "loss" = option expired worthless (or closed under 100%)
- Track all cycles in `memory/cycles.md`
- Review performance and adjust nothing after each cycle — the strategy is mechanical

---

## MEMORY SYSTEM — READ AT SESSION START

Always read these three files before any trading session:

| File | Purpose |
|------|---------|
| `memory/strategy.md` | This file — full rules reference |
| `memory/lessons.md` | Lessons learned from past trades |
| `memory/cycles.md` | Current and past cycle win/loss records |

**Write a lesson to `memory/lessons.md` after every closed trade.** Include:
- Date and symbol
- What the setup looked like
- Whether the trade won or lost
- What to watch for next time

---

## SCRIPTS REFERENCE

| Script | Command | Purpose |
|--------|---------|---------|
| `cardona_scanner.py` | `scan` | Full signal report for SPY and QQQ |
| `cardona_scanner.py` | `candles SPY` | Last 10 bars with pattern markers |
| `cardona_scanner.py` | `levels SPY` | S/R levels and round numbers |
| `cardona_trade.py` | `status` | Market clock, positions, P&L |
| `cardona_trade.py` | `buy SPY call 750 2026-05-30` | Buy a call option |
| `cardona_trade.py` | `buy QQQ put 460 2026-05-30` | Buy a put option |
| `cardona_trade.py` | `positions` | Options P&L with take-profit flags |
| `cardona_trade.py` | `close SYMBOL` | Sell-to-close a position |
| `notify.py` | _(no args)_ | Send EOD journal email |
| `notify.py` | `--test` | Preview email without sending |

---

## SLASH COMMAND

Run `/cardona_check` in Claude Code for a complete manual strategy check at any time. This runs the scanner, checks positions, and gives a clear action recommendation.

---

## REGIME FILTER

At scan start, read `~/trading-agent/data/regime.json`. The Markov regime overrides normal entry bias for the session.

| Regime | Effect on Cardona |
|--------|-------------------|
| **BULL_TRENDING** | Favor calls. If a symbol has both CALL and PUT signals, take the CALL only. |
| **BEAR_TRENDING** | Favor puts. If a symbol has both CALL and PUT signals, take the PUT only. |
| **HIGH_VOLATILITY** | All auto-trades blocked. Bot scans but does not enter. Log "HIGH_VOLATILITY regime blocked entry" in journal. |
| **SIDEWAYS** | Normal rules but drift tolerance tightens: 0.5% → 0.3% (no-chase threshold). |

**Default on failure:** If `regime.json` is missing or unreadable, default to SIDEWAYS.

Regime + tomorrow forecast are shown at the top of every scan output and in the EOD email.

---

## HARD RULES — NEVER VIOLATE

1. SPY and QQQ options only
2. 1-hour candles only for all analysis
3. No trade when trend is sideways
4. No trade without confirmation candle
5. Max 2 open positions
6. Max $200 per trade
7. Never close a losing trade early — let it expire
8. Close winners at 100% gain (flag at 90%)
9. No trades after 3:00 PM ET
10. Read memory files at every session start
11. Read regime file at every session start — apply regime rules before any entry
