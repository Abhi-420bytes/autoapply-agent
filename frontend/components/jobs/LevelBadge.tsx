import { Badge } from "@/components/ui";

export function LevelBadge({ level, reason, className }: { level?: string | null; reason?: string | null; className?: string }) {
  if (level === "fresher") return <span title={reason ?? undefined}><Badge tone="good" className={className}>Fresher</Badge></span>;
  if (level === "experienced") return <span title={reason ?? undefined}><Badge tone="warn" className={className}>Experienced</Badge></span>;
  return <span title={reason ?? "no experience level stated"}><Badge className={className}>Level not stated</Badge></span>;
}
