# Earnings alert bot

Provjerava 5× dnevno (radnim danima) kada velike kompanije objavljuju kvartalne
rezultate i šalje ti push poruku na Telegram. Vrti se besplatno na GitHub Actions —
ništa ne mora biti upaljeno kod tebe.

## Što šalje

| Okidač | Primjer |
|---|---|
| Novi datum objave | 🆕 NVDA — objava 2026-08-26, nakon zatvaranja burze |
| Datum pomaknut | 🔄 AAPL — datum POMAKNUT: 13.08 → 20.08 |
| Odbrojavanje | ⏰ MSFT — earnings za 3 d / za 1 d / DANAS |

Uz svaku poruku idu i očekivani EPS i prihod, te je li objava **prije otvaranja**
(BMO, ~13:00 po našem) ili **nakon zatvaranja** burze (AMC, ~22:00 po našem).

---

## Postavljanje (~15 minuta, jednokratno)

### 1. Finnhub API ključ (besplatno)
- Registriraj se na https://finnhub.io → ključ dobiješ odmah na dashboardu.
- Free tier pokriva US earnings kalendar mjesec dana unaprijed, 60 poziva/min.
  Bot troši **1 poziv po pokretanju** — daleko ispod limita.

### 2. Telegram bot
1. U Telegramu otvori **@BotFather** → `/newbot` → daj mu ime.
2. Dobiješ **token** (izgleda kao `8123456789:AAH...`).
3. Pošalji svom novom botu bilo koju poruku (npr. `/start`) — bez toga ti ne može pisati.
4. Otvori u pregledniku:
   `https://api.telegram.org/bot<TVOJ_TOKEN>/getUpdates`
   i iz odgovora prepiši `"chat":{"id": 123456789}` → to je tvoj **chat ID**.

### 3. GitHub repo
1. Napravi **privatni** repo i ubaci ove datoteke.
2. Idi na **Settings → Secrets and variables → Actions → New repository secret**
   i dodaj tri tajne:

   | Ime | Vrijednost |
   |---|---|
   | `FINNHUB_TOKEN` | ključ s Finnhuba |
   | `TELEGRAM_BOT_TOKEN` | token od BotFathera |
   | `TELEGRAM_CHAT_ID` | tvoj chat ID |

3. Idi na **Actions** tab → odaberi *Earnings alert* → **Run workflow**
   da provjeriš radi li odmah.

### 4. Watchlist
Uredi `watchlist.txt` — jedan ticker po retku, sve iza `#` je komentar:

```
AAPL      # Apple
NVDA      # NVIDIA
ASML      # ASML (US listing)
```

Promjena se primjenjuje pri sljedećem pokretanju. Nema praktičnog limita na broj
tickera jer bot ionako povlači cijeli kalendar jednim pozivom.

---

## Raspored

Definiran u `.github/workflows/earnings.yml` (cron je u **UTC**):

| UTC | Zagreb (ljeto) | Svrha |
|---|---|---|
| 05:00 | 07:00 | jutarnji pregled |
| 10:00 | 12:00 | prije BMO objava |
| 13:00 | 15:00 | pred otvaranje US burze |
| 18:00 | 20:00 | sredina US sesije |
| 21:00 | 23:00 | nakon AMC objava |

Zimi je Zagreb UTC+1, pa se sve pomiče za sat unatrag. Ako to smeta, samo pomakni
cron vrijednosti.

> GitHub Actions cron zna kasniti 5–15 minuta kod velikog opterećenja. Za ovu
> namjenu (datumi, ne sekunde) to nije problem.

---

## Lokalno testiranje

```bash
export FINNHUB_TOKEN=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export DRY_RUN=1          # ispiši u terminal umjesto slanja, ne diraj state.json
python3 earnings_bot.py
```

Nema vanjskih ovisnosti — samo standardni Python 3.11+.

---

## Kako radi dedupliciranje

`state.json` pamti zadnji poznati datum i koje su obavijesti već poslane po
tickeru. Workflow ga nakon svakog pokretanja commita natrag u repo, pa se isti
alarm nikad ne ponovi. Ako želiš "resetirati" bota, samo upiši `{}` u `state.json`.

## Ograničenja

- Finnhub free tier vidi **~30 dana unaprijed** i pokriva **US listinge**.
  Za europske burze treba plaćeni tier ili drugi izvor.
- Datumi objava su često "očekivani" dok ih kompanija službeno ne potvrdi —
  zato bot i prati promjene datuma.
- Ovo je alat za praćenje kalendara, ne investicijski savjet.

---

# iPhone widget (Scriptable)

Bot pri svakom pokretanju objavi `docs/earnings.json`. Widget na mobitelu samo
čita taj feed — ne treba mu ni ključ ni server.

### 1. Uključi GitHub Pages
Repo mora biti **javan** (Pages ne radi na privatnima u besplatnom planu).
Tvoji Secrets ostaju skriveni i u javnom repou — u kodu nema ničeg osjetljivog.

**Settings → Pages → Source: Deploy from a branch → Branch: `main`, folder: `/docs`**

Nakon par minuta feed je na:
`https://<korisnik>.github.io/<repo>/earnings.json`
a dashboard na:
`https://<korisnik>.github.io/<repo>/`

> Pages se pojavi tek nakon što workflow bar jednom odradi i commita `docs/`.

### 2. Scriptable
1. Instaliraj **Scriptable** iz App Storea (besplatno).
2. Otvori app → **+** → zalijepi sadržaj `earnings_widget.js` → nazovi ga `Earnings`.
3. U prvom retku zamijeni `FEED_URL` svojim linkom iz koraka 1.
4. Pokreni skriptu unutar Scriptablea (▶) — dobiješ pregled da provjeriš radi li.

### 3. Na home screen
Drži prst na praznom dijelu ekrana → **+** → *Scriptable* → odaberi veličinu →
**Add Widget**. Zatim drži prst na novom widgetu → **Edit Widget** → *Script:* `Earnings`.

| Veličina | Prikazuje |
|---|---|
| Mala | 3 tickera, samo odbrojavanje |
| Srednja | 4 tickera + datum i BMO/AMC |
| Velika | 9 tickera |

### Kako čitati widget

- **DANAS / SUTRA** crveno · **≤ 3 dana** žuto · ostalo bijelo
- `BMO` = prije otvaranja burze, `AMC` = nakon zatvaranja, `MID` = tijekom trgovanja
- `↻` pored tickera = datum je pomaknut otkad ga bot prati
- `⚠` u zaglavlju = feed se nije dao dohvatiti, prikazan je zadnji spremljeni

iOS sam odlučuje kada osvježiti widget (obično svakih 1–2 h). Za trenutni
osvježaj tapni widget — otvori se dashboard s najsvježijim podacima.

### Dashboard kao ikona (bonus)
Otvori `https://<korisnik>.github.io/<repo>/` u Safariju → *Share* →
**Add to Home Screen**. Dobiješ ikonu koja se otvara bez adresne trake.
