"""音楽・映画ニュース更新スクリプト(APIキー不要)
  python update.py news      # ニュースとチャートを更新(毎日)
  python update.py releases  # ニューリリースを更新(金曜)
  python update.py           # すべて更新
どの実行でも docs/index.html を再生成します。
"""
import html, json, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests

JST = timezone(timedelta(hours=9))
DATA = Path("data"); DATA.mkdir(exist_ok=True)
DOCS = Path("docs"); DOCS.mkdir(exist_ok=True)
UA = {"User-Agent": "personal-news-app/1.0"}

# ---- 設定 ----
FEEDS = {
    "music": [("音楽ナタリー", "https://natalie.mu/music/feed/news")],
    "movie": [("映画ナタリー", "https://natalie.mu/eiga/feed/news"),
              ("映画.com", "https://eiga.com/rss/news/")],
}
MAX_NEWS = 30
SUMMARY_LEN = 110
GLOBAL_STOREFRONTS = ["us", "gb", "jp", "de", "fr", "br", "kr", "mx", "au", "ca"]
RELEASE_DAYS = 60  # 何日以内のリリースを新作とみなすか
GENRES = [("pop", "Pop"), ("indie", "インディー"), ("rock", "Rock"), ("hiphop", "Hip-Hop"), ("rnb", "R&B")]
GENRE_RULES = {
    "hiphop": ["hip-hop", "hip hop", "rap", "ヒップホップ", "ラップ"],
    "rnb": ["r&b", "soul", "ソウル"],
    "rock": ["rock", "ロック"],
    "indie": ["indie", "alternative", "インディ", "オルタナ"],
    "pop": ["pop", "ポップ"],
}
APPLE_HOSTS = ["https://rss.applemarketingtools.com", "https://rss.marketingtools.apple.com"]


# ---- 共通 ----
def now():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def clean(t):
    t = html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return re.sub(r"\s+", " ", t).strip()


# ---- ニュース ----
def fetch_news():
    for cat, feeds in FEEDS.items():
        items = []
        for source, url in feeds:
            try:
                for e in feedparser.parse(url).entries:
                    ts = e.get("published_parsed") or e.get("updated_parsed")
                    summ = clean(e.get("summary") or e.get("description"))
                    if summ == clean(e.get("title")):
                        summ = ""
                    if len(summ) > SUMMARY_LEN:
                        summ = summ[:SUMMARY_LEN] + "…"
                    items.append({"title": clean(e.get("title")), "summary": summ,
                                  "url": e.get("link", ""), "source": source,
                                  "time": time.mktime(ts) if ts else 0})
            except Exception as ex:
                print(f"[warn] {source}: {ex}")
        items.sort(key=lambda x: x["time"], reverse=True)
        if items:
            save(f"{cat}_news.json", items[:MAX_NEWS])


# ---- Apple Music(リリース・チャート) ----
def apple_feed(cc, entity, limit):
    for host in APPLE_HOSTS:
        try:
            r = requests.get(f"{host}/api/v2/{cc}/music/most-played/{limit}/{entity}.json",
                             headers=UA, timeout=20)
            r.raise_for_status()
            return r.json()["feed"]["results"]
        except Exception as ex:
            print(f"[warn] apple {cc} {entity}: {ex}")
    return []


def genre_keys(names):
    text = " ".join(names).lower()
    return [k for k, words in GENRE_RULES.items() if any(w in text for w in words)]


def to_item(a):
    names = [g.get("name", "") for g in a.get("genres", [])]
    return {"id": str(a.get("id", "")), "title": a.get("name", ""),
            "artist": a.get("artistName", ""), "date": a.get("releaseDate", "") or "",
            "url": a.get("url", ""),
            "image": (a.get("artworkUrl100") or "").replace("100x100bb", "300x300bb"),
            "genres": genre_keys(names),
            "genre_names": [n for n in names if n not in ("Music", "ミュージック")][:2]}


def fetch_releases():
    cutoff = (datetime.now(JST) - timedelta(days=RELEASE_DAYS)).strftime("%Y-%m-%d")

    def recent(storefronts):
        seen, out = set(), []
        for cc in storefronts:
            for a in apple_feed(cc, "albums", 100):
                it = to_item(a)
                if it["id"] in seen or it["date"] < cutoff:
                    continue
                seen.add(it["id"]); out.append(it)
        out.sort(key=lambda x: x["date"], reverse=True)
        return out[:120]

    old = load("music_releases.json", {}).get("regions", {})
    new = {"JP": recent(["jp"]), "US": recent(["us"]), "GLOBAL": recent(GLOBAL_STOREFRONTS)}
    regions = {k: (v or old.get(k, [])) for k, v in new.items()}
    if any(regions.values()):
        save("music_releases.json", {"updated": now(), "regions": regions})


def fetch_charts():
    old = load("music_charts.json", {}).get("regions", {})
    ch = {}
    for key, cc in (("JP", "jp"), ("US", "us")):
        ch[key] = [{**to_item(a), "rank": i + 1} for i, a in enumerate(apple_feed(cc, "songs", 50))]
    try:  # グローバル: Deezer世界チャート
        r = requests.get("https://api.deezer.com/chart/0/tracks", params={"limit": 50},
                         headers=UA, timeout=20)
        r.raise_for_status()
        ch["GLOBAL"] = [{"rank": i + 1, "title": t["title"], "artist": t["artist"]["name"],
                         "url": t["link"], "image": t["album"].get("cover_medium", ""),
                         "genre_names": []} for i, t in enumerate(r.json()["data"])]
    except Exception as ex:
        print(f"[warn] deezer chart: {ex}")
        ch["GLOBAL"] = []
    regions = {k: (v or old.get(k, [])) for k, v in ch.items()}
    if any(regions.values()):
        save("music_charts.json", {"updated": now(), "regions": regions})


# ---- スマホ用(PWA)ファイル ----
def make_png(path, n):
    import struct, zlib
    raw = bytearray()
    c = n / 2
    for y in range(n):
        raw.append(0)
        for x in range(n):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            raw += b"\xff\xff\xff" if n * .1 < d < n * .28 else b"\x1d\xb9\x54"
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def ensure_pwa_files():
    if not (DOCS / "icon-192.png").exists():
        make_png(DOCS / "icon-192.png", 192)
        make_png(DOCS / "icon-512.png", 512)
    (DOCS / "manifest.webmanifest").write_text(json.dumps({
        "name": "音楽・映画ニュース", "short_name": "ニュース", "start_url": "./", "scope": "./",
        "display": "standalone", "background_color": "#111111", "theme_color": "#1db954",
        "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }, ensure_ascii=False), "utf-8")
    (DOCS / "sw.js").write_text(
        'const C="news-v1";self.addEventListener("install",e=>self.skipWaiting());'
        'self.addEventListener("activate",e=>e.waitUntil(clients.claim()));'
        'self.addEventListener("fetch",e=>{if(e.request.method!=="GET")return;'
        'e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(C).then(x=>x.put(e.request,c));return r})'
        '.catch(()=>caches.match(e.request)))});', "utf-8")


# ---- ページ生成 ----
PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>音楽・映画ニュース</title>
<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#1db954">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="ニュース">
<style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#777;--line:#e5e5e5;--card:#f4f4f4;--acc:#1db954}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#999;--line:#2a2a2a;--card:#1c1c1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif;
padding-top:env(safe-area-inset-top);padding-bottom:calc(64px + env(safe-area-inset-bottom))}
main{max-width:720px;margin:auto;padding:12px 16px}
h1{font-size:18px;margin:4px 0 0}.upd{font-size:11px;color:var(--mut);margin:2px 0 10px}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:var(--bg);border-top:1px solid var(--line);
padding-bottom:env(safe-area-inset-bottom)}
nav button{flex:1;border:0;background:none;color:var(--mut);font-size:13px;padding:12px 0;font-weight:600}
nav button.on{color:var(--acc)}
.seg{display:flex;gap:6px;margin:8px 0}.seg button{flex:1;border:1px solid var(--line);background:none;color:var(--fg);
border-radius:8px;padding:9px 0;font-size:14px}.seg button.on{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
.chips{display:flex;gap:6px;overflow-x:auto;margin:8px 0 12px;padding-bottom:4px}
.chips button{flex-shrink:0;border:1px solid var(--line);background:none;color:var(--fg);border-radius:16px;padding:6px 14px;font-size:13px}
.chips button.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
@media(min-width:520px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
a{color:inherit;text-decoration:none}
.card img,.ph{width:100%;aspect-ratio:1;border-radius:8px;background:var(--card);display:block}
.card b{display:block;font-size:13px;margin-top:6px;line-height:1.35}
small{display:block;color:var(--mut);font-size:11px;margin-top:2px;line-height:1.4}
.row{display:flex;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.rank{width:26px;text-align:center;font-weight:700;color:var(--mut)}
.row img{width:48px;height:48px;border-radius:6px;background:var(--card)}
.row b{font-size:14px;line-height:1.3}
.news{padding:12px 0;border-bottom:1px solid var(--line)}
.news a.t{font-weight:600;font-size:15px;line-height:1.4;display:block}
.news p{margin:6px 0;font-size:13px;line-height:1.6;color:var(--fg);opacity:.85}
.news .src{font-size:12px;color:var(--acc)}
.empty{color:var(--mut);font-size:13px;padding:20px 0;line-height:1.6}
</style></head><body><main><h1>🎵🎬 音楽・映画ニュース</h1><div class="upd" id="upd"></div><div id="view"></div></main>
<nav id="tabs"></nav>
<script>
const D=__DATA__;
const st={tab:"rel",reg:"JP",genre:"all",news:"music"};
const REG=[["JP","日本"],["US","アメリカ"],["GLOBAL","グローバル"]];
const TABS=[["rel","ニューリリース"],["chart","チャート"],["news","ニュース"]];
function el(t,a,...k){const e=document.createElement(t);for(const x in a||{}){if(x==="class")e.className=a[x];else if(x==="onclick")e.onclick=a[x];else e.setAttribute(x,a[x]);}
for(const c of k){if(c==null)continue;e.append(c.nodeType?c:document.createTextNode(c));}return e;}
const safe=u=>/^https?:\\/\\//.test(u||"")?u:"#";
function seg(cls,opts,cur,fn){const w=el("div",{class:cls});opts.forEach(([v,l])=>w.append(el("button",{class:v===cur?"on":"",onclick:()=>fn(v)},l)));return w;}
function img(u,cls){return u?el("img",{src:u,alt:"",loading:"lazy"}):el("div",{class:cls||"ph"});}
function empty(t){return el("div",{class:"empty"},t);}
function rel(v){
  v.append(seg("seg",REG,st.reg,x=>{st.reg=x;draw()}));
  v.append(seg("chips",[["all","すべて"],...D.genres],st.genre,x=>{st.genre=x;draw()}));
  const items=((D.releases.regions||{})[st.reg]||[]).filter(i=>st.genre==="all"||(i.genres||[]).includes(st.genre));
  if(!items.length){v.append(empty("該当する新作はありません。人気アルバムのうち直近"+D.days+"日以内にリリースされた作品が対象です。"));return;}
  const g=el("div",{class:"grid"});
  items.forEach(i=>g.append(el("a",{class:"card",href:safe(i.url),target:"_blank",rel:"noopener"},img(i.image),
    el("b",null,i.title),el("small",null,i.artist),el("small",null,[i.date,...(i.genre_names||[])].filter(Boolean).join(" · ")))));
  v.append(g);
}
function chart(v){
  v.append(seg("seg",REG,st.reg,x=>{st.reg=x;draw()}));
  const items=(D.charts.regions||{})[st.reg]||[];
  if(!items.length){v.append(empty("データがありません。"));return;}
  items.forEach(i=>v.append(el("a",{class:"row",href:safe(i.url),target:"_blank",rel:"noopener"},
    el("div",{class:"rank"},String(i.rank)),img(i.image,"ph"),el("div",null,el("b",null,i.title),el("small",null,i.artist)))));
}
function news(v){
  v.append(seg("seg",[["music","音楽"],["movie","映画"]],st.news,x=>{st.news=x;draw()}));
  const items=D.news[st.news]||[];
  if(!items.length){v.append(empty("データがありません。"));return;}
  items.forEach(i=>{
    const d=i.time?new Date(i.time*1000).toLocaleString("ja-JP",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):"";
    v.append(el("div",{class:"news"},el("a",{class:"t",href:safe(i.url),target:"_blank",rel:"noopener"},i.title),
      i.summary?el("p",null,i.summary):null,
      el("small",null,i.source+(d?" · "+d:"")+" "),
      el("a",{class:"src",href:safe(i.url),target:"_blank",rel:"noopener"},"元記事を読む ↗")));
  });
}
function draw(){
  const v=document.getElementById("view");v.replaceChildren();
  const u={rel:D.releases.updated,chart:D.charts.updated,news:D.updated}[st.tab];
  document.getElementById("upd").textContent=(st.tab==="rel"?"毎週金曜に更新 · ":"")+"最終更新 "+(u||"-");
  ({rel,chart,news})[st.tab](v);
  const t=document.getElementById("tabs");t.replaceChildren();
  TABS.forEach(([k,l])=>t.append(el("button",{class:k===st.tab?"on":"",onclick:()=>{st.tab=k;draw();scrollTo(0,0)}},l)));
}
draw();
if("serviceWorker"in navigator)navigator.serviceWorker.register("sw.js");
</script></body></html>"""


def render():
    payload = {
        "updated": now(), "genres": GENRES, "days": RELEASE_DAYS,
        "news": {"music": load("music_news.json", []), "movie": load("movie_news.json", [])},
        "releases": load("music_releases.json", {"updated": "", "regions": {}}),
        "charts": load("music_charts.json", {"updated": "", "regions": {}}),
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    ensure_pwa_files()
    (DOCS / "index.html").write_text(PAGE.replace("__DATA__", data), "utf-8")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("news", "all"):
        fetch_news(); fetch_charts()
    if mode in ("releases", "all"):
        fetch_releases()
    render()
