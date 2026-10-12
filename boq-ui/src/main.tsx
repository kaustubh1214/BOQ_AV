import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { Toaster } from "sonner";
import App from "./App";
import { ErrorBoundary, reloadOnceForNewBuild } from "./components/common/error-boundary";
import { AuthProvider } from "./lib/auth";
import "./index.css";

window.addEventListener("vite:preloadError", (e) => {
  e.preventDefault();
  reloadOnceForNewBuild();
});
setTimeout(() => {
  try {
    sessionStorage.removeItem("boq-chunk-reload"); // a stable page re-arms the one-time reload (no reload loops)
  } catch {
    /* ignore */
  }
}, 10_000);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (count, err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status;
        return count < 2 && (!status || status >= 500);
      },
      refetchOnWindowFocus: true,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <ErrorBoundary>
            <App />
          </ErrorBoundary>
          <Toaster position="top-center" richColors closeButton />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
