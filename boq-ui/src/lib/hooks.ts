import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { errorMessage, get, send } from "./api";

export function useList<T>(key: string, url: string, params: Record<string, unknown> = {}, enabled = true) {
  return useQuery({
    queryKey: [key, url, params],
    queryFn: () => get<T[]>(url, params),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useOne<T>(key: string, url: string | null) {
  return useQuery({ queryKey: [key, url], queryFn: async () => (await get<T>(url!)).data, enabled: !!url });
}

/** Mutation that toasts success / API error and refreshes the given query keys. */
export function useAction<TBody, TOut>(
  fn: (body: TBody) => Promise<TOut>,
  opts: { success?: string | ((out: TOut) => string); invalidate?: string[]; onSuccess?: (out: TOut) => void } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (out) => {
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(out) : opts.success);
      (opts.invalidate ?? []).forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      opts.onSuccess?.(out);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export const post = <T>(url: string, body?: unknown, headers?: Record<string, string>) => send<T>("post", url, body, headers);
export const put = <T>(url: string, body?: unknown) => send<T>("put", url, body);
export const patch = <T>(url: string, body?: unknown) => send<T>("patch", url, body);
export const del = <T>(url: string) => send<T>("delete", url);

/** Filters kept in the URL so lists survive refresh / back navigation and links like ?queue=pm work. */
export function useUrlFilters<T extends Record<string, string>>(defaults: T) {
  const [sp, setSp] = useSearchParams();
  const values = { ...defaults } as T;
  for (const k of Object.keys(defaults)) {
    const v = sp.get(k);
    if (v !== null) (values as Record<string, string>)[k] = v;
  }
  const page = Number(sp.get("page") ?? 1);
  const set = (patch: Partial<T> & { page?: number }) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "" || v === null) next.delete(k);
      else next.set(k, String(v));
    }
    if (!("page" in patch)) next.delete("page");
    setSp(next, { replace: true });
  };
  return { values, page, set };
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useMediaQuery(q: string) {
  const [m, setM] = useState(() => matchMedia(q).matches);
  useEffect(() => {
    const mq = matchMedia(q);
    const fn = () => setM(mq.matches);
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [q]);
  return m;
}
