import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { LogoMark } from "@/components/Logo";
import { Nav } from "@/components/Nav";
import { CREATOR, CREATOR_FULL, REPO_URL } from "@/lib/brand";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "AutoApply Agent",
  description: `Tailored resumes, applications and outreach. Created by ${CREATOR_FULL}.`,
  authors: [{ name: CREATOR_FULL, url: REPO_URL }],
  creator: CREATOR_FULL,
  applicationName: "AutoApply",
  icons: { icon: "/favicon-32.png", apple: "/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "AutoApply", statusBarStyle: "default" },
};

export const viewport: Viewport = { themeColor: "#4f46e5" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-zinc-50 font-sans text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        <div className="md:flex">
          <aside className="border-b border-zinc-200 bg-white px-3 py-3 dark:border-zinc-800 dark:bg-zinc-900 md:sticky md:top-0 md:flex md:h-screen md:w-64 md:shrink-0 md:flex-col md:border-b-0 md:border-r md:px-3 md:py-5">
            <div className="mb-3 flex items-center gap-2.5 px-2 md:mb-6">
              <LogoMark size={34} className="shrink-0 drop-shadow-sm" />
              <div className="leading-tight">
                <p className="text-sm font-semibold">AutoApply</p>
                <p className="text-[11px] text-zinc-500">Agentic job search</p>
              </div>
            </div>
            <div className="md:flex md:min-h-0 md:flex-1 md:flex-col md:overflow-y-auto">
              <Nav />
            </div>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-3 hidden rounded-lg px-2.5 py-2 text-[11px] leading-snug text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 md:block"
            >
              Created by <span className="font-semibold text-brand-600 dark:text-brand-300">{CREATOR}</span>
              <span className="block">Open source · MIT license</span>
            </a>
          </aside>
          <main className="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-8">
            <div className="mx-auto max-w-6xl">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
