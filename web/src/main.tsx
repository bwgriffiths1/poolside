import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";

import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/screens/overview.css";
import "./styles/screens/meeting.css";
import "./styles/screens/briefing.css";
import "./styles/screens/add.css";
import "./styles/screens/prompts.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
);
