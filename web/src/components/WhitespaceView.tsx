import { useEffect, useState } from "react";
import { api, type WhitespaceRow } from "../api";

const QUADRANTS: { key: string; title: string; gloss: string; tone: string }[] = [
  { key: "whitespace", title: "Whitespace", gloss: "High demand, low supply. Where to look first.", tone: "good" },
  { key: "ride the wave", title: "Ride the wave", gloss: "High demand, high supply. Real but crowded.", tone: "warm" },
  { key: "too early", title: "Too early", gloss: "Low demand, low supply. Nobody is asking yet.", tone: "cool" },
  { key: "saturated", title: "Saturated", gloss: "Low demand, high supply. Many builders, little fresh capital.", tone: "mute" },
];

export function WhitespaceView() {
  const [rows, setRows] = useState<WhitespaceRow[] | null>(null);

  useEffect(() => {
    api.whitespace().then(setRows).catch(() => setRows([]));
  }, []);

  const byQuad = (key: string) =>
    (rows ?? [])
      .filter((r) => r.quadrant === key)
      .sort((a, b) => b.score - a.score);

  return (
    <div className="whitespace">
      <div className="ws-intro">
        <h2 className="ws-h">Whitespace map</h2>
        <p className="ws-p">
          Every sub-domain placed by what capital is asking for against how many people are building.
          Demand is scored from public VC requests for startups. Supply is distinct builders seen
          across all sources. The top-left cell is the goal: loud demand, thin supply.
        </p>
      </div>

      <div className="ws-grid" aria-label="Whitespace quadrants">
        <div className="ws-axis ws-axis-y">
          <span>Demand</span>
        </div>
        <div className="ws-axis ws-axis-x">
          <span>Supply</span>
        </div>

        {QUADRANTS.map((q) => {
          const items = byQuad(q.key);
          return (
            <section key={q.key} className={"ws-cell tone-" + q.tone}>
              <header className="ws-cell-head">
                <h3 className="ws-cell-title">{q.title}</h3>
                <span className="ws-cell-count num">{items.length}</span>
              </header>
              <p className="ws-cell-gloss">{q.gloss}</p>
              {rows === null ? (
                <div className="ws-chips">
                  {[0, 1, 2, 3, 4].map((i) => (
                    <span key={i} className="sk sk-chip" />
                  ))}
                </div>
              ) : items.length === 0 ? (
                <div className="ws-empty">none</div>
              ) : (
                <div className="ws-chips">
                  {items.map((r) => (
                    <span
                      key={r.subdomain}
                      className="ws-chip"
                      title={`${r.subdomain}: demand ${r.demand}, supply ${r.supply}, score ${r.score}`}
                    >
                      <span className="ws-chip-name">{r.subdomain}</span>
                      <span className="ws-chip-nums num">
                        d{r.demand}/s{r.supply}
                      </span>
                    </span>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
