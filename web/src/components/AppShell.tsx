import { Outlet, useLocation } from "react-router-dom";
import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sidebar } from "./Sidebar";
import { api } from "../lib/api";

const DEMO_USER = {
  name: "Ben Griffiths",
  email: "ben@poolside.io",
  initials: "BG",
};

export function AppShell() {
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);

  const { data: user = DEMO_USER } = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me(),
  });

  // Scroll main to top on route change (per design spec).
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [pathname]);

  return (
    <div className="app">
      <Sidebar user={user} />
      <main className="main" ref={mainRef}>
        <Outlet />
      </main>
    </div>
  );
}
