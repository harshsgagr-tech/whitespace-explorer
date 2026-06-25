#!/usr/bin/env python3
"""Assign a clean, model-derived subcategory to each classified product.

Two passes per sub-domain. First the model reads a sample of the sub-domain's
products and proposes a short set of subcategory labels (what people actually
build inside it). Then it assigns every product to one of those labels, or
"other". Results land in products.subcategory, which the API serves as the
subcategory facet in place of the keyword guess.

Run:
  python subcategorize.py           fill missing subcategories only
  python subcategorize.py --force   redo every product

Needs ANTHROPIC_API_KEY (read from .env).
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys

from whitespace_tracker import (
    CLASSIFIER_MODEL,
    DB_PATH,
    UNCATEGORIZED,
    load_dotenv,
    setup_logging,
)

log = logging.getLogger("subcategorize")


def get_client():
    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.error("no ANTHROPIC_API_KEY in env or .env; subcategorization needs the model")
        return None
    import anthropic

    return anthropic.Anthropic(api_key=key)


def ensure_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(products)")]
    if "subcategory" not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN subcategory TEXT")
        conn.commit()
        log.info("added products.subcategory column")


def _json(text):
    raw = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(raw)


def derive_labels(client, subdomain, samples):
    """Ask the model for a short set of subcategory labels for a sub-domain."""
    body = "\n".join(f"- {s}" for s in samples[:60])
    prompt = (
        f'These are products and projects in the startup sub-domain "{subdomain}".\n\n'
        f"{body}\n\n"
        "Propose 6 to 10 concise subcategory labels (two to four words, lowercase) that "
        "capture the distinct things people are building here. Group by what the product "
        "does, not by who it is for, and do not reuse the words in the sub-domain name. "
        "Return only a JSON array of strings."
    )
    msg = client.messages.create(
        model=CLASSIFIER_MODEL, max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    labels = _json(msg.content[0].text)
    seen, out = set(), []
    for l in labels:
        l = str(l).strip().lower()
        if l and l not in seen:
            seen.add(l)
            out.append(l)
    return out[:10]


def assign_batch(client, subdomain, labels, items):
    """Assign a batch of (id, text) to one of the sub-domain's labels or 'other'."""
    vocab = "\n".join(f"- {l}" for l in labels)
    lines = "\n".join(f"{i}. {t[:240]}" for i, (_pid, t) in enumerate(items))
    prompt = (
        f"Sub-domain: {subdomain}. Assign each item to the single closest subcategory.\n\n"
        f"Subcategories:\n{vocab}\n- other\n\n"
        f"Items:\n{lines}\n\n"
        "Reply with a JSON object mapping each item number (as a string) to its subcategory "
        "label, exactly as written above. Only JSON."
    )
    msg = client.messages.create(
        model=CLASSIFIER_MODEL, max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    mapping = _json(msg.content[0].text)
    valid = set(labels) | {"other"}
    out = {}
    for k, v in mapping.items():
        try:
            pid = items[int(k)][0]
        except (ValueError, IndexError):
            continue
        v = str(v).strip().lower()
        out[pid] = v if v in valid else "other"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Model-derived subcategories")
    ap.add_argument("--force", action="store_true", help="redo every product, not just missing ones")
    ap.add_argument("--batch-size", type=int, default=30)
    args = ap.parse_args(argv)

    setup_logging()
    client = get_client()
    if not client:
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")  # cooperate with the read-only API, do not error
    ensure_column(conn)

    subs = [r[0] for r in conn.execute(
        "SELECT subdomain, COUNT(*) n FROM products WHERE subdomain IS NOT NULL AND subdomain!=? "
        "GROUP BY subdomain ORDER BY n DESC", (UNCATEGORIZED,)
    )]

    vocab_all, total = {}, 0
    for sub in subs:
        where = "subdomain=?" + ("" if args.force else " AND subcategory IS NULL")
        rows = conn.execute(f"SELECT id, name, description FROM products WHERE {where}", (sub,)).fetchall()
        if not rows:
            continue
        sample_rows = conn.execute(
            "SELECT name, description FROM products WHERE subdomain=? LIMIT 80", (sub,)
        ).fetchall()
        samples = [((r["name"] or "") + ": " + (r["description"] or ""))[:200] for r in sample_rows]
        try:
            labels = derive_labels(client, sub, samples)
        except Exception as exc:
            log.warning("%s: label derivation failed (%s), skipping", sub, exc)
            continue
        if not labels:
            log.warning("%s: no labels returned, skipping", sub)
            continue
        vocab_all[sub] = labels
        log.info("%s (%d): %s", sub, len(rows), ", ".join(labels))

        items = [(r["id"], (r["name"] or "") + ". " + (r["description"] or "")) for r in rows]
        for i in range(0, len(items), args.batch_size):
            chunk = items[i:i + args.batch_size]
            try:
                res = assign_batch(client, sub, labels, chunk)
            except Exception as exc:
                log.warning("%s: assign batch failed (%s), marking other", sub, exc)
                res = {}
            for pid, _ in chunk:
                conn.execute("UPDATE products SET subcategory=? WHERE id=?", (res.get(pid, "other"), pid))
                total += 1
            conn.commit()

    data_dir = os.path.join(os.path.dirname(DB_PATH), "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "subcategories.json"), "w") as fh:
        json.dump(vocab_all, fh, indent=2)
    log.info("subcategorized %d products across %d sub-domains", total, len(vocab_all))


if __name__ == "__main__":
    main()
