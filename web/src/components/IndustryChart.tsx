import { useEffect, useRef, useState } from "react";
import type { Source } from "../api";
import type { Selection } from "./ExplorerView";

export type ChartRow = {
  industry: string;
  total: number;
  by_source: Record<string, number>;
  momentum: number;
};

type Props = {
  rows: ChartRow[];
  sources: Source[];
  maxTotal: number;
  mode: "stacked" | "grouped";
  selected: Selection | null;
  onOpenBar: (industry: string) => void;
  onOpenSegment: (industry: string, source: string) => void;
};

type Tip = { x: number; y: number; label: string; count: number; color: string } | null;

export function IndustryChart({
  rows,
  sources,
  maxTotal,
  mode,
  selected,
  onOpenBar,
  onOpenSegment,
}: Props) {
  const [focus, setFocus] = useState(0);
  const [tip, setTip] = useState<Tip>(null);
  const rowRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Keep focus in range when the row set changes.
  useEffect(() => {
    if (focus > rows.length - 1) setFocus(Math.max(0, rows.length - 1));
  }, [rows.length]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocus((f) => {
        const n = Math.min(rows.length - 1, f + 1);
        rowRefs.current[n]?.scrollIntoView({ block: "nearest" });
        return n;
      });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocus((f) => {
        const n = Math.max(0, f - 1);
        rowRefs.current[n]?.scrollIntoView({ block: "nearest" });
        return n;
      });
    } else if (e.key === "Enter" && rows[focus]) {
      e.preventDefault();
      onOpenBar(rows[focus].industry);
    }
  };

  const srcLabel = (k: string) => sources.find((s) => s.key === k)?.label ?? k;

  const moveTip = (e: React.MouseEvent, label: string, count: number, color: string) =>
    setTip({ x: e.clientX, y: e.clientY, label, count, color });

  return (
    <div
      className="chart"
      tabIndex={0}
      role="listbox"
      aria-label="Industries by builder volume"
      aria-activedescendant={rows[focus] ? `bar-${focus}` : undefined}
      onKeyDown={onKeyDown}
      onMouseLeave={() => setTip(null)}
    >
      {rows.map((row, i) => {
        const isSel = selected?.industry === row.industry;
        const isFocus = i === focus;
        const segs = sources
          .filter((s) => (row.by_source[s.key] || 0) > 0)
          .map((s) => ({ key: s.key, count: row.by_source[s.key] }));
        return (
          <div
            id={`bar-${i}`}
            key={row.industry}
            ref={(el) => (rowRefs.current[i] = el)}
            role="option"
            aria-selected={isSel}
            className={
              "bar-row" + (isSel ? " is-selected" : "") + (isFocus ? " is-focused" : "")
            }
            onMouseEnter={() => setFocus(i)}
          >
            <button
              className="bar-label"
              onClick={() => onOpenBar(row.industry)}
              title={`Open ${row.industry}, all sources`}
            >
              {row.industry}
            </button>

            <div
              className={"bar-track mode-" + mode}
              onClick={() => onOpenBar(row.industry)}
              title={`Open ${row.industry}, all sources`}
            >
              {segs.map((seg) => {
                const pct = (seg.count / maxTotal) * 100;
                const color = `var(--src-${seg.key})`;
                return (
                  <button
                    key={seg.key}
                    className="bar-seg"
                    style={{ width: `${pct}%`, minWidth: 4, background: color }}
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenSegment(row.industry, seg.key);
                    }}
                    onMouseEnter={(e) => moveTip(e, srcLabel(seg.key), seg.count, color)}
                    onMouseMove={(e) => moveTip(e, srcLabel(seg.key), seg.count, color)}
                    onMouseLeave={() => setTip(null)}
                    aria-label={`${row.industry}, ${srcLabel(seg.key)}, ${seg.count}. Click to open this source.`}
                  />
                );
              })}
            </div>

            <span className="bar-total num">{row.total.toLocaleString()}</span>
          </div>
        );
      })}

      {tip && (
        <div className="tooltip" style={{ left: tip.x + 14, top: tip.y + 14 }} role="presentation">
          <span className="tooltip-dot" style={{ background: tip.color }} />
          <span className="tooltip-src">{tip.label}</span>
          <span className="tooltip-count num">{tip.count.toLocaleString()}</span>
        </div>
      )}
    </div>
  );
}
