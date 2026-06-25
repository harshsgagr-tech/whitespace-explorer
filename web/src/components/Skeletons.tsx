export function ChartSkeleton() {
  // Bars of varying length so the skeleton reads like a ranking, not a grid.
  const widths = [96, 82, 74, 61, 55, 48, 44, 39, 33, 30, 26, 22, 19, 16, 13];
  return (
    <div className="chart skeleton" aria-busy="true" aria-label="Loading industries">
      {widths.map((w, i) => (
        <div className="bar-row" key={i}>
          <div className="sk sk-label" />
          <div className="bar-track">
            <div className="sk sk-bar" style={{ width: `${w}%` }} />
          </div>
          <div className="sk sk-total" />
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton() {
  return (
    <div className="card" aria-busy="true">
      <div className="card-main">
        <div className="sk sk-name" />
        <div className="sk sk-line" />
        <div className="sk sk-line short" />
      </div>
    </div>
  );
}
