import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Company, type Filters, type IndustryDetail, type Source } from "../api";
import type { Selection } from "./ExplorerView";
import { CardSkeleton } from "./Skeletons";

type Sort = "traction" | "newest" | "az";

function fmtDate(d: string | null): string {
  if (!d) return "";
  const dt = new Date(d + "T00:00:00");
  if (isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function CompanyPanel({
  selection,
  sources,
  filters,
  onClose,
}: {
  selection: Selection;
  sources: Source[];
  filters: Filters;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<IndustryDetail | null>(null);
  const [sourceFilter, setSourceFilter] = useState(selection.source);
  const [subFilter, setSubFilter] = useState("");
  const [sort, setSort] = useState<Sort>("traction");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const closing = useRef(false);

  useEffect(() => {
    const id = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const close = () => {
    if (closing.current) return;
    closing.current = true;
    setOpen(false);
    setTimeout(onClose, 200);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Fetch every company in the industry once (all sources), respecting the
  // global filters. The source filter below is applied on the client so
  // switching sources is instant and never refetches.
  useEffect(() => {
    let live = true;
    setDetail(null);
    api
      .industry(selection.industry, "", "traction", filters)
      .then((d) => live && setDetail(d))
      .catch(() => live && setDetail({ industry: selection.industry, source: null, total: 0, subcategories: [], companies: [] }));
    return () => {
      live = false;
    };
  }, [selection.industry, filters.window, filters.aiNative, filters.businessModel]);

  const srcLabel = (k: string) => sources.find((s) => s.key === k)?.label ?? k;

  // Only offer chips for sources that actually appear in this industry.
  const availableSources = useMemo(() => {
    if (!detail) return [];
    const present = new Set<string>();
    detail.companies.forEach((c) => c.sources.forEach((s) => present.add(s)));
    return sources.filter((s) => present.has(s.key)).map((s) => s.key);
  }, [detail, sources]);

  const sourceFiltered = useMemo(
    () => (detail ? detail.companies.filter((c) => !sourceFilter || c.sources.includes(sourceFilter)) : []),
    [detail, sourceFilter]
  );

  // Subcategory facet, counted over the currently source-filtered set so the
  // numbers always match what is on screen.
  const subFacet = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of sourceFiltered) for (const t of c.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 14)
      .map(([tag, count]) => ({ tag, count }));
  }, [sourceFiltered]);

  const companies = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = sourceFiltered.filter(
      (c) =>
        (!subFilter || (c.tags || []).includes(subFilter)) &&
        (!q || c.name.toLowerCase().includes(q) || (c.description || "").toLowerCase().includes(q))
    );
    const sorted = [...list];
    if (sort === "newest") sorted.sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""));
    else if (sort === "az") sorted.sort((a, b) => a.name.localeCompare(b.name));
    else sorted.sort((a, b) => b.traction - a.traction);
    return sorted;
  }, [sourceFiltered, subFilter, sort, search]);

  return (
    <aside className={"panel" + (open ? " is-open" : "")} aria-label={`Companies in ${selection.industry}`}>
      <div className="panel-head">
        <div className="panel-head-top">
          <div className="panel-eyebrow">
            Industry
            {sourceFilter && (
              <>
                <span className="panel-sep">/</span>
                <span className="panel-src">
                  <span className="swatch" style={{ background: `var(--src-${sourceFilter})` }} />
                  {srcLabel(sourceFilter)} only
                </span>
              </>
            )}
          </div>
          <button className="icon-btn" onClick={close} title="Close (Esc)" aria-label="Close panel">
            ×
          </button>
        </div>
        <h2 className="panel-title">{selection.industry}</h2>
        <div className="panel-count num">
          {detail ? `${sourceFiltered.length.toLocaleString()} ${sourceFiltered.length === 1 ? "company" : "companies"}` : " "}
        </div>

        {detail && availableSources.length > 0 && (
          <div className="panel-sources" role="group" aria-label="Filter by source">
            <button
              className={"src-chip" + (sourceFilter === "" ? " is-on" : "")}
              onClick={() => { setSourceFilter(""); setSubFilter(""); }}
            >
              All sources
            </button>
            {availableSources.map((s) => (
              <button
                key={s}
                className={"src-chip" + (sourceFilter === s ? " is-on" : "")}
                onClick={() => { setSourceFilter(sourceFilter === s ? "" : s); setSubFilter(""); }}
                title={`Show only ${srcLabel(s)}`}
              >
                <span className="swatch" style={{ background: `var(--src-${s})` }} />
                {srcLabel(s)}
              </button>
            ))}
          </div>
        )}

        {detail && subFacet.length > 0 && (
          <div className="panel-subcats" role="group" aria-label="What people build inside this category">
            <span className="subcats-label">Building</span>
            <div className="subcat-chips">
              {subFacet.map((s) => (
                <button
                  key={s.tag}
                  className={"subcat-chip" + (subFilter === s.tag ? " is-on" : "")}
                  onClick={() => setSubFilter(subFilter === s.tag ? "" : s.tag)}
                  title={`Show only ${s.tag}`}
                >
                  {s.tag}
                  <span className="subcat-count num">{s.count}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="panel-controls">
          <input
            className="search"
            placeholder="Search companies"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search companies"
          />
          <div className="segmented" role="group" aria-label="Sort companies">
            <button className={"seg" + (sort === "traction" ? " is-on" : "")} onClick={() => setSort("traction")}>
              Traction
            </button>
            <button className={"seg" + (sort === "newest" ? " is-on" : "")} onClick={() => setSort("newest")}>
              Newest
            </button>
            <button className={"seg" + (sort === "az" ? " is-on" : "")} onClick={() => setSort("az")}>
              A to Z
            </button>
          </div>
        </div>
      </div>

      <div className="panel-list">
        {detail === null ? (
          <>
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
          </>
        ) : companies.length === 0 ? (
          <div className="empty">No companies here under the current filters.</div>
        ) : (
          companies.map((c) => <CompanyCard key={c.id} c={c} srcLabel={srcLabel} />)
        )}
      </div>
    </aside>
  );
}

function CompanyCard({ c, srcLabel }: { c: Company; srcLabel: (k: string) => string }) {
  const inner = (
    <>
      <div className="card-main">
        <div className="card-head">
          <span className="card-name">{c.name}</span>
          {c.url && <span className="card-go" aria-hidden="true">↗</span>}
        </div>
        {c.description && <p className="card-desc">{c.description}</p>}
        <div className="card-meta">
          <span className="card-badges">
            {c.sources.map((s) => (
              <span key={s} className="badge" style={{ background: `var(--src-${s})` }}>
                {srcLabel(s)}
              </span>
            ))}
          </span>
          <span className="card-stats num">
            {c.traction > 0 && <span className="card-traction">▲ {c.traction.toLocaleString()}</span>}
            {c.first_seen && <span className="card-date">{fmtDate(c.first_seen)}</span>}
          </span>
        </div>
      </div>
    </>
  );

  return c.url ? (
    <a className="card is-link" href={c.url} target="_blank" rel="noopener noreferrer" title={`Open ${c.name} in a new tab`}>
      {inner}
    </a>
  ) : (
    <div className="card">{inner}</div>
  );
}
