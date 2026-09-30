"use client";

import { useState } from "react";

export type Series = { name: string; color: string };
export type Datum = { label: string; values: number[] };

/** Thin vertical bars (grouped when >1 series), 4px rounded data-ends on the baseline,
 * 2px gaps, recessive grid, hover tooltip, legend for >=2 series, table view. */
export function BarChart({
  data,
  series,
  format = (v) => String(v),
  height = 200,
  title,
}: {
  data: Datum[];
  series: Series[];
  format?: (v: number) => string;
  height?: number;
  title: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const pad = { l: 40, r: 8, t: 8, b: 24 };
  const max = Math.max(1, ...data.flatMap((d) => d.values));
  const nice = niceMax(max);
  const plotW = W - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const band = plotW / Math.max(1, data.length);
  const groupW = Math.min(band * 0.7, 48);
  const barW = (groupW - 2 * (series.length - 1)) / series.length;
  const y = (v: number) => pad.t + plotH - (v / nice) * plotH;
  const ticks = [0, nice / 2, nice];
  const every = Math.ceil(data.length / 8);

  return (
    <figure className="viz-root relative">
      <figcaption className="sr-only">{title}</figcaption>
      {series.length > 1 && (
        <div className="mb-2 flex gap-4 text-xs" style={{ color: "var(--viz-muted)" }}>
          {series.map((s) => (
            <span key={s.name} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${height}`} className="w-full" role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--viz-grid)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--viz-muted)">{format(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x0 = pad.l + i * band + (band - groupW) / 2;
          return (
            <g key={d.label} onMouseEnter={() => setHover(i)}>
              <rect x={pad.l + i * band} y={pad.t} width={band} height={plotH} fill="transparent" />
              {d.values.map((v, k) => {
                const h = Math.max(0, pad.t + plotH - y(v));
                const x = x0 + k * (barW + 2);
                const r = Math.min(4, barW / 2, h);
                return h > 0 ? (
                  <path key={k} d={roundedTop(x, y(v), barW, h, r)} fill={series[k].color} opacity={hover === null || hover === i ? 1 : 0.45} />
                ) : null;
              })}
              {i % every === 0 && (
                <text x={pad.l + i * band + band / 2} y={height - 6} textAnchor="middle" fontSize={10} fill="var(--viz-muted)">{d.label}</text>
              )}
            </g>
          );
        })}
      </svg>
      {hover !== null && (
        <div
          className="pointer-events-none absolute top-6 z-10 rounded-md border border-zinc-200 bg-white px-2 py-1 text-xs shadow dark:border-zinc-700 dark:bg-zinc-900"
          style={{ left: `${Math.min(80, ((hover + 0.5) / data.length) * 100)}%` }}
        >
          <p className="font-medium">{data[hover].label}</p>
          {series.map((s, k) => (
            <p key={s.name} className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: s.color }} />
              {s.name}: <span className="tabular-nums">{format(data[hover].values[k])}</span>
            </p>
          ))}
        </div>
      )}
      <details className="mt-1 text-xs">
        <summary className="cursor-pointer" style={{ color: "var(--viz-muted)" }}>Table view</summary>
        <table className="mt-1 w-full text-left tabular-nums">
          <thead><tr><th className="py-0.5">{title}</th>{series.map((s) => <th key={s.name} className="text-right">{s.name}</th>)}</tr></thead>
          <tbody>{data.map((d) => <tr key={d.label}><td>{d.label}</td>{d.values.map((v, k) => <td key={k} className="text-right">{format(v)}</td>)}</tr>)}</tbody>
        </table>
      </details>
    </figure>
  );
}

/** Horizontal single-series bars with direct value labels (few categories). */
export function HBarChart({ data, format = (v) => String(v), title }: { data: { label: string; value: number }[]; format?: (v: number) => string; title: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <figure className="viz-root space-y-1.5" aria-label={title}>
      <figcaption className="sr-only">{title}</figcaption>
      {data.map((d) => (
        <div key={d.label} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-2 text-xs">
          <span className="truncate" title={d.label} style={{ color: "var(--viz-text)" }}>{d.label}</span>
          <span className="h-3">
            <span className="block h-full rounded-r" style={{ width: `${Math.max(2, (d.value / max) * 100)}%`, background: "var(--series-1)" }} />
          </span>
          <span className="tabular-nums" style={{ color: "var(--viz-muted)" }}>{format(d.value)}</span>
        </div>
      ))}
    </figure>
  );
}

function niceMax(v: number): number {
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
}

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}
