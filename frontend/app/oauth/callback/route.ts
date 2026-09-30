import { NextRequest, NextResponse } from "next/server";

// OAuth redirect target (register http://localhost:3000/oauth/callback in Google Cloud /
// Azure). The code is exchanged server-side by the backend; the browser never sees tokens.
// CSRF is prevented by the single-use `state` the backend issued when sign-in started.
const API_URL = process.env.API_URL ?? "http://localhost:8000";
const API_TOKEN = process.env.API_TOKEN ?? "";
// Where the browser should land (not the container's internal bind address).
const DASHBOARD_URL = process.env.DASHBOARD_URL ?? "http://localhost:3000";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code");
  const state = req.nextUrl.searchParams.get("state");
  const err = req.nextUrl.searchParams.get("error_description") ?? req.nextUrl.searchParams.get("error");
  const back = new URL("/settings/email", DASHBOARD_URL);
  if (err || !code || !state) {
    back.searchParams.set("error", err ?? "sign-in was cancelled");
    return NextResponse.redirect(back);
  }
  const res = await fetch(`${API_URL}/api/email/oauth/callback`, {
    method: "POST",
    headers: { Authorization: `Bearer ${API_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ code, state }),
    cache: "no-store",
  }).catch(() => null);
  const data = res ? await res.json().catch(() => ({})) : {};
  if (!res || !res.ok) back.searchParams.set("error", (data as { detail?: string }).detail ?? "connection failed");
  else back.searchParams.set("connected", (data as { address?: string }).address ?? "account");
  return NextResponse.redirect(back);
}
