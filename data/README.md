# PitchBook demand cache

`whitespace_tracker.py` enriches demand from PitchBook funding when it can reach
the data. A standalone Python script cannot call an MCP tool directly, so the
connector (`fetch_pitchbook_funding`) looks for funding data in two places:

1. A cached export at `data/pitchbook_funding.json` (this directory).
2. A PitchBook REST endpoint, if `PITCHBOOK_API_KEY` is set.

If neither is present, the pipeline logs the reason and falls back to the
hand-seeded RFS demand, so a run never depends on PitchBook being reachable.

## Cache format

`pitchbook_funding.json` is a JSON array of sector roll-ups for the last 12
months. One object per sector or keyword:

```json
[
  {"sector": "semiconductors", "deal_count": 41, "total_raised_usd": 3200000000, "window": "last_12_months"},
  {"sector": "robotics",       "deal_count": 88, "total_raised_usd": 2100000000, "window": "last_12_months"},
  {"sector": "legaltech",      "deal_count": 23, "total_raised_usd": 410000000,  "window": "last_12_months"}
]
```

Fields:

- `sector` (string): PitchBook sector or keyword. Mapped onto the fixed
  `SUBDOMAINS` vocabulary by `PITCHBOOK_SECTOR_MAP` in `whitespace_tracker.py`.
  A sector that does not map is skipped, so funding can only ever land on a label
  the supply side also uses.
- `deal_count` (int): number of deals in the window.
- `total_raised_usd` (number): total raised in the window, in USD.
- `window` (string, optional): for your own bookkeeping. The connector does not
  read it.

The connector normalizes `deal_count` and `total_raised_usd` across the batch to
a 0..3 weight, matching the hand-seed scale, and inserts one additive
`demand_signals` row per mapped sub-domain. The hand-seed RFS rows are kept.

## How an operator (or an agent with a PitchBook MCP) populates this

Pull recent funding grouped by sector from PitchBook, shape it as above, and
write it here. The next `python whitespace_tracker.py` run picks it up and the
log line changes from "falling back to hand-seed only" to "loaded N sector
rows".
