import { useSyncExternalStore } from "react";
import { dismissToast, getToasts, subscribeToasts } from "../lib/toast";
import { Icon } from "./Icon";

export function Toaster() {
  const toasts = useSyncExternalStore(subscribeToasts, getToasts);
  if (toasts.length === 0) return null;
  return (
    <div className="toaster">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} role="status">
          <span className="toast-msg">{t.message}</span>
          <button
            type="button"
            className="toast-x"
            aria-label="Dismiss"
            onClick={() => dismissToast(t.id)}
          >
            <Icon name="x" size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
