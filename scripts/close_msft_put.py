#!/usr/bin/env python3
"""
One-shot market-open close: MSFT260626P00410000 (4 contracts).
Reason: thesis_invalidated — MSFT broke up from squeeze.

Scheduled: 2026-06-02 09:31 ET via crontab.
Run manually any time:  python3 scripts/close_msft_put.py
"""

import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_SCRIPTS = Path(__file__).parent
_ROOT    = _SCRIPTS.parent
sys.path.insert(0, str(_SCRIPTS))

import cardona_trade as _ct

OCC_SYM  = "MSFT260626P00410000"
REASON   = "thesis_invalidated — MSFT broke up from squeeze"
LOG_FILE = _ROOT / "logs" / "live_trader.log"
MEM_FILE = _ROOT / "memory" / "lessons.md"
ET       = ZoneInfo("America/New_York")


def _log(msg: str, level: str = "INFO") -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"[{ts}] [{level:5s}]  {msg}\n"
    print(line, end="")
    with open(LOG_FILE, "a") as fh:
        fh.write(line)


def _write_lesson(occ_sym: str, reason: str) -> None:
    today  = date.today().isoformat()
    parsed = _ct._parse_occ(occ_sym)
    under  = parsed.get("underlying", occ_sym)
    typ    = parsed.get("type", "?")
    strike = parsed.get("strike", 0)
    expiry = parsed.get("expiration", "?")
    MEM_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEM_FILE, "a") as fh:
        fh.write(
            f"\n---\n**{today}** — {under} ${strike:.0f} {typ} exp {expiry}  "
            f"Manual close | Reason: {reason}\n"
        )


def main() -> None:
    _ct.load_env()

    _log(f"MANUAL CLOSE: {OCC_SYM} — {REASON}", "TRADE")

    # Confirm market is open before sending the order
    mins = _ct._mins_to_close()
    if mins < 0:
        _log("Market is CLOSED — aborting close. Re-run during market hours.", "ERROR")
        sys.exit(1)
    if not (0 < mins < float("inf")):
        _log("Market status unknown (clock API error) — aborting to be safe.", "ERROR")
        sys.exit(1)

    _log(f"Market open — {mins:.0f} min remaining. Placing close order...")

    _ct.close_position(OCC_SYM)
    _write_lesson(OCC_SYM, REASON)
    _log(f"Close complete: {OCC_SYM}")


if __name__ == "__main__":
    main()
