# CLAUDE.md

Kontekst projekta za Claude Code. Vlasnik komunicira na hrvatskom.

## Što je ovo

Bot koji prati datume objave kvartalnih rezultata (earnings) za tickere s
watchliste i šalje Telegram alarm. Vrti se na GitHub Actions 5× dnevno radnim
danima. Nema servera, nema baze, nema vanjskih Python paketa.

## Datoteke

| Datoteka | Uloga |
|---|---|
| `earnings_bot.py` | cijela logika — dohvat, alarmi, feed |
| `watchlist.txt` | tickeri, jedan po retku, `#` = komentar |
| `state.json` | što je već poslano; workflow ga commita natrag |
| `docs/earnings.json` | javni feed koji čita iPhone widget |
| `docs/index.html` | web dashboard (GitHub Pages) |
| `earnings_widget.js` | Scriptable skripta, kopira se ručno na iPhone |
| `.github/workflows/earnings.yml` | cron raspored |

## Tok podataka

```
Finnhub /calendar/earnings  (1 poziv, raspon 30 dana)
        ↓ filtriraj na watchlist, uzmi najraniji zapis po tickeru
   build_alerts()  ← usporedi sa state.json
        ↓                        ↓
   Telegram poruka         docs/earnings.json
                                 ↓
                    Scriptable widget / dashboard
```

## Pravila koja treba poštovati

- **Bez vanjskih ovisnosti.** Samo standardna Python 3.12 biblioteka
  (`urllib`, `json`, `datetime`). Ne dodavaj `requests`, `pandas` i sl.
- **Idempotentnost.** Bot se vrti 5× dnevno; isti alarm se ne smije poslati
  dvaput. Sva dedupliciranje ide kroz `state.json` (`sent` lista po tickeru).
- **Finnhub free tier** vidi ~30 dana unaprijed i samo US listinge. Ne piši kod
  koji pretpostavlja dulji horizont.
- **Nikad ne commitaj tajne.** `FINNHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID` dolaze isključivo iz environmenta / GitHub Secrets.
- `docs/` je javan sadržaj — ne stavljaj ništa osjetljivo tamo.

## Testiranje

```bash
export FINNHUB_TOKEN=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
DRY_RUN=1 python3 earnings_bot.py    # ispiši umjesto slanja, ne diraj state
```

`DRY_RUN=1` preskače slanje i spremanje stanja, ali **piše** `docs/earnings.json`.

## Vizualni stil

Paleta je "departure board" — earnings su najavljeni dolasci koji znaju kasniti.
Držati je dosljednom između `docs/index.html` i `earnings_widget.js`:

```
bg    #0C1017    panel #141A23    rule  #232C38
amber #F0A500    paper #E8E4DA    slate #6B7785    alert #E5484D
```

Odbrojavanje: crveno ≤ 1 dan, žuto ≤ 3 dana, inače neutralno.

## Poznata ograničenja / moguće nadogradnje

- Samo US listinge → za europske burze treba drugi izvor podataka
- GitHub Actions cron zna kasniti 5–15 min (nebitno za praćenje datuma)
- Nema povijesti reakcije dionice na prošle objave — potencijalna nadogradnja
- Watchlist se uređuje ručno; može se povezati s brokerskim portfeljem
