export type Rule = {
  id: number;
  sender_match: string;
  portal_id: number | null;
  apply_mode: "direct_link" | "read_email_then_apply";
  delay_minutes: number | null;
  match_forwarded: boolean;
  enabled: boolean;
};

export type Account = {
  id: number;
  provider: "outlook_graph" | "gmail_api" | "imap";
  address: string;
  display_name: string | null;
  imap_host: string | null;
  imap_port: number | null;
  imap_password_masked: string | null;
  oauth_connected: boolean;
  status: "pending" | "connected" | "error" | "disabled";
  last_sync_at: string | null;
  last_error: string | null;
  enabled: boolean;
  rules: Rule[];
};

export type Message = {
  id: number;
  account_id: number;
  sender: string;
  original_sender: string | null;
  subject: string | null;
  received_at: string;
  classification: string;
  extracted_link: string | null;
  resolved_link: string | null;
  link_safe: boolean | null;
  link_check_reason: string | null;
  job_id: number | null;
};

export const PROVIDER_LABEL: Record<Account["provider"], string> = {
  outlook_graph: "Outlook / Microsoft 365",
  gmail_api: "Gmail",
  imap: "IMAP",
};
