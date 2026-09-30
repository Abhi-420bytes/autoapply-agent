"use client";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        [Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "", d.msg]
          .filter(Boolean)
          .join(": "),
      )
      .join("; ");
  }
  return "request failed";
}

/** Browser-side call to the backend through the same-origin proxy. */
export async function call<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const res = await fetch(`/api/backend${path}`, {
    ...rest,
    headers: { "Content-Type": "application/json", "X-AutoApply": "1", ...rest.headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, detailToMessage((data as { detail?: unknown }).detail));
  return data as T;
}

/** Multipart upload through the proxy (browser sets the boundary header). */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`/api/backend${path}`, {
    method: "POST",
    headers: { "X-AutoApply": "1" },
    body: form,
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, detailToMessage((data as { detail?: unknown }).detail));
  return data as T;
}

/** Fetch a binary file (e.g. a PDF) and return an object URL for previewing. */
export async function blobUrl(path: string): Promise<string> {
  const res = await fetch(`/api/backend${path}`, { headers: { "X-AutoApply": "1" }, cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `could not load ${path}`);
  return URL.createObjectURL(await res.blob());
}

/** Fetch a text file (e.g. a .tex source) through the proxy. */
export async function text(path: string): Promise<string> {
  const res = await fetch(`/api/backend${path}`, { headers: { "X-AutoApply": "1" }, cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `could not load ${path}`);
  return res.text();
}
