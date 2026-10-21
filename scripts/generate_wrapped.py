#!/usr/bin/env python3
"""
Developer Wrapped generator.

Reads your GitHub activity (including private repos if the token allows it)
and writes ONE self-contained SVG dashboard: assets/dev-wrapped.svg

Env vars:
  GH_TOKEN      Personal access token (classic: repo + read:user)   [required unless --demo]
  GH_USER       Your GitHub username                                 [required unless --demo]
  YEAR          Year to report on (default: current year)
  TZ_OFFSET     Your UTC offset in hours, e.g. 5.5 (default 5.5)
  MASK_PRIVATE  "true" (default) shows private repos as "private"
  MAX_COMMITS   Max commits to inspect for LOC stats (default 400)
  OUT           Output path (default assets/dev-wrapped.svg)

Usage:
  python scripts/generate_wrapped.py          # real data
  python scripts/generate_wrapped.py --demo   # fake data, to preview the design
"""
import json
import math
import os
import random
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape

API = "https://api.github.com"

EXT_LANG = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "CSS", ".java": "Java", ".kt": "Kotlin", ".php": "PHP",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".swift": "Swift", ".dart": "Dart", ".sql": "SQL", ".sh": "Shell",
    ".vue": "Vue", ".md": "Markdown", ".ipynb": "Jupyter",
}
SKIP_PARTS = ("node_modules/", "dist/", "build/", ".min.", "package-lock.json",
              "yarn.lock", "pnpm-lock.yaml", ".lock", "vendor/")

PALETTE = ["#58a6ff", "#3fb950", "#d29922", "#f778ba", "#a371f7", "#ff7b72", "#79c0ff"]
HEAT = ["#21262d", "#0e4429", "#006d32", "#26a641", "#39d353"]
BG, CARD, TXT, MUTED = "#0d1117", "#161b22", "#e6edf3", "#8b949e"
FONT = "'Segoe UI', Helvetica, Arial, sans-serif"


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _request(url, token, data=None):
    req = urllib.request.Request(url, data=data)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "dev-wrapped")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def rest(path, token, params=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        return _request(url, token)
    except urllib.error.HTTPError:
        return None


GQL = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from,to:$to){
      totalCommitContributions totalIssueContributions
      totalPullRequestContributions totalPullRequestReviewContributions
      contributionCalendar{ totalContributions
        weeks{ contributionDays{ date contributionCount weekday } } }
      commitContributionsByRepository(maxRepositories:100){
        repository{ nameWithOwner isPrivate } contributions{ totalCount } }
    }
  }
}"""


def fetch_real(user, token, year, tz, max_commits):
    start = f"{year}-01-01T00:00:00Z"
    end_day = min(date.today(), date(year, 12, 31))
    end = f"{end_day.isoformat()}T23:59:59Z"
    payload = json.dumps({"query": GQL, "variables": {"login": user, "from": start, "to": end}}).encode()
    res = _request(API + "/graphql", token, payload)
    if "errors" in res:
        sys.exit(f"GraphQL error: {res['errors']}")
    cc = res["data"]["user"]["contributionsCollection"]

    days = {}
    weeks = []
    for w in cc["contributionCalendar"]["weeks"]:
        row = []
        for d in w["contributionDays"]:
            days[d["date"]] = d["contributionCount"]
            row.append((d["date"], d["contributionCount"], d["weekday"]))
        weeks.append(row)

    types = {
        "Commits": cc["totalCommitContributions"],
        "Pull requests": cc["totalPullRequestContributions"],
        "Issues": cc["totalIssueContributions"],
        "Reviews": cc["totalPullRequestReviewContributions"],
    }

    repos = sorted(cc["commitContributionsByRepository"],
                   key=lambda r: -r["contributions"]["totalCount"])
    commits = []
    for r in repos:
        if len(commits) >= max_commits:
            break
        name = r["repository"]["nameWithOwner"]
        private = r["repository"]["isPrivate"]
        page = 1
        while len(commits) < max_commits:
            lst = rest(f"/repos/{name}/commits", token, {
                "author": user, "since": start, "until": end, "per_page": 100, "page": page})
            if not lst:
                break
            for c in lst:
                if len(commits) >= max_commits:
                    break
                if len(c.get("parents", [])) > 1:          # skip merge commits
                    continue
                d = rest(f"/repos/{name}/commits/{c['sha']}", token)
                if not d:
                    continue
                ts = datetime.fromisoformat(d["commit"]["author"]["date"].replace("Z", "+00:00"))
                ts = ts.astimezone(timezone(timedelta(hours=tz)))
                langs = Counter()
                for f in d.get("files", []):
                    fn = f["filename"]
                    if any(p in fn for p in SKIP_PARTS):
                        continue
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in EXT_LANG:
                        langs[EXT_LANG[ext]] += f["additions"] + f["deletions"]
                commits.append({
                    "ts": ts, "add": d["stats"]["additions"], "del": d["stats"]["deletions"],
                    "repo": name, "private": private, "langs": langs})
            if len(lst) < 100:
                break
            page += 1
    return days, weeks, types, commits


def fetch_demo(year):
    random.seed(7)
    d = date(year, 1, 1)
    last = date(year, 12, 31)
    days, weeks, row = {}, [], []
    pad = (d.weekday() + 1) % 7          # GitHub weeks start on Sunday
    row = [(None, 0, i) for i in range(pad)]
    month_weight = [1, 1.2, 2, 2, .4, .6, 1.1, .5, .2, .15, .6, .1]
    while d <= last:
        wd = (d.weekday() + 1) % 7
        p = 0.55 * month_weight[d.month - 1] * (0.5 if wd in (0, 6) else 1)
        n = random.choice([1, 2, 3, 5, 8, 14]) if random.random() < min(p, .95) else 0
        if d > date.today():
            n = 0
        days[d.isoformat()] = n
        row.append((d.isoformat(), n, wd))
        if wd == 6:
            weeks.append(row)
            row = []
        d += timedelta(days=1)
    if row:
        weeks.append(row)
    commits = []
    for _ in range(300):
        dd = random.choice([k for k, v in days.items() if v])
        hour = random.choice([1, 2, 3, 21, 22, 23, 14, 15, 16, 19, 20, 0, 10])
        ts = datetime.fromisoformat(dd).replace(hour=hour, minute=random.randint(0, 59),
                                                tzinfo=timezone.utc)
        a, dl = int(random.expovariate(1 / 120)), int(random.expovariate(1 / 60))
        lang = random.choices(["JavaScript", "TypeScript", "Python", "CSS", "HTML"],
                              [5, 4, 3, 2, 1])[0]
        repo = random.choice([("you/language-passport", False), ("you/cutestuff-site", False),
                              ("you/infra-shield", True), ("you/trading-bot", True)])
        commits.append({"ts": ts, "add": a, "del": dl, "repo": repo[0], "private": repo[1],
                        "langs": Counter({lang: a + dl})})
    types = {"Commits": 820, "Pull requests": 64, "Issues": 31, "Reviews": 12}
    return days, weeks, types, commits


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
def compute(days, commits):
    today = date.today()
    keys = sorted(k for k in days if date.fromisoformat(k) <= today)
    counts = [days[k] for k in keys]
    total = sum(days.values())
    active = sum(1 for c in counts if c)

    streak = best_streak = gap = best_gap = 0
    for c in counts:
        if c:
            streak += 1
            gap = 0
        else:
            gap += 1
            streak = 0
        best_streak, best_gap = max(best_streak, streak), max(best_gap, gap)

    busiest = max(days.items(), key=lambda kv: kv[1]) if days else ("", 0)
    by_month, by_wd = [0] * 12, [0] * 7
    for k, v in days.items():
        dd = date.fromisoformat(k)
        by_month[dd.month - 1] += v
        by_wd[dd.weekday()] += v                     # Mon=0 … Sun=6
    weekend = (by_wd[5] + by_wd[6]) / total * 100 if total else 0

    by_hour = [0] * 24
    langs, repos = Counter(), Counter()
    adds = dels = 0
    for c in commits:
        by_hour[c["ts"].hour] += 1
        adds += c["add"]
        dels += c["del"]
        langs.update(c["langs"])
        repos[(c["repo"], c["private"])] += c["add"] + c["del"]
    typ_add = int(statistics.median([c["add"] for c in commits])) if commits else 0
    typ_del = int(statistics.median([c["del"] for c in commits])) if commits else 0
    per_day = int((adds + dels) / active) if active else 0
    return dict(total=total, active=active, streak=best_streak, gap=best_gap, busiest=busiest,
                by_month=by_month, by_wd=by_wd, weekend=weekend, by_hour=by_hour,
                langs=langs, repos=repos, adds=adds, dels=dels,
                typ=(typ_add, typ_del), per_day=per_day, n_commits=len(commits))


# --------------------------------------------------------------------------- #
# SVG helpers
# --------------------------------------------------------------------------- #
def fmt(n):
    n = int(n)
    return f"{n/1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def text(x, y, s, size=13, fill=TXT, weight=400, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
            f'text-anchor="{anchor}">{escape(str(s))}</text>')


def card(x, y, w, h, title=None, sub=None, right_sub=False):
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{CARD}"/>'
    if title:
        s += text(x + 16, y + 26, title, 14, TXT, 600)
    if sub and right_sub:
        s += text(x + w - 16, y + 26, sub, 11, MUTED, 400, "end")
    elif sub:
        s += text(x + 16, y + 43, sub, 11, MUTED)
    return s


def tile(x, y, w, h, value, label, color=TXT):
    return (card(x, y, w, h) + text(x + w / 2, y + 42, value, 26, color, 700, "middle")
            + text(x + w / 2, y + 64, label, 11, MUTED, 400, "middle"))


def bars(x, y, w, h, labels, values, color, hi=None, every=1, show_vals=True):
    n, m = len(values), max(max(values), 1)
    gap = 6
    bw = (w - gap * (n - 1)) / n
    out = ""
    top = max(values)
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = (v / m) * (h - 22)
        bx = x + i * (bw + gap)
        col = hi if (hi and v == top and v > 0) else color
        out += f'<rect x="{bx:.1f}" y="{y + h - 18 - bh:.1f}" width="{bw:.1f}" height="{max(bh, 1):.1f}" rx="3" fill="{col}"/>'
        if show_vals and v and bw > 18:
            out += text(f"{bx + bw / 2:.1f}", f"{y + h - 22 - bh:.1f}", v, 10, MUTED, 400, "middle")
        if i % every == 0:
            out += text(f"{bx + bw / 2:.1f}", y + h - 3, lab, 10, MUTED, 400, "middle")
    return out


def donut(cx, cy, r1, r0, items, total):
    out, a = "", -math.pi / 2
    if len(items) == 1:
        return (f'<circle cx="{cx}" cy="{cy}" r="{(r0 + r1) / 2}" fill="none" '
                f'stroke="{PALETTE[0]}" stroke-width="{r1 - r0}"/>')
    for i, (_, v) in enumerate(items):
        da = v / total * 2 * math.pi
        b = a + da
        large = 1 if da > math.pi else 0
        p = lambda r, ang: (cx + r * math.cos(ang), cy + r * math.sin(ang))
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p(r1, a), p(r1, b), p(r0, b), p(r0, a)
        out += (f'<path d="M{x0:.1f},{y0:.1f} A{r1},{r1} 0 {large} 1 {x1:.1f},{y1:.1f} '
                f'L{x2:.1f},{y2:.1f} A{r0},{r0} 0 {large} 0 {x3:.1f},{y3:.1f}Z" '
                f'fill="{PALETTE[i % len(PALETTE)]}"/>')
        a = b
    return out


def donut_card(x, y, w, h, title, sub, items):
    s = card(x, y, w, h, title, sub)
    total = sum(v for _, v in items) or 1
    if not items:
        return s + text(x + w / 2, y + h / 2, "No data yet", 12, MUTED, 400, "middle")
    s += donut(x + 78, y + 142, 62, 36, items, total)
    for i, (name, v) in enumerate(items):
        yy = y + 78 + i * 24
        s += f'<circle cx="{x + 164}" cy="{yy - 4}" r="5" fill="{PALETTE[i % len(PALETTE)]}"/>'
        s += text(x + 176, yy, (name[:11] + "…") if len(name) > 12 else name, 12, TXT)
        s += text(x + w - 16, yy, f"{v / total * 100:.0f}%", 12, MUTED, 400, "end")
    return s


def heatmap(x, y, weeks):
    cell, gap = 12, 3
    mx = max([c for w in weeks for _, c, _ in w] + [1])
    out, last_month = "", None
    for wi, w in enumerate(weeks):
        for dstr, c, wd in w:
            if dstr is None:
                continue
            lvl = 0 if c == 0 else min(4, math.ceil(c / mx * 4))
            cx_, cy_ = x + wi * (cell + gap), y + 16 + wd * (cell + gap)
            out += (f'<rect x="{cx_}" y="{cy_}" width="{cell}" height="{cell}" rx="2" '
                    f'fill="{HEAT[lvl]}"><title>{dstr}: {c}</title></rect>')
            m = int(dstr[5:7])
            if m != last_month and int(dstr[8:10]) <= 7:
                out += text(cx_, y + 8, date(2000, m, 1).strftime("%b"), 10, MUTED)
                last_month = m
    return out


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def build_svg(user, year, types, weeks, s, mask):
    W, H, P, G = 900, 1110, 24, 12
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="{FONT}"><rect width="{W}" height="{H}" rx="14" fill="{BG}"/>']
    # o.append(text(W / 2, 44, f"{user}'s Developer Wrapped {year}", 24, TXT, 700, "middle"))
    # o.append(text(W / 2, 66, "Auto-generated from my GitHub activity, private work included",
    #               12, MUTED, 400, "middle"))

    # row 1: tiles
    tw = (W - 2 * P - 4 * G) / 5
    tiles = [(fmt(s["total"]), "Contributions", "#3fb950"), (s["active"], "Active days", TXT),
             (s["streak"], "Longest streak (days)", "#3fb950"),
             (s["gap"], "Longest gap (days)", "#ff7b72"), (f"{s['weekend']:.0f}%", "Weekend activity", "#58a6ff")]
    for i, (v, l, c) in enumerate(tiles):
        o.append(tile(P + i * (tw + G), 86, tw, 80, v, l, c))

    # # calendar
    # o.append(card(P, 182, W - 2 * P, 164, "Contribution Calendar",
    #              f"Busiest day: {s['busiest'][1]} contributions on {s['busiest'][0]}", right_sub=True))
    # o.append(heatmap(P + 16, 220, weeks))

    # LOC row
    langs = [(k, v) for k, v in s["langs"].most_common(6)]
    rp = []
    for (name, priv), v in s["repos"].most_common(6):
        rp.append(("private" if (priv and mask) else name.split("/")[-1], v))
    cw = 300
    o.append(donut_card(P, 354, cw, 250, "Most Used Languages", "By lines changed", langs))
    o.append(donut_card(P + cw + G, 354, cw, 250, "Most Active Repositories", "By lines changed", rp))
    sx, sw = P + 2 * (cw + G), W - P - (P + 2 * (cw + G))
    o.append(card(sx, 354, sw, 250, "Lines of Code"))
    o.append(text(sx + 16, 414, f"+{fmt(s['adds'])}", 24, "#3fb950", 700))
    o.append(text(sx + 16, 432, "lines added", 11, MUTED))
    o.append(text(sx + 16, 468, f"-{fmt(s['dels'])}", 24, "#ff7b72", 700))
    o.append(text(sx + 16, 486, "lines deleted", 11, MUTED))
    o.append(text(sx + 16, 522, f"{s['typ'][0]} / {s['typ'][1]}", 20, TXT, 700))
    o.append(text(sx + 16, 540, "typical commit (+ / -)", 11, MUTED))
    o.append(text(sx + 16, 576, fmt(s["per_day"]), 20, TXT, 700))
    o.append(text(sx + 16, 594, "lines changed / active day", 11, MUTED))

    # breakdown by type + month
    o.append(card(P, 620, 280, 226, "Contributions by Type"))
    tmax = max(max(types.values()), 1)
    for i, (k, v) in enumerate(types.items()):
        yy = 670 + i * 40
        o.append(text(P + 16, yy, k, 12, TXT))
        o.append(f'<rect x="{P + 16}" y="{yy + 8}" width="248" height="8" rx="4" fill="#21262d"/>')
        o.append(f'<rect x="{P + 16}" y="{yy + 8}" width="{max(248 * v / tmax, 3):.0f}" height="8" rx="4" '
                 f'fill="{PALETTE[i]}"/>')
        o.append(text(P + 264, yy, fmt(v), 12, MUTED, 400, "end"))
    mx = P + 280 + G
    o.append(card(mx, 620, W - P - mx, 226, "Contributions by Month"))
    months = [date(2000, m, 1).strftime("%b") for m in range(1, 13)]
    o.append(bars(mx + 16, 672, W - P - mx - 32, 166, months, s["by_month"], "#58a6ff", "#3fb950"))

    # weekday + hour
    o.append(card(P, 862, 400, 200, "Contributions by Day"))
    o.append(bars(P + 16, 902, 368, 152, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                  s["by_wd"], "#58a6ff", "#3fb950"))
    hx = P + 400 + G
    o.append(card(hx, 862, W - P - hx, 200, "Contributions by Time of Day",
                  "Commit hours, local time"))
    o.append(bars(hx + 16, 916, W - P - hx - 32, 138, [f"{h:02d}" for h in range(24)],
                  s["by_hour"], "#a371f7", "#3fb950", every=3, show_vals=False))

    # o.append(text(W / 2, H - 20, "Generated by scripts/generate_wrapped.py", 11, MUTED, 400, "middle"))
    o.append("</svg>")
    return "".join(o)


def main():
    demo = "--demo" in sys.argv
    year = int(os.getenv("YEAR", date.today().year))
    tz = float(os.getenv("TZ_OFFSET", "5.5"))
    mask = os.getenv("MASK_PRIVATE", "true").lower() == "true"
    out_path = os.getenv("OUT", "assets/dev-wrapped.svg")
    user = os.getenv("GH_USER", "you")

    if demo:
        days, weeks, types, commits = fetch_demo(year)
    else:
        token = os.getenv("GH_TOKEN")
        if not token or user == "you":
            sys.exit("Set GH_TOKEN and GH_USER (or run with --demo).")
        days, weeks, types, commits = fetch_real(user, token, year, tz,
                                                 int(os.getenv("MAX_COMMITS", "400")))
    stats = compute(days, commits)
    svg = build_svg(user, year, types, weeks, stats, mask)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Wrote {out_path}  ({stats['total']} contributions, {stats['n_commits']} commits analysed)")


if __name__ == "__main__":
    main()
