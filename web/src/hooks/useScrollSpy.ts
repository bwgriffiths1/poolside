import { useEffect, useState } from "react";

/**
 * Scroll-spy: returns the id of the section whose top is ≤140px from the
 * top of the scroll container. Listens to scroll on the `.main` container
 * (or `window` as a fallback) and re-evaluates on resize.
 */
export function useScrollSpy(
  ids: string[],
  refs: { current: Record<string, HTMLElement | null> },
  initial = "top",
  offset = 140
): string {
  const [active, setActive] = useState(initial);

  useEffect(() => {
    const main = document.querySelector(".main") as HTMLElement | null;
    if (!main) return;

    const update = () => {
      let current = initial;
      for (const id of ids) {
        const el = refs.current[id];
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        if (rect.top - offset <= 0) current = id;
      }
      setActive(current);
    };

    main.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
    return () => {
      main.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [ids, refs, initial, offset]);

  return active;
}
