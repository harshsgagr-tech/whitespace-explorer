import { useEffect, useState } from "react";
import { api, type Filters, type Meta } from "./api";
import { TopBar } from "./components/TopBar";
import { ExplorerView } from "./components/ExplorerView";
import { WhitespaceView } from "./components/WhitespaceView";
import { SignalsView } from "./components/SignalsView";

export type View = "explorer" | "signals" | "whitespace";

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState(false);
  const [view, setView] = useState<View>("explorer");
  const [filters, setFilters] = useState<Filters>({
    window: "all",
    aiNative: "all",
    businessModel: "all",
  });

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMetaError(true));
  }, []);

  return (
    <div className="app">
      <TopBar
        meta={meta}
        view={view}
        onView={setView}
        filters={filters}
        onFilters={setFilters}
      />
      {metaError ? (
        <div className="app-error">
          Could not reach the API. Start it with{" "}
          <code>uvicorn api:app --port 8000</code> and reload.
        </div>
      ) : view === "explorer" ? (
        <ExplorerView meta={meta} filters={filters} />
      ) : view === "signals" ? (
        <SignalsView />
      ) : (
        <WhitespaceView />
      )}
    </div>
  );
}
