import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Meta, type Trends } from "../api";

const VBW = 1000;
const VBH = 340;
const PAD = { l: 46, r: 18, t: 14, b: 30 };
const PLOT_W = VBW - PAD.l - PAD.r;
const PLOT_H = VBH - PAD.t - PAD.b;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtMonth(p: string): string {
  const [y, m] = p.split("-");
  return `${MONTHS[+m - 1]} '${y.slice(2)}`;
}
function niceMax(v: number): number {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

export function TrendsView({ meta }: { meta: Meta | null }) {
  const [category, setCategory] = useState("");
  const [data, setData] = useState<Trends | null>(null);
  const [visible, setVisible] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const sources = meta?.sources ?? [];
  const srcLabel = (k: string) => sources.find((s) => s.key === k)?.label ?? k;

  useEffect(() => {
    if (sources.length) setVisible(new Set(sources.map((s) => s.key)));
  }, [meta]);

  useEffect(() => {
    let live = true;
    setData(null);
    api.trends(category).then((d) => live && setData(d)).catch(() => live && setData({ periods: [], series: {}, category: null }));
    return () => {
      live = false;
    };
  }, [category]);

  const periods = data?.periods ?? [];
  const shownSources = useMemo(
    () => sources.filter((s) => visible.has(s.key) && data?.series[s.key]),
    [sources, visible, data]
  );

  const maxY = useMemo(() => {
    let m = 0;
    for (const s of shownSources) for (const v of data!.series[s.key]) m = Math.max(m, v);
    return niceMax(m);
  }, [shownSources, data]);

  const x = (i: number) => PAD.l + (periods.length > 1 ? i / (periods.length - 1) : 0.5) * PLOT_W;
  const y = (v: number) => PAD.t + (1 - v / maxY) * PLOT_H;

  const toggle = (k: string) =>
    setVisible((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });

  const onMove = (e: React.MouseEvent) => {
    const svg = svgRef.current;
    if (!svg || periods.length < 2) return;
    const rect = svg.getBoundingClientRect();
    const vbX = ((e.clientX - rect.left) / rect.width) * VBW;
    let i = Math.round(((vbX - PAD.l) / PLOT_W) * (periods.length - 1));
    i = Math.max(0, Math.min(periods.length - 1, i));
    setHover({ i, x: e.clientX, y: e.clientY });
  };

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * maxY));
  const labelStep = Math.max(1, Math.ceil(periods.length / 9));

  return (
    <div className="trends">
      <div className="tr-head">
        <div>
          <h2 className="tr-h">Volume over time</h2>
          <p className="tr-p">
            New entrants per month, by source. Toggle sources in the legend and pick a category. Heads up:
            the sources cover different spans (Show HN and Product Hunt only the last 6 months, arXiv only days,
            while YC, GitHub, and Hugging Face go back years), so turn the recent-only ones off to read the
            long-run trend.
          </p>
        </div>
        <label className="field">
          <span className="field-label">Category</span>
          <select className="select" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {(meta?.industries ?? []).map((ind) => (
              <option key={ind} value={ind}>
                {ind}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="legend tr-legend" role="group" aria-label="Sources">
        {sources.map((s) => (
          <button
            key={s.key}
            className={"legend-item" + (visible.has(s.key) ? "" : " is-off")}
            onClick={() => toggle(s.key)}
          >
            <span className="swatch" style={{ background: `var(--src-${s.key})` }} />
            {s.label}
          </button>
        ))}
      </div>

      {data === null ? (
        <div className="tr-chart-wrap">
          <div className="sk" style={{ height: 340, borderRadius: 8 }} />
        </div>
      ) : periods.length === 0 ? (
        <div className="empty">No dated activity for this category.</div>
      ) : (
        <div className="tr-chart-wrap">
          <svg
            ref={svgRef}
            className="tr-svg"
            viewBox={`0 0 ${VBW} ${VBH}`}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          >
            {/* y gridlines + labels */}
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.l} x2={VBW - PAD.r} y1={y(t)} y2={y(t)} className="tr-grid" />
                <text x={PAD.l - 8} y={y(t) + 3} className="tr-ytick" textAnchor="end">
                  {t.toLocaleString()}
                </text>
              </g>
            ))}
            {/* x labels */}
            {periods.map((p, i) =>
              i % labelStep === 0 ? (
                <text key={p} x={x(i)} y={VBH - 10} className="tr-xtick" textAnchor="middle">
                  {fmtMonth(p)}
                </text>
              ) : null
            )}
            {/* hover guide */}
            {hover && (
              <line x1={x(hover.i)} x2={x(hover.i)} y1={PAD.t} y2={PAD.t + PLOT_H} className="tr-guide" />
            )}
            {/* lines */}
            {shownSources.map((s) => {
              const vals = data!.series[s.key];
              const pts = vals.map((v, i) => `${x(i)},${y(v)}`).join(" ");
              return (
                <polyline key={s.key} points={pts} fill="none" stroke={`var(--src-${s.key})`} strokeWidth={2}
                          strokeLinejoin="round" strokeLinecap="round" />
              );
            })}
            {/* hover dots */}
            {hover &&
              shownSources.map((s) => (
                <circle key={s.key} cx={x(hover.i)} cy={y(data!.series[s.key][hover.i])} r={3.5}
                        fill={`var(--src-${s.key})`} stroke="var(--surface)" strokeWidth={1.5} />
              ))}
          </svg>

          {hover && (
            <div className="tr-tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
              <div className="tr-tip-month num">{fmtMonth(periods[hover.i])}</div>
              {shownSources
                .map((s) => ({ s, v: data!.series[s.key][hover.i] }))
                .sort((a, b) => b.v - a.v)
                .map(({ s, v }) => (
                  <div className="tr-tip-row" key={s.key}>
                    <span className="swatch" style={{ background: `var(--src-${s.key})` }} />
                    <span className="tr-tip-name">{srcLabel(s.key)}</span>
                    <span className="tr-tip-val num">{v}</span>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
