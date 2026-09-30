import "server-only";

// The API token lives only on the Next.js server. Browser code never sees it:
// pages call the backend from server components / route handlers via this helper.
const API_URL = process.env.API_URL ?? "http://localhost:8000";
const API_TOKEN = process.env.API_TOKEN ?? "";

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${API_TOKEN}`,
      ...init.headers,
    },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API ${init.method ?? "GET"} ${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export type AppSettings = {
  auto_apply: boolean;
  page_limit: number;
  ats_threshold: number;
  max_quality_iterations: number;
  global_delay_minutes: number;
  github_username: string | null;
  github_include_private: boolean;
  github_include_forks: boolean;
  github_exclude_repos: string[];
  monthly_budget_usd: number | null;
  timezone: string;
  target_levels: ("fresher" | "experienced" | "unknown")[];
  skip_title_words: string[];
  resume_file_name: string;
  include_signature_project: boolean;
};
