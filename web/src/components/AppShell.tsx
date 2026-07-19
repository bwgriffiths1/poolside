import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { CommandPalette } from "./CommandPalette";
import { Toaster } from "./Toaster";
import { useMe } from "../lib/queries";

export function AppShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const mainRef = useRef<HTMLElement>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Mobile-only overlay drawer state; a no-op ≥900px where the sidebar is
  // part of the grid (the toggle/backdrop are display:none there).
  const [navOpen, setNavOpen] = useState(false);

  const { data: user, isLoading, isError } = useMe();

  useEffect(() => {
    if (isError) navigate("/login", { replace: true });
  }, [isError, navigate]);

  // Scroll main to top on route change (per design spec); also close the
  // mobile drawer so tapping a nav item lands on content, not the menu.
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
    setNavOpen(false);
  }, [pathname]);

  // Global ⌘K / Ctrl+K to open the command palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (isLoading || !user) {
    return <div className="login-shell" />;
  }

  return (
    <div className={`app${navOpen ? " nav-open" : ""}`}>
      <button
        type="button"
        className="nav-toggle"
        aria-label={navOpen ? "Close navigation" : "Open navigation"}
        onClick={() => setNavOpen(!navOpen)}
      >
        <span className="nav-toggle-bar" />
        <span className="nav-toggle-bar" />
        <span className="nav-toggle-bar" />
      </button>
      {navOpen && (
        <div className="nav-backdrop" onClick={() => setNavOpen(false)} />
      )}
      <Sidebar user={user} onOpenPalette={() => setPaletteOpen(true)} />
      <main className="main" ref={mainRef}>
        <Outlet />
      </main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <Toaster />
    </div>
  );
}
