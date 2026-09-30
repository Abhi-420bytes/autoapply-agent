import type { JobStatus } from "@/lib/types";

export const STATUS_LABEL: Record<JobStatus, string> = {
  detected: "Detected",
  scraped: "Analyzed",
  resume_ready: "Resume ready",
  awaiting_approval: "Awaiting approval",
  applied: "Applied",
  shortlisted: "Shortlisted",
  rejected: "Rejected",
  offer: "Offer",
  suspicious: "Suspicious",
  ineligible: "Ineligible",
  failed: "Failed",
  paused: "Paused",
};

export const STATUS_TONE: Record<JobStatus, "neutral" | "good" | "bad" | "warn"> = {
  detected: "neutral",
  scraped: "neutral",
  resume_ready: "good",
  awaiting_approval: "warn",
  applied: "good",
  shortlisted: "good",
  rejected: "bad",
  offer: "good",
  suspicious: "bad",
  ineligible: "warn",
  failed: "bad",
  paused: "warn",
};

export const ALL_STATUSES = Object.keys(STATUS_LABEL) as JobStatus[];

export const STEP_LABEL: Record<string, string> = {
  analyze: "Reading the job description",
  eligibility: "Checking eligibility",
  retrieve: "Finding your most relevant experience",
  write: "Writing the resume",
  evaluate: "Compiling and scoring",
  polish: "Polishing wording",
  finalize: "Saving the final version",
};
