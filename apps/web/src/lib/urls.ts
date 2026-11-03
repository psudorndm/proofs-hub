// Base-path-aware URLs. Never hard-code root-relative paths in components.
export function basePath(): string {
  const base = (import.meta.env?.BASE_URL as string | undefined) ?? "/";
  return base.endsWith("/") ? base : `${base}/`;
}

export function withBase(path: string, base: string = basePath()): string {
  const clean = path.replace(/^\/+/, "");
  return `${base}${clean}`;
}

export function pageHref(prefix: string, page: number, base: string = basePath()): string {
  const root = prefix.replace(/^\/+|\/+$/g, "");
  return withBase(page <= 1 ? `${root}/` : `${root}/${page}/`, base);
}

export function releaseAsset(releaseId: string, rel: string, base: string = basePath()): string {
  return withBase(`assets/releases/${encodeURIComponent(releaseId)}/${rel.replace(/^\/+/, "")}`, base);
}
