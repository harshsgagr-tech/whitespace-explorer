import { useEffect, useState } from "react";
import { api, type Brief, type Signal } from "../api";

const ACTIONS: Record<string, { tone: string; hint: string }> = {
  Build: { tone: "build", hint: "Demand is high, builders are early, YC has not piled in" },
  Wedge: { tone: "wedge", hint: "Category is crowded, but this sub-theme is still open" },
  Watch: { tone: "watch", hint: "Forming on the edge, demand not yet proven" },
  Wait: { tone: "wait", hint: "Very early, too few builders to call" },
  Avoid: { tone: "avoid", hint: "Crowded and already mainstream" },
  Crossing: { tone: "crossing", hint: "Moving from the edge into the mainstream" },
};

function fmtSince(d: string | null): string {
  if (!d) return "";
  const dt = new Date(d + "T00:00:00");
  if (isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString(undefined, { year: "numeric", month: "short" });
}

export function SignalsView() {
  const [signals, setSignals] = useState<Signal[] | null>(null);

  useEffect(() => {
    api.momentum().then(setSignals).catch(() => setSignals([]));
  }, []);

  return (
    <div className="signals">
      <div className="sig-intro">
        <h2 className="sig-h">What is forming</h2>
        <p className="sig-p">
          Sub-themes ranked by builder and research activity on leading sources (Show HN, GitHub,
          Hugging Face, arXiv) that has not yet shown up in YC. A high leading edge with a thin YC
          presence is the earliest sign of a category forming. Each row carries an action label and a
          thesis you can generate on demand.
        </p>
      </div>

      {signals === null ? (
        <div className="sig-list">
          {[0, 1, 2, 3, 4].map((i) => (
            <div className="sig-card" key={i} aria-busy="true">
              <div className="sk sk-name" />
              <div className="sk sk-line" />
              <div className="sk sk-line short" />
            </div>
          ))}
        </div>
      ) : signals.length === 0 ? (
        <div className="empty">No signals yet. The pipeline and subcategorize.py need to have run.</div>
      ) : (
        <div className="sig-list">
          {signals.map((s) => (
            <SignalCard key={s.subdomain + "|" + s.subcategory} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function SignalCard({ s }: { s: Signal }) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const tone = (ACTIONS[s.action] ?? ACTIONS.Crossing).tone;
  const hint = (ACTIONS[s.action] ?? ACTIONS.Crossing).hint;

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !brief && !loading) {
      setLoading(true);
      api
        .brief(s.subdomain, s.subcategory)
        .then(setBrief)
        .catch(() => setBrief({ summary: "Could not generate a thesis.", gap: "", risk: "" }))
        .finally(() => setLoading(false));
    }
  };

  return (
    <div className="sig-card">
      <div className="sig-top">
        <div className="sig-titles">
          <span className="sig-name">{s.subcategory}</span>
          <span className="sig-parent">{s.subdomain}</span>
        </div>
        <span className={"act act-" + tone} title={hint}>
          {s.action}
        </span>
      </div>

      <div className="sig-stats num">
        <span className="sig-stat">
          <b>{s.supply}</b> builders
        </span>
        <span className="sig-dot">·</span>
        <span className="sig-stat sig-edge">
          <b>{Math.round(s.leading_ratio * 100)}%</b> leading edge
        </span>
        <span className="sig-arrow">to</span>
        <span className="sig-stat">
          <b>{s.yc}</b> in YC
        </span>
        <span className="sig-dot">·</span>
        <span className="sig-stat">
          demand <b>{s.demand}</b>
        </span>
        {s.first_seen && (
          <>
            <span className="sig-dot">·</span>
            <span className="sig-stat sig-muted">since {fmtSince(s.first_seen)}</span>
          </>
        )}
      </div>

      {s.companies.length > 0 && (
        <div className="sig-builders">
          <span className="sig-builders-label">building</span>
          {s.companies.map((c, i) =>
            c.url ? (
              <a key={i} className="sig-co" href={c.url} target="_blank" rel="noopener noreferrer">
                {c.name}
              </a>
            ) : (
              <span key={i} className="sig-co">
                {c.name}
              </span>
            )
          )}
        </div>
      )}

      <button className={"sig-thesis-btn" + (open ? " is-open" : "")} onClick={toggle}>
        {loading ? "Writing thesis..." : open ? "Hide thesis" : "Generate thesis"}
      </button>

      {open && (loading || brief) && (
        <div className="sig-brief">
          {loading ? (
            <>
              <div className="sk sk-line" />
              <div className="sk sk-line" />
              <div className="sk sk-line short" />
            </>
          ) : (
            brief && (
              <>
                <p className="sig-summary">{brief.summary}</p>
                {brief.gap && (
                  <p className="sig-bline">
                    <span className="sig-btag tag-gap">Gap</span>
                    {brief.gap}
                  </p>
                )}
                {brief.risk && (
                  <p className="sig-bline">
                    <span className="sig-btag tag-risk">Risk</span>
                    {brief.risk}
                  </p>
                )}
              </>
            )
          )}
        </div>
      )}
    </div>
  );
}
