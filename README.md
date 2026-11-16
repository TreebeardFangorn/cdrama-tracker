# 🏮 Drama Tracker (Cdramas + Kdramas)

A private, free, self-updating website for finding and tracking Chinese and Korean dramas:
upcoming, airing now, and already released (2015 onward).

The two are separate sections with a switch at the top. Each has its own data file
(`docs/data/cn.json`, `docs/data/kr.json`), its own cache folder (`cache/cn`, `cache/kr`), its own
people credits, and its own saved filters and personal list, so nothing from one appears in the other.

## What it shows

- **Upcoming / Airing now / Hot now / My list / All** tabs
- Poster, English + Chinese title, release date (with countdown), episodes, platform, genres, tags, synopsis
- Scores side by side: **MyDramaList, Viki, IMDb**, plus an average
- Buzz: how many people are tracking a drama on MyDramaList (this is the "anticipation" number for
  unreleased dramas), Viki followers, IMDb vote counts
- **Hot now** tab: dramas gaining MyDramaList audience fastest (new trackers per day, measured from the
  tracker's own daily snapshots; shown as an estimate with `~` until a few days of history exist)
- Sort by any score, by buzz, by release date, or by "recently added to the tracker"
- Filter by genre, year, platform; search by title, actor, director, writer or tag
- Main stars, director(s) and screenwriter(s), each with their 3 most popular dramas since 2020
- Where to watch in the US (Netflix, Viki, WeTV, iQIYI, Prime Video…) with direct links, plus a
  "Watch in US" filter
- Links: YouTube trailer search, MyDramaList page, Viki page, IMDb page, Douban search
- Personal list (Want to watch / Watching / Watched / Not interested), saved in the browser
- "New" badge on dramas added in the last two weeks

## How it stays fresh

`scripts/update.py` (plain Python, no installs) rebuilds `docs/data/cn.json` and `docs/data/kr.json`.
On GitHub, `.github/workflows/refresh.yml` runs it once a day and commits the result,
and GitHub Pages serves the `docs/` folder. Nothing needs to stay switched on.

Data sources, all free and key-less:

| Source | Used for | Notes |
|---|---|---|
| MyDramaList via the open-source [Kuryana API](https://github.com/tbdsux/kuryana) | catalog, posters, cast/crew, MDL score, watchers | unofficial community mirror; if it is down the site keeps yesterday's data |
| Viki public catalog API | Viki score, followers, watch link | unofficial but public, read-only |
| JustWatch (unofficial public API) | US streaming availability and watch links | attribution shown in the site footer; if it is down the last known availability is kept |
| [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/) | IMDb score and votes | official; personal/non-commercial use only |

Scope (Kdramas): South Korean drama series; the overseas-only rule below does not apply to them, since
Netflix and Disney+ originals are a core part of Korean drama.

Scope (Cdramas): mainland Chinese dramas only. Taiwanese and Hong Kong productions are excluded, and so is anything
released only on overseas platforms (Viki, Netflix, GTV, etc.; see `OVERSEAS` in `scripts/update.py`).

Boys' love titles are also left out by personal preference (`EXCLUDE_TAGS` in `scripts/update.py`).

Known limits: real view counts are not published anywhere for free, so "most watched" uses the
audience numbers above. Trailer links open a YouTube search rather than one fixed video.
IMDb matching is by title + year and skips anything ambiguous, so some dramas have no IMDb score.

## Put it online (one time, about 5 minutes)

1. Create a free GitHub account, then a **new public repository** (for example `cdrama-tracker`).
   Free GitHub Pages needs a public repo; the page itself asks search engines not to index it.
2. From this folder:
   ```bash
   git init -b main && git add . && git commit -m "Cdrama Tracker"
   git remote add origin https://github.com/YOUR-NAME/cdrama-tracker.git
   git push -u origin main
   ```
3. On GitHub: **Settings → Pages → Source: Deploy from a branch → Branch `main`, folder `/docs` → Save**.
4. On GitHub: **Settings → Actions → General → Workflow permissions → Read and write → Save**.
5. Share `https://YOUR-NAME.github.io/cdrama-tracker/` with your friend.

The refresh then runs every day by itself. To run it on demand: **Actions → Daily data refresh → Run workflow**.

## Run it on a computer instead

```bash
python3 scripts/update.py                      # refresh data
python3 -m http.server 8822 --directory docs   # then open http://localhost:8822
```

Optional settings (environment variables): `CDT_MAX_DETAIL`, `CDT_MAX_PEOPLE`, `CDT_MAX_POP_RANK`,
`CDT_SKIP_IMDB=1`, `CDT_SKIP_VIKI=1`, `CDT_FORCE_IMDB=1`. See the top of `scripts/update.py`.
