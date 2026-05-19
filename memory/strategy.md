# Cardona Strategy — Full Reference

## Overview
Paper trading strategy using SPY and QQQ options. Pattern-based entries on 1-hour candles. Fixed risk per trade. Hold winners to 100%, let losers expire.

---

## Instruments
- SPY options and QQQ options only
- Weekly expiration, maximum 2 weeks out
- Strike ~10 points OTM from current price

## Timeframe
- 1-hour candles for all analysis and pattern detection

---

## Call Entry Checklist
- [ ] Market is in uptrend (higher highs + higher lows on 1H)
- [ ] Fewer than 2 open positions
- [ ] Identified support level from last 20 candles (or key $5 round number)
- [ ] Hammer candle formed AT or NEAR support (small green body in upper third, lower tail ≥ 2x body)
- [ ] Next 1H candle closed GREEN (confirmation)
- [ ] Buy call ~10 points above current price, ≤2 weeks expiry, max $200

## Put Entry Checklist
- [ ] Market is in downtrend (lower highs + lower lows on 1H)
- [ ] Fewer than 2 open positions
- [ ] Identified resistance level from last 20 candles (or key $5 round number)
- [ ] Hanging man candle formed AT or NEAR resistance (same shape as hammer)
- [ ] Next 1H candle closed RED (confirmation)
- [ ] Buy put ~10 points below current price, ≤2 weeks expiry, max $200

---

## Exit Rules
| Condition | Action |
|-----------|--------|
| Option up 100%+ | EXIT — take profit |
| Option up 90–99% | Consider closing — monitor closely |
| Option losing | HOLD — let expire (no early stop) |

## Position Limits
- Max 2 open at once
- Max $200 per trade

---

## Support & Resistance Rules
- Scan last 20 one-hour candles
- Support = lowest low bounced from at least once
- Resistance = highest high rejected from at least once
- Every $5 round number (e.g. $540, $545, $550) counts as a level on SPY and QQQ

## Market Direction Filter
- Uptrend: HH + HL sequence → calls only
- Downtrend: LH + LL sequence → puts only
- Sideways / unclear → skip, no trade

---

## Cycle Performance Target
- 10 trades = 1 cycle
- Target: 6–7 wins per cycle
- Track in `memory/cycles.md`
