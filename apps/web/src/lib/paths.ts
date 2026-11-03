// getStaticPaths helpers: page 1 at the list root, later pages at /<n>/.
import type { ListPage } from "./contracts";

export function pagedPaths<E extends Record<string, string>>(pages: ListPage[], extra: E = {} as E) {
  return pages.map((page) => ({
    params: { ...extra, page: page.page === 1 ? undefined : String(page.page) },
    props: { page, ...extra },
  }));
}
