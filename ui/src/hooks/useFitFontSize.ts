import { useLayoutEffect, useRef, type RefObject } from 'react';

export function rowOverflowsWidth(row: HTMLElement, slop = 1): boolean {
  if (row.scrollWidth > row.clientWidth + slop) {
    return true;
  }
  const box = row.getBoundingClientRect();
  const children = [...row.children];
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index];
    if (!(child instanceof HTMLElement)) {
      continue;
    }
    const range = document.createRange();
    range.selectNodeContents(child);
    const ink = range.getBoundingClientRect();
    const childBox = child.getBoundingClientRect();
    const right = Math.max(childBox.right, ink.right || 0);
    if (right > box.right + slop) {
      return true;
    }
    const next = children[index + 1];
    if (next instanceof HTMLElement && ink.right > next.getBoundingClientRect().left + slop) {
      return true;
    }
  }
  return false;
}

/** One size for every row: the CSS ceiling, or smaller if the widest row still overflows. */
export function applySharedFitFontSize(rows: HTMLElement[], minPx = 12): void {
  if (rows.length === 0) {
    return;
  }
  for (const row of rows) {
    row.style.fontSize = '';
  }
  const maxPx = Math.min(...rows.map((row) => parseFloat(getComputedStyle(row).fontSize)));
  if (!Number.isFinite(maxPx) || maxPx <= 0 || rows.some((row) => row.clientWidth <= 0)) {
    return;
  }

  const apply = (px: number) => {
    for (const row of rows) {
      row.style.fontSize = `${px}px`;
    }
  };
  const fits = (px: number) => {
    apply(px);
    return rows.every((row) => !rowOverflowsWidth(row));
  };

  if (fits(maxPx)) {
    for (const row of rows) {
      row.style.fontSize = '';
    }
    return;
  }

  let low = minPx;
  let high = maxPx;
  let best = minPx;
  for (let step = 0; step < 14; step += 1) {
    const mid = (low + high) / 2;
    if (fits(mid)) {
      best = mid;
      low = mid;
    } else {
      high = mid;
    }
  }
  apply(best);
}

/**
 * Split rows by the nearest `data-fit-group`, so differently sized tiles (the
 * Copper hero tiles) each share a size with their own kind.
 */
export function groupRowsByFitGroup(rows: HTMLElement[]): HTMLElement[][] {
  const groups = new Map<string, HTMLElement[]>();
  for (const row of rows) {
    const key = row.closest<HTMLElement>('[data-fit-group]')?.dataset.fitGroup ?? '';
    const group = groups.get(key);
    if (group) {
      group.push(row);
    } else {
      groups.set(key, [row]);
    }
  }
  return [...groups.values()];
}

/**
 * Fit every `.metric-card__value-row` in a grid to the same font-size so Live
 * numbers stay consistent (181 and 2,328 look the same size). Tiles marked
 * with a `data-fit-group` are fitted with their group instead.
 */
export function useSharedFitFontSize(enabled: boolean, token: string): RefObject<HTMLDivElement | null> {
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const grid = ref.current;
    if (!enabled || !grid) {
      return undefined;
    }

    let cancelled = false;
    const mutationObserver = new MutationObserver(() => {
      fit();
    });
    const fit = () => {
      if (cancelled) {
        return;
      }
      mutationObserver.disconnect();
      for (const group of groupRowsByFitGroup([...grid.querySelectorAll<HTMLElement>('.metric-card__value-row')])) {
        applySharedFitFontSize(group);
      }
      if (!cancelled) {
        mutationObserver.observe(grid, { subtree: true, characterData: true, childList: true });
      }
    };

    // cqi/cqb and webfonts can change size after the first layout; refit then.
    fit();
    const frame = requestAnimationFrame(() => {
      fit();
      requestAnimationFrame(fit);
    });
    const retry = window.setTimeout(fit, 50);
    void document.fonts.ready.then(fit);
    const resizeObserver = new ResizeObserver(fit);
    resizeObserver.observe(grid);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      window.clearTimeout(retry);
      resizeObserver.disconnect();
      mutationObserver.disconnect();
      for (const row of grid.querySelectorAll<HTMLElement>('.metric-card__value-row')) {
        row.style.fontSize = '';
      }
    };
  }, [enabled, token]);

  return ref;
}
