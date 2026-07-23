import { useEffect, type ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useCan } from "../lib/queries";
import { toast } from "../lib/toast";

/** Route guard: renders children only when the user's role clears `min`.
 *  Otherwise bounces to Overview with a toast. Backend enforcement is the
 *  real gate (403s) — this just keeps viewers out of dead-end screens. */
export function RequireRole({
  min,
  children,
}: {
  min: "editor" | "admin";
  children: ReactNode;
}) {
  const { isAdmin, canEdit } = useCan();
  const allowed = min === "admin" ? isAdmin : canEdit;

  useEffect(() => {
    if (!allowed) toast.info("That page needs more access than your account has.");
  }, [allowed]);

  if (!allowed) return <Navigate to="/overview" replace />;
  return <>{children}</>;
}
