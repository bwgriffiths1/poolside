import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { App } from "./App";
import { qk } from "./lib/queries";
import { toast } from "./lib/toast";

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/screens/overview.css";
import "./styles/screens/meeting.css";
import "./styles/screens/briefing.css";
import "./styles/screens/roundup.css";
import "./styles/screens/deepdive.css";
import "./styles/screens/ask.css";
import "./styles/screens/add.css";
import "./styles/screens/prompts.css";
import "./styles/screens/editor.css";

function is401(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("401");
}

const queryClient: QueryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
  queryCache: new QueryCache({
    // A 401 on any query means the session died mid-use. Re-check /me so
    // AppShell's isError gate — the single login redirect — kicks in.
    // (Skip when /me itself failed, or we'd refetch in a loop.)
    onError: (error, query) => {
      if (is401(error) && query.queryKey[0] !== qk.me[0]) {
        queryClient.invalidateQueries({ queryKey: qk.me });
      }
    },
  }),
  mutationCache: new MutationCache({
    // Default feedback path: a mutation without its own onError toasts here,
    // so call sites don't need per-mutation error boilerplate.
    onError: (error, _vars, _ctx, mutation) => {
      if (is401(error)) {
        queryClient.invalidateQueries({ queryKey: qk.me });
      }
      if (!mutation.options.onError) {
        toast.error(error instanceof Error ? error.message : String(error));
      }
    },
  }),
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
);
