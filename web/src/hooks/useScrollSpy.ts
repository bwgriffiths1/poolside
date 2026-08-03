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
    // Authed pages scroll inside .main; the public share pages scroll the
    // window itself. getBoundingClientRect is viewport-relative either way.
    const main = document.querySelector(".main") as HTMLElement | null;
    const scroller: HTMLElement | Window = main ?? window;

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

    scroller.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
    return () => {
      scroller.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [ids, refs, initial, offset]);

  return active;
}
