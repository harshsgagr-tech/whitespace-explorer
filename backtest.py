#!/usr/bin/env python3
"""Back-test the leading-to-lagging assumption.

The scorer in whitespace_tracker.py up-weights sub-domains that cluster on
leading sources (Show HN, GitHub, Hugging Face) on the bet that leading activity
foreshadows the next YC batch. This script tests that bet instead of trusting it.

Method:
  1. Pick a cutoff date.
  2. Spike set: sub-domains with at least MIN_LEADING leading-source appearances
     strictly before the cutoff (the early signal).
  3. Following batch: sub-domains among YC companies that launched on or after
     the cutoff (the lagging outcome we wanted to predict).
  4. Hit rate: share of spiked sub-domains that then showed up in YC.
  5. Compare to the base rate (share of all sub-domains that showed up in YC
     anyway). Lift above 1.0 means the leading signal carries information.

If the sample is thin or the lift is at or below 1.0, the script says so plainly
so the leading-edge weight can be lowered rather than trusted.

Run:
  python backtest.py
  python backtest.py --cutoff 2026-03-01 --min-leading 2
  python backtest.py --prior-days 120        # only count leading signal in a window
"""

import argparse
import datetime as dt
import sqlite3

from whitespace_tracker import DB_PATH, LEADING_WEIGHT, SUBDOMAINS, UNCATEGORIZED, SOURCE_TIERS

LEADING_SOURCES = [s for s, t in SOURCE_TIERS.items() if t == "leading"]


def _default_cutoff(conn):
    """Default cutoff: 90 days before the most recent appearance in the DB."""
    row = conn.execute("SELECT MAX(seen_at) m FROM appearances WHERE seen_at IS NOT NULL").fetchone()
    if not row or not row[0]:
        return dt.date.today().isoformat()
    latest = dt.date.fromisoformat(row[0][:10])
    return (latest - dt.timedelta(days=90)).isoformat()


def spike_set(conn, cutoff, min_leading, prior_days=None):
    """Sub-domains that spiked on leading sources before the cutoff.

    Returns {subdomain: leading_count}. If prior_days is set, only appearances in
    [cutoff - prior_days, cutoff) count, so an old long-tail does not look like a
    spike.
    """
    lower = "0000-00-00"
    if prior_days:
        lower = (dt.date.fromisoformat(cutoff) - dt.timedelta(days=prior_days)).isoformat()
    placeholders = ",".join("?" for _ in LEADING_SOURCES)
    rows = conn.execute(
        f"""
        SELECT p.subdomain AS sub, COUNT(*) AS n
        FROM appearances a JOIN products p ON p.id = a.product_id
        WHERE a.source IN ({placeholders})
          AND a.seen_at IS NOT NULL AND a.seen_at >= ? AND a.seen_at < ?
          AND p.subdomain IS NOT NULL AND p.subdomain != ?
        GROUP BY p.subdomain
        HAVING n >= ?
        """,
        (*LEADING_SOURCES, lower, cutoff, UNCATEGORIZED, min_leading),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def following_yc(conn, cutoff):
    """YC companies that launched on or after the cutoff.

    Returns (subdomain_set, company_rows) where company_rows is a list of
    (name, subdomain) for the per-company coverage measure.
    """
    rows = conn.execute(
        """
        SELECT p.name AS name, p.subdomain AS sub
        FROM appearances a JOIN products p ON p.id = a.product_id
        WHERE a.source = 'yc' AND a.seen_at IS NOT NULL AND a.seen_at >= ?
          AND p.subdomain IS NOT NULL AND p.subdomain != ?
        """,
        (cutoff, UNCATEGORIZED),
    ).fetchall()
    subs = set(r[1] for r in rows)
    return subs, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back-test leading-to-lagging prediction")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--cutoff", default=None, help="ISO date splitting prior (leading) from following (YC)")
    parser.add_argument("--min-leading", type=int, default=2, help="min leading appearances to count as a spike")
    parser.add_argument("--prior-days", type=int, default=None, help="only count leading signal within this many days before cutoff")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    cutoff = args.cutoff or _default_cutoff(conn)

    spikes = spike_set(conn, cutoff, args.min_leading, args.prior_days)
    yc_subs, yc_rows = following_yc(conn, cutoff)
    universe = set(SUBDOMAINS)

    print("Leading-to-lagging back-test")
    print("=" * 64)
    print(f"cutoff date            {cutoff}")
    print(f"leading sources        {', '.join(LEADING_SOURCES)}")
    print(f"min leading to spike   {args.min_leading}")
    if args.prior_days:
        print(f"prior window           {args.prior_days} days before cutoff")
    print(f"spiked sub-domains     {len(spikes)}")
    print(f"following YC companies {len(yc_rows)} across {len(yc_subs)} sub-domains")
    print()

    if not spikes or not yc_rows:
        print("VERDICT: not enough data to test. Need leading-source appearances "
              "before the cutoff and YC companies after it.")
        print("Ingest more historical leading signal (older Show HN / GitHub / "
              "Hugging Face) or move the cutoff, then re-run.")
        conn.close()
        return

    # Primary measure: of the sub-domains that spiked on leading sources, what
    # share then appeared in the following YC batch?
    hits = [s for s in spikes if s in yc_subs]
    hit_rate = len(hits) / len(spikes)

    # Base rate: share of all sub-domains that appeared in the following batch,
    # i.e. the chance a random sub-domain shows up in YC regardless of signal.
    base_rate = len(yc_subs & universe) / len(universe)
    lift = (hit_rate / base_rate) if base_rate else float("inf")

    # Coverage: of the following YC companies, what share sit in a sub-domain
    # that had a prior leading spike?
    covered = sum(1 for _name, sub in yc_rows if sub in spikes)
    coverage = covered / len(yc_rows)

    print("Spiked sub-domains and whether they showed up in the next YC batch:")
    for sub, n in sorted(spikes.items(), key=lambda kv: kv[1], reverse=True):
        mark = "hit " if sub in yc_subs else "miss"
        print(f"  [{mark}] {sub:<34} leading={n}")
    print()
    print(f"hit rate (spiked -> appeared in YC)   {hit_rate:.0%}  ({len(hits)}/{len(spikes)})")
    print(f"base rate (any sub-domain -> YC)      {base_rate:.0%}")
    print(f"lift over base rate                   {lift:.2f}x")
    print(f"coverage (YC companies in a spike)    {coverage:.0%}  ({covered}/{len(yc_rows)})")
    print()

    # Verdict. Be explicit so the leading-edge weight is not trusted blindly.
    thin = len(spikes) < 3 or len(yc_subs) < 3
    print("VERDICT")
    print("-" * 64)
    if thin:
        print(f"Sample is thin (spiked={len(spikes)}, following sub-domains={len(yc_subs)}).")
        print("Treat the hit rate as directional only. The leading-edge weight "
              f"(LEADING_WEIGHT={LEADING_WEIGHT}) is neither confirmed nor refuted by this data.")
        print("To test it properly, ingest more historical leading-source data.")
    elif lift > 1.1:
        print(f"Leading-source clustering beats the base rate (lift {lift:.2f}x). "
              f"The leading-edge weight (LEADING_WEIGHT={LEADING_WEIGHT}) is supported by this data.")
        if len(spikes) < 6:
            print(f"Caveat: only {len(spikes)} sub-domains cleared the spike bar, so this is "
                  "suggestive, not conclusive. More historical leading data would firm it up.")
        elif lift < 1.5:
            print("The edge is modest, so keep the weight conservative.")
    else:
        print(f"Leading-source clustering does NOT beat the base rate (lift {lift:.2f}x). "
              f"The leading-edge weight (LEADING_WEIGHT={LEADING_WEIGHT}) is not justified here.")
        print("Consider lowering LEADING_WEIGHT in whitespace_tracker.py toward 0 "
              "until the back-test shows real lift.")
    conn.close()


if __name__ == "__main__":
    main()
