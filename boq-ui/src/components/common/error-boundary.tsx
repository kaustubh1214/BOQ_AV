import { Component, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

const CHUNK_RE = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i;

/** After a redeploy an open tab still points at old hashed chunks; reload once to pick up the new build. */
export function reloadOnceForNewBuild(): boolean {
  try {
    if (sessionStorage.getItem("boq-chunk-reload") === "1") return false;
    sessionStorage.setItem("boq-chunk-reload", "1");
  } catch {
    /* storage blocked: still reload once */
  }
  window.location.reload();
  return true;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    if (CHUNK_RE.test(error.message)) reloadOnceForNewBuild();
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="grid min-h-[50dvh] place-items-center p-6 text-center">
        <div className="max-w-sm">
          <p className="text-lg font-semibold">Something went wrong</p>
          <p className="mt-1 text-sm text-muted-foreground">The app was probably updated. Reload to continue — your data is safe.</p>
          <Button className="mt-4" onClick={() => window.location.reload()}>
            Reload
          </Button>
        </div>
      </div>
    );
  }
}
