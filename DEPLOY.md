# Deploy (shareable public link)

The app is packaged as one secrets-free Docker service: FastAPI serves the API
and the built frontend from a single origin. The thesis briefs are pre-baked into
`data/briefs.json`, so the running service needs no API key and spends nothing.
Your `.env` (real tokens) is gitignored and never ships.

## Option A: Render (free, public URL, recommended)

1. Put this folder in a GitHub repo (see "Push to GitHub" below).
2. Go to https://dashboard.render.com → New → Blueprint, and pick the repo.
   Render reads `render.yaml`, builds the `Dockerfile`, and deploys on the free
   plan. No environment variables to set.
3. You get a public `https://whitespace-explorer.onrender.com` style URL. Share it.

The free plan sleeps after about 15 minutes idle, so the first visit after a nap
takes ~30 seconds to wake. Fine for sharing; upgrade the plan if you want it warm.

## Push to GitHub

A git repo is already initialized and committed here. Create an empty repo at
https://github.com/new (call it whatever), then:

```bash
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```

## Option B: Any Docker host (Fly.io, Railway, Cloud Run, your own box)

```bash
docker build -t whitespace-explorer .
docker run -p 8000:8000 whitespace-explorer
# open http://localhost:8000
```

That image runs anywhere. For Fly.io: `fly launch` (it detects the Dockerfile),
then `fly deploy`.

## Optional: live thesis generation

The deploy serves pre-baked briefs only, which covers every signal shown. If you
later want the server to generate a thesis for a brand-new sub-theme on demand,
set `ANTHROPIC_API_KEY` as a service environment variable on your host. That puts
a billable key on a public service, so leave it off unless you need it.

## Refresh the data later

Re-run the pipeline locally, re-bake briefs, rebuild, and push:

```bash
python whitespace_tracker.py        # refresh supply and classification
python subcategorize.py             # refresh subcategories
# (re-bake briefs: hit /api/brief for the top signals against a local run)
cd web && npm run build && cd ..
git commit -am "refresh data" && git push
```
