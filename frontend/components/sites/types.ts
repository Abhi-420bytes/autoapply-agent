export type SiteMode = "search_only" | "search_and_apply";

export type Site = {
  id: number;
  name: string;
  portal_id: number;
  portal_name: string;
  allowed_domains: string[];
  has_login: boolean;
  search_urls: string[];
  categories: string[];
  exclude_keywords: string[];
  mode: SiteMode;
  check_every_minutes: number;
  apply_delay_minutes: number;
  max_new_per_check: number;
  enabled: boolean;
  last_checked_at: string | null;
  last_error: string | null;
  found: number;
  matched: number;
  jobs: number;
  tos_warning: string | null;
  prompt: { kind: string; question: string; asked_at: string } | null;
};

export type Posting = {
  id: number;
  site_id: number;
  site_name: string;
  url: string;
  title: string | null;
  company: string | null;
  matched: boolean;
  matched_category: string | null;
  skip_reason: string | null;
  job_id: number | null;
  job_status: string | null;
  discovered_at: string;
};

export const RISKY_HOSTS = ["linkedin.com", "indeed.com", "naukri.com"];
export const isRisky = (urls: string[]) =>
  urls.some((u) => {
    try {
      const h = new URL(u).hostname;
      return RISKY_HOSTS.some((d) => h === d || h.endsWith("." + d));
    } catch {
      return false;
    }
  });
