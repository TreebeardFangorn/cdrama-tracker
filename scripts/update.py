#!/usr/bin/env python3
"""Cdrama Tracker - daily data refresh.

Pulls Chinese drama data from free, key-less sources and writes docs/data/cn.json and docs/data/kr.json
for the static site. Python standard library only.

Sources
  * MyDramaList data via the open-source Kuryana mirror API (catalog, cast/crew, MDL score)
  * Viki public catalog API (Viki score, followers, watch link)
  * IMDb official non-commercial datasets (IMDb score and vote count)

Every source is optional: if one is down, the last cached values are kept.
Incremental: results are cached in cache/ so a daily run only makes a few hundred requests.

Environment knobs (all optional)
  CDT_MAX_DETAIL   max drama detail requests per run   (default 1200, per region)
  CDT_MAX_PEOPLE   max people page requests per run    (default 60)
  CDT_SKIP_IMDB=1 / CDT_SKIP_VIKI=1   skip a source
  CDT_REGIONS=cn,kr  which sections to refresh (default: all)
  CDT_FORCE_IMDB=1 re-download the big IMDb title list now (otherwise weekly)
"""
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")
OUT = os.path.join(ROOT, "docs", "data")
MDL_API = "https://kuryana.tbdh.app"
VIKI_API = "https://api.viki.io/v4"
VIKI_APP = "100000a"
UA = "Mozilla/5.0 (compatible; cdrama-tracker/1.0; personal non-commercial)"
FIRST_YEAR = 2015
TODAY = dt.date.today()
NOW = int(time.time())
DAY = 86400
MAX_DETAIL = int(os.environ.get("CDT_MAX_DETAIL", "1200"))
MAX_PEOPLE = int(os.environ.get("CDT_MAX_PEOPLE", "60"))
MAX_POP_RANK = int(os.environ.get("CDT_MAX_POP_RANK", "20000"))  # MDL popularity rank cutoff


# Each region is fetched, cached and published completely separately (own cache folder, own data file),
# so titles and people from one can never show up in the other.
REGIONS = {
    "cn": {"label": "Chinese", "type": "Chinese Drama", "country": "China", "viki": "cn",
           "viki_native": ("zh", "zt"), "overseas_rule": True},
    "kr": {"label": "Korean", "type": "Korean Drama", "country": "South Korea", "viki": "kr",
           "viki_native": ("ko",), "overseas_rule": False},
}
R = REGIONS["cn"]
RKEY = "cn"
CACHE_ROOT = CACHE


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- helpers
def http(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def get_json(url, tries=3):
    err = None
    for i in range(tries):
        try:
            return json.loads(http(url))
        except Exception as e:  # noqa: BLE001
            err = e
            if getattr(e, "code", None) == 404:
                return {"_gone": True}
            time.sleep(2 + 3 * i)
    log("  ! failed:", url, "-", err)
    return None


def load(name, default):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def save(name, obj, folder=None):
    folder = folder or CACHE
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, os.path.join(folder, name))


def stagger(key, days):
    """Stable per-item offset (seconds) so periodic refreshes spread across days."""
    h = int(hashlib.md5(str(key).encode()).hexdigest()[:8], 16)
    return (h % days) * DAY


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w]+", "", s, flags=re.UNICODE).replace("_", "")
    if s.startswith("the") and len(s) > 5:
        s = s[3:]
    return s


def has_cjk(s):
    """True if the text contains Chinese characters or Korean Hangul (i.e. a native-script title)."""
    return bool(re.search(r"[㐀-鿿가-힯]", s or ""))


def to_float(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def to_int(x):
    if x is None:
        return None
    m = re.sub(r"[^\d]", "", str(x))
    return int(m) if m else None


def as_list(x):
    if not x:
        return []
    if isinstance(x, str):
        return [p.strip() for p in x.split(",") if p.strip()]
    return [str(p).strip() for p in x if str(p).strip()]


def parse_date(s):
    """-> (iso_start_of_period, precision 'd'|'m'|'y') or (None, None)."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s or s in ("?", "TBA", "N/A"):
        return None, None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat(), "d"
        except ValueError:
            pass
    for fmt in ("%b %Y", "%B %Y", "%b, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat(), "m"
        except ValueError:
            pass
    m = re.fullmatch(r"(19|20)\d\d", s)
    if m:
        return s + "-01-01", "y"
    return None, None


def period_end(iso, prec):
    d = dt.date.fromisoformat(iso)
    if prec == "y":
        return dt.date(d.year, 12, 31)
    if prec == "m":
        nxt = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return nxt - dt.timedelta(days=1)
    return d


NETWORKS = ["Tencent Video", "iQiyi", "Youku", "Mango TV", "Hunan TV", "CCTV", "Sohu TV", "Kuaishou",
            "Dragon TV", "ZJTV", "JSTV", "Douyin", "Bilibili", "BTV", "LeTV", "Anhui TV", "Shenzhen TV",
            "LINE TV", "GTV", "Viki", "Netflix", "WeTV", "Migu Video", "Disney+", "PPTV", "Shandong TV",
            "Guangdong TV", "Tianjin TV", "Hubei TV", "Jiangxi TV", "Sichuan TV", "Hebei TV", "Xigua Video",
            "Hong Guo", "Youku Tudou", "Tudou", "Amazon Prime", "Apple TV+", "Hulu", "Viu", "TVB",
            # Korean broadcasters and streamers
            "tvN", "JTBC", "SBS", "KBS2", "KBS1", "MBC", "ENA", "OCN", "TVING", "Wavve", "Coupang Play",
            "Channel A", "TV Chosun", "MBN", "Genie TV", "U+ Mobile TV", "KakaoTV", "Naver TV", "Naver TV Cast",
            "Olleh TV", "seezn", "Watcha", "SBS Plus", "MBC every1", "Dramax", "ViuTV", "Kocowa", "KBS Joy",
            "tvN STORY", "SkyTV", "Channel S", "Lifetime", "Oksusu", "vLive", "YouTube", "Disney+ Hotstar"]


# Platforms outside mainland China. A drama listed ONLY on these was made for overseas release.
OVERSEAS = {"Viki", "Netflix", "GTV", "LINE TV", "Channel 9", "LaLa TV", "Viu", "WeTV", "Disney+",
            "Amazon Prime", "Apple TV+", "Hulu", "TVB", "GagaOOLala", "YouTube", "iQIYI International"}


# Personal preference: dramas carrying any of these MyDramaList tags are left out (boys' love titles).
EXCLUDE_TAGS = {"Gay Romance", "Gay Male Lead", "Gay Relationship",
                "Lesbian Romance", "Lesbian Female Lead", "Lesbian Relationship"}


def split_networks(s):
    """MDL lists platforms as one space-joined string ("Hunan TV Mango TV"); split into names."""
    s = re.sub(r"\s+", " ", (s or "").replace(",", " ")).strip()
    out = []
    while s:
        hit = next((n for n in sorted(NETWORKS, key=len, reverse=True)
                    if s.lower().startswith(n.lower()) and (len(s) == len(n) or s[len(n)] == " ")), None)
        if hit:
            out.append(hit)
            s = s[len(hit):].strip()
        else:  # unknown name: keep words until the next known platform starts
            words = s.split(" ")
            i = 1
            while i < len(words) and not any(" ".join(words[i:]).lower().startswith(n.lower()) for n in NETWORKS):
                i += 1
            out.append(" ".join(words[:i]))
            s = " ".join(words[i:])
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- MyDramaList
def refresh_seasonal(seasonal):
    cur_q = (TODAY.month - 1) // 3 + 1
    cur_idx = TODAY.year * 4 + cur_q
    todo = []
    for y in range(FIRST_YEAR, TODAY.year + 2):
        for q in (1, 2, 3, 4):
            key = f"{y}-{q}"
            idx = y * 4 + q
            got = seasonal.get(key, {}).get("fetched", 0)
            max_age = 0.8 * DAY if idx >= cur_idx - 2 else 30 * DAY
            if NOW - got >= max_age - (0 if idx >= cur_idx - 2 else stagger(key, 20)):
                todo.append((key, y, q))
    log(f"MDL seasonal: refreshing {len(todo)} quarters")

    def one(t):
        key, y, q = t
        res = get_json(f"{MDL_API}/seasonal/{y}/{q}")
        if not isinstance(res, list):
            return key, None
        items = []
        for x in res:
            if x.get("type") != R["type"] or not x.get("id"):
                continue
            items.append({k: x.get(k) for k in (
                "id", "title", "url", "released", "rating", "ranked", "popularity",
                "synopsis", "genres", "cover")})
        return key, items

    with ThreadPoolExecutor(3) as ex:
        for key, items in ex.map(one, todo):
            if items is not None:
                seasonal[key] = {"fetched": NOW, "items": items}
    save("seasonal.json", seasonal)


def trim_detail(d):
    det = d.get("details") or {}
    oth = d.get("others") or {}
    directors, writers = [], []
    for k, v in oth.items():
        lk = k.lower()
        if "director" in lk:
            directors += as_list(v)
        if "screenwriter" in lk or lk == "writer":
            writers += as_list(v)
    m = re.match(r"\s*([\d.]+)\s*\(scored by ([\d,]+)", str(det.get("score") or ""))
    return {
        "title": re.sub(r"\s*\((19|20)\d\d\)\s*$", "", d.get("title") or ""),
        "link": d.get("link"),
        "poster": d.get("poster"),
        "synopsis": (d.get("synopsis") or "").strip(),
        "rating": to_float(m.group(1)) if m else to_float(d.get("rating")),
        "votes": to_int(m.group(2)) if m else None,
        "casts": [{"n": c.get("name"), "s": c.get("slug"), "i": c.get("profile_image")}
                  for c in (d.get("casts") or [])[:8] if c.get("name")],
        "country": det.get("country"),
        "type": det.get("type"),
        "episodes": to_int(det.get("episodes")),
        "aired": det.get("aired"),
        "network": det.get("original_network"),
        "duration": det.get("duration"),
        "ranked": to_int(det.get("ranked")),
        "popularity": to_int(det.get("popularity")),
        "watchers": to_int(det.get("watchers")),
        "content_rating": det.get("content_rating"),
        "native": as_list(oth.get("native_title")),
        "aka": as_list(oth.get("also_known_as")),
        "director": list(dict.fromkeys(directors)),
        "screenwriter": list(dict.fromkeys(writers)),
        "related": [r.get("slug") for r in (oth.get("related_content") or [])
                    if r.get("slug") and R["label"] in (r.get("description") or "")],
        "genres": as_list(oth.get("genres")),
        "tags": [re.sub(r"\s*\(Vote tags\)", "", t) for t in as_list(oth.get("tags"))],
    }


def seasonal_start(item):
    return parse_date(item.get("released"))


def is_fresh(iso, prec, days):
    """True if the title is upcoming or started within `days`."""
    if not iso:
        return True
    return period_end(iso, prec) >= TODAY - dt.timedelta(days=days)


def refresh_details(candidates, details):
    """candidates: {id: {"slug":..., "pop":rank|None, "fresh":bool}}"""
    todo = []
    for cid, c in candidates.items():
        d = details.get(cid)
        if d is None:
            todo.append((0 if c["fresh"] else 1, c.get("pop") or 10**9, cid))
            continue
        age = NOW - d.get("fetched", 0)
        if d.get("error"):
            if age > 7 * DAY + stagger(cid, 7):
                todo.append((3, 0, cid))
        elif "related" not in d and (d.get("watchers") or 0) >= 2000:
            todo.append((2, 0, cid))  # one-time backfill of sequel/prequel links
        elif c["fresh"]:
            if age > 0.8 * DAY:  # daily, so audience growth ("hot now") can be measured
                todo.append((2, age * -1, cid))
        elif age > 45 * DAY + stagger(cid, 30):
            todo.append((4, age * -1, cid))
    todo.sort()
    batch = [t[2] for t in todo[:MAX_DETAIL]]
    log(f"MDL details: {len(todo)} due, fetching {len(batch)} this run")

    def one(cid):
        res = get_json(f"{MDL_API}/id/{candidates[cid]['slug']}")
        time.sleep(0.25)
        if isinstance(res, dict) and res.get("_gone"):
            return cid, "gone"
        if not isinstance(res, dict) or not isinstance(res.get("data"), dict):
            return cid, None
        try:
            return cid, trim_detail(res["data"])
        except Exception as e:  # noqa: BLE001
            log("  ! parse error", cid, e)
            return cid, None

    done = 0
    with ThreadPoolExecutor(4) as ex:
        for cid, d in ex.map(one, batch):
            done += 1
            if d is None or d == "gone":
                prev = details.get(cid) or {}
                if "title" in prev:
                    pass  # keep the old good data; it stays due and is retried next run
                elif d == "gone":
                    details[cid] = {"fetched": NOW, "error": True}
                # else: network hiccup on a never-fetched title -> simply retried next run
            else:
                d["fetched"] = NOW
                details[cid] = d
            if done % 200 == 0:
                log(f"  {done}/{len(batch)}")
                save("details.json", details)
    save("details.json", details)


def discover_from_people(details, people, extra, known_ids):
    """Fetch filmographies of the most-watched stars to find announced (TBA) dramas."""
    score = {}
    for d in details.values():
        if d.get("error") or not d.get("watchers"):
            continue
        yr = (parse_date((d.get("aired") or "").split(" - ")[0])[0] or "0000")[:4]
        if yr < str(TODAY.year - 4):
            continue
        for c in d.get("casts", [])[:4]:
            if c.get("s"):
                score[c["s"]] = score.get(c["s"], 0) + d["watchers"]
    top = sorted(score, key=score.get, reverse=True)[:300]
    due = [s for s in top if NOW - people.get(s, {}).get("fetched", 0) > 10 * DAY + stagger(s, 6)]
    batch = due[:MAX_PEOPLE]
    log(f"MDL people: {len(due)} due, fetching {len(batch)} this run")

    def one(slug):
        res = get_json(f"{MDL_API}{slug}")
        time.sleep(0.25)
        return slug, res

    found = 0
    with ThreadPoolExecutor(3) as ex:
        for slug, res in ex.map(one, batch):
            people[slug] = {"fetched": NOW}
            data = (res or {}).get("data") if isinstance(res, dict) else None
            works = (data or {}).get("works") or {}
            for w in works.get("Drama") or []:
                yr = str(w.get("year") or "")
                if not (yr == "TBA" or (yr.isdigit() and int(yr) >= TODAY.year)):
                    continue
                link = ((w.get("title") or {}).get("link") or "")
                m = re.search(r"mydramalist\.com/((\d+)-[^/?#]+)", link)
                if not m:
                    continue
                cid = m.group(2)
                if cid not in known_ids and cid not in extra:
                    extra[cid] = {"slug": m.group(1), "title": (w.get("title") or {}).get("name"),
                                  "found": NOW}
                    found += 1
    log(f"  discovered {found} announced dramas")
    save("people.json", people)
    save("extra.json", extra)


def discover_related(details, extra, known_ids):
    """Sequels, prequels and spin-offs linked from dramas we track (finds announced new seasons)."""
    found = 0
    for d in list(details.values()):
        for slug in d.get("related") or []:
            m = re.match(r"(\d+)-", slug)
            if m and m.group(1) not in known_ids and m.group(1) not in extra and m.group(1) not in details:
                extra[m.group(1)] = {"slug": slug, "title": None, "found": NOW}
                found += 1
    log(f"MDL related titles: discovered {found}")
    save("extra.json", extra)


# ---------------------------------------------------------------- Viki
def fetch_viki():
    out = []
    page = 1
    while page < 80:
        res = get_json(f"{VIKI_API}/containers.json?app={VIKI_APP}&origin_country={R['viki']}"
                       f"&type=series&per_page=50&page={page}")
        if not res or not res.get("response"):
            if res is None and page == 1:
                return None
            break
        for c in res["response"]:
            t = c.get("titles") or {}
            rs = c.get("review_stats") or {}
            out.append({
                "id": c.get("id"),
                "en": t.get("en"), "native": [t[k] for k in R["viki_native"] if t.get(k)],
                "aka": as_list((c.get("titles_aka") or {}).get("en")),
                "score": to_float(rs.get("average_rating")), "count": rs.get("count"),
                "followers": (c.get("subscriptions") or {}).get("count"),
                "url": (c.get("url") or {}).get("web"),
                "poster": ((c.get("images") or {}).get("poster") or {}).get("url"),
                "trailers": (c.get("trailers") or {}).get("count"),
            })
        if not res.get("more"):
            break
        page += 1
        time.sleep(0.3)
    return out


def viki_index(viki):
    native, english = {}, {}
    for v in viki:
        for s in (v.get("native") or [v.get("zh"), v.get("zt")]) + [a for a in v["aka"] if has_cjk(a)]:
            if s:
                native.setdefault(norm(s), v)
        if v.get("en"):
            english.setdefault(norm(v["en"]), v)
    return native, english


# ---------------------------------------------------------------- IMDb
_IMDB = {}


def imdb_basics_file():
    """Download IMDb's title list once per run (it is large) and reuse it for every region."""
    if "basics" not in _IMDB:
        import shutil
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "cdt_title_basics.tsv.gz")
        req = urllib.request.Request("https://datasets.imdbws.com/title.basics.tsv.gz",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
        _IMDB["basics"] = path
    return _IMDB["basics"]


def build_imdb_map(dramas, imdb_map):
    """Match dramas to IMDb ids by title + year using the official title list (large download)."""
    log("IMDb: downloading title list (large, done weekly)...")
    want = {}
    for d in dramas:
        if d["id"] in imdb_map and imdb_map[d["id"]]:
            continue
        for name in [d["t"]] + d["_names"]:
            n = norm(name)
            if len(n) >= 2:
                want.setdefault(n, []).append(d)
    cands = {}
    with gzip.open(imdb_basics_file(), "rb") as gz:
        for raw in io.TextIOWrapper(gz, encoding="utf-8", errors="replace"):
            p = raw.rstrip("\n").split("\t")
            if len(p) < 6 or p[1] not in ("tvSeries", "tvMiniSeries"):
                continue
            year = int(p[5]) if p[5].isdigit() else None
            if year is not None and year < FIRST_YEAR - 1:
                continue
            np_, no = norm(p[2]), norm(p[3])
            for n, via_orig in ((np_, False), (no, True)):
                if n not in want or (via_orig and no == np_):
                    continue
                for d in want[n]:
                    dy = d.get("y")
                    if dy and year and abs(dy - year) > 1:
                        continue
                    if dy and not year and d["st"] != "upcoming":
                        continue
                    strong = no != np_ or has_cjk(p[3]) or len(p[2].split()) >= 3
                    cands.setdefault(d["id"], {})[p[0]] = max(strong, cands.get(d["id"], {}).get(p[0], False))
    matched = 0
    used = {v for k, v in imdb_map.items() if k != "_built" and v}
    for d in sorted(dramas, key=lambda d: -(d["mdl"]["w"] or 0)):
        if imdb_map.get(d["id"]):
            continue
        c = {k: v for k, v in cands.get(d["id"], {}).items() if k not in used}
        strong = [k for k, s in c.items() if s]
        pick = None
        if len(strong) == 1:
            pick = strong[0]
        elif not strong and len(c) == 1 and len(d["t"].split()) >= 2:
            pick = next(iter(c))
        imdb_map[d["id"]] = pick
        matched += bool(pick)
        if pick:
            used.add(pick)
    imdb_map["_built"] = NOW
    log(f"  matched {matched} new titles")
    save("imdb_map.json", imdb_map)


def fetch_imdb_ratings(ids):
    out = {}
    try:
        if "ratings" not in _IMDB:
            _IMDB["ratings"] = gzip.decompress(
                http("https://datasets.imdbws.com/title.ratings.tsv.gz", timeout=300)).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        log("  ! IMDb ratings failed:", e)
        return None
    for line in _IMDB["ratings"].splitlines():
        p = line.split("\t")
        if p[0] in ids:
            out[p[0]] = [float(p[1]), int(p[2])]
    return out


# ---------------------------------------------------------------- JustWatch (US streaming availability)
JW_QUERY_ONE = """q%d: popularTitles(country: US, filter: {searchQuery: $q%d, objectTypes: [SHOW]}, first: 5) {
  edges { node { content(country: US, language: "en") { title originalTitle originalReleaseYear externalIds { imdbId } }
    offers(country: US, platform: WEB) { monetizationType standardWebURL package { clearName } } } } }"""
JW_KIND = {"FREE": "free", "ADS": "free", "FAST": "free", "FLATRATE": "sub",
           "FLATRATE_AND_BUY": "sub", "RENT": "rent", "BUY": "buy"}
JW_RANK = {"free": 0, "sub": 1, "rent": 2, "buy": 3}
MAX_JW = int(os.environ.get("CDT_MAX_JW", "800"))


def jw_provider(name):
    """Fold JustWatch's many variants into one platform name; None = skip (add-on channels, discs)."""
    if re.search(r"Amazon Channel|Apple TV [Cc]hannel|Roku Premium Channel|DVD|Blu-ray|Live", name):
        return None
    name = re.sub(r"\s+(Standard with Ads|Free with Ads|with Ads|Basic with Ads)$", "", name).strip()
    return {"Rakuten Viki": "Viki", "Tubi TV": "Tubi", "Tencent Video": "WeTV (Tencent Video)",
            "Amazon Video": "Amazon (buy/rent)", "Apple TV Store": "Apple TV (buy/rent)"}.get(name, name)


def refresh_justwatch(dramas, jw):
    due = []
    for d in dramas:
        if d["st"] == "upcoming":
            continue
        c = jw.get(d["id"])
        age = NOW - c["fetched"] if c else None
        recent = d["st"] == "airing" or is_fresh(d["start"], d["sp"], 120)
        if c is None:
            due.append((0, -(d["mdl"]["w"] or 0), d))
        elif recent and age > 1.8 * DAY:
            due.append((1, -age, d))
        elif not recent and age > 30 * DAY + stagger(d["id"], 20):
            due.append((2, -age, d))
    due.sort(key=lambda t: t[:2])
    batch = [t[2] for t in due[:MAX_JW]]
    log(f"JustWatch: {len(due)} due, checking {len(batch)} this run")
    groups = [batch[i:i + 8] for i in range(0, len(batch), 8)]

    def one(group):
        query = ("query(" + ", ".join(f"$q{i}: String!" for i in range(len(group))) + ") {\n"
                 + "\n".join(JW_QUERY_ONE % (i, i) for i in range(len(group))) + "\n}")
        body = json.dumps({"query": query, "variables": {f"q{i}": d["t"] for i, d in enumerate(group)}})
        res = None
        for attempt in range(3):
            try:
                req = urllib.request.Request("https://apis.justwatch.com/graphql", data=body.encode(),
                                             headers={"Content-Type": "application/json", "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r:
                    res = json.loads(r.read())
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(3 + 4 * attempt)
        time.sleep(0.4)
        if not res or not isinstance(res.get("data"), dict):
            log("  ! JustWatch batch failed:", (res or {}).get("errors") or err)
            return group, None
        return group, res["data"]

    with ThreadPoolExecutor(2) as ex:
        for group, data in ex.map(one, groups):
            if data is None:
                continue
            for i, d in enumerate(group):
                native = {norm(n) for n in [d.get("nt")] + d["_names"] if n and has_cjk(n)}
                # JustWatch often lists Korean shows under a romanized title, so also accept any of the
                # drama's known alternative titles, but only together with a matching year
                names = {norm(n) for n in [d["t"]] + d["_names"] if n and len(norm(n)) >= 4}
                tt = (d.get("imdb") or {}).get("id")
                pick = None
                for e in ((data.get(f"q{i}") or {}).get("edges") or []):
                    n = e["node"]
                    c = n.get("content") or {}
                    if norm(c.get("originalTitle")) in native or (tt and (c.get("externalIds") or {}).get("imdbId") == tt):
                        pick = n
                        break
                    yr = c.get("originalReleaseYear")
                    if (not pick and d.get("y") and yr and abs(yr - d["y"]) <= 1
                            and (norm(c.get("originalTitle")) in names
                                 or (norm(c.get("title")) in names and norm(c.get("title")) != norm(c.get("originalTitle"))))):
                        pick = n
                best = {}
                for o in (pick or {}).get("offers") or []:
                    name = jw_provider((o.get("package") or {}).get("clearName") or "")
                    kind = JW_KIND.get(o.get("monetizationType"))
                    url = o.get("standardWebURL") or ""
                    if not name or not kind or not url.startswith("https://"):
                        continue
                    if name not in best or JW_RANK[kind] < JW_RANK[best[name][1]]:
                        best[name] = [name, kind, url]
                jw[d["id"]] = {"fetched": NOW, "offers": sorted(best.values(), key=lambda x: (JW_RANK[x[1]], x[0]))}
            if len(jw) % 400 < 8:
                save("justwatch.json", jw)
    save("justwatch.json", jw)


# ---------------------------------------------------------------- build
def build(region):
    global R, RKEY, CACHE
    RKEY, R = region, REGIONS[region]
    CACHE = os.path.join(CACHE_ROOT, region)
    log(f"\n===== {R['label']} dramas =====")
    os.makedirs(CACHE, exist_ok=True)
    seasonal = load("seasonal.json", {})
    details = load("details.json", {})
    people = load("people.json", {})
    extra = load("extra.json", {})
    first_seen = load("first_seen.json", {})

    refresh_seasonal(seasonal)

    # candidate set from seasonal lists
    base = {}
    for key, q in seasonal.items():
        for it in q.get("items", []):
            cid = str(it["id"])
            iso, prec = seasonal_start(it)
            fresh = is_fresh(iso, prec, 120)
            pop = it.get("popularity")
            if not (pop and pop <= MAX_POP_RANK) and not is_fresh(iso, prec, 90):
                continue  # skip the long tail of obscure older titles (mostly mini-dramas)
            it = dict(it, _q=key)
            if cid not in base or key > base[cid]["_q"]:
                base[cid] = it
    cands = {cid: {"slug": it["url"].lstrip("/"), "pop": it.get("popularity"),
                   "fresh": is_fresh(*seasonal_start(it), 120)} for cid, it in base.items()}
    for cid, e in extra.items():
        if cid not in cands:
            cands[cid] = {"slug": e["slug"], "pop": None, "fresh": True}
    log(f"Catalog candidates: {len(cands)}")

    refresh_details(cands, details)
    discover_from_people(details, people, extra, set(base))
    all_seasonal = {str(it["id"]) for q in seasonal.values() for it in q.get("items", [])}
    discover_related(details, extra, all_seasonal)
    new_extra = {cid: {"slug": e["slug"], "pop": None, "fresh": True}
                 for cid, e in extra.items() if cid not in details and cid not in base}
    if new_extra:
        refresh_details(new_extra, details)
        cands.update(new_extra)

    # ---- assemble drama records
    dramas = []
    for cid, c in cands.items():
        s = base.get(cid) or {}
        d = details.get(cid) or {}
        if d.get("error"):
            d = {}
        if not s and not d:
            continue
        if d and d.get("country") and d["country"] != R["country"]:
            continue
        if d and d.get("type") and d["type"] not in ("Drama",):
            continue
        start = end = sp = None
        if d.get("aired"):
            parts = [p.strip() for p in d["aired"].split(" - ")]
            start, sp = parse_date(parts[0])
            if len(parts) > 1:
                end, _ = parse_date(parts[1])
            elif sp == "d" and (d.get("episodes") or 0) <= 1:
                end = start
        if not start and s:
            start, sp = seasonal_start(s)
        if not start:
            st = "upcoming"
        elif period_end(start, sp) > TODAY or (sp != "d" and not d.get("rating") and not s.get("rating")
                                                 and period_end(start, sp) >= TODAY - dt.timedelta(days=200)):
            st = "upcoming"
        elif end:
            st = "released" if dt.date.fromisoformat(end) < TODAY else "airing"
        else:
            st = "airing" if dt.date.fromisoformat(start) >= TODAY - dt.timedelta(days=75) else "released"
        slug = c["slug"]
        syn = d.get("synopsis") or s.get("synopsis") or ""
        syn = re.sub(r"\s*\(Source:[^)]*\)", "", syn).strip()
        if len(syn) > 900:
            syn = syn[:900].rsplit(" ", 1)[0] + "…"
        rec = {
            "id": cid,
            "t": d.get("title") or s.get("title") or (extra.get(cid) or {}).get("title") or "Untitled",
            "nt": (d.get("native") or [None])[0],
            "y": int(start[:4]) if start else None,
            "start": start, "sp": sp, "end": end, "st": st,
            "ep": d.get("episodes"), "dur": d.get("duration"),
            "net": split_networks(d.get("network")),
            "cr": d.get("content_rating"),
            "g": d.get("genres") or s.get("genres") or [],
            "tags": (d.get("tags") or [])[:10],
            "syn": syn,
            "img": d.get("poster") or s.get("cover"),
            "url": f"https://mydramalist.com/{slug}",
            "mdl": {"s": d.get("rating") or to_float(s.get("rating")), "n": d.get("votes"),
                    "w": d.get("watchers"), "r": d.get("ranked") or s.get("ranked"),
                    "p": d.get("popularity") or s.get("popularity")},
            "_names": (d.get("native") or []) + (d.get("aka") or []),
            "_cast": d.get("casts") or [], "_dir": d.get("director") or [],
            "_wri": d.get("screenwriter") or [],
        }
        if cid not in base and st == "released" and not is_fresh(start, sp, 120):
            continue  # found via cast/sequel links but already out: only announced titles come in this way
        for k in ("r", "p"):
            if (rec["mdl"][k] or 0) >= 99999:
                rec["mdl"][k] = None  # MDL placeholder rank for brand-new entries
        if EXCLUDE_TAGS & set((d.get("tags") or []) + (d.get("genres") or [])):
            continue
        if R["overseas_rule"] and rec["net"] and all(n in OVERSEAS for n in rec["net"]):
            continue  # overseas-only release, not a mainland drama
        if cid not in first_seen:
            first_seen[cid] = TODAY.isoformat()
        rec["add"] = first_seen[cid]
        dramas.append(rec)
    save("first_seen.json", first_seen)

    # ---- "hot now": how fast a drama is gaining MyDramaList trackers (people per day)
    history = load("history.json", {})
    tiso = TODAY.isoformat()
    day = dt.date.fromisoformat
    for d in dramas:
        w = d["mdl"]["w"]
        recent = d["st"] != "released" or (
            d["start"] and period_end(d["start"], d["sp"]) >= TODAY - dt.timedelta(days=120))
        if not recent:
            history.pop(d["id"], None)
            continue
        h = history.get(d["id"]) or []
        if w and NOW - (details.get(d["id"]) or {}).get("fetched", 0) < 1.5 * DAY:
            h = [x for x in h if x[0] != tiso] + [[tiso, w]]
            history[d["id"]] = h = h[-40:]
        hot = src = None
        if len(h) >= 2 and (TODAY - day(h[-1][0])).days <= 3:
            last = h[-1]
            prev = [x for x in h[:-1] if 1 <= (day(last[0]) - day(x[0])).days <= 8]
            if prev:  # measured: gain over the past week (or as many days as we have)
                hot = (last[1] - prev[0][1]) / (day(last[0]) - day(prev[0][0])).days
                src = "m"
        if hot is None and w and d["st"] != "upcoming" and d["start"] and d["sp"] == "d":
            days = (TODAY - day(d["start"])).days
            if 0 <= days <= 60:  # estimate until a few days of history exist
                hot = w / (days + 14)
                src = "e"
        if hot and hot > 0:
            d["hot"] = round(hot, 1)
            d["hs"] = src
    save("history.json", history)
    log(f"Dramas in catalog: {len(dramas)}")

    # ---- Viki
    viki = None if os.environ.get("CDT_SKIP_VIKI") else fetch_viki()
    if viki:
        save("viki.json", viki)
    else:
        viki = load("viki.json", [])
    log(f"Viki: {len(viki)} {R['label']} series")
    v_native, v_en = viki_index(viki)
    used = set()
    # most-watched first, so when several dramas share a title the well-known one gets the match
    for d in sorted(dramas, key=lambda d: -(d["mdl"]["w"] or 0)):
        v = None
        for name in [d["nt"]] + d["_names"]:
            if name and has_cjk(name) and norm(name) in v_native:
                v = v_native[norm(name)]
                break
        if not v:
            v = v_en.get(norm(d["t"]))
            if v and d["st"] == "upcoming" and v.get("count"):
                v = None  # an unreleased drama cannot already have Viki reviews: different show
        if v and v["id"] in used:
            v = None
        if v:
            used.add(v["id"])
            d["viki"] = {"s": v["score"], "n": v["count"], "f": v["followers"], "u": v["url"],
                         "tr": v.get("trailers") or 0}
            if not d["img"]:
                d["img"] = v.get("poster")

    # ---- IMDb
    if not os.environ.get("CDT_SKIP_IMDB"):
        imdb_map = load("imdb_map.json", {})
        unmapped = sum(1 for d in dramas if d["id"] not in imdb_map)
        if (os.environ.get("CDT_FORCE_IMDB") or NOW - imdb_map.get("_built", 0) > 6.5 * DAY
                or unmapped > 150):
            for d in dramas:  # retry titles that had no match and are still recent
                if imdb_map.get(d["id"]) is None and d["st"] != "released":
                    imdb_map.pop(d["id"], None)
            try:
                build_imdb_map(dramas, imdb_map)
            except Exception as e:  # noqa: BLE001
                log("  ! IMDb title list failed:", e)
        ids = {v for k, v in imdb_map.items() if k != "_built" and v}
        ratings = fetch_imdb_ratings(ids)
        if ratings is not None:
            save("imdb_ratings.json", ratings)
        else:
            ratings = load("imdb_ratings.json", {})
        for d in dramas:
            tt = imdb_map.get(d["id"])
            if tt:
                r = ratings.get(tt) or [None, None]
                d["imdb"] = {"id": tt, "s": r[0], "n": r[1]}
        log(f"IMDb: {sum(1 for d in dramas if d.get('imdb'))} matched, "
            f"{sum(1 for d in dramas if (d.get('imdb') or {}).get('s'))} with ratings")

    # ---- where to watch in the US (JustWatch)
    jw = load("justwatch.json", {})
    if not os.environ.get("CDT_SKIP_JW"):
        try:
            refresh_justwatch(dramas, jw)
        except Exception as e:  # noqa: BLE001
            log("  ! JustWatch failed:", e)
    for d in dramas:
        offers = (jw.get(d["id"]) or {}).get("offers")
        if offers and d["st"] != "upcoming":
            d["us"] = offers
    log(f"JustWatch: {sum(1 for d in dramas if d.get('us'))} dramas streamable in the US")

    # ---- people index + "known for" (top works since 2020, by MDL watchers)
    ppl = {}
    credits = {}

    def person(key, name, link=None, img=None):
        p = ppl.setdefault(key, {"n": name})
        if link:
            p["l"] = link
        if img and "i" not in p and not img.endswith("/avatar.jpg"):
            p["i"] = img
        return key

    for d in dramas:
        d["cast"] = []
        for c in d.pop("_cast")[:6]:
            m = re.search(r"/people/(\d+)", c.get("s") or "")
            key = m.group(1) if m else "n:" + c["n"]
            person(key, c["n"], ("https://mydramalist.com" + c["s"]) if c.get("s") else None, c.get("i"))
            d["cast"].append(key)
            credits.setdefault(key, []).append((d, "a"))
        d["dir"] = []
        for n in d.pop("_dir")[:3]:
            d["dir"].append(person("d:" + n, n))
            credits.setdefault("d:" + n, []).append((d, "d"))
        d["wri"] = []
        for n in d.pop("_wri")[:4]:
            d["wri"].append(person("w:" + n, n))
            credits.setdefault("w:" + n, []).append((d, "w"))
        d.pop("_names")
        if not d["nt"]:
            d.pop("nt")
    for key, works in credits.items():
        recent = [(d["mdl"].get("w") or 0, d["id"]) for d, _ in works
                  if d["st"] != "upcoming" and (d.get("y") or 0) >= 2020]  # only things already out
        recent.sort(reverse=True)
        ppl[key]["w"] = [cid for _, cid in recent[:4]]
        ppl[key]["c"] = len(works)

    for d in dramas:
        scores = [x for x in (d["mdl"].get("s"), (d.get("viki") or {}).get("s"),
                              (d.get("imdb") or {}).get("s")) if x]
        d["avg"] = round(sum(scores) / len(scores), 2) if scores else None
        d["mdl"] = {k: v for k, v in d["mdl"].items() if v is not None}
        for k in [k for k, v in d.items() if v is None or v == [] or v == ""]:
            d.pop(k)

    dramas.sort(key=lambda d: -(d["mdl"].get("w") or 0))
    out = {
        "region": RKEY,
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline": min(first_seen.values()) if first_seen else TODAY.isoformat(),
        "dramas": dramas,
        "people": ppl,
    }
    save(f"{RKEY}.json", out, OUT)
    size = os.path.getsize(os.path.join(OUT, f"{RKEY}.json")) / 1e6
    by = {}
    for d in dramas:
        by[d["st"]] = by.get(d["st"], 0) + 1
    log(f"Wrote docs/data/{RKEY}.json ({size:.1f} MB): {by}")
    missing = sum(1 for cid in cands if cid not in details)
    if missing:
        log(f"Note: {missing} titles still awaiting full details; they fill in over the next runs.")


if __name__ == "__main__":
    try:
        only = [r for r in os.environ.get("CDT_REGIONS", "").split(",") if r in REGIONS]
        for region in only or list(REGIONS):
            build(region)
    except KeyboardInterrupt:
        sys.exit(1)
