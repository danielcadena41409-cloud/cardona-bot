# Cardona Strategy Bot — Rules & Configuration

> **This bot is fully autonomous.** It scans, enters trades, monitors positions,
> and closes take-profit targets without human approval. The human role is to
> review the EOD journal and manage account-level risk only.

---

## AUTONOMOUS RULES

These are hard limits enforced in code. The bot will not override them.

1. **Maximum 2 open options positions at all times** — checked before every entry
2. **Maximum $200 per trade** — ask price × 100 must be ≤ $200; skip if over
3. **Only enter on confirmed signals** — requires: hammer/hanging man pattern + next candle confirmation + trend alignment (uptrend for calls, downtrend for puts)
4. **Auto-close at 90% gain** — `auto_monitor()` fires every hour and closes positions at 90%+ without waiting for 100%
5. **Never chase a signal** — if the confirmation candle's close has already moved more than 0.5% past the S/R level, skip the trade
6. **No trades in the last 30 minutes of market hours** — hard cutoff at 3:30 PM ET, enforced via Alpaca clock `next_close`
7. **No trades on earnings day** — `_is_earnings_day()` check before every auto-entry (requires earnings calendar API; currently a safe stub returning False)

---

## INSTRUMENTS

**Watchlist (10 symbols):** SPY, QQQ, TSLA, AAPL, NVDA, MSFT, AMZN, META, GOOGL, GLD

- Options only (calls and puts) on all 10 symbols
- Weekly options, maximum 2 weeks to expiration
- **Strike price:**
  - SPY and QQQ: approximately 10 points out of the money
  - All other symbols: approximately 2% out of the money, rounded to nearest $5 strike

---

## CANDLE TIMEFRAME
- 1-hour candles exclusively for all pattern detection

---

## ENTRY — CALLS
1. Identify key support level using previous lows on 1-hour chart
2. Wait for hammer candle AT or NEAR that support
   - Hammer: green candle with small body in upper third and long lower tail at least 2x the body size
3. Wait for next 1-hour candle to close GREEN as confirmation
4. Buy call option approximately 10 points above current price

**Example:** SPY at $740 → buy $750 call expiring within 2 weeks

---

## ENTRY — PUTS
1. Identify key resistance level using previous highs on 1-hour chart
2. Wait for hanging man candle AT or NEAR that resistance
   - Hanging man: same shape as hammer but occurring at resistance
3. Wait for next 1-hour candle to close RED as confirmation
4. Buy put option approximately 10 points below current price

**Example:** SPY at $740 → buy $730 put expiring within 2 weeks

---

## POSITION SIZING
- Fixed $200 per trade maximum on paper account
- Never risk more than $200 on a single trade

---

## PROFIT TARGET
- Exit when option gains 100% or more
- Check every hour
- If at 90% gain or above, consider closing

---

## STOP LOSS
- Let option go to zero — do NOT cut early
- Hold to expiration or 100% gain
- This is intentional per Cardona strategy — no early exits on losing trades

---

## CYCLE LOGIC
- Track every 10 trades as a cycle
- Target: win 6 or 7 out of every 10 trades
- Review performance after every 10 trades
- See `memory/cycles.md` for tracking template

---

## SUPPORT AND RESISTANCE
- Look at last 20 one-hour candles
- **Support:** lowest low that price has bounced from at least once
- **Resistance:** highest high that price has rejected from at least once
- Key round numbers every $5 increment on SPY and QQQ also count as S/R

---

## MARKET DIRECTION (check before every trade)
- **Uptrend** (higher highs + higher lows) → favor calls only
- **Downtrend** (lower highs + lower lows) → favor puts only
- **Sideways** → no trade

---

## MAX POSITIONS
- Maximum 2 open positions at once
- Never open a new trade if 2 are already open

---

## SCHEDULE
| Time (ET) | Action |
|-----------|--------|
| 9:30 AM   | Morning scan — assess market direction, identify S/R levels |
| 10 AM – 3 PM | Hourly scan — check for hammer/hanging man patterns |
| 4:00 PM   | EOD journal — log closed trades, update lessons |

---

## MEMORY SYSTEM
At every session start, read these files in order:
1. `memory/strategy.md` — full strategy reference
2. `memory/lessons.md` — lessons from past trades
3. `memory/cycles.md` — current cycle win/loss tracking

After every closed trade, write a lesson to `memory/lessons.md`.

---

## REGIME INTEGRATION

At the start of every scan, the bot reads `~/trading-agent/data/regime.json` (written by the stock bot's Markov regime detector). The regime governs trade entry behavior for that session.

| Regime | Cardona Action |
|--------|----------------|
| **BULL_TRENDING** | Favor call signals. If both CALL and PUT appear on the same symbol, keep only the CALL. |
| **BEAR_TRENDING** | Favor put signals. If both CALL and PUT appear on the same symbol, keep only the PUT. |
| **HIGH_VOLATILITY** | Skip all new entries — bot goes silent. Signals are displayed for visibility only. |
| **SIDEWAYS** | Normal rules apply but drift tolerance tightens from 0.5% → 0.3% (stronger confirmation required). |

The current regime and tomorrow's forecast are printed at the top of every scan output and displayed in the daily EOD email journal. If `regime.json` is missing or unreadable, the bot defaults to SIDEWAYS (safest posture).
