#!/usr/bin/env python3
"""
Earnings alert bot
------------------
Provjerava Finnhub earnings kalendar za tickere iz watchlist.txt
i salje Telegram poruku kad:
  * se pojavi novi (do sad nepoznat) datum objave
  * se vec poznati datum PROMIJENI (cesto se dogadja!)
  * je do objave ostalo 7 / 3 / 1 / 0 dana

Stanje se cuva u state.json da se ista obavijest ne salje dvaput.
"""

import datetime as dt
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- config ----

ROOT = pathlib.Path(__file__).resolve().parent
WATCHLIST_FILE = ROOT / "watchlist.txt"
STATE_FILE = ROOT / "state.json"
DOCS_DIR = ROOT / "docs"
FEED_FILE = DOCS_DIR / "earnings.json"

# Na koliko dana prije objave zelis podsjetnik
REMIND_DAYS = [3, 1, 0]

# Finnhub free tier dopusta ~1 mjesec po pozivu, pa horizont slazemo
# iz vise uzastopnih prozora (4 x 30 = 120 dana, 4 poziva po pokretanju).
LOOKAHEAD_DAYS = 120
WINDOW_DAYS = 30

# Telegram alarm za "novi datum" / "pomak" salje se samo ako je objava
# blize od ovoliko dana. Dalje od toga se tiho zapise u state i feed,
# da te bot ne zatrpa kad ugleda cijeli kvartal unaprijed.
ANNOUNCE_WITHIN_DAYS = 14

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

HOUR_LABELS = {
    "bmo": "prije otvaranja burze (~13:00 po nasem)",
    "amc": "nakon zatvaranja burze (~22:00 po nasem)",
    "dmh": "tijekom trgovanja",
}


def env(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        sys.exit(f"[FATAL] Nedostaje environment varijabla: {name}")
    return val


# ------------------------------------------------------------------ io -----


def load_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        sys.exit(f"[FATAL] Ne postoji {WATCHLIST_FILE}")
    tickers = []
    for line in WATCHLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().upper()
        if line:
            tickers.append(line)
    if not tickers:
        sys.exit("[FATAL] watchlist.txt je prazan")
    return sorted(set(tickers))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[WARN] state.json je neispravan, krecem od nule")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------- fetching ----


def fetch_window(token: str, date_from: dt.date, date_to: dt.date) -> list[dict]:
    """Jedan poziv - kalendar za zadani raspon (max ~30 dana na free tieru)."""
    params = urllib.parse.urlencode(
        {"from": date_from.isoformat(), "to": date_to.isoformat(), "token": token}
    )
    req = urllib.request.Request(
        f"{FINNHUB_URL}?{params}", headers={"User-Agent": "earnings-bot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("earningsCalendar") or []


def fetch_calendar(token: str, today: dt.date) -> list[dict]:
    """Poslozi horizont od LOOKAHEAD_DAYS iz vise uzastopnih prozora."""
    rows: list[dict] = []
    for offset in range(0, LOOKAHEAD_DAYS, WINDOW_DAYS):
        start = today + dt.timedelta(days=offset)
        end = today + dt.timedelta(days=min(offset + WINDOW_DAYS, LOOKAHEAD_DAYS) - 1)
        try:
            chunk = fetch_window(token, start, end)
            print(f"[INFO] {start} - {end}: {len(chunk)} zapisa")
            rows.extend(chunk)
        except Exception as exc:  # prozor moze pasti, ostali i dalje vrijede
            print(f"[WARN] prozor {start} - {end} nije uspio: {exc}")
    return rows


def index_by_symbol(rows: list[dict], watchlist: list[str]) -> dict[str, dict]:
    """Za svaki ticker uzmi NAJRANIJI nadolazeci zapis."""
    wanted = set(watchlist)
    best: dict[str, dict] = {}
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym not in wanted or not row.get("date"):
            continue
        if sym not in best or row["date"] < best[sym]["date"]:
            best[sym] = row
    return best


# ------------------------------------------------------------- alerting ----


def fmt_estimate(row: dict) -> str:
    bits = []
    eps = row.get("epsEstimate")
    rev = row.get("revenueEstimate")
    if eps is not None:
        bits.append(f"EPS est. {eps}")
    if rev:
        bits.append(f"prihod est. {rev / 1e9:.2f} B$")
    return " · ".join(bits)


def build_alerts(
    calendar: dict[str, dict], state: dict, today: dt.date
) -> tuple[list[str], dict, set[str]]:
    lines: list[str] = []
    new_state = dict(state)
    moved: set[str] = set()

    for sym in sorted(calendar):
        row = calendar[sym]
        date_str = row["date"]
        edate = dt.date.fromisoformat(date_str)
        days = (edate - today).days
        if days < 0:
            continue

        hour = (row.get("hour") or "").lower()
        hour_txt = HOUR_LABELS.get(hour, "vrijeme nepoznato")
        quarter = row.get("quarter")
        year = row.get("year")
        # Finnhub vraca FISKALNI kvartal - kod NVDA je npr. FQ2 2027 usred
        # kalendarske 2026. Zato eksplicitno pise "fisk.".
        q_txt = f"FQ{quarter} {year} (fisk.)" if quarter and year else ""
        est = fmt_estimate(row)
        est_txt = f"\n   <i>{est}</i>" if est else ""

        prev = new_state.get(sym, {})
        prev_date = prev.get("date")
        sent = set(prev.get("sent", []))
        announced_now = False
        near = days <= ANNOUNCE_WITHIN_DAYS

        if prev_date is None:
            if near:
                lines.append(
                    f"🆕 <b>{sym}</b> — objava {date_str} (za {days} d)\n"
                    f"   {q_txt}, {hour_txt}{est_txt}"
                )
                announced_now = True
            sent = {"new"}
        elif prev_date != date_str:
            if near:
                lines.append(
                    f"🔄 <b>{sym}</b> — datum POMAKNUT: {prev_date} → <b>{date_str}</b> "
                    f"(za {days} d)\n   {hour_txt}{est_txt}"
                )
                announced_now = True
            sent = {"new"}
            moved.add(sym)

        for d in REMIND_DAYS:
            key = f"d{d}"
            if days == d and key not in sent:
                if announced_now:
                    # vec smo ga upravo najavili s brojem dana - ne dupliciraj
                    sent.add(key)
                    continue
                when = "<b>DANAS</b>" if d == 0 else f"za <b>{d}</b> d"
                lines.append(
                    f"⏰ <b>{sym}</b> — earnings {when} ({date_str})\n"
                    f"   {q_txt}, {hour_txt}{est_txt}"
                )
                sent.add(key)

        new_state[sym] = {"date": date_str, "hour": hour, "sent": sorted(sent)}

    # ocisti tickere kojih vise nema na watchlisti
    for sym in list(new_state):
        if sym not in calendar and sym not in state:
            new_state.pop(sym, None)

    return lines, new_state, moved


def write_feed(calendar: dict[str, dict], moved: set[str], today: dt.date) -> None:
    """Objavi JSON koji cita iPhone widget (i web dashboard)."""
    items = []
    for sym in sorted(calendar):
        row = calendar[sym]
        edate = dt.date.fromisoformat(row["date"])
        days = (edate - today).days
        if days < 0:
            continue
        hour = (row.get("hour") or "").lower()
        items.append(
            {
                "symbol": sym,
                "date": row["date"],
                "days": days,
                "hour": hour,
                "hourLabel": {"bmo": "BMO", "amc": "AMC", "dmh": "MID"}.get(hour, "—"),
                "quarter": row.get("quarter"),
                "year": row.get("year"),
                "epsEstimate": row.get("epsEstimate"),
                "revenueEstimate": row.get("revenueEstimate"),
                "moved": sym in moved,
            }
        )
    items.sort(key=lambda i: (i["days"], i["symbol"]))

    DOCS_DIR.mkdir(exist_ok=True)
    FEED_FILE.write_text(
        json.dumps(
            {
                "generated": dt.datetime.now(dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "count": len(items),
                "items": items,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[INFO] feed zapisan: {len(items)} stavki")


def send_telegram(token: str, chat_id: str, text: str) -> None:
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(TELEGRAM_URL.format(token=token), data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def chunk(text: str, limit: int = 3800):
    """Telegram limit je 4096 znakova."""
    buf = ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > limit and buf:
            yield buf
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        yield buf


# ----------------------------------------------------------------- main ----


def main() -> None:
    finnhub_token = env("FINNHUB_TOKEN")
    tg_token = env("TELEGRAM_BOT_TOKEN")
    tg_chat = env("TELEGRAM_CHAT_ID")
    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}

    today = dt.date.today()
    watchlist = load_watchlist()
    print(f"[INFO] {today} | pratim {len(watchlist)} tickera")

    rows = fetch_calendar(finnhub_token, today)
    print(f"[INFO] Finnhub vratio ukupno {len(rows)} zapisa")

    calendar = index_by_symbol(rows, watchlist)
    print(f"[INFO] pogodaka na watchlisti: {len(calendar)}")

    state = load_state()
    alerts, new_state, moved = build_alerts(calendar, state, today)

    if not alerts:
        print("[INFO] Nema novih obavijesti.")
    else:
        header = f"📊 <b>Earnings alarm</b> — {today.strftime('%d.%m.%Y')}\n"
        message = header + "\n\n".join(alerts)
        if dry_run:
            print("--- DRY RUN ---")
            print(message)
        else:
            for part in chunk(message):
                send_telegram(tg_token, tg_chat, part)
            print(f"[INFO] Poslano {len(alerts)} obavijesti.")

    write_feed(calendar, moved, today)

    if not dry_run:
        save_state(new_state)


if __name__ == "__main__":
    main()
