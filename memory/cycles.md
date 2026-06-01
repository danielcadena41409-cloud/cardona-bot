# Cycle Tracking

_Each cycle = 10 trades. Target: 6–7 wins per cycle._

---

## Cycle Template

```
## Cycle N — [Start Date] to [End Date]

| # | Date | Instrument | Type | Strike | Expiry | Entry $ | Exit $ | P/L | Result |
|---|------|------------|------|--------|--------|---------|--------|-----|--------|
| 1 |      |            |      |        |        |         |        |     |        |
| 2 |      |            |      |        |        |         |        |     |        |
| 3 |      |            |      |        |        |         |        |     |        |
| 4 |      |            |      |        |        |         |        |     |        |
| 5 |      |            |      |        |        |         |        |     |        |
| 6 |      |            |      |        |        |         |        |     |        |
| 7 |      |            |      |        |        |         |        |     |        |
| 8 |      |            |      |        |        |         |        |     |        |
| 9 |      |            |      |        |        |         |        |     |        |
|10 |      |            |      |        |        |         |        |     |        |

**Wins:** _ / 10  
**Total P/L:** $___  
**Notes:**
```

---

## Cycle 1 — 2026-05-28 to [End Date]

| # | Date | Instrument | Type | Strike | Expiry | Entry $ | Exit $ | P/L | Result |
|---|------|------------|------|--------|--------|---------|--------|-----|--------|
| 1 |      |            |      |        |        |         |        |     |        |
| 2 |      |            |      |        |        |         |        |     |        |
| 3 |      |            |      |        |        |         |        |     |        |
| 4 |      |            |      |        |        |         |        |     |        |
| 5 |      |            |      |        |        |         |        |     |        |
| 6 |      |            |      |        |        |         |        |     |        |
| 7 |      |            |      |        |        |         |        |     |        |
| 8 |      |            |      |        |        |         |        |     |        |
| 9 |      |            |      |        |        |         |        |     |        |
|10 |      |            |      |        |        |         |        |     |        |

**Wins:** 0 / 0 completed  
**Total P/L:** $0  
**Notes:** Cycle 1 started 2026-05-28. 0 completed trades as of 2026-05-31.

_Void entries (not counted as trades):_
- **2026-05-28** — AAPL $305 PUT exp 2026-06-05: bot fired twice (duplicate-order bug, now fixed).
  Both orders placed as market orders at ~$1.68 and ~$1.60; neither filled in Alpaca paper
  simulation. No position held, no P/L. Not counted as wins or losses.
