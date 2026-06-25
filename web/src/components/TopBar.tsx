import type { Filters, Meta } from "../api";
import type { View } from "../App";

type Props = {
  meta: Meta | null;
  view: View;
  onView: (v: View) => void;
  filters: Filters;
  onFilters: (f: Filters) => void;
};

export function TopBar({ meta, view, onView, filters, onFilters }: Props) {
  const set = (patch: Partial<Filters>) => onFilters({ ...filters, ...patch });

  return (
    <header className="topbar">
      <div className="topbar-row1">
        <div className="topbar-brand">
          <span className="topbar-title">What people are building</span>
          <span className="topbar-sub">
            {meta?.date_range.min ? `${meta.date_range.min} to ${meta.date_range.max}` : "loading"}
          </span>
        </div>

        <nav className="tabs" role="tablist" aria-label="Views">
          <button
            role="tab"
            aria-selected={view === "explorer"}
            className={"tab" + (view === "explorer" ? " is-on" : "")}
            onClick={() => onView("explorer")}
          >
            Industry explorer
          </button>
          <button
            role="tab"
            aria-selected={view === "trends"}
            className={"tab" + (view === "trends" ? " is-on" : "")}
            onClick={() => onView("trends")}
          >
            Trends
          </button>
          <button
            role="tab"
            aria-selected={view === "signals"}
            className={"tab" + (view === "signals" ? " is-on" : "")}
            onClick={() => onView("signals")}
          >
            Signals
          </button>
          <button
            role="tab"
            aria-selected={view === "whitespace"}
            className={"tab" + (view === "whitespace" ? " is-on" : "")}
            onClick={() => onView("whitespace")}
          >
            Whitespace map
          </button>
        </nav>
      </div>

      <div className="filters">
        <label className="field">
          <span className="field-label">Window</span>
          <select
            className="select"
            value={filters.window}
            onChange={(e) => set({ window: e.target.value })}
          >
            {(meta?.windows ?? [{ key: "all", label: "All time", days: null }]).map((w) => (
              <option key={w.key} value={w.key}>
                {w.label}
              </option>
            ))}
          </select>
        </label>

        <div className="field">
          <span className="field-label">Companies</span>
          <div className="segmented" role="group" aria-label="AI-native filter">
            {(meta?.ai_native ?? [{ key: "all", label: "All" }]).map((o) => (
              <button
                key={o.key}
                className={"seg" + (filters.aiNative === o.key ? " is-on" : "")}
                onClick={() => set({ aiNative: o.key })}
                title={o.label}
              >
                {o.key === "all" ? "All" : o.key === "ai" ? "AI-native" : "Other"}
              </button>
            ))}
          </div>
        </div>

        <label className="field">
          <span className="field-label">Model</span>
          <select
            className="select"
            value={filters.businessModel}
            onChange={(e) => set({ businessModel: e.target.value })}
          >
            <option value="all">Any business model</option>
            {(meta?.business_models ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
