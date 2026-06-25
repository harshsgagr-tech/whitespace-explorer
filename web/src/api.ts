export type Source = { key: string; label: string; tier: string };
export type WindowOpt = { key: string; label: string; days: number | null };
export type AiNativeOpt = { key: string; label: string };

export type Meta = {
  sources: Source[];
  windows: WindowOpt[];
  ai_native: AiNativeOpt[];
  business_models: string[];
  industries: string[];
  date_range: { min: string | null; max: string | null };
};

export type Trends = {
  periods: string[];
  series: Record<string, number[]>;
  category: string | null;
};

export type Industry = {
  industry: string;
  total: number;
  by_source: Record<string, number>;
  recent: number;
  prior: number;
  momentum: number;
};

export type Company = {
  id: number;
  name: string;
  url: string | null;
  description: string | null;
  subdomain: string;
  sources: string[];
  traction: number;
  first_seen: string | null;
  tags: string[];
};

export type Subcategory = { tag: string; count: number };

export type IndustryDetail = {
  industry: string;
  source: string | null;
  total: number;
  subcategories: Subcategory[];
  companies: Company[];
};

export type WhitespaceRow = {
  subdomain: string;
  demand: number;
  supply: number;
  leading_ratio: number;
  leading_appearances: number;
  total_appearances: number;
  score: number;
  quadrant: string;
};

export type SignalCompany = { name: string; url: string | null; traction: number; sources: string[] };

export type Signal = {
  subdomain: string;
  subcategory: string;
  supply: number;
  leading: number;
  mid: number;
  yc: number;
  leading_ratio: number;
  recent: number;
  prior: number;
  growth: number;
  first_seen: string | null;
  demand: number;
  score: number;
  action: string;
  companies: SignalCompany[];
};

export type Brief = { summary: string; gap: string; risk: string };

export type Filters = {
  window: string;
  aiNative: string;
  businessModel: string;
};

function qs(params: Record<string, string>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) u.set(k, v);
  const s = u.toString();
  return s ? `?${s}` : "";
}

// Base URL for the API. Defaults to same-origin (the Vite proxy). Set
// VITE_API_BASE to call the shim directly, which avoids any proxy issues.
const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  industries: (f: Filters) =>
    get<Industry[]>(
      "/api/industries" +
        qs({ window: f.window, ai_native: f.aiNative, business_model: f.businessModel })
    ),
  industry: (name: string, source: string, sort: string, f: Filters) =>
    get<IndustryDetail>(
      `/api/industry/${encodeURIComponent(name)}` +
        qs({ source, sort, window: f.window, ai_native: f.aiNative, business_model: f.businessModel })
    ),
  whitespace: () => get<WhitespaceRow[]>("/api/whitespace"),
  momentum: () => get<Signal[]>("/api/momentum"),
  brief: (subdomain: string, subcategory: string) =>
    get<Brief>("/api/brief" + qs({ subdomain, subcategory })),
  trends: (category: string) => get<Trends>("/api/trends" + qs({ category })),
};
