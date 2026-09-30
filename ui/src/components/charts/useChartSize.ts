import { useEffect, useState, type RefObject } from 'react';

export interface ChartSize {
  width: number;
  height: number;
  fontPx: number;
}

export const DEFAULT_CHART_SIZE: ChartSize = { width: 640, height: 240, fontPx: 13 };

/** Draw in real pixels; before the first measurement (and in SSR) the fallback is scaled to fit. */
export function useChartSize(ref: RefObject<HTMLElement | null>, fallback = DEFAULT_CHART_SIZE): ChartSize {
  const [size, setSize] = useState(fallback);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const fontPx = parseFloat(getComputedStyle(element).fontSize) || fallback.fontPx;
      const next = { width: Math.round(rect.width), height: Math.round(rect.height), fontPx };
      setSize((current) =>
        current.width === next.width && current.height === next.height && current.fontPx === next.fontPx
          ? current
          : next
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, fallback.fontPx]);

  return size;
}
