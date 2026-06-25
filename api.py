#!/usr/bin/env python3
"""Read-only API shim over whitespace.db for the explorer frontend.

Glue only. The pipeline (whitespace_tracker.py) does the classification and
scoring; this just shapes rows for the UI. Industries are the pipeline's
sub-domains (the real classified unit). Two fields the UI filters on are not
stored by the pipeline yet, so they are derived here and clearly marked:
ai_native and business_model. Time window, source, and traction are real.

Run: uvicorn api:app --reload --port 8000
"""

import datetime as dt
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from whitespace_tracker import CLASSIFIER_MODEL, load_dotenv, score_whitespace

load_dotenv()  # make ANTHROPIC_API_KEY available to the thesis endpoint

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whitespace.db")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Stable source order drives the legend and the color assignment in the UI.
# Ordered from most validated (YC, lagging) to earliest signal (arXiv, leading).
SOURCES = [
    {"key": "yc", "label": "Y Combinator", "tier": "lagging"},
    {"key": "product_hunt", "label": "Product Hunt", "tier": "mid"},
    {"key": "betalist", "label": "BetaList", "tier": "mid"},
    {"key": "show_hn", "label": "Show HN", "tier": "leading"},
    {"key": "github", "label": "GitHub", "tier": "leading"},
    {"key": "huggingface", "label": "Hugging Face", "tier": "leading"},
    {"key": "arxiv", "label": "arXiv", "tier": "leading"},
]
SOURCE_ORDER = {s["key"]: i for i, s in enumerate(SOURCES)}

WINDOWS = [
    {"key": "all", "label": "All time", "days": None},
    {"key": "7d", "label": "Last 7 days", "days": 7},
    {"key": "30d", "label": "Last 30 days", "days": 30},
    {"key": "90d", "label": "Last 90 days", "days": 90},
    {"key": "12m", "label": "Last 12 months", "days": 365},
]
WINDOW_DAYS = {w["key"]: w["days"] for w in WINDOWS}

BUSINESS_MODELS = ["Infrastructure/API", "Application", "Consumer", "Hardware"]

# Derivation tables for business_model. Transparent and display-only, not scoring.
_HARDWARE_SUBS = {
    "inference chips", "ai chip interconnect", "datacenter cooling", "robotics and humanoids",
    "physical ai data", "autonomous vehicles", "drones and defense autonomy", "space and satellites",
    "battery and energy storage", "nuclear and fusion", "energy for data centers", "grid software",
    "defense tech",
}
_INFRA_SUBS = {
    "ai agent infrastructure", "vector and retrieval infra", "fintech infrastructure",
    "ai devops and sre", "ai data engineering", "llm observability and evals", "ai security",
    "on-device and edge ai", "synthetic data", "ai chip interconnect",
}
_AI_RE = re.compile(
    r"\b(ai|a\.i\.|ml|llm|llms|gpt|genai|generative|agent|agentic|neural|"
    r"machine learning|deep learning|transformer|diffusion|model|copilot|chatbot)\b",
    re.I,
)
_INFRA_RE = re.compile(r"\b(api|sdk|infrastructure|platform for developers|developer tool|self-hosted)\b", re.I)
_CONSUMER_RE = re.compile(r"\b(consumer|personal|for everyone|creator|your life|everyday)\b", re.I)


def derive_ai_native(name, description, subdomain):
    text = f"{name or ''} {description or ''}"
    if (subdomain or "").startswith("ai ") or "ai" in (subdomain or "").split():
        return True
    return bool(_AI_RE.search(text))


def derive_business_model(name, description, subdomain):
    text = f"{name or ''} {description or ''}"
    if subdomain in _HARDWARE_SUBS:
        return "Hardware"
    if subdomain in _INFRA_SUBS or _INFRA_RE.search(text):
        return "Infrastructure/API"
    if _CONSUMER_RE.search(text):
        return "Consumer"
    return "Application"


app = FastAPI(title="Whitespace explorer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _conn():
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=3000")  # wait, do not error, if a writer holds the db
    return c


def _cutoff(window):
    days = WINDOW_DAYS.get(window)
    if not days:
        return None
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def _base_rows(c, window):
    """Joined product+appearance rows, already filtered by the time window."""
    # subcategory may not exist yet (it is added by subcategorize.py), so select
    # it only if present and otherwise return NULL in its place.
    has_subcat = any(r["name"] == "subcategory" for r in c.execute("PRAGMA table_info(products)"))
    subcol = "p.subcategory" if has_subcat else "NULL AS subcategory"
    q = (
        f"SELECT p.id, p.name, p.url, p.description, p.subdomain, {subcol}, "
        "a.source, a.tier, a.seen_at, a.raw_signal "
        "FROM products p JOIN appearances a ON a.product_id = p.id "
        "WHERE p.subdomain IS NOT NULL AND p.subdomain != 'uncategorized'"
    )
    params = []
    cutoff = _cutoff(window)
    if cutoff:
        q += " AND a.seen_at IS NOT NULL AND a.seen_at >= ?"
        params.append(cutoff)
    return c.execute(q, params).fetchall()


def _passes(row, ai_native, business_model):
    if ai_native == "ai" and not derive_ai_native(row["name"], row["description"], row["subdomain"]):
        return False
    if ai_native == "non" and derive_ai_native(row["name"], row["description"], row["subdomain"]):
        return False
    if business_model and business_model != "all":
        if derive_business_model(row["name"], row["description"], row["subdomain"]) != business_model:
            return False
    return True


def _momentum():
    """Per sub-domain appearance change, last 7 days vs the prior 7. Sort metric."""
    today = dt.date.today()
    wk1 = (today - dt.timedelta(days=7)).isoformat()
    wk2 = (today - dt.timedelta(days=14)).isoformat()
    today_s = today.isoformat()
    out = {}
    with _conn() as c:
        rows = c.execute(
            """
            SELECT p.subdomain AS sub,
                   SUM(CASE WHEN a.seen_at >= ? AND a.seen_at <= ? THEN 1 ELSE 0 END) AS recent,
                   SUM(CASE WHEN a.seen_at >= ? AND a.seen_at <  ? THEN 1 ELSE 0 END) AS prior
            FROM appearances a JOIN products p ON p.id = a.product_id
            WHERE p.subdomain IS NOT NULL AND p.subdomain != 'uncategorized' AND a.seen_at IS NOT NULL
            GROUP BY p.subdomain
            """,
            (wk1, today_s, wk2, wk1),
        ).fetchall()
    for r in rows:
        out[r["sub"]] = {"recent": r["recent"] or 0, "prior": r["prior"] or 0,
                         "momentum": (r["recent"] or 0) - (r["prior"] or 0)}
    return out


@app.get("/api/meta")
def meta():
    with _conn() as c:
        rng = c.execute("SELECT MIN(seen_at) lo, MAX(seen_at) hi FROM appearances WHERE seen_at IS NOT NULL").fetchone()
    return {
        "sources": SOURCES,
        "windows": WINDOWS,
        "ai_native": [
            {"key": "all", "label": "All companies"},
            {"key": "ai", "label": "AI-native only"},
            {"key": "non", "label": "Non AI-native"},
        ],
        "business_models": BUSINESS_MODELS,
        "date_range": {"min": rng["lo"], "max": rng["hi"]},
    }


@app.get("/api/industries")
def industries(window: str = "all", ai_native: str = "all", business_model: str = "all"):
    try:
        with _conn() as c:
            rows = _base_rows(c, window)
    except sqlite3.Error:
        return []
    # industry -> source -> set(product_id), so multi-source companies are not
    # double counted within a single source segment.
    agg = {}
    for r in rows:
        if not _passes(r, ai_native, business_model):
            continue
        sub = r["subdomain"]
        agg.setdefault(sub, {}).setdefault(r["source"], set()).add(r["id"])
    momentum = _momentum()
    out = []
    for sub, by_src in agg.items():
        counts = {src: len(ids) for src, ids in by_src.items()}
        total = sum(counts.values())
        mom = momentum.get(sub, {"recent": 0, "prior": 0, "momentum": 0})
        out.append({
            "industry": sub,
            "total": total,
            "by_source": counts,
            "recent": mom["recent"],
            "prior": mom["prior"],
            "momentum": mom["momentum"],
        })
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


# Generic words to ignore when deriving subcategories, so the tags are the real
# sub-themes inside a category, not "ai platform tool" boilerplate.
_SUB_STOP = set(
    (
        "a an and are as at be but by for from has have how in into is it its of on or "
        "that the their them they this to was were will with your you we our us about your "
        "more most than then so too very can may might just only also let lets get gets "
        "getting make makes making made build building builds built create creating creates "
        "created help helps helping enable enables enabling power powers powered driven based "
        "first native new now today turn turns one all any each "
        "ai ml llm llms gen genai generative model models app apps application applications "
        "platform platforms tool tools toolkit software system systems solution solutions "
        "service services product products company companies startup startups open source "
        "opensource api apis sdk web online cloud saas data "
        "smart intelligent simple easy fast faster best better real realtime time automatic "
        "automated automatically automation autonomous seamless seamlessly instantly modern "
        "team teams business businesses enterprise user users customer customers people human "
        "humans world way ways work works working using used uses run runs running "
        "artificial intelligence use via propose proposes proposed approach approaches "
        "method methods novel paper papers results experiments experimental benchmark "
        "benchmarks demonstrate demonstrates show shows showing present presents presented "
        "leverage leverages task tasks performance designed develop developed "
        "across every within around beyond multiple various including general scale scales "
        "like multi really actually want need needs lets able etc plus full set "
        "python typescript javascript rust golang java ruby node react"
    ).split()
)


def derive_subcategories(companies, subdomain):
    """Salient sub-theme tags per company, from name plus description.

    There is no fixed taxonomy at this level, so tags are the most common content
    phrases (unigrams and bigrams) across the category, with generic startup and
    AI words and the category name itself filtered out. Returns {company_id:
    [tags]}; the client aggregates these into a facet with counts.
    """
    sub_words = set(subdomain.lower().replace("-", " ").split())
    sub_stems = [w for w in sub_words if len(w) >= 4]

    def keep(t):
        # drop generic words, the category words, and their variants (agent ->
        # agents, agentic), so tags are the real sub-themes.
        if t in _SUB_STOP or t in sub_words:
            return False
        if len(t) >= 4 and any(t.startswith(s) or s.startswith(t) for s in sub_stems):
            return False
        return True

    company_phrases = {}
    phrase_companies = defaultdict(set)
    for c in companies:
        text = f"{c.get('name', '')} {c.get('description', '')}".lower()
        toks = [t for t in re.findall(r"[a-z][a-z0-9+#]{2,}", text) if keep(t)]
        phrases = set(toks)
        for i in range(len(toks) - 1):
            phrases.add(toks[i] + " " + toks[i + 1])
        company_phrases[c["id"]] = phrases
        for p in phrases:
            phrase_companies[p].add(c["id"])

    scored = []
    for p, cset in phrase_companies.items():
        cnt = len(cset)
        if cnt < 3:
            continue
        is_bigram = " " in p
        scored.append((cnt * (1.7 if is_bigram else 1.0), cnt, is_bigram, p))
    scored.sort(reverse=True)

    vocab, chosen_bigrams = [], []
    for _score, _cnt, is_bigram, p in scored:
        if len(vocab) >= 16:
            break
        if not is_bigram and any(p in bg.split() for bg in chosen_bigrams):
            continue  # skip a unigram already covered by a chosen bigram
        vocab.append(p)
        if is_bigram:
            chosen_bigrams.append(p)
    vocab_set = set(vocab)

    return {cid: [p for p in vocab if p in phrases] for cid, phrases in company_phrases.items()}


@app.get("/api/industry/{name}")
def industry(name: str, source: str = "", sort: str = "traction",
             window: str = "all", ai_native: str = "all", business_model: str = "all"):
    try:
        with _conn() as c:
            rows = _base_rows(c, window)
    except sqlite3.Error:
        return []
    # Gather all appearances per product within this industry, then keep only
    # products that appear in `source` if a source filter is set. The card still
    # shows every source the company was seen on.
    prod = {}
    for r in rows:
        if r["subdomain"] != name:
            continue
        if not _passes(r, ai_native, business_model):
            continue
        p = prod.setdefault(r["id"], {
            "id": r["id"], "name": r["name"], "url": r["url"],
            "description": r["description"], "subdomain": r["subdomain"],
            "subcategory": r["subcategory"],
            "sources": set(), "traction": 0, "first_seen": None,
        })
        p["sources"].add(r["source"])
        if r["raw_signal"] is not None:
            p["traction"] = max(p["traction"], int(r["raw_signal"]))
        if r["seen_at"]:
            p["first_seen"] = r["seen_at"] if p["first_seen"] is None else min(p["first_seen"], r["seen_at"])

    items = [p for p in prod.values() if (not source or source in p["sources"])]
    for p in items:
        p["sources"] = sorted(p["sources"], key=lambda s: SOURCE_ORDER.get(s, 99))

    # Break a broad category into what people are building inside it. Prefer the
    # model-assigned subcategory (from subcategorize.py); fall back to the
    # on-the-fly keyword derivation until that pass has been run.
    if any(p.get("subcategory") for p in items):
        for p in items:
            sc = p.get("subcategory")
            p["tags"] = [sc] if sc else []
    else:
        tagmap = derive_subcategories(items, name)
        for p in items:
            p["tags"] = tagmap.get(p["id"], [])
    subcats = Counter(t for p in items for t in p["tags"])

    if sort == "newest":
        items.sort(key=lambda p: (p["first_seen"] or ""), reverse=True)
    elif sort == "az":
        items.sort(key=lambda p: (p["name"] or "").lower())
    else:  # traction
        items.sort(key=lambda p: p["traction"], reverse=True)

    return {
        "industry": name,
        "source": source or None,
        "total": len(items),
        "subcategories": [{"tag": t, "count": n} for t, n in subcats.most_common()],
        "companies": items,
    }


# --------------------------------------------------------------------------
# Momentum signals: turn the snapshot into "what is forming". Per subcategory we
# look at the tier split (leading sources are the early edge, YC is lagging), how
# new it is, and how it is growing, then label an action.
# --------------------------------------------------------------------------


def _action(supply, leading_ratio, yc, demand, growth, supply_hi):
    """Rule-based action label from the signal shape."""
    crowded = supply >= supply_hi and leading_ratio < 0.4 and yc >= 3
    if crowded:
        return "Avoid"
    if leading_ratio >= 0.5 and yc <= 3:
        return "Build" if demand >= 2 else "Watch"
    if supply >= supply_hi and leading_ratio >= 0.45:
        return "Wedge"
    if leading_ratio >= 0.55 and supply < 8:
        return "Wait"
    return "Crossing"


def _signals(min_supply=5):
    today = dt.date.today()
    d180 = (today - dt.timedelta(days=180)).isoformat()
    d540 = (today - dt.timedelta(days=540)).isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT p.id, p.name, p.url, p.subdomain, p.subcategory, "
            "a.source, a.tier, a.seen_at, a.raw_signal "
            "FROM products p JOIN appearances a ON a.product_id = p.id "
            "WHERE p.subdomain IS NOT NULL AND p.subdomain != 'uncategorized' "
            "AND p.subcategory IS NOT NULL AND p.subcategory NOT IN ('other', '')"
        ).fetchall()
        demand = {r["subdomain"]: r["d"] for r in c.execute(
            "SELECT subdomain, SUM(weight) d FROM demand_signals GROUP BY subdomain")}

    groups = {}
    for r in rows:
        g = groups.setdefault((r["subdomain"], r["subcategory"]),
                              {"prods": {}, "lead": 0, "mid": 0, "lag": 0,
                               "recent": 0, "prior": 0, "first": None})
        p = g["prods"].setdefault(r["id"], {"name": r["name"], "url": r["url"],
                                            "traction": 0, "sources": set()})
        if r["raw_signal"] is not None:
            p["traction"] = max(p["traction"], int(r["raw_signal"]))
        p["sources"].add(r["source"])
        g[{"leading": "lead", "mid": "mid", "lagging": "lag"}.get(r["tier"], "mid")] += 1
        sa = r["seen_at"]
        if sa:
            g["first"] = sa if g["first"] is None else min(g["first"], sa)
            if sa >= d180:
                g["recent"] += 1
            elif sa >= d540:
                g["prior"] += 1

    supplies = sorted(len(g["prods"]) for g in groups.values() if len(g["prods"]) >= min_supply)
    supply_hi = supplies[int(0.75 * (len(supplies) - 1))] if supplies else 0

    out = []
    for (sub, subcat), g in groups.items():
        supply = len(g["prods"])
        if supply < min_supply:
            continue
        total = g["lead"] + g["mid"] + g["lag"]
        leading_ratio = g["lead"] / total if total else 0.0
        yc = g["lag"]
        dem = float(demand.get(sub, 0.0))
        growth = (g["recent"] / g["prior"]) if g["prior"] else float(g["recent"])
        score = leading_ratio * (g["lead"] ** 0.5) * (dem + 0.5) / (1 + yc)
        top = sorted(g["prods"].values(), key=lambda x: x["traction"], reverse=True)[:5]
        out.append({
            "subdomain": sub, "subcategory": subcat, "supply": supply,
            "leading": g["lead"], "mid": g["mid"], "yc": yc,
            "leading_ratio": round(leading_ratio, 2),
            "recent": g["recent"], "prior": g["prior"], "growth": round(growth, 2),
            "first_seen": g["first"], "demand": round(dem, 1),
            "score": round(score, 3),
            "action": _action(supply, leading_ratio, yc, dem, growth, supply_hi),
            "companies": [{"name": t["name"], "url": t["url"], "traction": t["traction"],
                           "sources": sorted(t["sources"], key=lambda s: SOURCE_ORDER.get(s, 99))}
                          for t in top],
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


@app.get("/api/momentum")
def momentum(min_supply: int = 5, limit: int = 80):
    try:
        return _signals(min_supply)[:limit]
    except sqlite3.Error:
        return []


# Thesis briefs are cached to disk so we pay for each one once.
_BRIEF_PATH = os.path.join(DATA_DIR, "briefs.json")
try:
    _briefs = json.load(open(_BRIEF_PATH)) if os.path.exists(_BRIEF_PATH) else {}
except Exception:
    _briefs = {}


@app.get("/api/brief")
def brief(subdomain: str, subcategory: str, refresh: int = 0):
    key = f"{subdomain}||{subcategory}"
    if not refresh and key in _briefs:
        return _briefs[key]

    sig = next((s for s in _signals(1) if s["subdomain"] == subdomain and s["subcategory"] == subcategory), None)
    if not sig:
        return {"summary": "Not enough data for a thesis yet.", "gap": "", "risk": ""}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"summary": "Set ANTHROPIC_API_KEY to generate a thesis.", "gap": "", "risk": ""}

    comps = "\n".join(f"- {c['name']} (traction {c['traction']})" for c in sig["companies"])
    prompt = (
        f'You are a venture analyst. Write a tight thesis for the sub-theme "{subcategory}" '
        f'inside the category "{subdomain}".\n\n'
        f"Signal: {sig['supply']} builders, {int(sig['leading_ratio'] * 100)}% on leading sources "
        f"(research and early launches), {sig['yc']} in YC. Demand for the parent category is "
        f"{sig['demand']} on a 0 to 3 scale. Earliest seen {sig['first_seen']}.\n"
        f"Top builders:\n{comps}\n\n"
        "Reply with a JSON object, plain text values, no em dashes, with keys: "
        '"summary" (2 to 3 sentences on what this is and why the signal matters now), '
        '"gap" (one sentence on what is still open to build), '
        '"risk" (one sentence on the main reason this could be a trap).'
    )
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(model=CLASSIFIER_MODEL, max_tokens=400,
                                     messages=[{"role": "user", "content": prompt}])
        raw = re.sub(r"^```(?:json)?|```$", "", msg.content[0].text.strip(), flags=re.M).strip()
        parsed = json.loads(raw)
        out = {"summary": parsed.get("summary", ""), "gap": parsed.get("gap", ""), "risk": parsed.get("risk", "")}
    except Exception as exc:
        return {"summary": f"Could not generate a thesis ({type(exc).__name__}).", "gap": "", "risk": ""}

    _briefs[key] = out
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_BRIEF_PATH, "w") as fh:
            json.dump(_briefs, fh)
    except Exception:
        pass
    return out


@app.get("/api/whitespace")
def whitespace():
    try:
        with _conn() as c:
            return score_whitespace(c)
    except sqlite3.Error:
        return []


# Serve the built frontend (web/dist) from the same origin when it is present, so
# one deployed service hosts both the API and the UI. The /api routes above are
# matched first; anything else falls through to the static files.
_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
if os.path.isdir(_DIST):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_DIST, html=True), name="static")
