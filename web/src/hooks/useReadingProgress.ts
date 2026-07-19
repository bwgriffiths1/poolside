import { useEffect, useState } from "react";

/** Scroll progress (0..1) of the app's `.main` scroll container — the app
 *  shell scrolls that element, not the window. Re-measures on scroll and
 *  resize; cheap enough to run unthrottled at 60fps. */
export function useReadingProgress(): number {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const main = document.querySelector(".main") as HTMLElement | null;
    if (!main) return;

    const measure = () => {
      const scrollable = main.scrollHeight - main.clientHeight;
      setProgress(scrollable <= 0 ? 0 : Math.min(1, main.scrollTop / scrollable));
    };

    measure();
    main.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      main.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, []);

  return progress;
}
