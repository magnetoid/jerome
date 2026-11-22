import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { vi } from "vitest";
import ThemeProvider from "@/components/ThemeProvider";

export function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search}</div>;
}

export function renderApp(ui: ReactElement, entry = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[entry]}>{ui}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { client, ...view };
}

type Handler = (init: RequestInit & { body?: string }) => Response | Promise<Response>;

export function json(status: number, body?: unknown, headers?: Record<string, string>) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
}

/** Route "METHOD /path" to a handler; unknown routes fail the test loudly. */
export function mockApi(routes: Record<string, Handler | Response>) {
  const fn = vi.fn(async (path: string, init: RequestInit = {}) => {
    const key = `${(init.method ?? "GET").toUpperCase()} ${path}`;
    const h = routes[key];
    if (!h) throw new Error(`unmocked request: ${key}`);
    return typeof h === "function" ? h(init as never) : h.clone();
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const ME = { id: 1, email: "ann@example.com", name: "Ann", must_change_password: false, role: "super_admin" as const };
export const MEMBER = { ...ME, role: "member" as const };
