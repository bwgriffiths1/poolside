// Tiny module-level toast store — no context, so it's callable from anywhere
// (components, api helpers, and the QueryClient's global error handlers in
// main.tsx). The <Toaster /> mounted in AppShell renders whatever is queued.

export type ToastKind = "success" | "error" | "info";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

export function subscribeToasts(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getToasts(): ToastItem[] {
  return items;
}

export function dismissToast(id: number): void {
  if (!items.some((t) => t.id === id)) return;
  items = items.filter((t) => t.id !== id);
  notify();
}

const DEFAULT_MS: Record<ToastKind, number> = {
  success: 5000,
  info: 6000,
  error: 9000,
};

export function toast(
  message: string,
  kind: ToastKind = "info",
  durationMs?: number,
): void {
  const id = nextId++;
  items = [...items, { id, kind, message }];
  notify();
  window.setTimeout(() => dismissToast(id), durationMs ?? DEFAULT_MS[kind]);
}

toast.success = (message: string, durationMs?: number) =>
  toast(message, "success", durationMs);
toast.error = (message: string, durationMs?: number) =>
  toast(message, "error", durationMs);
toast.info = (message: string, durationMs?: number) =>
  toast(message, "info", durationMs);
