import axios, { AxiosError, type AxiosRequestConfig } from "axios";

/** Access token lives only in memory; the refresh token is an httpOnly cookie the browser sends to /auth. */
let accessToken: string | null = null;
let refreshing: Promise<string | null> | null = null;
const listeners = new Set<() => void>();

export const setAccessToken = (t: string | null) => {
  accessToken = t;
};
export const onSessionExpired = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};

export const http = axios.create({ baseURL: "/api/v1", withCredentials: true, timeout: 30000 });

http.interceptors.request.use((cfg) => {
  if (accessToken) cfg.headers.set("Authorization", `Bearer ${accessToken}`);
  return cfg;
});

export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = axios
      .post("/api/v1/auth/refresh", null, { withCredentials: true })
      .then((r) => {
        accessToken = r.data.data.access_token;
        return accessToken;
      })
      .catch(() => {
        accessToken = null;
        return null;
      })
      .finally(() => {
        setTimeout(() => (refreshing = null), 0);
      });
  }
  return refreshing;
}

http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError<ApiErrorBody>) => {
    const cfg = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
    const code = error.response?.data?.error?.code;
    if (error.response?.status === 401 && cfg && !cfg._retried && !cfg.url?.startsWith("/auth/")) {
      cfg._retried = true;
      const token = await refreshAccessToken();
      if (token) return http(cfg);
      listeners.forEach((fn) => fn());
    }
    if (code === "PASSWORD_CHANGE_REQUIRED" && !location.pathname.startsWith("/change-password")) {
      location.assign("/change-password");
    }
    return Promise.reject(error);
  },
);

export interface ApiErrorBody {
  success: false;
  error: { code: string; message: string; details?: unknown };
}

export interface Envelope<T> {
  success: true;
  data: T;
  meta: { page?: number; limit?: number; total?: number; totalPages?: number; nextCursor?: number | null };
}

export function errorMessage(e: unknown): string {
  const err = e as AxiosError<ApiErrorBody>;
  const body = err?.response?.data?.error;
  if (body) {
    if (body.code === "VALIDATION_ERROR" && Array.isArray(body.details) && body.details.length) {
      return body.details.map((d: { field: string; message: string }) => `${d.field ? d.field + ": " : ""}${d.message}`).join(" · ");
    }
    return body.message;
  }
  if (err?.code === "ERR_NETWORK") return "Network error — check your connection";
  return (e as Error)?.message ?? "Something went wrong";
}

export const errorCode = (e: unknown) => (e as AxiosError<ApiErrorBody>)?.response?.data?.error?.code;
export const errorDetails = (e: unknown) => (e as AxiosError<ApiErrorBody>)?.response?.data?.error?.details;

export async function get<T>(url: string, params?: object): Promise<Envelope<T>> {
  const r = await http.get<Envelope<T>>(url, { params: clean(params) });
  return r.data;
}

export async function send<T>(method: "post" | "put" | "patch" | "delete", url: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  const r = await http.request<Envelope<T>>({ method, url, data: body, headers });
  return r.data.data;
}

/** Download a binary (PDF / Excel) with auth, then open or save it. */
export async function download(url: string, params?: object, mode: "open" | "save" = "save") {
  const r = await http.get(url, { params: clean(params), responseType: "blob" });
  const name = /filename="([^"]+)"/.exec(r.headers["content-disposition"] ?? "")?.[1] ?? "download";
  const href = URL.createObjectURL(r.data);
  if (mode === "open") {
    window.open(href, "_blank", "noopener");
  } else {
    const a = document.createElement("a");
    a.href = href;
    a.download = name;
    a.click();
  }
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
}

function clean(params?: object) {
  if (!params) return undefined;
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""));
}
