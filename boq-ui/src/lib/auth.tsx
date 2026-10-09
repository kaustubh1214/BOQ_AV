import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { get, onSessionExpired, refreshAccessToken, setAccessToken } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  reload: () => Promise<Me | null>;
  can: (...perms: string[]) => boolean;
  /** Site-level user (no global/store/project scope) → mobile-first bottom navigation. */
  isSiteUser: boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const reload = useCallback(async () => {
    try {
      const r = await get<Me>("/me");
      setMe(r.data);
      return r.data;
    } catch {
      setMe(null);
      return null;
    }
  }, []);

  useEffect(() => {
    // Restore the session from the httpOnly refresh cookie.
    (async () => {
      const token = await refreshAccessToken();
      if (token) await reload();
      setLoading(false);
    })();
    return onSessionExpired(() => {
      setAccessToken(null);
      setMe(null);
      qc.clear();
    });
  }, [reload, qc]);

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await axios.post("/api/v1/auth/login", { email, password }, { withCredentials: true });
      setAccessToken(r.data.data.access_token);
      const m = await reload();
      if (!m) throw new Error("Could not load your profile");
      return m;
    },
    [reload],
  );

  const logout = useCallback(async () => {
    try {
      await axios.post("/api/v1/auth/logout", null, { withCredentials: true });
    } finally {
      setAccessToken(null);
      setMe(null);
      qc.clear();
    }
  }, [qc]);

  const value = useMemo<AuthState>(() => {
    const perms = new Set(me?.permissions ?? []);
    const isSiteUser = !!me && !me.scopes.all && me.scopes.sites.length > 0 && me.scopes.projects.length <= 1 && perms.has("rfq.create");
    return {
      me,
      loading,
      login,
      logout,
      reload,
      can: (...p) => p.some((x) => perms.has(x)),
      isSiteUser,
    };
  }, [me, loading, login, logout, reload]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
