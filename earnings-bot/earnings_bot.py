#!/usr/bin/env python3
"""
Earnings alert bot  (v10)
-------------------------
Za svaki ticker s watchliste odreduje sljedecu objavu kvartalnih rezultata
i javlja na Telegram. Nikad ne izmislja datum: ako ga izvor nema, tako i pise.

Tri stanja termina:
  SLUZBENO    - Finnhub ima datum I oznaku BMO/AMC (kompanija ga je potvrdila)
  NESLUZBENO  - Finnhub ima datum, ali bez oznake (nitko ga nije potvrdio)
  NEMA        - izvor nema buduci termin -> "termin jos nije objavljen"

Telegram, kroz zivot jednog izvjestaja:
  NOVO        novi termin se pojavio
  POMAK       datum se promijenio
  POTVRDJEN   iz neslozbenog presao u sluzbeni
  DANAS       na dan objave
  BEAT/MISS   rezultat, dan nakon objave
  PRACENJE    kretanje cijene 5 dana nakon objave
"""

import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- config ----

ROOT = pathlib.Path(__file__).resolve().parent
WATCHLIST_FILE = ROOT / "watchlist.txt"
STATE_FILE = ROOT / "state.json"
DOCS_DIR = ROOT / "docs"
FEED_FILE = DOCS_DIR / "earnings.json"

# Podsjetnik samo na dan objave
REMIND_DAYS = [0]

# Obavijest o novom / pomaknutom / potvrdjenom terminu samo ako je blize
ANNOUNCE_WITHIN_DAYS = 14

# Koliko dana znacka POMAK ostaje vidljiva
MOVED_BADGE_DAYS = 5

# Sazetak rezultata samo za objave od jucer i danas
RESULT_WITHIN_DAYS = 2

# Koliko radnih dana pratimo cijenu nakon objave
TRACK_DAYS = 5

# Pracenje cijene salje se samo nakon zatvaranja US burze (UTC sat)
TRACK_AFTER_UTC_HOUR = 21

LOOKBACK_DAYS = 400
LOOKAHEAD_DAYS = 200
QUARTER_DAYS = 91
CALL_DELAY = 0.25

HEALTH_MIN_RESOLVED = 25
META_KEY = "__meta"

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
QUOTE_API = "https://finnhub.io/api/v1/quote"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
QUOTE_URL = "https://finance.yahoo.com/quote/{symbol}"

TZ_ET = ZoneInfo("America/New_York")
TZ_LOCAL = ZoneInfo("Europe/Zagreb")

HOUR_WINDOWS_ET = {
    "bmo": (dt.time(6, 30), dt.time(9, 15)),
    "amc": (dt.time(16, 5), dt.time(16, 45)),
    "dmh": (dt.time(9, 30), dt.time(16, 0)),
}

STATUS_OFFICIAL = "official"
STATUS_UNOFFICIAL = "unofficial"
STATUS_NONE = "none"

STATUS_TXT = {
    STATUS_OFFICIAL: "SLUZBENO",
    STATUS_UNOFFICIAL: "NESLUZBENO",
}


def env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"[FATAL] Nedostaje environment varijabla: {name}")
    return val


# ------------------------------------------------------------------ io -----


def load_watchlist() -> list[str]:
    if not WATCHLIST_FILE.exists():
        sys.exit(f"[FATAL] Ne postoji {WATCHLIST_FILE}")
    out = []
    for line in WATCHLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().upper()
        if line:
            out.append(line)
    if not out:
        sys.exit("[FATAL] watchlist.txt je prazan")
    return sorted(set(out))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[WARN] state.json neispravan, krecem od nule")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------- fetching ----


def api_get(url: str, params: dict) -> dict | list:
    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "earnings-bot/10.0"},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_symbol(token: str, symbol: str, a: dt.date, b: dt.date) -> list[dict]:
    payload = api_get(
        FINNHUB_URL,
        {"symbol": symbol, "from": a.isoformat(), "to": b.isoformat(), "token": token},
    )
    return payload.get("earningsCalendar") or []


def fetch_quote(token: str, symbol: str) -> dict:
    """c = trenutna cijena, pc = jucerasnje zatvaranje, dp = promjena danas %"""
    return api_get(QUOTE_API, {"symbol": symbol, "token": token})


def fetch_all(token: str, watchlist: list[str], today: dt.date) -> list[dict]:
    rows: list[dict] = []
    a = today - dt.timedelta(days=LOOKBACK_DAYS)
    b = today + dt.timedelta(days=LOOKAHEAD_DAYS)
    empty = []
    for sym in watchlist:
        try:
            chunk = fetch_symbol(token, sym, a, b)
            rows.extend(chunk)
            if chunk:
                ds = sorted(r["date"] for r in chunk if r.get("date"))
                print(f"[INFO] {sym:6} {len(chunk)} zapisa ({ds[0]} .. {ds[-1]})")
            else:
                empty.append(sym)
                print(f"[INFO] {sym:6} 0 zapisa")
        except Exception as exc:
            empty.append(sym)
            print(f"[WARN] {sym:6} nije uspjelo: {exc}")
        time.sleep(CALL_DELAY)
    if empty:
        print(f"[WARN] Finnhub nema nista za: {', '.join(empty)}")
    return rows


# ------------------------------------------------------------- helpers -----


def is_official(row: dict) -> bool:
    """Kompanija je potvrdila termin -> Finnhub ima oznaku bmo/amc/dmh."""
    return (row.get("hour") or "").lower() in HOUR_WINDOWS_ET


def is_reported(row: dict) -> bool:
    """Rezultat je vec objavljen -> popunjen 'actual'."""
    return row.get("epsActual") is not None or row.get("revenueActual") is not None


def local_window(date_str: str, hour: str) -> str | None:
    w = HOUR_WINDOWS_ET.get(hour)
    if not w:
        return None
    day = dt.date.fromisoformat(date_str)
    return "-".join(
        dt.datetime.combine(day, t, tzinfo=TZ_ET)
        .astimezone(TZ_LOCAL)
        .strftime("%H:%M")
        for t in w
    )


def fmt_money(v) -> str:
    if v is None:
        return "\u2014"
    return f"{v / 1e9:.2f} B$" if abs(v) >= 1e9 else f"{v / 1e6:.0f} M$"


def pct(actual, estimate) -> float | None:
    if actual is None or estimate in (None, 0):
        return None
    return (actual - estimate) / abs(estimate) * 100.0


# ------------------------------------------------------------ resolving ----


def resolve_next(rows: list[dict], watchlist: list[str], today: dt.date) -> dict:
    """Sljedeci termin po tickeru. Nikad ne racuna datum sam."""
    wanted = set(watchlist)
    by_sym: dict[str, list[dict]] = {}
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym in wanted and row.get("date"):
            by_sym.setdefault(sym, []).append(row)

    result: dict[str, dict] = {}
    for sym in sorted(wanted):
        items = sorted(by_sym.get(sym, []), key=lambda r: r["date"])
        future = [
            r
            for r in items
            if dt.date.fromisoformat(r["date"]) >= today and not is_reported(r)
        ]
        if future:
            entry = dict(future[0])
            entry["status"] = (
                STATUS_OFFICIAL if is_official(entry) else STATUS_UNOFFICIAL
            )
        else:
            entry = {"symbol": sym, "date": None, "status": STATUS_NONE}
        result[sym] = entry

    off = sum(1 for r in result.values() if r["status"] == STATUS_OFFICIAL)
    unoff = sum(1 for r in result.values() if r["status"] == STATUS_UNOFFICIAL)
    none = sum(1 for r in result.values() if r["status"] == STATUS_NONE)
    print(f"[INFO] {off} sluzbeno, {unoff} nesluzbeno, {none} bez termina")
    if none:
        names = sorted(k for k, r in result.items() if r["status"] == STATUS_NONE)
        print(f"[INFO] termin jos nije objavljen: {', '.join(names)}")
    return result


def find_results(rows: list[dict], watchlist: list[str], today: dt.date) -> dict:
    wanted = set(watchlist)
    best: dict[str, dict] = {}
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym not in wanted or not row.get("date") or not is_reported(row):
            continue
        age = (today - dt.date.fromisoformat(row["date"])).days
        if not 0 <= age <= RESULT_WITHIN_DAYS:
            continue
        if sym not in best or row["date"] > best[sym]["date"]:
            best[sym] = row
    if best:
        print(f"[INFO] svjeze objave: {', '.join(sorted(best))}")
    return best


# ------------------------------------------------------------- alerting ----


def build_alerts(calendar: dict, state: dict, today: dt.date) -> tuple[list[str], dict]:
    lines: list[str] = []
    st = dict(state)

    for sym in sorted(calendar):
        row = calendar[sym]
        prev = dict(st.get(sym, {}))

        if row["status"] == STATUS_NONE:
            prev["date"] = None
            prev["status"] = STATUS_NONE
            prev["sent"] = []
            st[sym] = prev
            continue

        date_str = row["date"]
        days = (dt.date.fromisoformat(date_str) - today).days
        hour = (row.get("hour") or "").lower()
        win = local_window(date_str, hour)
        when = f", oko {win} po nasem" if win else ""
        tag = STATUS_TXT[row["status"]]
        link = QUOTE_URL.format(symbol=sym)
        eps_e = row.get("epsEstimate")
        extra = f"\n   ocekivani EPS {eps_e}" if eps_e is not None else ""
        extra += f'\n   <a href="{link}">prati objavu \u2192</a>'

        prev_date = prev.get("date")
        prev_status = prev.get("status")
        sent = set(prev.get("sent", []))
        moved_at = prev.get("movedAt")
        near = days <= ANNOUNCE_WITHIN_DAYS
        announced = False

        if prev_date is None:
            if near:
                lines.append(
                    f"\U0001f195 <b>{sym}</b> \u2014 termin {date_str}{when}\n"
                    f"   <b>{tag}</b>, za {days} d{extra}"
                )
                announced = True
            sent = {"new"}

        elif prev_date != date_str:
            if near:
                lines.append(
                    f"\U0001f504 <b>{sym}</b> \u2014 POMAK: {prev_date} "
                    f"\u2192 <b>{date_str}</b>{when}\n"
                    f"   <b>{tag}</b>, za {days} d{extra}"
                )
                announced = True
            sent = {"new"}
            moved_at = today.isoformat()

        elif prev_status == STATUS_UNOFFICIAL and row["status"] == STATUS_OFFICIAL:
            if near:
                lines.append(
                    f"\u2705 <b>{sym}</b> \u2014 termin POTVRDJEN: "
                    f"{date_str}{when}\n   <b>SLUZBENO</b>, za {days} d{extra}"
                )
                announced = True

        for d in REMIND_DAYS:
            key = f"d{d}"
            if days == d and key not in sent:
                if announced:
                    sent.add(key)
                    continue
                lines.append(
                    f"\u23f0 <b>{sym}</b> \u2014 izvjestaj <b>DANAS</b>{when}\n"
                    f"   <b>{tag}</b>{extra}"
                )
                sent.add(key)

        entry = {
            "date": date_str,
            "status": row["status"],
            "hour": hour,
            "sent": sorted(sent),
        }
        for keep in ("resultSent", "track"):
            if keep in prev:
                entry[keep] = prev[keep]
        if moved_at:
            entry["movedAt"] = moved_at
        st[sym] = entry

    return lines, st


def build_results(results: dict, state: dict, today: dt.date) -> tuple[list[str], dict]:
    lines: list[str] = []
    st = dict(state)

    for sym in sorted(results):
        row = results[sym]
        date_str = row["date"]
        entry = dict(st.get(sym, {}))
        if entry.get("resultSent") == date_str:
            continue

        eps_a, eps_e = row.get("epsActual"), row.get("epsEstimate")
        rev_a, rev_e = row.get("revenueActual"), row.get("revenueEstimate")
        ep, rp = pct(eps_a, eps_e), pct(rev_a, rev_e)

        if ep is None:
            head, mark = "objavila rezultate", "\U0001f4c4"
        elif ep >= 1:
            head, mark = f"BEAT po EPS-u ({ep:+.1f}%)", "\U0001f7e2"
        elif ep <= -1:
            head, mark = f"MISS po EPS-u ({ep:+.1f}%)", "\U0001f534"
        else:
            head, mark = "u skladu s procjenom", "\u26aa"

        parts = [f"{mark} <b>{sym}</b> \u2014 {head}", f"   objavljeno {date_str}"]
        if eps_a is not None:
            parts.append(
                f"   EPS {eps_a}" + (f" (oc. {eps_e})" if eps_e is not None else "")
            )
        if rev_a is not None:
            tail = f", {rp:+.1f}%" if rp is not None else ""
            parts.append(
                f"   prihod {fmt_money(rev_a)}"
                + (f" (oc. {fmt_money(rev_e)})" if rev_e is not None else "")
                + tail
            )
        parts.append(f'   <a href="{QUOTE_URL.format(symbol=sym)}">reakcija dionice \u2192</a>')
        lines.append("\n".join(parts))

        entry["resultSent"] = date_str
        entry["track"] = {"from": date_str, "base": None, "day": 0, "lastDay": None}
        st[sym] = entry

    return lines, st


def build_tracking(
    state: dict, token: str, today: dt.date, now_utc: dt.datetime
) -> tuple[list[str], dict]:
    """Kretanje cijene 5 radnih dana nakon objave, jednom dnevno."""
    lines: list[str] = []
    st = dict(state)

    if now_utc.hour < TRACK_AFTER_UTC_HOUR or today.weekday() >= 5:
        return lines, st  # samo nakon zatvaranja burze, radnim danom

    for sym in sorted(st):
        if sym == META_KEY:
            continue
        entry = dict(st[sym])
        track = entry.get("track")
        if not track or track.get("lastDay") == today.isoformat():
            continue
        if track["day"] >= TRACK_DAYS:
            entry.pop("track", None)
            st[sym] = entry
            continue

        try:
            q = fetch_quote(token, sym)
        except Exception as exc:
            print(f"[WARN] cijena {sym} nije dohvacena: {exc}")
            continue
        time.sleep(CALL_DELAY)

        cur, prev_close, day_pct = q.get("c"), q.get("pc"), q.get("dp")
        if not cur:
            continue

        if track["base"] is None:
            track["base"] = prev_close or cur
        base = track["base"]
        track["day"] += 1
        track["lastDay"] = today.isoformat()
        cum = (cur - base) / base * 100 if base else 0.0

        arrow = "\U0001f4c8" if cum >= 0 else "\U0001f4c9"
        dtxt = f"{day_pct:+.1f}%" if day_pct is not None else "\u2014"
        lines.append(
            f"{arrow} <b>{sym}</b> \u2014 {track['day']}. dan nakon objave\n"
            f"   danas {dtxt}, ukupno od objave <b>{cum:+.1f}%</b>\n"
            f"   cijena {cur:.2f} $"
        )

        if track["day"] >= TRACK_DAYS:
            entry.pop("track", None)
        else:
            entry["track"] = track
        st[sym] = entry

    return lines, st


def health_warning(resolved: int, total: int, state: dict, today: dt.date):
    st = dict(state)
    if resolved >= HEALTH_MIN_RESOLVED:
        return [], st
    meta = dict(st.get(META_KEY, {}))
    if meta.get("healthWarnedOn") == today.isoformat():
        return [], st
    meta["healthWarnedOn"] = today.isoformat()
    st[META_KEY] = meta
    print(f"[WARN] pokrivenost pala: {resolved}/{total}")
    return [
        f"\u26a0\ufe0f <b>Bot ne dobiva podatke</b>\n"
        f"   Rijeseno samo {resolved} od {total} tickera.\n"
        f"   Provjeri Finnhub kljuc i limit poziva."
    ], st


# ----------------------------------------------------------------- feed ----


def recently_moved(state: dict, sym: str, today: dt.date) -> bool:
    stamp = (state.get(sym) or {}).get("movedAt")
    if not stamp:
        return False
    try:
        return (today - dt.date.fromisoformat(stamp)).days <= MOVED_BADGE_DAYS
    except ValueError:
        return False


def write_feed(calendar: dict, state: dict, today: dt.date) -> None:
    items = []
    for sym in sorted(calendar):
        row = calendar[sym]
        if row["status"] == STATUS_NONE:
            items.append(
                {
                    "symbol": sym,
                    "status": STATUS_NONE,
                    "date": None,
                    "days": None,
                    "hourLabel": None,
                    "localWindow": None,
                    "moved": False,
                }
            )
            continue
        days = (dt.date.fromisoformat(row["date"]) - today).days
        if days < 0:
            continue
        hour = (row.get("hour") or "").lower()
        items.append(
            {
                "symbol": sym,
                "status": row["status"],
                "date": row["date"],
                "days": days,
                "hour": hour,
                "hourLabel": {"bmo": "BMO", "amc": "AMC", "dmh": "MID"}.get(hour),
                "localWindow": local_window(row["date"], hour),
                "quarter": row.get("quarter"),
                "year": row.get("year"),
                "epsEstimate": row.get("epsEstimate"),
                "revenueEstimate": row.get("revenueEstimate"),
                "moved": recently_moved(state, sym, today),
            }
        )

    # bez termina idu na dno
    items.sort(key=lambda i: (i["days"] is None, i["days"] or 0, i["symbol"]))

    DOCS_DIR.mkdir(exist_ok=True)
    FEED_FILE.write_text(
        json.dumps(
            {
                "generated": dt.datetime.now(dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "count": len(items),
                "official": sum(1 for i in items if i["status"] == STATUS_OFFICIAL),
                "items": items,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[INFO] feed zapisan: {len(items)} stavki")


# --------------------------------------------------------------- telegram --


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

    now_utc = dt.datetime.now(dt.timezone.utc)
    today = now_utc.date()
    watchlist = load_watchlist()
    print(f"[INFO] {today} | pratim {len(watchlist)} tickera")

    rows = fetch_all(finnhub_token, watchlist, today)
    calendar = resolve_next(rows, watchlist, today)
    results = find_results(rows, watchlist, today)

    state = load_state()
    alerts, state = build_alerts(calendar, state, today)
    result_lines, state = build_results(results, state, today)
    track_lines, state = build_tracking(state, finnhub_token, today, now_utc)
    resolved = sum(1 for r in calendar.values() if r["status"] != STATUS_NONE)
    health_lines, state = health_warning(resolved, len(watchlist), state, today)

    message_parts = result_lines + track_lines + alerts + health_lines

    if not message_parts:
        print("[INFO] Nema novih obavijesti.")
    else:
        header = f"\U0001f4ca <b>Earnings</b> \u2014 {today.strftime('%d.%m.%Y')}\n"
        message = header + "\n\n".join(message_parts)
        if dry_run:
            print("--- DRY RUN ---")
            print(message)
        else:
            for part in chunk(message):
                send_telegram(tg_token, tg_chat, part)
            print(f"[INFO] Poslano {len(message_parts)} obavijesti.")

    write_feed(calendar, state, today)

    if not dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
