# /cardona_check — Complete Cardona Strategy Check

> **The bot is fully autonomous.** The scanner places trades automatically when
> all conditions are met. This command is for human review only — to see what
> the bot has done, what it is holding, and whether any action is needed.
> You do not need to approve or place trades manually.

Run a full strategy review. Execute every step below in order without skipping any.

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
- **TAKE PROFIT**: any option up 90%+ — note that `auto_monitor` should have already closed it; if still open, run `python3 scripts/cardona_trade.py monitor` manually
- **EXPIRY WARNING**: any option expiring within 2 trading days → state the symbol and expiry date
- **SLOT AVAILABLE**: if fewer than 2 options positions are open → confirm how many auto-trade slots are available

## Step 6 — Compile the session review

After running all commands, write a clean summary in this format:

---

**CARDONA CHECK — [DATE] [TIME] ET**

**Market Direction**
- SPY: [UPTREND / DOWNTREND / SIDEWAYS] — [bot will trade calls / puts / sitting out]
- QQQ: [UPTREND / DOWNTREND / SIDEWAYS] — [bot will trade calls / puts / sitting out]
- (list any other symbols with signals)

**Bot Activity Since Last Check**
- Trades entered automatically: [list or NONE]
- Positions auto-closed: [list or NONE]

**Active Signals** (detected this scan)
- [CONFIRMED → bot fired trade / WATCHING → unconfirmed / NONE]

**Key Levels**
- SPY support: $X.XX | resistance: $X.XX
- (list key levels for any symbol showing a signal)

**Open Positions** ([N] open, [N] auto-trade slots free)
- [symbol]: [+X%] — [holding / expiry warning / take-profit missed — run monitor]

**Human Action Required**
- If a take-profit was missed by auto-monitor: `python3 scripts/cardona_trade.py monitor`
- If a position has an expiry warning: review and decide whether to close manually
- Otherwise: NONE — the bot is handling everything

---

The bot acts autonomously. Only flag items that require a human decision that the bot cannot make (e.g. missed take-profit, expiry in <2 days, unexpected position, account issue).
