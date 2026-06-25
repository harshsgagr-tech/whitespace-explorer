# Whitespace Trend Tracker

Finds sub-domains where VC demand is high but builder supply is low. The output
is a ranked whitespace list: where capital is asking and few people are building.

Deploy the explorer in one click with
[Render](https://render.com/deploy?repo=https://github.com/harshsgagr-tech/whitespace-explorer),
or see [DEPLOY.md](DEPLOY.md).

## Run

```bash
pip install -r requirements.txt
python whitespace_tracker.py            # full run, heuristic classifier
python backtest.py                      # test the leading-to-lagging assumption
```

Optional environment:

- `ANTHROPIC_API_KEY`: use the model classifier instead of the keyword heuristic.
  If it fails for any reason, the run falls back to the heuristic.
- `GITHUB_TOKEN`: lift the unauthed GitHub search rate limit.
- `PRODUCT_HUNT_TOKEN`: enable the Product Hunt connector (skipped without it).
- `PITCHBOOK_API_KEY` or `data/pitchbook_funding.json`: add PitchBook funding to
  demand (see `data/README.md`). Without it, demand uses the hand-seed only.

Useful flags: `--reclassify` relabels every product, `--skip-ingest` reuses the
existing DB without fetching.

## How it works

- **Supply** is builders seen on six sources, each tagged with a tier:
  Show HN, GitHub, Hugging Face (leading), Product Hunt, BetaList (mid), and YC
  (lagging, the outcome we try to predict).
- **Demand** is how loudly capital is asking, hand-seeded from public VC requests
  for startups and, when reachable, PitchBook funding.
- Supply and demand only meet because both use one fixed vocabulary
  (`SUBDOMAINS`) and one classifier that picks the closest label or
  `uncategorized`, never a new label.
- The **scorer** rewards high demand, low supply, and supply that clusters on
  leading sources. `backtest.py` checks whether that leading-edge bet actually
  predicts the next YC batch.

## Data model (SQLite, three tables)

- `products`: one row per builder, deduped by name and domain, with a classified
  `subdomain`.
- `appearances`: one row per time a product shows up on a source, with `source`,
  `tier`, `seen_at`, and a raw signal (points, stars, votes).
- `demand_signals`: demand weight (0..3) per sub-domain, from `rfs_handseed` and
  `pitchbook`.

## Output

- `digests/YYYY-MM-DD.md`: top whitespace sub-domains, week-over-week supply
  movers, and the four-quadrant breakdown (whitespace, ride the wave, saturated,
  too early). Readable on its own.
- A short summary is printed to the console.

## Files

- `whitespace_tracker.py`: the pipeline (schema, connectors, classifier, demand,
  scorer, report).
- `backtest.py`: leading-to-lagging hit-rate test.
- `data/`: optional PitchBook funding cache (format in `data/README.md`).
- `api.py`: read-only FastAPI shim over the database for the explorer frontend.
- `web/`: the "What people are building" explorer (React, Vite, TypeScript).

## Explorer frontend

A founder-facing explorer built on the same database: a horizontal stacked bar
chart of industries segmented by source, click a bar (or a single source segment)
to slide in the companies, with a second tab for the whitespace quadrant map.

Run the API and the web app in two terminals:

```bash
uvicorn api:app --port 8010 --host 127.0.0.1   # read-only API over whitespace.db
cd web && npm install && npm run dev           # explorer at http://localhost:5173
```

The web app reads `web/.env` (`VITE_API_BASE=http://127.0.0.1:8010`) and calls
the shim directly, with CORS open on the shim. Port 8010 avoids the common 8000
clash; change it in both places if you like. The dev server binds IPv4 (the dev
script passes `--host 0.0.0.0`) so `http://localhost:5173` and
`http://127.0.0.1:5173` both work.

The shim derives two fields the pipeline does not store yet (ai_native,
business_model); everything else maps straight from the database. Industries are
the pipeline sub-domains.
