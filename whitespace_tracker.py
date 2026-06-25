#!/usr/bin/env python3
"""Whitespace Trend Tracker.

Surfaces sub-domains where VC demand is high but builder supply is low. The
output is a ranked whitespace list: where capital is asking and few people are
building.

How it works:
  supply  = products that show up on builder sources (Show HN, GitHub, Hugging
            Face, Product Hunt, BetaList, YC). Each appearance is tagged with a
            tier: leading (earliest signal), mid, or lagging (YC, the thing we
            try to predict).
  demand  = how loudly capital is asking for a sub-domain. Hand-seeded from
            public VC requests for startups, plus PitchBook funding when a
            connector is reachable.
  meet    = supply and demand only line up if they share labels, so both go
            through one fixed vocabulary (SUBDOMAINS) and one classifier.

The scorer rewards high demand, low supply, and supply that clusters on leading
sources (an early signal). The report writes a weekly markdown digest.

Run:
  python whitespace_tracker.py              full run, heuristic classifier
  python whitespace_tracker.py --reclassify reclassify every product
  ANTHROPIC_API_KEY=... python whitespace_tracker.py   use the model classifier

Stack is intentionally small: Python, SQLite, requests, anthropic. No web
framework, no server, no ORM.
"""

import argparse
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

DB_PATH = os.environ.get("WHITESPACE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "whitespace.db"))
DIGEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digests")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USER_AGENT = "whitespace-tracker/0.1 (research; harshshpr@gmail.com)"

# Each builder source is tagged with a tier. Leading sources are the earliest
# place a theme shows up; lagging (YC) is what we try to predict.
SOURCE_TIERS = {
    "show_hn": "leading",
    "github": "leading",
    "huggingface": "leading",
    "arxiv": "leading",
    "product_hunt": "mid",
    "betalist": "mid",
    "yc": "lagging",
}

# How much a leading-source cluster lifts a sub-domain's whitespace score.
# leading_factor = 1 + LEADING_WEIGHT * leading_ratio, so the factor sits in
# [1, 1 + LEADING_WEIGHT]. backtest.py tests whether this weight earns its keep.
LEADING_WEIGHT = 1.0

# Quadrant thresholds. Demand is on the curated 0..3-per-source scale. Supply
# high-water mark is computed from the data at scoring time (see score_whitespace)
# with this as a floor so the quadrants stay meaningful when data is sparse.
DEMAND_HI = 1.5
SUPPLY_HI_FLOOR = 2

CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"  # cheap and fast, fine for labeling

# Splits a Show HN title "Name - tagline" on the separators authors use:
# hyphen, en dash, em dash, or colon surrounded by spaces. Built with chr() so
# no literal dash glyph lives in this file.
_SHOW_HN_SPLIT = re.compile("\\s[-" + chr(0x2013) + chr(0x2014) + ":]\\s")

log = logging.getLogger("whitespace")


# --------------------------------------------------------------------------
# Fixed taxonomy (Task 3)
# --------------------------------------------------------------------------
# One vocabulary shared by the classifier and the demand seed. The classifier
# must pick the closest match or return "uncategorized". It must never invent a
# label, because supply and demand only meet when they use the same strings.
SUBDOMAINS = [
    "agentic sdr",
    "ai medical scribing",
    "ai-native law firm",
    "ai legal copilot",
    "ai coding agents",
    "ai code review",
    "llm observability and evals",
    "ai customer support agents",
    "voice ai agents",
    "ai search and answer engines",
    "ai marketing content",
    "ai recruiting",
    "ai accounting and bookkeeping",
    "ai financial analysis",
    "ai insurance",
    "healthcare revenue cycle",
    "ai clinical decision support",
    "ai drug discovery",
    "ai medical imaging",
    "ai tutoring and education",
    "inference chips",
    "ai chip interconnect",
    "datacenter cooling",
    "energy for data centers",
    "battery and energy storage",
    "nuclear and fusion",
    "grid software",
    "climate and carbon capture",
    "physical ai data",
    "robotics and humanoids",
    "autonomous vehicles",
    "drones and defense autonomy",
    "defense tech",
    "space and satellites",
    "ai security",
    "ai compliance and grc",
    "vector and retrieval infra",
    "ai agent infrastructure",
    "synthetic data",
    "ai video generation",
    "ai voice and music generation",
    "fintech infrastructure",
    "stablecoin and crypto payments",
    "ai devops and sre",
    "on-device and edge ai",
    "ai data engineering",
]

UNCATEGORIZED = "uncategorized"

# Keyword cues for the no-API heuristic classifier. Each cue is matched on word
# boundaries (see _build_cues), so "space" does not fire inside "workspace".
# Longer cues are more specific and weigh more. These only steer the heuristic
# path; the model path gets the SUBDOMAINS list instead.
KEYWORDS = {
    "agentic sdr": ["sdr", "sales development", "outbound sales", "cold email", "cold outreach", "prospecting", "sales agent", "lead generation"],
    "ai medical scribing": ["medical scribe", "ambient scribe", "clinical note", "clinical documentation", "soap note", "medical transcription", "ambient documentation", "scribe"],
    "ai-native law firm": ["law firm", "legal practice", "legal services firm", "ai lawyer", "alternative legal"],
    "ai legal copilot": ["legal copilot", "contract review", "legal research", "legal ai", "paralegal", "due diligence", "clm", "contract lifecycle"],
    "ai coding agents": ["coding agent", "code generation", "ai developer", "ai engineer", "writes code", "autonomous coding", "software agent", "pair programmer", "vibe coding", "coding", "codegen", "code completion", "coding copilot", "coder"],
    "ai code review": ["code review", "pull request review", "pr review", "bug detection", "static analysis", "reviews code"],
    "llm observability and evals": ["llm observability", "prompt evaluation", "eval", "llm monitoring", "tracing for llm", "prompt management", "guardrail", "llm gateway"],
    "ai customer support agents": ["customer support", "support agent", "help desk", "customer service", "ticket deflection", "support automation", "chatbot for support"],
    "voice ai agents": ["voice agent", "voice ai", "phone agent", "ai phone", "call center", "voice assistant", "speech agent", "ai receptionist"],
    "ai search and answer engines": ["answer engine", "ai search", "semantic search", "enterprise search", "rag search", "perplexity"],
    "ai marketing content": ["marketing content", "content generation", "copywriting", "seo content", "ad creative", "social media content", "blog generation"],
    "ai recruiting": ["recruiting", "recruiter", "sourcing candidates", "applicant tracking", "hiring", "talent acquisition", "interview ai"],
    "ai accounting and bookkeeping": ["bookkeeping", "accounting automation", "accounts payable", "accounts receivable", "expense management", "invoice processing", "ledger"],
    "ai financial analysis": ["financial analysis", "fp&a", "financial planning", "equity research", "investment analysis", "spreadsheet ai", "financial model"],
    "ai insurance": ["insurance", "underwriting", "claims processing", "insurtech", "actuarial"],
    "healthcare revenue cycle": ["revenue cycle", "prior authorization", "prior auth", "medical billing", "medical coding", "claims denial", "payer", "rcm"],
    "ai clinical decision support": ["clinical decision", "diagnosis support", "triage", "patient risk", "care pathway", "clinical ai"],
    "ai drug discovery": ["drug discovery", "drug design", "molecule", "protein", "biotech ai", "therapeutic discovery", "small molecule", "antibody"],
    "ai medical imaging": ["radiology", "medical imaging", "ct scan", "mri", "pathology slide", "x-ray", "diagnostic imaging"],
    "ai tutoring and education": ["tutor", "tutoring", "edtech", "learning platform", "study assistant", "homework", "student learning"],
    "inference chips": ["inference chip", "ai accelerator", "asic for ai", "npu", "tensor processor", "silicon for ai", "inference hardware", "ai chip"],
    "ai chip interconnect": ["interconnect", "chiplet", "photonic", "nvlink", "die-to-die", "optical interconnect", "networking fabric"],
    "datacenter cooling": ["liquid cooling", "immersion cooling", "datacenter cooling", "data center cooling", "thermal management", "cooling system"],
    "energy for data centers": ["data center power", "datacenter power", "power for data", "energy for data", "behind-the-meter", "data center energy", "powering ai"],
    "battery and energy storage": ["battery", "energy storage", "grid storage", "lithium", "solid state battery", "bess"],
    "nuclear and fusion": ["nuclear", "nuclear fusion", "fusion energy", "fusion reactor", "small modular reactor", "smr", "reactor", "fission"],
    "grid software": ["grid software", "electricity grid", "demand response", "virtual power plant", "vpp", "energy trading", "grid management"],
    "climate and carbon capture": ["carbon capture", "carbon removal", "ccs", "direct air capture", "decarbonization", "emissions", "climate tech"],
    "physical ai data": ["teleoperation", "teleop", "robot data", "manipulation data", "physical ai", "robot demonstration", "embodied data", "imitation learning", "real-world data for robots"],
    "robotics and humanoids": ["humanoid", "robotics", "robot arm", "manipulation", "warehouse robot", "industrial robot", "legged robot", "robot", "robots"],
    "autonomous vehicles": ["autonomous vehicle", "self-driving", "self driving", "autonomous driving", "adas", "robotaxi", "av stack"],
    "drones and defense autonomy": ["drone", "uav", "loitering", "counter-uas", "autonomous aircraft", "swarm"],
    "defense tech": ["defense", "military", "warfighter", "defense department", "national security", "munition", "battlefield"],
    "space and satellites": ["satellite", "spacecraft", "launch vehicle", "orbital", "rocket", "in-orbit", "earth observation", "aerospace", "low earth orbit"],
    "ai security": ["ai security", "prompt injection", "model security", "agent security", "llm security", "ai red team", "adversarial"],
    "ai compliance and grc": ["compliance", "grc", "audit automation", "soc 2", "regulatory", "risk and compliance", "governance"],
    "vector and retrieval infra": ["vector database", "vector search", "embeddings store", "retrieval", "rag infrastructure", "vector index"],
    "ai agent infrastructure": ["agent infrastructure", "agent framework", "agent orchestration", "agent memory", "tool use", "tool calling", "multi-agent", "agent runtime", "mcp server", "computer use", "browser agent", "agent harness", "agent sandbox", "agent observability"],
    "synthetic data": ["synthetic data", "data generation", "labeled data", "training data generation", "data augmentation"],
    "ai video generation": ["video generation", "text-to-video", "text to video", "video model", "ai video", "generative video"],
    "ai voice and music generation": ["text-to-speech", "text to speech", "voice cloning", "music generation", "audio generation", "speech synthesis", "tts"],
    "fintech infrastructure": ["payment infrastructure", "banking api", "ledger api", "fintech infrastructure", "card issuing", "embedded finance", "payments api"],
    "stablecoin and crypto payments": ["stablecoin", "crypto payment", "onchain", "on-chain", "web3 payment", "blockchain payment", "usdc"],
    "ai devops and sre": ["devops", "sre", "incident response", "observability for infra", "on-call", "kubernetes ai", "infrastructure automation", "kubernetes", "terraform", "ci/cd"],
    "on-device and edge ai": ["on-device", "on device", "edge ai", "edge inference", "local llm", "tinyml", "webgpu", "quantization"],
    "ai data engineering": ["data pipeline", "etl", "data engineering", "data transformation", "data warehouse", "data quality"],
}


# --------------------------------------------------------------------------
# Logging and HTTP (Task 2: light retry with backoff on every network call)
# --------------------------------------------------------------------------

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def load_dotenv(path=None):
    """Load KEY=VALUE pairs from a .env file next to this script, if present.

    Dependency-free convenience for holding tokens (GITHUB_TOKEN,
    PRODUCT_HUNT_TOKEN, ANTHROPIC_API_KEY) without exporting them by hand. A real
    environment variable always wins, so an exported token overrides the file.
    Blank lines and lines starting with # are ignored.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as exc:
        log.warning("could not read %s: %s", path, exc)


def _request(method, url, retries=3, backoff=1.5, **kwargs):
    """One HTTP call with light exponential backoff.

    Retries on connection errors and on 429/5xx. Returns the Response. The
    caller decides what a non-200 means, since some sources (GitHub unauthed)
    answer 403 when rate limited and that should be logged, not retried forever.
    """
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", {}) or {})
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 25), **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = backoff ** attempt
                log.warning("%s %s -> %s, retry in %.1fs", method, url, resp.status_code, wait)
                time.sleep(wait)
                continue
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            wait = backoff ** attempt
            log.warning("%s %s failed (%s), retry in %.1fs", method, url, type(exc).__name__, wait)
            time.sleep(wait)
    if last_exc:
        raise last_exc
    return resp  # last response from a retry loop that kept seeing 5xx/429


def http_get(url, **kwargs):
    return _request("GET", url, **kwargs)


def http_post(url, **kwargs):
    return _request("POST", url, **kwargs)


# --------------------------------------------------------------------------
# Database (three-table schema: products, appearances, demand_signals)
# --------------------------------------------------------------------------

def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            url         TEXT,
            domain      TEXT,
            description TEXT,
            subdomain   TEXT,            -- label from SUBDOMAINS, or 'uncategorized', or NULL before classification
            first_seen  TEXT,
            dedupe_key  TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS appearances (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER NOT NULL,
            source      TEXT NOT NULL,    -- show_hn, github, huggingface, product_hunt, betalist, yc
            tier        TEXT NOT NULL,    -- leading, mid, lagging
            external_id TEXT,
            seen_at     TEXT,             -- ISO date the signal is dated to
            raw_signal  REAL,            -- points, stars, votes, trending score
            extra       TEXT,            -- source-specific note (batch, pipeline_tag)
            UNIQUE(source, external_id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS demand_signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            subdomain   TEXT NOT NULL,    -- must be a SUBDOMAINS label
            source      TEXT NOT NULL,    -- rfs_handseed, pitchbook
            weight      REAL NOT NULL,    -- 0..3
            detail      TEXT,
            seen_at     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_app_product ON appearances(product_id);
        CREATE INDEX IF NOT EXISTS idx_app_source ON appearances(source);
        CREATE INDEX IF NOT EXISTS idx_prod_subdomain ON products(subdomain);
        CREATE INDEX IF NOT EXISTS idx_demand_sub ON demand_signals(subdomain);
        """
    )
    conn.commit()
    return conn


def _domain_of(url):
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _dedupe_key(name, url):
    return (name or "").strip().lower() + "|" + _domain_of(url)


def upsert_product(conn, name, url, description, source, tier, external_id, seen_at, raw_signal=None, extra=None):
    """Insert (or find) a product and record one appearance.

    Products are de-duplicated across sources by name + domain. Appearances are
    de-duplicated by (source, external_id) so re-running the pipeline does not
    double count. New connectors only ever append here.
    """
    name = (name or "").strip()
    if not name:
        return None
    key = _dedupe_key(name, url)
    today = dt.date.today().isoformat()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO products (name, url, domain, description, first_seen, dedupe_key) VALUES (?,?,?,?,?,?)",
        (name, url, _domain_of(url), (description or "").strip(), today, key),
    )
    row = cur.execute("SELECT id FROM products WHERE dedupe_key=?", (key,)).fetchone()
    if row is None:
        return None
    product_id = row["id"]
    cur.execute(
        "INSERT OR IGNORE INTO appearances (product_id, source, tier, external_id, seen_at, raw_signal, extra) VALUES (?,?,?,?,?,?,?)",
        (product_id, source, tier, str(external_id), seen_at, raw_signal, extra),
    )
    return product_id


# --------------------------------------------------------------------------
# Supply connectors
# Each is standalone, wrapped in try/except, logs a clear reason on failure,
# and returns a row count. One broken source must not break the run.
# --------------------------------------------------------------------------

def ingest_show_hn(conn, days_back=180, cap=8000, per_page=100, min_points=5):
    """Show HN posts via the Hacker News Algolia API. Tier: leading.

    Endpoint and field names verified 2026-06-22:
    https://hn.algolia.com/api/v1/search_by_date?tags=show_hn
    hits[].{objectID, title, url, points, num_comments, created_at, created_at_i, author}
    Pages backward by created_at_i over the last days_back days (default 6 months),
    filtered to posts with at least min_points points so the long tail of
    zero-engagement submissions does not drown the signal. Verified 2026-06-23:
    last 6 months is about 26k Show HN posts but only 4.4k with 5+ points. Capped
    at cap posts. Logs the oldest date reached.
    """
    source, tier = "show_hn", SOURCE_TIERS["show_hn"]
    count = 0
    try:
        cutoff_ts = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_back)).timestamp())
        before = None
        oldest = None
        while count < cap:
            nf = f"points>={min_points}"
            if before is not None:
                nf += f",created_at_i<{before}"
            url = (
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?tags=show_hn&hitsPerPage={per_page}&numericFilters={requests.utils.quote(nf)}"
            )
            resp = http_get(url)
            if resp.status_code != 200:
                log.warning("%s: HTTP %s", source, resp.status_code)
                break
            hits = resp.json().get("hits", [])
            if not hits:
                break
            page_min_ts = None
            for h in hits:
                ts = h.get("created_at_i") or 0
                if page_min_ts is None or (ts and ts < page_min_ts):
                    page_min_ts = ts
                if ts and ts < cutoff_ts:
                    continue
                title = (h.get("title") or "").strip()
                if not title:
                    continue
                cleaned = re.sub(r"^show\s+hn:\s*", "", title, flags=re.I)
                name = _SHOW_HN_SPLIT.split(cleaned, maxsplit=1)[0].strip() or cleaned
                seen_at = (h.get("created_at") or "")[:10]
                oldest = seen_at or oldest
                if upsert_product(
                    conn, name=name[:120], url=h.get("url"),
                    description=cleaned, source=source, tier=tier,
                    external_id=h.get("objectID"), seen_at=seen_at,
                    raw_signal=h.get("points"),
                ):
                    count += 1
            # Stop when the page reaches past the cutoff or the window stops moving.
            if page_min_ts is None or page_min_ts < cutoff_ts:
                break
            if before is not None and page_min_ts >= before:
                break
            before = page_min_ts
        conn.commit()
        log.info("%s: ingested %d products back to %s", source, count, oldest)
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


GITHUB_TOPICS = ["llm", "ai-agents", "rag", "llmops", "inference", "robotics", "diffusion-models", "mcp"]


def ingest_github(conn, days_back=730, min_stars=15, per_page=100, topics=None):
    """New, fast-rising GitHub repos via the search API. Tier: leading.

    Verified 2026-06-22: https://api.github.com/search/repositories
    items[].{full_name, html_url, description, stargazers_count, created_at, language, topics}
    Without a token, unauthed search is capped near 10 requests/min and can 403,
    so we make a single request (top 100 by stars). With GITHUB_TOKEN the limit
    rises to 30/min, so we page the broad query and add AI-theme topic queries to
    broaden toward the taxonomy. A 403 mid-run ends that query, not the connector.
    """
    source, tier = "github", SOURCE_TIERS["github"]
    since = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    topics = GITHUB_TOPICS if topics is None else topics
    if token:
        # (query, max_pages). GitHub search caps at 1000 results (10 pages) per query.
        queries = [(f"created:>{since} stars:>{min_stars}", 5)]
        queries += [(f"topic:{t} created:>{since}", 1) for t in topics]
    else:
        queries = [(f"created:>{since} stars:>{min_stars}", 1)]

    count = 0
    seen_ids = set()
    try:
        for q, max_pages in queries:
            for page in range(1, max_pages + 1):
                url = (
                    "https://api.github.com/search/repositories"
                    f"?q={requests.utils.quote(q)}&sort=stars&order=desc&per_page={per_page}&page={page}"
                )
                resp = http_get(url, headers=headers)
                if resp.status_code == 403:
                    log.warning("%s: 403 rate limit on '%s' page %s%s", source, q, page,
                                "" if token else " (set GITHUB_TOKEN to go deeper)")
                    break
                if resp.status_code != 200:
                    log.warning("%s: HTTP %s on '%s': %s", source, resp.status_code, q, resp.text[:100])
                    break
                items = resp.json().get("items", [])
                if not items:
                    break
                for item in items:
                    ext = item.get("id")
                    if ext in seen_ids:
                        continue
                    seen_ids.add(ext)
                    name = item.get("name") or (item.get("full_name") or "").split("/")[-1]
                    tps = " ".join(item.get("topics") or [])
                    desc = " ".join(x for x in [item.get("description"), item.get("language"), tps] if x)
                    if upsert_product(
                        conn, name=name, url=item.get("html_url"),
                        description=desc, source=source, tier=tier,
                        external_id=ext, seen_at=(item.get("created_at") or "")[:10],
                        raw_signal=item.get("stargazers_count"),
                    ):
                        count += 1
                if len(items) < per_page:
                    break
        conn.commit()
        log.info("%s: ingested %d products (%s)", source, count, "authed deep" if token else "unauthed single page")
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


def _yc_recent_batches(meta, min_year=None, max_batches=6):
    """Pick recent populated batch endpoints from yc-oss meta.json."""
    if min_year is None:
        min_year = dt.date.today().year - 1
    season_idx = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}
    chosen = []
    for key, info in (meta.get("batches") or {}).items():
        if not isinstance(info, dict) or not info.get("api"):
            continue
        if (info.get("count") or 0) <= 0:
            continue
        parts = key.split("-")
        if len(parts) != 2 or parts[0] not in season_idx:
            continue
        try:
            year = int(parts[1])
        except ValueError:
            continue
        if year < min_year:
            continue
        chosen.append((year, season_idx[parts[0]], key, info["api"]))
    chosen.sort(reverse=True)
    return [(k, api) for (_y, _s, k, api) in chosen[:max_batches]]


def ingest_yc(conn, cap=3000, years_back=2):
    """Recent YC companies via the community yc-oss API. Tier: lagging.

    Verified 2026-06-22: meta at https://yc-oss.github.io/api/meta.json lists
    per-batch endpoints. Company fields used: name, one_liner, long_description,
    website, industry, tags, batch, status, launched_at, url.
    yc-oss is the maintained public mirror of the YC company directory; if it
    ever moves, swap YC_META below for the new index.
    """
    source, tier = "yc", SOURCE_TIERS["yc"]
    YC_META = "https://yc-oss.github.io/api/meta.json"
    count = 0
    try:
        meta_resp = http_get(YC_META)
        if meta_resp.status_code != 200:
            log.warning("%s: meta HTTP %s, cannot resolve batches", source, meta_resp.status_code)
            return 0
        batches = _yc_recent_batches(meta_resp.json(), min_year=dt.date.today().year - years_back, max_batches=16)
        if not batches:
            log.warning("%s: no recent populated batches found in meta", source)
            return 0
        for batch_key, api in batches:
            if count >= cap:
                break
            resp = http_get(api)
            if resp.status_code != 200:
                log.warning("%s: batch %s HTTP %s, skipping", source, batch_key, resp.status_code)
                continue
            for c in resp.json():
                if count >= cap:
                    break
                name = (c.get("name") or "").strip()
                if not name:
                    continue
                text = " ".join(x for x in [c.get("one_liner"), c.get("long_description"), " ".join(c.get("tags") or [])] if x)
                launched = c.get("launched_at")
                seen_at = dt.datetime.fromtimestamp(launched, dt.timezone.utc).date().isoformat() if launched else None
                if upsert_product(
                    conn, name=name, url=c.get("website") or c.get("url"),
                    description=text, source=source, tier=tier,
                    external_id=f"yc-{c.get('id')}", seen_at=seen_at,
                    raw_signal=c.get("team_size"), extra=c.get("batch"),
                ):
                    count += 1
        conn.commit()
        log.info("%s: ingested %d products across %d batches", source, count, len(batches))
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


def ingest_huggingface(conn, limit=200):
    """Trending Hugging Face models and Spaces. Tier: leading.

    Verified 2026-06-22:
    models: https://huggingface.co/api/models?sort=trendingScore&direction=-1&full=true
            items[].{id, author, likes, trendingScore, downloads, tags, pipeline_tag, createdAt}
    spaces: https://huggingface.co/api/spaces?sort=trendingScore&direction=-1&full=true
            items[].{id, author, cardData.title, likes, trendingScore, sdk, tags, createdAt}
    Public API, no token required for trending lists.
    """
    source, tier = "huggingface", SOURCE_TIERS["huggingface"]
    count = 0
    try:
        # Models
        m_url = f"https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit={limit}&full=true"
        resp = http_get(m_url)
        if resp.status_code == 200:
            for m in resp.json():
                mid = m.get("id")
                if not mid:
                    continue
                tags = [t for t in (m.get("tags") or []) if ":" not in t][:8]
                desc = " ".join(x for x in [m.get("pipeline_tag"), m.get("library_name"), " ".join(tags)] if x)
                if upsert_product(
                    conn, name=mid, url=f"https://huggingface.co/{mid}",
                    description=desc, source=source, tier=tier,
                    external_id=f"hf-model-{mid}", seen_at=(m.get("createdAt") or "")[:10],
                    raw_signal=m.get("trendingScore") or m.get("likes"), extra=m.get("pipeline_tag"),
                ):
                    count += 1
        else:
            log.warning("%s: models HTTP %s", source, resp.status_code)
        # Spaces
        s_url = f"https://huggingface.co/api/spaces?sort=trendingScore&direction=-1&limit={limit}&full=true"
        resp = http_get(s_url)
        if resp.status_code == 200:
            for s in resp.json():
                sid = s.get("id")
                if not sid:
                    continue
                card = s.get("cardData") or {}
                title = card.get("title") or sid
                tags = [t for t in (s.get("tags") or []) if ":" not in t][:8]
                desc = " ".join(x for x in [title, s.get("sdk"), " ".join(tags)] if x)
                if upsert_product(
                    conn, name=title[:120], url=f"https://huggingface.co/spaces/{sid}",
                    description=desc, source=source, tier=tier,
                    external_id=f"hf-space-{sid}", seen_at=(s.get("createdAt") or "")[:10],
                    raw_signal=s.get("trendingScore") or s.get("likes"), extra="space",
                ):
                    count += 1
        else:
            log.warning("%s: spaces HTTP %s", source, resp.status_code)
        conn.commit()
        log.info("%s: ingested %d products (models + spaces)", source, count)
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


def ingest_arxiv(conn, cap=600, page_size=200, categories=None):
    """Recent AI research papers via the arXiv API. Tier: leading.

    Verified 2026-06-23: http://export.arxiv.org/api/query returns Atom XML.
    entry fields used: title, summary, published, id, category[@term]. A paper is
    the earliest proxy for builder interest in a theme, ahead of Show HN or
    GitHub, so it sharpens the leading signal the backtest leans on. arXiv asks
    for slow polling, so pages are spaced 3 seconds apart. The abstract is the
    text the classifier reads.
    """
    source, tier = "arxiv", SOURCE_TIERS["arxiv"]
    if categories is None:
        categories = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.MA", "cs.SE", "cs.CR"]
    cat_q = "+OR+".join(f"cat:{c}" for c in categories)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    count = 0
    try:
        for start in range(0, cap, page_size):
            url = (
                "http://export.arxiv.org/api/query"
                f"?search_query={cat_q}&start={start}&max_results={page_size}"
                "&sortBy=submittedDate&sortOrder=descending"
            )
            resp = http_get(url, timeout=40)
            if resp.status_code != 200:
                log.warning("%s: HTTP %s at start=%s", source, resp.status_code, start)
                break
            entries = ET.fromstring(resp.text).findall("a:entry", ns)
            if not entries:
                break
            for e in entries:
                title = " ".join((e.findtext("a:title", default="", namespaces=ns) or "").split())
                summary = " ".join((e.findtext("a:summary", default="", namespaces=ns) or "").split())
                aid = (e.findtext("a:id", default="", namespaces=ns) or "").strip()
                published = (e.findtext("a:published", default="", namespaces=ns) or "")[:10]
                cats = [c.get("term") for c in e.findall("a:category", ns)]
                if not title:
                    continue
                if upsert_product(
                    conn, name=title[:160], url=aid,
                    description=summary[:600], source=source, tier=tier,
                    external_id=f"arxiv-{aid.rsplit('/', 1)[-1]}", seen_at=published,
                    raw_signal=None, extra=cats[0] if cats else None,
                ):
                    count += 1
            if len(entries) < page_size:
                break
            time.sleep(3)  # arXiv politeness
        conn.commit()
        log.info("%s: ingested %d papers", source, count)
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


def ingest_product_hunt(conn, days_back=180, cap=500, page_size=20):
    """Product Hunt launches via the GraphQL API. Tier: mid.

    Verified 2026-06-23: POST https://api.producthunt.com/v2/api/graphql with a
    bearer token returns posts. The posts connection caps page size near 20, so
    this cursor-paginates the top-voted launches from the last days_back days
    (default 6 months), up to cap. Reads PRODUCT_HUNT_TOKEN and skips cleanly when
    absent. Fields: id, name, tagline, description, url, votesCount, createdAt,
    topics. A non-200 (including a hard 429 rate limit) ends paging with whatever
    was collected, logged.
    """
    source, tier = "product_hunt", SOURCE_TIERS["product_hunt"]
    token = os.environ.get("PRODUCT_HUNT_TOKEN") or os.environ.get("PRODUCTHUNT_TOKEN") or os.environ.get("PH_TOKEN")
    if not token:
        log.warning("%s: no PRODUCT_HUNT_TOKEN in env, skipping (set it to ingest)", source)
        return 0
    query = """
    query($after:String, $postedAfter:DateTime){
      posts(first:20, after:$after, postedAfter:$postedAfter, order:VOTES){
        pageInfo{ endCursor hasNextPage }
        edges{ node{
          id name tagline description url votesCount createdAt
          topics(first:5){edges{node{name}}}
        }}
      }
    }"""
    posted_after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    after = None
    count = 0
    try:
        while count < cap:
            resp = http_post(
                "https://api.producthunt.com/v2/api/graphql",
                json={"query": query, "variables": {"after": after, "postedAfter": posted_after}},
                headers=headers,
            )
            if resp.status_code != 200:
                log.warning("%s: HTTP %s: %s", source, resp.status_code, resp.text[:120])
                break
            posts = ((resp.json() or {}).get("data") or {}).get("posts") or {}
            edges = posts.get("edges") or []
            if not edges:
                break
            for e in edges:
                node = e.get("node") or {}
                name = (node.get("name") or "").strip()
                if not name:
                    continue
                topics = " ".join(t["node"]["name"] for t in (node.get("topics") or {}).get("edges", []))
                desc = " ".join(x for x in [node.get("tagline"), node.get("description"), topics] if x)
                if upsert_product(
                    conn, name=name, url=node.get("url"),
                    description=desc, source=source, tier=tier,
                    external_id=f"ph-{node.get('id')}", seen_at=(node.get("createdAt") or "")[:10],
                    raw_signal=node.get("votesCount"),
                ):
                    count += 1
            page = posts.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
            if not after:
                break
        conn.commit()
        log.info("%s: ingested %d products (last %d days, top voted)", source, count, days_back)
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


def ingest_betalist(conn):
    """Pre-launch startups from the BetaList homepage. Tier: mid.

    BetaList has no public JSON or RSS (verified 2026-06-22: /feed, /startups,
    /sitemap.xml all 404). The homepage embeds startup cards we parse directly:
        <a href="/startups/SLUG"><div class="font-medium ...">NAME</div></a>
        <div class="text-gray-600 ...">TAGLINE</div>
    If the markup changes, this logs a specific 0-row reason instead of failing.
    """
    source, tier = "betalist", SOURCE_TIERS["betalist"]
    count = 0
    try:
        resp = http_get("https://betalist.com/", headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        })
        if resp.status_code != 200:
            log.warning("%s: homepage HTTP %s", source, resp.status_code)
            return 0
        pattern = re.compile(
            r'href="/startups/([a-z0-9\-]+)">\s*<div class="font-medium[^"]*">([^<]+)</div>'
            r'.*?<div class="text-gray-600[^"]*">([^<]+)</div>',
            re.DOTALL,
        )
        seen = set()
        for slug, name, tagline in pattern.findall(resp.text):
            if slug in seen:
                continue
            seen.add(slug)
            name = name.strip()
            tagline = tagline.strip()
            if not name:
                continue
            if upsert_product(
                conn, name=name, url=f"https://betalist.com/startups/{slug}",
                description=tagline, source=source, tier=tier,
                external_id=f"betalist-{slug}", seen_at=dt.date.today().isoformat(),
                raw_signal=None,
            ):
                count += 1
        conn.commit()
        if count == 0:
            log.warning("%s: homepage parsed but 0 cards matched, markup may have changed", source)
        else:
            log.info("%s: ingested %d products", source, count)
    except Exception as exc:
        log.error("%s: failed: %s", source, exc)
    return count


# --------------------------------------------------------------------------
# Classifier (Task 3). Heuristic by default, model when ANTHROPIC_API_KEY is set.
# Both only ever emit a SUBDOMAINS label or "uncategorized".
# --------------------------------------------------------------------------

_CUE_CACHE = None


def _build_cues():
    """Compile each cue to a word-boundary regex once.

    Boundaries stop generic tokens from matching inside other words, so 'space'
    no longer fires on 'workspace' and 'code' will not match 'barcode'. Longer
    cues carry more specificity weight.
    """
    cache = {}
    for sub, cues in KEYWORDS.items():
        compiled = []
        for cue in cues:
            pat = re.compile(r"(?<![a-z0-9])" + re.escape(cue) + r"(?![a-z0-9])")
            compiled.append((pat, 1.0 + 0.15 * len(cue.split())))
        cache[sub] = compiled
    return cache


def classify_text_heuristic(text):
    """Pick the best SUBDOMAINS label by keyword cues, else 'uncategorized'.

    A specific phrase ("clinical note") scores higher than a generic token, and
    the highest-scoring sub-domain wins. Deterministic and offline.
    """
    global _CUE_CACHE
    if _CUE_CACHE is None:
        _CUE_CACHE = _build_cues()
    if not text:
        return UNCATEGORIZED
    low = text.lower()
    best, best_score = UNCATEGORIZED, 0.0
    for sub, cues in _CUE_CACHE.items():
        score = 0.0
        for pat, spec in cues:
            if pat.search(low):
                score += spec
        if score > best_score:
            best, best_score = sub, score
    return best


def _anthropic_classify_batch(client, items):
    """Classify a batch of (id, text) with the model. Returns {id: label}.

    The prompt hands the model the fixed vocabulary and forbids new labels.
    Any label the model returns that is not in the vocabulary is dropped to
    'uncategorized', so the vocabularies cannot drift apart.
    """
    vocab = "\n".join(f"- {s}" for s in SUBDOMAINS)
    lines = "\n".join(f'{i}. {text[:300]}' for i, (pid, text) in enumerate(items))
    prompt = (
        "You label startups and open-source projects into a fixed taxonomy of "
        "venture sub-domains. Pick the single closest label for each item. If "
        "none fit, use \"uncategorized\". Never invent a label outside the list.\n\n"
        f"Allowed labels:\n{vocab}\n- {UNCATEGORIZED}\n\n"
        "Items:\n" + lines + "\n\n"
        "Reply with a JSON object mapping each item number (as a string) to its "
        "label. Only JSON, no prose."
    )
    msg = client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    mapping = json.loads(raw)
    valid = set(SUBDOMAINS) | {UNCATEGORIZED}
    out = {}
    for idx_str, label in mapping.items():
        try:
            pid = items[int(idx_str)][0]
        except (ValueError, IndexError):
            continue
        label = label if label in valid else UNCATEGORIZED
        out[pid] = label
    return out


def classify_all(conn, reclassify=False, batch_size=30):
    """Classify products into SUBDOMAINS. Caches labels on products.subdomain.

    reclassify=True relabels every row (use after the taxonomy changes). With no
    ANTHROPIC_API_KEY, or if any model call fails, falls back to the heuristic so
    the run never breaks.
    """
    cur = conn.cursor()
    if reclassify:
        rows = cur.execute("SELECT id, name, description FROM products").fetchall()
    else:
        rows = cur.execute("SELECT id, name, description FROM products WHERE subdomain IS NULL").fetchall()
    if not rows:
        log.info("classify: nothing to do")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = None
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            log.info("classify: using model %s for %d products", CLASSIFIER_MODEL, len(rows))
        except Exception as exc:
            log.warning("classify: anthropic unavailable (%s), using heuristic", exc)
            client = None
    else:
        log.info("classify: no ANTHROPIC_API_KEY, using heuristic for %d products", len(rows))

    labeled = 0
    items = [(r["id"], (r["name"] or "") + ". " + (r["description"] or "")) for r in rows]
    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]
        results = {}
        if client is not None:
            try:
                results = _anthropic_classify_batch(client, chunk)
            except Exception as exc:
                # One failure (bad key, network, quota) is enough. Stop calling
                # the API and finish the run on the heuristic.
                log.warning("classify: model call failed (%s), switching to heuristic for the rest", exc)
                client = None
                results = {}
        for pid, text in chunk:
            label = results.get(pid) or classify_text_heuristic(text)
            cur.execute("UPDATE products SET subdomain=? WHERE id=?", (label, pid))
            labeled += 1
    conn.commit()
    n_uncat = cur.execute("SELECT COUNT(*) c FROM products WHERE subdomain=?", (UNCATEGORIZED,)).fetchone()["c"]
    log.info("classify: labeled %d products (%d uncategorized)", labeled, n_uncat)
    return labeled


# --------------------------------------------------------------------------
# Demand enrichment (Task 1)
# --------------------------------------------------------------------------
# Hand-seeded demand from public VC requests for startups. Weights are 0..3:
# 3 = many funds asking loudly, 2 = clearly active thesis, 1 = present but quiet.
# Every label here is in SUBDOMAINS, which is what makes supply and demand meet.
HANDSEED_RFS = [
    ("ai coding agents", 3.0, "Top RFS theme across YC, a16z, Sequoia: agents that write and ship code"),
    ("ai agent infrastructure", 3.0, "Heavy investor focus on agent runtimes, memory, orchestration"),
    ("agentic sdr", 2.5, "Named GTM-agent thesis at multiple seed funds"),
    ("voice ai agents", 2.5, "Voice agents called out in YC RFS and a16z voice thesis"),
    ("ai customer support agents", 2.0, "Crowded but still actively funded support-automation thesis"),
    ("ai medical scribing", 2.5, "Ambient clinical documentation, repeated RFS mentions"),
    ("healthcare revenue cycle", 2.5, "Prior auth and RCM automation, strong payer-pain thesis"),
    ("ai clinical decision support", 2.0, "Funded but regulated clinical-AI thesis"),
    ("ai drug discovery", 2.5, "Bio plus AI, sustained fund interest"),
    ("ai medical imaging", 1.5, "Radiology and pathology AI, steady interest"),
    ("ai-native law firm", 2.0, "AI-native services firm thesis (legal)"),
    ("ai legal copilot", 2.5, "Contract review and legal research, named RFS theme"),
    ("inference chips", 3.0, "Inference-cost crisis, top hardware thesis"),
    ("ai chip interconnect", 2.0, "Photonics and chiplets for AI clusters"),
    ("datacenter cooling", 2.0, "Liquid and immersion cooling for AI datacenters"),
    ("energy for data centers", 3.0, "Powering AI datacenters, very loud RFS theme"),
    ("nuclear and fusion", 2.5, "SMR and fusion for compute power, strong thesis"),
    ("battery and energy storage", 2.0, "Grid storage to back AI load"),
    ("grid software", 1.5, "VPP and grid-management software"),
    ("climate and carbon capture", 1.5, "Carbon removal, steady but quieter than 2022"),
    ("physical ai data", 2.5, "Data for robot manipulation, named RFS theme"),
    ("robotics and humanoids", 3.0, "Humanoids and industrial robots, top hardware thesis"),
    ("autonomous vehicles", 1.5, "Mature thesis, selective new funding"),
    ("drones and defense autonomy", 2.5, "Autonomous defense systems, hot thesis"),
    ("defense tech", 3.0, "Defense and national security, very active fund focus"),
    ("space and satellites", 2.0, "Launch and earth observation, steady interest"),
    ("ai security", 2.5, "Securing models and agents, named RFS theme"),
    ("ai compliance and grc", 2.0, "Compliance automation, active thesis"),
    ("vector and retrieval infra", 1.5, "Retrieval infra, crowded but funded"),
    ("llm observability and evals", 2.0, "Evals and observability for LLM apps"),
    ("synthetic data", 1.5, "Synthetic and training data generation"),
    ("ai video generation", 2.0, "Generative video, hot but capital intensive"),
    ("ai voice and music generation", 1.5, "Audio generation, active but narrower"),
    ("ai recruiting", 1.5, "Recruiting automation, steady"),
    ("ai accounting and bookkeeping", 2.0, "AI bookkeeping and AP/AR, active vertical thesis"),
    ("ai financial analysis", 2.0, "FP&A and analyst copilots"),
    ("ai insurance", 1.5, "Underwriting and claims AI"),
    ("ai tutoring and education", 1.5, "AI tutoring, steady consumer and edu interest"),
    ("ai marketing content", 1.0, "Content generation, very crowded"),
    ("fintech infrastructure", 2.0, "Payments and embedded-finance infra"),
    ("stablecoin and crypto payments", 2.5, "Stablecoin rails, renewed strong interest"),
    ("ai devops and sre", 2.0, "Agentic incident response and SRE"),
    ("on-device and edge ai", 1.5, "Local and edge inference"),
    ("ai data engineering", 1.5, "Agentic data pipelines and ETL"),
    ("ai search and answer engines", 2.0, "Answer engines and enterprise search"),
    ("ai code review", 2.0, "Automated code review, active thesis"),
]


def seed_handseed_demand(conn):
    """Insert the curated RFS demand rows. Idempotent: clears its own source first."""
    cur = conn.cursor()
    cur.execute("DELETE FROM demand_signals WHERE source='rfs_handseed'")
    today = dt.date.today().isoformat()
    n = 0
    for sub, weight, detail in HANDSEED_RFS:
        if sub not in SUBDOMAINS:
            log.warning("handseed: '%s' not in SUBDOMAINS, skipping", sub)
            continue
        cur.execute(
            "INSERT INTO demand_signals (subdomain, source, weight, detail, seen_at) VALUES (?,?,?,?,?)",
            (sub, "rfs_handseed", float(weight), detail, today),
        )
        n += 1
    conn.commit()
    log.info("demand: seeded %d hand-seed RFS rows", n)
    return n


def fetch_pitchbook_funding():
    """Return recent funding grouped by sector, or [] if no source is reachable.

    This is the integration point for PitchBook demand. A standalone Python
    script cannot call an MCP tool directly, so this looks for funding data in
    two places that an operator (or an agent with a PitchBook MCP) can populate:

      1. data/pitchbook_funding.json, a cached export shaped as:
         [{"sector": "...", "deal_count": int, "total_raised_usd": number,
           "window": "last_12_months"}, ...]
      2. a PitchBook REST endpoint, if PITCHBOOK_API_KEY is set.

    When neither is present it logs the reason and returns [], and the pipeline
    falls back to the hand-seed. Sector strings are mapped to SUBDOMAINS by
    map_sector_to_subdomain below.
    """
    cache = os.path.join(DATA_DIR, "pitchbook_funding.json")
    if os.path.exists(cache):
        try:
            with open(cache) as fh:
                rows = json.load(fh)
            log.info("pitchbook: loaded %d sector rows from %s", len(rows), cache)
            return rows
        except Exception as exc:
            log.warning("pitchbook: cache present but unreadable (%s)", exc)
            return []

    api_key = os.environ.get("PITCHBOOK_API_KEY")
    if api_key:
        # Documented hook. PitchBook's API is private and per-contract, so the
        # exact path is filled in per deployment. Verify the JSON shape with one
        # real call before trusting field names here.
        try:
            log.info("pitchbook: PITCHBOOK_API_KEY set but no endpoint wired for this deployment")
            return []
        except Exception as exc:
            log.warning("pitchbook: API call failed (%s)", exc)
            return []

    log.warning("pitchbook: no MCP connector, cache, or API key reachable, falling back to hand-seed only")
    return []


# Maps PitchBook sector or keyword strings onto SUBDOMAINS. Kept explicit so a
# funding row can only ever land on a label the supply side also uses.
PITCHBOOK_SECTOR_MAP = {
    "artificial intelligence": "ai agent infrastructure",
    "generative ai": "ai agent infrastructure",
    "ai infrastructure": "ai agent infrastructure",
    "developer tools": "ai coding agents",
    "devtools": "ai coding agents",
    "legaltech": "ai legal copilot",
    "legal tech": "ai legal copilot",
    "healthtech": "ai clinical decision support",
    "digital health": "ai clinical decision support",
    "medical devices": "ai medical imaging",
    "biotech": "ai drug discovery",
    "drug discovery": "ai drug discovery",
    "semiconductors": "inference chips",
    "chips": "inference chips",
    "energy": "energy for data centers",
    "energy storage": "battery and energy storage",
    "nuclear": "nuclear and fusion",
    "fusion": "nuclear and fusion",
    "climate tech": "climate and carbon capture",
    "robotics": "robotics and humanoids",
    "autonomous vehicles": "autonomous vehicles",
    "defense": "defense tech",
    "aerospace": "space and satellites",
    "space": "space and satellites",
    "cybersecurity": "ai security",
    "security": "ai security",
    "fintech": "fintech infrastructure",
    "payments": "fintech infrastructure",
    "crypto": "stablecoin and crypto payments",
    "blockchain": "stablecoin and crypto payments",
    "insurtech": "ai insurance",
    "hr tech": "ai recruiting",
    "edtech": "ai tutoring and education",
    "sales tech": "agentic sdr",
    "customer support": "ai customer support agents",
    "data infrastructure": "ai data engineering",
}


def map_sector_to_subdomain(sector):
    if not sector:
        return None
    s = sector.strip().lower()
    if s in PITCHBOOK_SECTOR_MAP:
        return PITCHBOOK_SECTOR_MAP[s]
    for key, sub in PITCHBOOK_SECTOR_MAP.items():
        if key in s or s in key:
            return sub
    return None


def seed_pitchbook_demand(conn):
    """Map PitchBook funding rows to SUBDOMAINS and insert weighted demand.

    Weight is scaled from deal count and total raised, normalized to 0..3 so it
    sits on the same scale as the hand-seed. Funding rows are additive: the
    hand-seed RFS rows stay. Idempotent: clears its own source first.
    """
    cur = conn.cursor()
    cur.execute("DELETE FROM demand_signals WHERE source='pitchbook'")
    rows = fetch_pitchbook_funding()
    if not rows:
        return 0

    # Normalize across the batch so weights span 0..3 by relative funding heat.
    def heat(r):
        return (r.get("deal_count") or 0) * 1.0 + (r.get("total_raised_usd") or 0) / 1e8

    by_sub = {}
    for r in rows:
        sub = map_sector_to_subdomain(r.get("sector"))
        if not sub or sub not in SUBDOMAINS:
            continue
        by_sub.setdefault(sub, {"deal_count": 0, "total_raised_usd": 0.0})
        by_sub[sub]["deal_count"] += r.get("deal_count") or 0
        by_sub[sub]["total_raised_usd"] += r.get("total_raised_usd") or 0.0
    if not by_sub:
        log.warning("pitchbook: funding rows present but none mapped to SUBDOMAINS")
        return 0

    heats = {sub: heat(v) for sub, v in by_sub.items()}
    hi = max(heats.values()) or 1.0
    today = dt.date.today().isoformat()
    n = 0
    for sub, agg in by_sub.items():
        weight = round(3.0 * heats[sub] / hi, 2)
        detail = f"PitchBook last 12 months: {agg['deal_count']} deals, ${agg['total_raised_usd']/1e6:.0f}M raised"
        cur.execute(
            "INSERT INTO demand_signals (subdomain, source, weight, detail, seen_at) VALUES (?,?,?,?,?)",
            (sub, "pitchbook", weight, detail, today),
        )
        n += 1
    conn.commit()
    log.info("demand: seeded %d PitchBook funding rows", n)
    return n


def seed_demand(conn):
    """Populate demand_signals from the hand-seed RFS rows.

    PitchBook enrichment is wired up (seed_pitchbook_demand, map_sector_to_subdomain,
    fetch_pitchbook_funding) but turned off for now per request, so demand comes
    from the hand-seed only. Re-enable by uncommenting the call below once a
    PitchBook source is available.
    """
    n = seed_handseed_demand(conn)
    # PitchBook disabled for now. Uncomment to re-enable additive funding rows:
    # n += seed_pitchbook_demand(conn)
    return n


# --------------------------------------------------------------------------
# Scorer. Stable interface: score_whitespace(conn) -> list[dict] sorted by score.
# --------------------------------------------------------------------------

def _median(vals):
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return 0.0
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _supply_hi(supplies):
    """Dynamic 'crowded' line: 70th percentile of non-zero supply, floored."""
    vals = sorted(v for v in supplies if v > 0)
    if not vals:
        return SUPPLY_HI_FLOOR
    idx = int(0.7 * (len(vals) - 1))
    return max(SUPPLY_HI_FLOOR, vals[idx])


def score_whitespace(conn):
    """Score every sub-domain that has supply or demand.

    score = demand * leading_factor / (1 + supply)
      demand        = summed demand_signals weight for the sub-domain
      supply        = distinct products classified into the sub-domain
      leading_ratio = leading-tier appearances / all appearances for that supply
      leading_factor= 1 + LEADING_WEIGHT * leading_ratio

    High score means capital is asking, few are building, and the few who are
    show up on leading sources (an early signal). Also assigns a quadrant.
    """
    cur = conn.cursor()
    demand = {row["subdomain"]: row["d"] for row in cur.execute(
        "SELECT subdomain, SUM(weight) d FROM demand_signals GROUP BY subdomain"
    )}
    supply_rows = cur.execute(
        "SELECT subdomain, COUNT(*) s FROM products WHERE subdomain IS NOT NULL AND subdomain!=? GROUP BY subdomain",
        (UNCATEGORIZED,),
    ).fetchall()
    supply = {r["subdomain"]: r["s"] for r in supply_rows}

    # Leading-tier appearance counts per sub-domain.
    lead_rows = cur.execute(
        """
        SELECT p.subdomain AS sub,
               SUM(CASE WHEN a.tier='leading' THEN 1 ELSE 0 END) AS lead,
               COUNT(*) AS total
        FROM appearances a JOIN products p ON p.id=a.product_id
        WHERE p.subdomain IS NOT NULL AND p.subdomain!=?
        GROUP BY p.subdomain
        """,
        (UNCATEGORIZED,),
    ).fetchall()
    lead = {r["sub"]: (r["lead"], r["total"]) for r in lead_rows}

    subs = set(demand) | set(supply)
    supply_hi = _supply_hi(list(supply.values()))
    # Demand split is relative: the median ask, but never below the DEMAND_HI
    # floor. Demand is seeded broadly, so a relative line is what separates the
    # loud themes from the quiet ones and keeps all four quadrants meaningful.
    demand_hi = max(DEMAND_HI, _median([v for v in demand.values() if v > 0]))

    out = []
    for sub in subs:
        d = float(demand.get(sub, 0.0))
        s = int(supply.get(sub, 0))
        lead_n, total_n = lead.get(sub, (0, 0))
        leading_ratio = (lead_n / total_n) if total_n else 0.0
        leading_factor = 1.0 + LEADING_WEIGHT * leading_ratio
        score = d * leading_factor / (1.0 + s)

        demand_high = d >= demand_hi
        supply_high = s >= supply_hi
        if demand_high and not supply_high:
            quadrant = "whitespace"
        elif demand_high and supply_high:
            quadrant = "ride the wave"
        elif not demand_high and supply_high:
            quadrant = "saturated"
        else:
            quadrant = "too early"

        out.append({
            "subdomain": sub,
            "demand": round(d, 2),
            "supply": s,
            "leading_appearances": lead_n,
            "total_appearances": total_n,
            "leading_ratio": round(leading_ratio, 2),
            "score": round(score, 3),
            "quadrant": quadrant,
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


# --------------------------------------------------------------------------
# Week-over-week movers
# --------------------------------------------------------------------------

def weekly_movers(conn, top=10):
    """Sub-domains with the biggest change in appearances, last 7 days vs prior 7."""
    cur = conn.cursor()
    today = dt.date.today()
    wk1 = (today - dt.timedelta(days=7)).isoformat()
    wk2 = (today - dt.timedelta(days=14)).isoformat()
    today_s = today.isoformat()
    rows = cur.execute(
        """
        SELECT p.subdomain AS sub,
               SUM(CASE WHEN a.seen_at >= ? AND a.seen_at <= ? THEN 1 ELSE 0 END) AS recent,
               SUM(CASE WHEN a.seen_at >= ? AND a.seen_at < ?  THEN 1 ELSE 0 END) AS prior
        FROM appearances a JOIN products p ON p.id=a.product_id
        WHERE p.subdomain IS NOT NULL AND p.subdomain!=? AND a.seen_at IS NOT NULL
        GROUP BY p.subdomain
        """,
        (wk1, today_s, wk2, wk1, UNCATEGORIZED),
    ).fetchall()
    movers = [{"subdomain": r["sub"], "recent": r["recent"], "prior": r["prior"], "delta": r["recent"] - r["prior"]}
              for r in rows if (r["recent"] or r["prior"])]
    movers.sort(key=lambda r: (r["delta"], r["recent"]), reverse=True)
    return movers[:top]


# --------------------------------------------------------------------------
# Report and weekly digest (Task 6). Stable interface: report(conn) -> path.
# --------------------------------------------------------------------------

def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def report(conn):
    """Print a summary and write digests/YYYY-MM-DD.md. Returns the file path."""
    scored = score_whitespace(conn)
    movers = weekly_movers(conn)
    today = dt.date.today().isoformat()

    quadrants = {"whitespace": [], "ride the wave": [], "saturated": [], "too early": []}
    for r in scored:
        quadrants[r["quadrant"]].append(r)

    # The proof that the vocabularies line up: rows with supply and demand both
    # greater than zero. These are the real whitespace candidates.
    both = [r for r in scored if r["demand"] > 0 and r["supply"] > 0]
    whitespace_ranked = [r for r in both if r["quadrant"] == "whitespace"]

    lines = []
    lines.append(f"# Whitespace digest, {today}")
    lines.append("")
    lines.append("Sub-domains where VC demand is high and builder supply is low. "
                 "Demand is on a 0..3 scale from public VC requests for startups. "
                 "Supply is distinct builders seen across Show HN, GitHub, Hugging Face, Product Hunt, BetaList, and YC.")
    lines.append("")

    lines.append("## Top whitespace (demand and supply both present)")
    lines.append("")
    if whitespace_ranked:
        lines.append(_table(
            ["sub-domain", "score", "demand", "supply", "leading %"],
            [(r["subdomain"], r["score"], r["demand"], r["supply"], f'{int(r["leading_ratio"]*100)}%') for r in whitespace_ranked[:15]],
        ))
    else:
        lines.append("_No sub-domain currently has high demand and low supply with both populated. "
                     "See the full ranking below._")
    lines.append("")

    lines.append("## Full ranking (top 20 by score)")
    lines.append("")
    lines.append(_table(
        ["sub-domain", "score", "demand", "supply", "quadrant"],
        [(r["subdomain"], r["score"], r["demand"], r["supply"], r["quadrant"]) for r in scored[:20]],
    ))
    lines.append("")

    lines.append("## Biggest week-over-week supply movers")
    lines.append("")
    if movers:
        lines.append(_table(
            ["sub-domain", "last 7d", "prior 7d", "delta"],
            [(m["subdomain"], m["recent"], m["prior"], f'{m["delta"]:+d}') for m in movers],
        ))
    else:
        lines.append("_No dated appearances in the trailing two weeks._")
    lines.append("")

    lines.append("## Four-quadrant breakdown")
    lines.append("")
    order = [
        ("whitespace", "high demand, low supply. Where to look first."),
        ("ride the wave", "high demand, high supply. Real but crowded."),
        ("saturated", "low demand, high supply. Many builders, little fresh capital."),
        ("too early", "low demand, low supply. Nobody is asking yet."),
    ]
    for name, gloss in order:
        items = sorted(quadrants[name], key=lambda r: r["score"], reverse=True)
        lines.append(f"### {name} ({len(items)})")
        lines.append(f"_{gloss}_")
        lines.append("")
        if items:
            lines.append(", ".join(f'{r["subdomain"]} (d{r["demand"]}/s{r["supply"]})' for r in items[:18]))
        else:
            lines.append("_none_")
        lines.append("")

    text = "\n".join(lines)

    os.makedirs(DIGEST_DIR, exist_ok=True)
    path = os.path.join(DIGEST_DIR, f"{today}.md")
    with open(path, "w") as fh:
        fh.write(text + "\n")

    # Console summary.
    print()
    print(f"Whitespace digest written to {path}")
    print()
    print("Top whitespace (demand and supply both present):")
    if whitespace_ranked:
        for r in whitespace_ranked[:10]:
            print(f"  {r['score']:>6}  {r['subdomain']:<32} demand {r['demand']}  supply {r['supply']}  leading {int(r['leading_ratio']*100)}%")
    else:
        for r in both[:10]:
            print(f"  {r['score']:>6}  {r['subdomain']:<32} demand {r['demand']}  supply {r['supply']}  ({r['quadrant']})")
    print()
    counts = {k: len(v) for k, v in quadrants.items()}
    print("Quadrants: " + ", ".join(f"{k} {counts[k]}" for k, _ in order))
    return path


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run_connectors(conn):
    """Run every supply connector. Returns {source: count}. Failures are logged
    inside each connector and do not stop the others."""
    return {
        "show_hn": ingest_show_hn(conn),
        "github": ingest_github(conn),
        "huggingface": ingest_huggingface(conn),
        "arxiv": ingest_arxiv(conn),
        "product_hunt": ingest_product_hunt(conn),
        "betalist": ingest_betalist(conn),
        "yc": ingest_yc(conn),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Whitespace Trend Tracker")
    parser.add_argument("--reclassify", action="store_true", help="relabel every product, not just new ones")
    parser.add_argument("--skip-ingest", action="store_true", help="reuse the existing DB, do not fetch sources")
    args = parser.parse_args(argv)

    setup_logging()
    load_dotenv()
    conn = init_db()

    if not args.skip_ingest:
        counts = run_connectors(conn)
        log.info("ingest totals: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))

    classify_all(conn, reclassify=args.reclassify)
    seed_demand(conn)
    report(conn)
    conn.close()


if __name__ == "__main__":
    main()
