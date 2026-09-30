export type ProviderKind =
  | "anthropic"
  | "openai"
  | "gemini"
  | "groq"
  | "openrouter"
  | "ollama"
  | "openai_compatible"
  | "voyage"
  | "local_sentence_transformers";

export type LLMTask =
  | "email_classifier"
  | "jd_analyzer"
  | "resume_writer"
  | "ats_scorer"
  | "final_polish"
  | "portal_helper"
  | "repo_summarizer"
  | "embedding";

export type ProviderSpec = {
  kind: ProviderKind;
  display_name: string;
  requires_api_key: boolean;
  requires_base_url: boolean;
  default_base_url: string | null;
  supports_chat: boolean;
  supports_embeddings: boolean;
  suggested_models: string[];
  suggested_embedding_models: string[];
  key_hint: string;
  notes: string;
};

export type Provider = {
  id: number;
  label: string;
  kind: ProviderKind;
  display_name: string;
  has_api_key: boolean;
  api_key_masked: string | null;
  base_url: string | null;
  enabled: boolean;
  supports_chat: boolean;
  supports_embeddings: boolean;
  last_test_ok: boolean | null;
  last_test_at: string | null;
  last_test_latency_ms: number | null;
  last_test_error: string | null;
  used_by_tasks: LLMTask[];
};

export type TestResult = {
  ok: boolean;
  latency_ms: number | null;
  model: string | null;
  error: string | null;
  error_kind: string | null;
  reply: string | null;
};

export type ModelList = { models: string[]; source: "live" | "suggested"; error: string | null };

export type TaskParams = {
  temperature: number;
  max_tokens: number;
  timeout_s: number;
  prompt_version: number | null;
};

export type TaskConfig = {
  task: LLMTask;
  description: string;
  is_embedding: boolean;
  provider_id: number | null;
  model: string | null;
  fallback_provider_id: number | null;
  fallback_model: string | null;
  params: TaskParams;
  configured: boolean;
};

export type UsageRow = {
  key: string;
  calls: number;
  failures: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
};

export type UsageSummary = {
  period_start: string;
  month_to_date_usd: number;
  budget_usd: number | null;
  budget_exceeded: boolean;
  by_task: UsageRow[];
  by_model: UsageRow[];
  by_job: UsageRow[];
  recent: {
    id: number;
    created_at: string;
    task: string | null;
    provider_kind: string;
    model: string;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    latency_ms: number | null;
    success: boolean;
    is_fallback: boolean;
    error_kind: string | null;
    job_id: number | null;
  }[];
};

export type OverfullBox = {
  kind: string;
  amount_pt: number;
  line_start: number | null;
  line_end: number | null;
  region: string | null;
};

export type BuildReport = {
  ok: boolean;
  compiled: boolean;
  page_count: number | null;
  page_limit: number;
  within_limit: boolean;
  alignment_ok: boolean;
  overfull: OverfullBox[];
  errors: string[];
  removed_bullets: { region: string; text: string; priority: number }[];
  compiles: number;
  duration_ms: number;
  last_page_fill?: number | null;
};

export type Template = {
  id: number;
  label: string | null;
  is_active: boolean;
  created_at: string;
  page_count: number | null;
  page_limit: number | null;
  regions: { name: string; line: number; content: string }[];
  bullet_command: string | null;
  auto_marked: string[];
  main_file: string | null;
  assets: string[];
  overleaf_check: {
    reference_pages: number;
    compiled_pages: number;
    text_similarity: number;
    ok: boolean;
    message: string | null;
  } | null;
  build_report: BuildReport | null;
  tex_source: string;
};

export type ResumeVersion = {
  id: number;
  kind: string;
  lineage_id: number | null;
  version: number;
  parent_id: number | null;
  job_id: number | null;
  label: string | null;
  created_at: string;
  page_count: number | null;
  page_limit: number | null;
  build_report: BuildReport | null;
  contents: Record<string, string> | null;
  ats_score?: number | null;
  score_report?: ScoreReport | null;
};

export type RenderResult = { resume: ResumeVersion | null; report: BuildReport };

export type KBullet = {
  id: number;
  text: string;
  section: string | null;
  heading: string | null;
  skills: string[];
  source_type: "past_resume" | "github" | "profile";
  source_resume_id: number | null;
  github_repo_id: number | null;
  source_date: string | null;
  outcome_score: number;
  is_active: boolean;
};

export type Repo = {
  id: number;
  full_name: string;
  url: string;
  description: string | null;
  languages: Record<string, number>;
  topics: string[];
  stars: number;
  forks: number;
  is_fork: boolean;
  is_private: boolean;
  archived: boolean;
  commit_count: number | null;
  pushed_at: string | null;
  synced_at: string | null;
  summary: {
    problem?: string;
    tech_stack?: string[];
    outcomes?: string[];
    role?: string;
    bullets?: string[];
    dropped?: string[];
    models?: string[];
  } | null;
  sync_error: string | null;
  bullets: KBullet[];
};

export type SyncStatus = {
  state: "queued" | "running" | "done" | "failed";
  requested_at?: string;
  started_at?: string;
  finished_at?: string;
  error?: string;
  repos_seen?: number;
  repos_kept?: number;
  summarized?: number;
  unchanged?: number;
  removed?: number;
  bullets?: number;
  embedded?: number;
  errors?: string[];
};

export type KnowledgeStatus = {
  github_username: string | null;
  github_token_set: boolean;
  github_sync: SyncStatus | null;
  repos: number;
  bullets_by_source: Record<string, number>;
  past_resumes: number;
  embedding_model: string | null;
  embedded_chunks: number;
  unembedded_items: number;
  reindex_pending: boolean;
};

export type PastResume = {
  id: number;
  filename: string | null;
  format: string | null;
  source_date: string | null;
  created_at: string;
  bullet_count: number;
};

export type IngestResult = {
  resume_id: number;
  filename: string;
  found: number;
  added: number;
  exact_duplicates: number;
  near_duplicates: number;
  warnings: string[];
  bullets: string[];
};

export type SearchHit = {
  kind: "bullet" | "github_repo";
  score: number;
  similarity: number;
  keyword_overlap: number;
  matched_keywords: string[];
  content: string;
  bullet: KBullet | null;
  repo_name: string | null;
};

export type JobStatus =
  | "detected" | "scraped" | "resume_ready" | "awaiting_approval" | "applied"
  | "shortlisted" | "rejected" | "offer" | "suspicious" | "ineligible" | "failed" | "paused";

export type Run = {
  id: number;
  kind: string;
  state: "queued" | "running" | "done" | "failed";
  step: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Job = {
  id: number;
  source: "email" | "manual" | "site";
  status: JobStatus;
  status_reason: string | null;
  company: string | null;
  role: string | null;
  location: string | null;
  ctc: string | null;
  deadline: string | null;
  detected_at: string;
  scheduled_at: string | null;
  notes: string | null;
  portal_id?: number | null;
  level?: "fresher" | "experienced" | null;
  level_reason?: string | null;
  latest_resume_id: number | null;
  ats_score: number | null;
  active_run: Run | null;
};

export type Application = {
  id: number;
  resume_id: number | null;
  status: string;
  auto_submit_at: string | null;
  approved_at: string | null;
  submitted_at: string | null;
  error: string | null;
};

export type JobDetail = Job & {
  jd_text: string | null;
  jd_structured: {
    required_skills?: string[];
    preferred_skills?: string[];
    keywords?: string[];
    seniority?: string | null;
    role_category?: string | null;
    deadline?: string | null;
  } | null;
  eligibility: { eligible?: boolean | null; reasons?: string[]; unknown?: string[] } | null;
  apply_url: string | null;
  runs: Run[];
  resumes: ResumeVersion[];
  applications: Application[];
  apply_mode: "direct_link" | "read_email_then_apply" | null;
  files: { id: number; kind: string; original_name: string | null; mime_type: string | null; created_at: string }[];
  source_emails: {
    id: number;
    sender: string;
    original_sender: string | null;
    subject: string | null;
    received_at: string;
    classification: string;
    body_html: string | null;
    body_text: string | null;
    extracted_link: string | null;
    resolved_link: string | null;
    link_safe: boolean | null;
    link_check_reason: string | null;
  }[];
  prompt: { kind: string; question: string; screenshot_file_id: number | null; asked_at: string } | null;
};

export type ATS = {
  score: number;
  keyword_score: number;
  semantic_score: number | null;
  format_score: number;
  matched_required: string[];
  missing_required: string[];
  matched_preferred: string[];
  missing_preferred: string[];
  matched_keywords: string[];
  missing_keywords?: string[];
  synonym_matches: Record<string, string>;
  similarity: number | null;
  format_checks: Record<string, unknown>;
};

export type ScoreReport = {
  passed: boolean;
  threshold: number;
  ats: ATS | null;
  build: BuildReport;
  gaps: string[];
  guard_removed: string[];
  jd_terms_dropped: string[];
  changes: Record<string, { added: string[]; removed: string[]; kept: number }>;
  iterations: { source: string; score: number | null; passed: boolean; issues: string[]; pages: number | null }[];
  structure?: { ok: boolean; problems: string[]; projects: number; projects_required: number; thin_projects: number; last_page_fill: number | null } | null;
  review?: {
    summary: string;
    suggestions: { region: string; change: string; fact_ids: string[]; terms: string[] }[];
    supported: Record<string, string[]>;
    gaps: string[];
    weak_responsibilities: string[];
  } | null;
  ceiling?: number | null;
  writer_stopped?: string | null;
  eligibility: { eligible?: boolean | null; reasons?: string[]; unknown?: string[] } | null;
  models_used: string[];
  cost_usd: number;
};

export type Notification = {
  id: number;
  level: "info" | "warning" | "error";
  kind: string;
  title: string;
  body: string | null;
  job_id: number | null;
  read_at: string | null;
  created_at: string;
};
