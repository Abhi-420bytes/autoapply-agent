// Small shadcn/ui-style primitives (Tailwind only). The props mirror shadcn's, so these can
// be swapped for generated shadcn components later without touching call sites.
import * as React from "react";
import { IconAlert, IconCheck, IconInfo } from "@/components/icons";
import { cn } from "@/lib/utils";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "ghost" | "destructive" | "soft";
  size?: "sm" | "md";
};

export function Button({ className, variant = "default", size = "md", ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-all",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 disabled:pointer-events-none disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-4 text-sm",
        variant === "default" && "bg-brand-600 text-white shadow-sm hover:bg-brand-700",
        variant === "soft" && "bg-brand-50 text-brand-700 hover:bg-brand-100 dark:bg-brand-500/10 dark:text-brand-300 dark:hover:bg-brand-500/20",
        variant === "outline" && "border border-zinc-200 bg-white text-zinc-700 shadow-sm hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800",
        variant === "ghost" && "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800",
        variant === "destructive" && "bg-red-600 text-white shadow-sm hover:bg-red-700",
        className,
      )}
      {...props}
    />
  );
}

const fieldBase =
  "h-9 w-full min-w-0 rounded-lg border border-zinc-200 bg-white px-3 text-sm shadow-sm transition-colors " +
  "placeholder:text-zinc-400 focus-visible:border-brand-400 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand-500/10 " +
  "disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(fieldBase, className)} {...props} />;
  },
);

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(fieldBase, "pr-8", className)} {...props} />;
}

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldBase, "h-auto min-h-24 py-2 leading-relaxed", className)} {...props} />;
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn("mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400", className)}
      {...props}
    />
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-zinc-200/80 bg-white p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04)] dark:border-zinc-800 dark:bg-zinc-900/60",
        className,
      )}
      {...props}
    />
  );
}

type Tone = "neutral" | "good" | "bad" | "warn" | "accent";

export function Badge({
  className,
  tone = "neutral",
  dot,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        tone === "neutral" && "bg-zinc-50 text-zinc-600 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-700",
        tone === "good" && "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30",
        tone === "bad" && "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-500/30",
        tone === "warn" && "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30",
        tone === "accent" && "bg-brand-50 text-brand-700 ring-brand-200 dark:bg-brand-500/10 dark:text-brand-300 dark:ring-brand-500/30",
        className,
      )}
      {...props}
    >
      {dot && (
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            tone === "good" ? "bg-emerald-500" : tone === "bad" ? "bg-red-500" : tone === "warn" ? "bg-amber-500" : tone === "accent" ? "bg-brand-500" : "bg-zinc-400",
          )}
        />
      )}
      {children}
    </span>
  );
}

export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-brand-600" : "bg-zinc-300 dark:bg-zinc-700",
      )}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

export function Notice({
  tone = "neutral",
  children,
  className,
}: {
  tone?: "neutral" | "good" | "bad" | "warn";
  children: React.ReactNode;
  className?: string;
}) {
  const Icon = tone === "good" ? IconCheck : tone === "neutral" ? IconInfo : IconAlert;
  return (
    <div
      className={cn(
        "flex gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm",
        tone === "neutral" && "border-zinc-200 bg-zinc-50/80 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300",
        tone === "good" && "border-emerald-200 bg-emerald-50/80 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200",
        tone === "bad" && "border-red-200 bg-red-50/80 text-red-900 dark:border-red-900 dark:bg-red-950/60 dark:text-red-200",
        tone === "warn" && "border-amber-200 bg-amber-50/80 text-amber-900 dark:border-amber-900 dark:bg-amber-950/60 dark:text-amber-200",
        className,
      )}
    >
      <Icon size={16} className="mt-0.5 shrink-0 opacity-70" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function SectionTitle({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <div>
        <h2 className="text-base font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="text-sm text-zinc-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  icon,
  actions,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start gap-4">
      {icon && (
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100 dark:bg-brand-500/10 dark:text-brand-300 dark:ring-brand-500/20">
          {icon}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-zinc-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
  icon,
  active,
  onClick,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: Tone;
  icon?: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      onClick={onClick}
      className={cn(
        "flex items-start justify-between gap-3 rounded-xl border bg-white p-4 text-left shadow-[0_1px_2px_rgba(16,24,40,0.04)] transition-colors dark:bg-zinc-900/60",
        active ? "border-brand-300 ring-2 ring-brand-500/15" : "border-zinc-200/80 dark:border-zinc-800",
        onClick && "hover:border-zinc-300 dark:hover:border-zinc-700",
      )}
    >
      <div>
        <p className="text-xs font-medium text-zinc-500">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
        {hint && <p className="mt-0.5 text-xs text-zinc-500">{hint}</p>}
      </div>
      {icon && (
        <span
          className={cn(
            "grid h-8 w-8 place-items-center rounded-lg",
            tone === "good" && "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10",
            tone === "warn" && "bg-amber-50 text-amber-600 dark:bg-amber-500/10",
            tone === "bad" && "bg-red-50 text-red-600 dark:bg-red-500/10",
            (tone === "accent" || tone === "neutral") && "bg-brand-50 text-brand-600 dark:bg-brand-500/10",
          )}
        >
          {icon}
        </span>
      )}
    </Comp>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-zinc-200 dark:border-zinc-800" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            value === t.value
              ? "border-brand-600 text-brand-700 dark:text-brand-300"
              : "border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span
              className={cn(
                "rounded-full px-1.5 text-[11px] tabular-nums",
                value === t.value ? "bg-brand-100 text-brand-700 dark:bg-brand-500/20 dark:text-brand-200" : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
              )}
            >
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

const AVATAR_COLORS = [
  "bg-indigo-100 text-indigo-700",
  "bg-emerald-100 text-emerald-700",
  "bg-amber-100 text-amber-800",
  "bg-sky-100 text-sky-700",
  "bg-rose-100 text-rose-700",
  "bg-violet-100 text-violet-700",
  "bg-teal-100 text-teal-700",
];

export function Avatar({ name, size = 36 }: { name?: string | null; size?: number }) {
  const label = (name || "?").trim();
  const initials = label.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("") || "?";
  let h = 0;
  for (const ch of label) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return (
    <span
      className={cn("grid shrink-0 place-items-center rounded-lg text-xs font-semibold", AVATAR_COLORS[h % AVATAR_COLORS.length])}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {initials}
    </span>
  );
}

export function EmptyState({ icon, title, children }: { icon?: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-zinc-300 bg-white/50 px-6 py-10 text-center dark:border-zinc-700 dark:bg-zinc-900/30">
      {icon && <span className="mb-3 grid h-10 w-10 place-items-center rounded-xl bg-zinc-100 text-zinc-500 dark:bg-zinc-800">{icon}</span>}
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-zinc-500">{children}</div>}
    </div>
  );
}
