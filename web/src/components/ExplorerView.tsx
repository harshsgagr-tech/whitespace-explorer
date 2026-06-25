import { useEffect, useMemo, useState } from "react";
import { api, type Filters, type Industry, type Meta } from "../api";
import { IndustryChart } from "./IndustryChart";
import { CompanyPanel } from "./CompanyPanel";
import { ChartSkeleton } from "./Skeletons";

type Sort = "volume" | "momentum" | "az";
type Mode = "stacked" | "grouped";
export type Selection = { industry: string; source: string };

export function ExplorerView({ meta, filters }: { meta: Meta | null; filters: Filters }) {
  const [industries, setIndustries] = useState<Industry[] | null>(null);
  const [visible, setVisible] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<Sort>("volume");
  const [mode, setMode] = useState<Mode>("stacked");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Selection | null>(null);

  const sources = meta?.sources ?? [];

  // Start with every source visible once meta arrives.
  useEffect(() => {
    if (sources.length) setVisible(new Set(sources.map((s) => s.key)));
  }, [meta]);

  // Refetch industries whenever the global filters change.
  useEffect(() => {
    let live = true;
    setIndustries(null);
    api
      .industries(filters)
      .then((d) => live && setIndustries(d))
      .catch(() => live && setIndustries([]));
    return () => {
      live = false;
    };
  }, [filters.window, filters.aiNative, filters.businessModel]);

  const displayed = useMemo(() => {
    if (!industries) return [];
    const q = search.trim().toLowerCase();
    const rows = industries
      .map((ind) => {
        const by: Record<string, number> = {};
        let total = 0;
        for (const s of sources) {
          const c = ind.by_source[s.key] || 0;
          if (c && visible.has(s.key)) {
            by[s.key] = c;
            total += c;
          }
        }
        return { industry: ind.industry, total, by_source: by, momentum: ind.momentum };
      })
      .filter((r) => r.total > 0 && (!q || r.industry.toLowerCase().includes(q)));
    rows.sort((a, b) => {
      if (sort === "az") return a.industry.localeCompare(b.industry);
      if (sort === "momentum") return b.momentum - a.momentum || b.total - a.total;
      return b.total - a.total;
    });
    return rows;
  }, [industries, visible, sort, search, sources]);

  const maxTotal = displayed.reduce((m, r) => Math.max(m, r.total), 0);

  const toggleSource = (key: string) => {
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className={"explorer" + (selected ? " has-panel" : "")}>
      <div className="explorer-main">
        <div className="controls">
          <div className="legend" role="group" aria-label="Sources, click to toggle">
            {sources.map((s) => {
              const on = visible.has(s.key);
              return (
                <button
                  key={s.key}
                  className={"legend-item" + (on ? "" : " is-off")}
                  onClick={() => toggleSource(s.key)}
                  title={`${s.label} (${s.tier})`}
                >
                  <span className="swatch" style={{ background: `var(--src-${s.key})` }} />
                  {s.label}
                </button>
              );
            })}
          </div>

          <div className="controls-right">
            <input
              className="search"
              placeholder="Find an industry"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search industries"
            />
            <div className="segmented" role="group" aria-label="Bar mode">
              <button
                className={"seg" + (mode === "stacked" ? " is-on" : "")}
                onClick={() => setMode("stacked")}
                title="Stacked: source composition per industry"
              >
                Stacked
              </button>
              <button
                className={"seg" + (mode === "grouped" ? " is-on" : "")}
                onClick={() => setMode("grouped")}
                title="Grouped: compare one source across industries"
              >
                Grouped
              </button>
            </div>
            <div className="segmented" role="group" aria-label="Sort">
              <button className={"seg" + (sort === "volume" ? " is-on" : "")} onClick={() => setSort("volume")}>
                Volume
              </button>
              <button className={"seg" + (sort === "momentum" ? " is-on" : "")} onClick={() => setSort("momentum")}>
                Momentum
              </button>
              <button className={"seg" + (sort === "az" ? " is-on" : "")} onClick={() => setSort("az")}>
                A to Z
              </button>
            </div>
          </div>
        </div>

        {industries === null ? (
          <ChartSkeleton />
        ) : displayed.length === 0 ? (
          <div className="empty">
            No industries match these filters. Widen the time window or turn sources back on.
          </div>
        ) : (
          <IndustryChart
            rows={displayed}
            sources={sources}
            maxTotal={maxTotal}
            mode={mode}
            selected={selected}
            onOpenBar={(industry) => setSelected({ industry, source: "" })}
            onOpenSegment={(industry, source) => setSelected({ industry, source })}
          />
        )}
      </div>

      {selected && (
        <CompanyPanel
          key={selected.industry + "|" + selected.source}
          selection={selected}
          sources={sources}
          filters={filters}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
