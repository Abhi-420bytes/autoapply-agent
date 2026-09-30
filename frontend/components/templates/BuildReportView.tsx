import { Badge } from "@/components/ui";
import type { BuildReport } from "@/lib/types";

export function BuildReportView({ report }: { report: BuildReport }) {
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {report.compiled ? <Badge tone="good">compiled</Badge> : <Badge tone="bad">compile failed</Badge>}
        {report.page_count !== null && (
          <Badge tone={report.within_limit ? "good" : "bad"}>
            {report.page_count} / {report.page_limit} page{report.page_limit > 1 ? "s" : ""}
          </Badge>
        )}
        {report.compiled && (
          <Badge tone={report.alignment_ok ? "good" : "warn"}>
            {report.alignment_ok ? "no overfull boxes" : `${report.overfull.length} overfull box(es)`}
          </Badge>
        )}
        {report.last_page_fill != null && report.compiled && (
          <Badge tone={report.last_page_fill >= 0.85 ? "good" : "warn"}>
            last page {Math.round(report.last_page_fill * 100)}% full
          </Badge>
        )}
        <span className="text-xs text-zinc-500">
          {report.compiles} compile{report.compiles === 1 ? "" : "s"} · {(report.duration_ms / 1000).toFixed(1)} s
        </span>
      </div>
      {report.errors.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-red-700 dark:text-red-400">
          {report.errors.map((e, i) => (
            <li key={i} className="break-words">{e}</li>
          ))}
        </ul>
      )}
      {report.overfull.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-amber-800 dark:text-amber-300">
          {report.overfull.map((b, i) => (
            <li key={i}>
              {b.kind} {b.amount_pt.toFixed(1)}pt too wide{b.region ? ` in ${b.region}` : " (outside regions)"}
              {b.line_start ? `, line ${b.line_start}` : ""}
            </li>
          ))}
        </ul>
      )}
      {report.removed_bullets.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer">
            Removed {report.removed_bullets.length} lowest-priority bullet(s) to fit the page limit
          </summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-zinc-600 dark:text-zinc-400">
            {report.removed_bullets.map((b, i) => (
              <li key={i} className="break-words">
                <span className="font-mono">{b.region}</span> (prio {b.priority.toFixed(2)}): {b.text}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
