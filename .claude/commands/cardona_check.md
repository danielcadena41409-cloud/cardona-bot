# /cardona_check — Complete Cardona Strategy Check

Run a full Cardona strategy check. Execute every step below in order without skipping any.

## Step 1 — Read memory files

Read these three files and hold their contents as active context for this session:
- `memory/strategy.md` — full strategy rules
- `memory/lessons.md` — lessons from past trades
- `memory/cycles.md` — current cycle win/loss tracking

Briefly confirm which cycle is active and how many trades are in the current cycle.

## Step 2 — Run the full scanner

```bash
python3 scripts/cardona_scanner.py scan
```

After showing the output, extract and summarize:
- SPY trend direction and whether calls, puts, or no trades are allowed
- QQQ trend direction and whether calls, puts, or no trades are allowed
- Any CONFIRMED signals — state them clearly with the required action
- Any unconfirmed signals — note them as "watching"
- Key support and resistance levels for both symbols (top 2 each)

## Step 3 — Show candle detail for both symbols

```bash
python3 scripts/cardona_scanner.py candles SPY
python3 scripts/cardona_scanner.py candles QQQ
```

After showing output, identify:
- The most recent candle — is it a hammer, hanging man, or neither?
- Any hammer or hanging man in the last 5 bars — note time and price

## Step 4 — Show S/R levels for both symbols

```bash
python3 scripts/cardona_scanner.py levels SPY
python3 scripts/cardona_scanner.py levels QQQ
```

After showing output, highlight:
- Nearest support below current price for each symbol
- Nearest resistance above current price for each symbol
- Any $5 round numbers within 1% of current price (these are high-probability levels)

## Step 5 — Show open positions and P&L

```bash
python3 scripts/cardona_trade.py status
```

After showing output, flag any of these conditions clearly:
- **TAKE PROFIT**: any option position up 90% or more → state the symbol and recommend closing now
- **EXPIRY WARNING**: any option expiring within 2 days → state the symbol and expiry date
- **SLOT AVAILABLE**: if fewer than 2 options positions are open → confirm how many slots are free

## Step 6 — Compile the session verdict

After running all commands, write a clean summary in this format:

---

**CARDONA CHECK — [DATE] [TIME] ET**

**Market Direction**
- SPY: [UPTREND / DOWNTREND / SIDEWAYS] — [calls OK / puts OK / no trade]
- QQQ: [UPTREND / DOWNTREND / SIDEWAYS] — [calls OK / puts OK / no trade]

**Active Signals**
- [CONFIRMED / WATCHING / NONE] — describe any signals

**Key Levels**
- SPY support: $X.XX | resistance: $X.XX
- QQQ support: $X.XX | resistance: $X.XX

**Open Positions** ([N] open, [N] slots free)
- [symbol]: [+X% → TAKE PROFIT / holding / expiry warning]

**Action Required**
- [what to do right now, if anything — be specific]

---

If there is nothing to do (sideways trend, no signals, no take profits), say so explicitly. Do not suggest trades that violate strategy rules.
