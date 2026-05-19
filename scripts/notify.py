#!/usr/bin/env python3
"""Cardona Notify — daily journal email via SendGrid."""

import json
import os
import re
import sys
import requests
from datetime import date, datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

TRADE_URL    = "https://paper-api.alpaca.markets/v2"
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
OPTION_RE    = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
TP_THRESHOLD = 0.90


# ── Environment ───────────────────────────────────────────────────────────────

def load_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _alpaca_hdrs() -> dict:
    return {
        "APCA-API-KEY-ID":     os.environ.get("APCA_API_KEY_ID", ""),
        "APCA-API-SECRET-KEY": os.environ.get("APCA_API_SECRET_KEY", ""),
    }


# ── Data gathering ────────────────────────────────────────────────────────────

def get_positions() -> list:
    try:
        r = requests.get(f"{TRADE_URL}/positions", headers=_alpaca_hdrs(), timeout=15)
        return r.json() if r.ok else []
    except Exception:
        return []


def get_account() -> dict:
    try:
        r = requests.get(f"{TRADE_URL}/account", headers=_alpaca_hdrs(), timeout=15)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def read_journal(today: str) -> list:
    """Read JSON files from journal/ matching today's date prefix."""
    journal_dir = Path(__file__).parent.parent / "journal"
    if not journal_dir.exists():
        return []
    entries = []
    for f in sorted(journal_dir.glob(f"{today}*.json")):
        try:
            with open(f) as fh:
                entries.append(json.load(fh))
        except Exception:
            entries.append({"file": f.name, "error": "could not parse"})
    return entries


def read_recent_lessons(n: int = 5) -> list:
    """Return up to n non-blank, non-header lines from memory/lessons.md."""
    path = Path(__file__).parent.parent / "memory" / "lessons.md"
    if not path.exists():
        return []
    lines = [
        ln.strip()
        for ln in path.read_text().splitlines()
        if ln.strip()
        and not ln.startswith("#")
        and not ln.startswith("_")
        and ln.strip() != "---"
    ]
    return lines[-n:]


# ── OCC parser ────────────────────────────────────────────────────────────────

def _parse_occ(sym: str) -> dict:
    m = re.match(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", sym)
    if not m:
        return {}
    return {
        "underlying": m.group(1),
        "expiration": f"20{m.group(2)}-{m.group(3)}-{m.group(4)}",
        "type":       "CALL" if m.group(5) == "C" else "PUT",
        "strike":     int(m.group(6)) / 1000,
    }


# ── Email construction ────────────────────────────────────────────────────────

def _pct_color(pct: float) -> str:
    if pct >= 90:  return "#00dd55"
    if pct >= 50:  return "#88cc00"
    if pct >= 0:   return "#cccc00"
    return "#dd3300"


def build_email(today: str, positions: list, account: dict,
                journal: list, lessons: list) -> tuple:
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    equity  = account.get("equity")
    cash    = account.get("cash")
    opts    = [p for p in positions if OPTION_RE.match(p["symbol"])]
    tp_list = [p for p in opts if float(p.get("unrealized_plpc", 0)) >= TP_THRESHOLD]

    # ── Plain text ────────────────────────────────────────────────────────────
    txt = ["CARDONA BOT — DAILY JOURNAL",
           f"Date: {today}  |  Generated: {ts}",
           "=" * 62, ""]

    txt += ["ACCOUNT"]
    txt += [f"  Equity : ${float(equity):>12,.2f}" if equity else "  Equity : N/A"]
    txt += [f"  Cash   : ${float(cash):>12,.2f}"   if cash   else "  Cash   : N/A"]
    txt += [""]

    txt += [f"OPEN OPTIONS POSITIONS ({len(opts)})"]
    if opts:
        for pos in opts:
            sym    = pos["symbol"]
            pct    = float(pos.get("unrealized_plpc", 0)) * 100
            pl_d   = float(pos.get("unrealized_pl", 0))
            entry  = float(pos.get("avg_entry_price", 0))
            cur    = float(pos.get("current_price", 0))
            parsed = _parse_occ(sym)
            tp_tag = "  *** TAKE PROFIT ***" if pct >= TP_THRESHOLD * 100 else ""
            txt   += ["", f"  {sym}{tp_tag}"]
            if parsed:
                txt += [f"  {parsed['underlying']} ${parsed['strike']:.0f} "
                        f"{parsed['type']}  exp {parsed['expiration']}"]
            txt += [f"  Entry ${entry:.2f} → Current ${cur:.2f}  |  P&L: {pct:+.1f}%  (${pl_d:+.0f})"]
    else:
        txt += ["  No open options positions"]

    txt += ["", "TODAY'S JOURNAL ENTRIES"]
    if journal:
        for entry in journal:
            txt += [f"  {json.dumps(entry, indent=2)}"]
    else:
        txt += ["  No entries recorded today"]

    txt += ["", "RECENT LESSONS"]
    txt += [f"  • {ln}" for ln in lessons] if lessons else ["  No lessons yet"]
    txt += ["", "─" * 62, "Cardona Strategy Bot — Paper Trading"]

    text_body = "\n".join(txt)

    # ── HTML ──────────────────────────────────────────────────────────────────
    equity_str = f"${float(equity):,.2f}" if equity else "N/A"
    cash_str   = f"${float(cash):,.2f}"   if cash   else "N/A"

    pos_rows = ""
    for pos in opts:
        sym    = pos["symbol"]
        pct    = float(pos.get("unrealized_plpc", 0)) * 100
        pl_d   = float(pos.get("unrealized_pl", 0))
        entry  = float(pos.get("avg_entry_price", 0))
        cur    = float(pos.get("current_price", 0))
        parsed = _parse_occ(sym)
        desc   = (f"{parsed['underlying']} ${parsed['strike']:.0f} "
                  f"{parsed['type']} exp {parsed['expiration']}"
                  if parsed else sym)
        color  = _pct_color(pct)
        tp_tag = (' <span style="color:#00dd55;font-weight:bold">★ TAKE PROFIT</span>'
                  if pct >= TP_THRESHOLD * 100 else "")
        pos_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a">{sym}{tp_tag}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;color:#888">{desc}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a">${entry:.2f} → ${cur:.2f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #2a2a2a;color:{color};font-weight:bold">{pct:+.1f}% (${pl_d:+.0f})</td>
        </tr>"""

    if not opts:
        pos_rows = ('<tr><td colspan="4" style="padding:12px;color:#555;text-align:center">'
                    "No open options positions</td></tr>")

    journal_html = ""
    for entry in journal:
        journal_html += (f'<pre style="background:#1a1a1a;color:#ccc;padding:12px;'
                         f'border-radius:4px;overflow-x:auto;font-size:13px">'
                         f'{json.dumps(entry, indent=2)}</pre>')
    if not journal_html:
        journal_html = '<p style="color:#555">No journal entries for today.</p>'

    lessons_html = ""
    if lessons:
        items = "".join(f'<li style="margin:4px 0">{ln}</li>' for ln in lessons)
        lessons_html = f'<ul style="color:#bbb;padding-left:20px">{items}</ul>'
    else:
        lessons_html = '<p style="color:#555">No lessons recorded yet.</p>'

    tp_banner = ""
    if tp_list:
        syms = ", ".join(p["symbol"] for p in tp_list)
        tp_banner = (f'<div style="background:#003311;border:1px solid #00dd55;'
                     f'border-radius:4px;padding:12px 16px;margin-bottom:20px;color:#00dd55;'
                     f'font-weight:bold">★ TAKE PROFIT — {len(tp_list)} position(s) at 90%+: {syms}</div>')

    html_body = f"""<!DOCTYPE html>
<html>
<body style="background:#0d0d0d;color:#e0e0e0;font-family:monospace,monospace;padding:24px;margin:0">
<div style="max-width:720px;margin:0 auto">

  <h2 style="color:#00aaff;border-bottom:2px solid #1a1a1a;padding-bottom:10px;margin-top:0">
    Cardona Strategy Bot — Daily Journal
  </h2>
  <p style="color:#555;margin-top:-8px;font-size:13px">{today} &nbsp;|&nbsp; {ts}</p>

  {tp_banner}

  <h3 style="color:#888;font-size:14px;text-transform:uppercase;letter-spacing:1px">Account</h3>
  <table style="border-collapse:collapse;margin-bottom:24px">
    <tr>
      <td style="padding:4px 20px 4px 0;color:#555">Equity</td>
      <td style="padding:4px 20px 4px 0;color:#e0e0e0;font-weight:bold">{equity_str}</td>
      <td style="padding:4px 20px 4px 0;color:#555">Cash</td>
      <td style="padding:4px 0;color:#e0e0e0">{cash_str}</td>
    </tr>
  </table>

  <h3 style="color:#888;font-size:14px;text-transform:uppercase;letter-spacing:1px">
    Open Options Positions ({len(opts)})
  </h3>
  <table style="width:100%;border-collapse:collapse;background:#111;border-radius:6px;
                overflow:hidden;margin-bottom:24px">
    <thead>
      <tr style="color:#555;font-size:11px;text-transform:uppercase;background:#0a0a0a">
        <th style="padding:10px 12px;text-align:left">Symbol</th>
        <th style="padding:10px 12px;text-align:left">Contract</th>
        <th style="padding:10px 12px;text-align:left">Entry → Current</th>
        <th style="padding:10px 12px;text-align:left">P&amp;L</th>
      </tr>
    </thead>
    <tbody>{pos_rows}</tbody>
  </table>

  <h3 style="color:#888;font-size:14px;text-transform:uppercase;letter-spacing:1px">
    Today's Journal
  </h3>
  <div style="margin-bottom:24px">{journal_html}</div>

  <h3 style="color:#888;font-size:14px;text-transform:uppercase;letter-spacing:1px">
    Recent Lessons
  </h3>
  <div style="margin-bottom:24px">{lessons_html}</div>

  <hr style="border:none;border-top:1px solid #1a1a1a;margin-top:32px">
  <p style="color:#333;font-size:11px;margin-bottom:0">
    Cardona Strategy Bot &nbsp;·&nbsp; Paper Trading Account &nbsp;·&nbsp; {today}
  </p>

</div>
</body>
</html>"""

    return text_body, html_body


# ── SendGrid delivery ─────────────────────────────────────────────────────────

def send_email(subject: str, text_body: str, html_body: str) -> bool:
    api_key  = os.environ.get("SENDGRID_API_KEY")
    to_email = os.environ.get("NOTIFY_EMAIL")

    if not api_key:
        print("ERROR: SENDGRID_API_KEY not set in .env")
        return False
    if not to_email:
        print("ERROR: NOTIFY_EMAIL not set in .env")
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from":    {"email": to_email, "name": "Cardona Strategy Bot"},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html",  "value": html_body},
        ],
    }
    hdrs = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        r = requests.post(SENDGRID_URL, json=payload, headers=hdrs, timeout=20)
    except requests.RequestException as e:
        print(f"ERROR sending email: {e}")
        return False

    if r.status_code == 202:
        print(f"Email sent → {to_email}")
        return True

    print(f"SendGrid error {r.status_code}: {r.text[:200]}")
    return False


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    load_env()

    today     = date.today().isoformat()
    test_mode = "--test" in sys.argv or "--dry-run" in sys.argv

    print(f"Cardona Notify — {today}")

    positions = get_positions()
    account   = get_account()
    journal   = read_journal(today)
    lessons   = read_recent_lessons()

    text_body, html_body = build_email(today, positions, account, journal, lessons)

    opts   = [p for p in positions if OPTION_RE.match(p["symbol"])]
    tp_cnt = sum(1 for p in opts if float(p.get("unrealized_plpc", 0)) >= TP_THRESHOLD)
    tp_tag = f" | {tp_cnt} TAKE PROFIT" if tp_cnt else ""
    subject = f"Cardona EOD {today} — {len(opts)} position(s) open{tp_tag}"

    if test_mode:
        print(f"Subject: {subject}")
        print("[--test mode: email not sent]\n")
        print(text_body)
        return

    send_email(subject, text_body, html_body)


if __name__ == "__main__":
    main()
