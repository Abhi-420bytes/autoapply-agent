"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type * as React from "react";
import {
  IconBook,
  IconBriefcase,
  IconChart,
  IconCpu,
  IconFile,
  IconGlobe,
  IconKey,
  IconMail,
  IconSearch,
  IconSend,
  IconSettings,
  IconSparkles,
} from "@/components/icons";
import { NotificationBell } from "@/components/NotificationBell";
import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: (p: { size?: number; className?: string }) => React.ReactNode };

const GROUPS: { title: string; items: Item[] }[] = [
  {
    title: "Work",
    items: [
      { href: "/jobs", label: "Jobs", icon: IconBriefcase },
      { href: "/outreach", label: "Outreach", icon: IconSend },
      { href: "/sites", label: "Job websites", icon: IconGlobe },
    ],
  },
  {
    title: "Resumes",
    items: [
      { href: "/jobs/new", label: "New resume from JD", icon: IconSparkles },
      { href: "/templates", label: "Resume template", icon: IconFile },
      { href: "/knowledge", label: "Knowledge base", icon: IconBook },
    ],
  },
  { title: "Insights", items: [{ href: "/analytics", label: "Analytics", icon: IconChart }] },
  {
    title: "Settings",
    items: [
      { href: "/settings/general", label: "General & profile", icon: IconSettings },
      { href: "/settings/email", label: "Email accounts", icon: IconMail },
      { href: "/settings/portals", label: "Portals", icon: IconKey },
      { href: "/settings/llm", label: "AI models", icon: IconCpu },
      { href: "/settings/outreach", label: "Web search", icon: IconSearch },
    ],
  },
];

export function Nav() {
  const path = usePathname();
  const active = (href: string) =>
    path === href || (href === "/jobs" && /^\/jobs\/\d+/.test(path)) || (href === "/templates" && path.startsWith("/studio"));
  return (
    <nav className="flex gap-1 overflow-x-auto md:flex-1 md:flex-col md:gap-5 md:overflow-visible" aria-label="Main">
      {GROUPS.map((g) => (
        <div key={g.title} className="flex gap-1 md:flex-col md:gap-0.5">
          <p className="hidden px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-400 md:block">{g.title}</p>
          {g.items.map((l) => {
            const on = active(l.href);
            const Icon = l.icon;
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 text-sm transition-colors",
                  on
                    ? "bg-brand-50 font-medium text-brand-700 dark:bg-brand-500/10 dark:text-brand-300"
                    : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100",
                )}
              >
                <Icon size={17} className={cn("shrink-0", on ? "text-brand-600 dark:text-brand-300" : "opacity-80")} />
                {l.label}
              </Link>
            );
          })}
        </div>
      ))}
      <div className="md:mt-auto md:border-t md:border-zinc-200 md:pt-3 dark:md:border-zinc-800">
        <NotificationBell />
      </div>
    </nav>
  );
}
