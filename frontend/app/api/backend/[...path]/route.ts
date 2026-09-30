import { NextRequest, NextResponse } from "next/server";

// Server-side proxy: the browser calls /api/backend/<path>, and this handler forwards to
// the FastAPI /api/<path> with the API token attached. The token never reaches the browser.
//
// Because the proxy adds credentials, it must not be usable cross-site (CSRF) or via DNS
// rebinding. Requests are rejected unless they come from this origin, carry our custom
// header (which forces a CORS preflight that we never approve), and target an allowed host.

const API_URL = process.env.API_URL ?? "http://localhost:8000";
const API_TOKEN = process.env.API_TOKEN ?? "";
const ALLOWED_HOSTS = (process.env.ALLOWED_HOSTS ?? "localhost:3000,127.0.0.1:3000")
  .split(",")
  .map((h) => h.trim())
  .filter(Boolean);

export const dynamic = "force-dynamic";

function forbidden(reason: string) {
  return NextResponse.json({ detail: `forbidden: ${reason}` }, { status: 403 });
}

async function proxy(req: NextRequest, ctx: { params: { path: string[] } }) {
  const host = req.headers.get("host") ?? "";
  if (!ALLOWED_HOSTS.includes(host)) return forbidden("host");
  const site = req.headers.get("sec-fetch-site");
  if (site && site !== "same-origin") return forbidden("cross-site request");
  if (req.headers.get("x-autoapply") !== "1") return forbidden("missing header");

  const segments = ctx.params.path;
  if (segments.some((s) => s === ".." || s === "." || s === "")) return forbidden("path");
  const url = `${API_URL}/api/${segments.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const init: RequestInit = {
    method: req.method,
    headers: {
      Authorization: `Bearer ${API_TOKEN}`,
      "Content-Type": req.headers.get("content-type") ?? "application/json",
    },
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "HEAD") init.body = await req.arrayBuffer(); // binary-safe (uploads)

  try {
    const res = await fetch(url, init);
    const headers: Record<string, string> = {
      "Content-Type": res.headers.get("content-type") ?? "application/json",
    };
    const disposition = res.headers.get("content-disposition");
    if (disposition) headers["Content-Disposition"] = disposition;
    return new NextResponse(res.status === 204 ? null : res.body, { status: res.status, headers });
  } catch {
    return NextResponse.json({ detail: "backend unreachable" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
