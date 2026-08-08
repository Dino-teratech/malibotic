#!/usr/bin/env python3
"""
Earnings alert bot  (v3)
------------------------
Za svaki ticker s watchliste odreduje SLJEDECU objavu kvartalnih rezultata:

  1. POTVRDJEN datum  - ako ga Finnhub vec ima u kalendaru
  2. PROCJENA         - ako nema, racuna se iz zadnje objave + ~91 dan
                        (kompanije objavljuju vrlo pravilno)

Telegram alarm salje se SAMO za potvrdjene datume. Procjene se prikazuju
u widgetu i na dashboardu, ali ne okidaju obavijesti - da te bot ne budi
zbog nagadanja.

Stanje se cuva u state.json da se ista obavijest ne posalje dvaput.
"""

import datetime as dt
import json
import os
import pathlib
import sys
import time
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

# Alarm za "novi datum" / "pomak" salje se samo unutar ovog raspona
ANNOUNCE_WITHIN_DAYS = 14

# Koliko unatrag gledamo povijest objava (treba > 1 godina za dobru procjenu)
LOOKBACK_DAYS = 400
# Koliko unaprijed trazimo potvrdjene datume
LOOKAHEAD_DAYS = 120
# Finnhub free tier dopusta ~1 mjesec po pozivu
WINDOW_DAYS = 30
# Prosjecni razmak izmedu objava
QUARTER_DAYS = 91
# Pauza izmedu poziva (limit je 60/min)
CALL_DELAY = 0.25

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


def fetch_symbol(
    token: str, symbol: str, date_from: dt.date, date_to: dt.date
) -> list[dict]:
    """Kalendar za JEDAN ticker. Kljucno: upit bez simbola vraca cijelu
    burzu i Finnhub ga odsijece na 1500 redaka, pa nasi tickeri ispadnu."""
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "token": token,
        }
    )
    req = urllib.request.Request(
        f"{FINNHUB_URL}?{params}", headers={"User-Agent": "earnings-bot/4.0"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("earningsCalendar") or []


def fetch_all(token: str, watchlist: list[str], today: dt.date) -> list[dict]:
    """Jedan poziv po tickeru, siroki raspon (povijest + buducnost)."""
    rows: list[dict] = []
    start = today - dt.timedelta(days=LOOKBACK_DAYS)
    end = today + dt.timedelta(days=LOOKAHEAD_DAYS)
    empty: list[str] = []

    for sym in watchlist:
        try:
            chunk = fetch_symbol(token, sym, start, end)
            rows.extend(chunk)
            if chunk:
                dates = sorted(r["date"] for r in chunk if r.get("date"))
                print(f"[INFO] {sym:6} {len(chunk)} zapisa "
                      f"({dates[0]} .. {dates[-1]})")
            else:
                empty.append(sym)
                print(f"[INFO] {sym:6} 0 zapisa")
        except Exception as exc:
            empty.append(sym)
            print(f"[WARN] {sym:6} nije uspjelo: {exc}")
        time.sleep(CALL_DELAY)

    print(f"[INFO] ukupno {len(rows)} zapisa za {len(watchlist)} tickera")
    if empty:
        print(f"[WARN] Finnhub nema nista za: {', '.join(empty)}")
    return rows


# ------------------------------------------------------------ resolving ----


def next_weekday(d: dt.date) -> dt.date:
    """Objave su radnim danima - pomakni vikend na ponedjeljak."""
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def resolve_next(
    rows: list[dict], watchlist: list[str], today: dt.date
) -> dict[str, dict]:
    """Za svaki ticker vrati sljedecu objavu: potvrdjenu ili procijenjenu."""
    wanted = set(watchlist)
    by_sym: dict[str, list[dict]] = {}
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym in wanted and row.get("date"):
            by_sym.setdefault(sym, []).append(row)

    result: dict[str, dict] = {}
    for sym, items in by_sym.items():
        items.sort(key=lambda r: r["date"])
        future = [r for r in items if dt.date.fromisoformat(r["date"]) >= today]
        past = [r for r in items if dt.date.fromisoformat(r["date"]) < today]

        if future:
            entry = dict(future[0])
            entry["estimated"] = False
            result[sym] = entry
            continue

        if past:
            last = past[-1]
            last_date = dt.date.fromisoformat(last["date"])
            # kotrljaj po kvartalima dok procjena ne padne u buducnost
            est = last_date + dt.timedelta(days=QUARTER_DAYS)
            while est < today:
                est += dt.timedelta(days=QUARTER_DAYS)
            est = next_weekday(est)
            result[sym] = {
                "symbol": sym,
                "date": est.isoformat(),
                "hour": last.get("hour") or "",
                "quarter": None,
                "year": None,
                "epsEstimate": None,
                "revenueEstimate": None,
                "estimated": True,
                "basedOn": last["date"],
            }

    missing = sorted(wanted - set(result))
    if missing:
        print(f"[WARN] bez ijednog zapisa (ni povijesnog): {', '.join(missing)}")

    confirmed = sum(1 for r in result.values() if not r["estimated"])
    print(f"[INFO] rijeseno {len(result)}/{len(wanted)} "
          f"({confirmed} potvrdjeno, {len(result) - confirmed} procjena)")
    return result


# ------------------------------------------------------------- alerting ----


def fmt_estimate(row: dict) -> str:
    bits = []
    eps = row.get("epsEstimate")
    rev = row.get("revenueEstimate")
    if eps is not None:
        bits.append(f"EPS est. {eps}")
    if rev:
        bits.append(f"prihod est. {rev / 1e9:.2f} B$")
    return " \u00b7 ".join(bits)


def build_alerts(
    calendar: dict[str, dict], state: dict, today: dt.date
) -> tuple[list[str], dict, set[str]]:
    lines: list[str] = []
    new_state = dict(state)
    moved: set[str] = set()

    for sym in sorted(calendar):
        row = calendar[sym]

        # procjene nikad ne okidaju alarm
        if row.get("estimated"):
            continue

        date_str = row["date"]
        edate = dt.date.fromisoformat(date_str)
        days = (edate - today).days
        if days < 0:
            continue

        hour = (row.get("hour") or "").lower()
        hour_txt = HOUR_LABELS.get(hour, "vrijeme nepoznato")
        quarter, year = row.get("quarter"), row.get("year")
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
                    f"\U0001f195 <b>{sym}</b> \u2014 objava {date_str} (za {days} d)\n"
                    f"   {q_txt}, {hour_txt}{est_txt}"
                )
                announced_now = True
            sent = {"new"}
        elif prev_date != date_str:
            if near:
                lines.append(
                    f"\U0001f504 <b>{sym}</b> \u2014 datum POMAKNUT: {prev_date} "
                    f"\u2192 <b>{date_str}</b> (za {days} d)\n   {hour_txt}{est_txt}"
                )
                announced_now = True
            sent = {"new"}
            moved.add(sym)

        for d in REMIND_DAYS:
            key = f"d{d}"
            if days == d and key not in sent:
                if announced_now:
                    sent.add(key)
                    continue
                when = "<b>DANAS</b>" if d == 0 else f"za <b>{d}</b> d"
                lines.append(
                    f"\u23f0 <b>{sym}</b> \u2014 earnings {when} ({date_str})\n"
                    f"   {q_txt}, {hour_txt}{est_txt}"
                )
                sent.add(key)

        new_state[sym] = {"date": date_str, "hour": hour, "sent": sorted(sent)}

    return lines, new_state, moved


def write_feed(calendar: dict[str, dict], moved: set[str], today: dt.date) -> None:
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
                "hourLabel": {"bmo": "BMO", "amc": "AMC", "dmh": "MID"}.get(hour, "\u2014"),
                "quarter": row.get("quarter"),
                "year": row.get("year"),
                "epsEstimate": row.get("epsEstimate"),
                "revenueEstimate": row.get("revenueEstimate"),
                "estimated": bool(row.get("estimated")),
                "basedOn": row.get("basedOn"),
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
                "confirmed": sum(1 for i in items if not i["estimated"]),
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

    rows = fetch_all(finnhub_token, watchlist, today)
    calendar = resolve_next(rows, watchlist, today)

    state = load_state()
    alerts, new_state, moved = build_alerts(calendar, state, today)

    if not alerts:
        print("[INFO] Nema novih obavijesti.")
    else:
        header = f"\U0001f4ca <b>Earnings alarm</b> \u2014 {today.strftime('%d.%m.%Y')}\n"
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
