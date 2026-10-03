import { afterEach, describe, expect, it, vi } from 'vitest';
import { applySharedFitFontSize, groupRowsByFitGroup, rowOverflowsWidth } from './useFitFontSize';

function fakeRow(contentWidthAt16: number, boxWidth: number): HTMLElement {
  const row = {
    style: { fontSize: '16px' },
    children: [] as unknown as HTMLCollection,
    get clientWidth() {
      return boxWidth;
    },
    get scrollWidth() {
      const px = parseFloat(this.style.fontSize) || 16;
      return (contentWidthAt16 * px) / 16;
    },
    getBoundingClientRect() {
      return { left: 0, right: boxWidth, top: 0, bottom: 20, width: boxWidth, height: 20, x: 0, y: 0, toJSON() {} };
    },
  };
  return row as unknown as HTMLElement;
}

describe('applySharedFitFontSize', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses one size for every row, set by the widest value', () => {
    const short = fakeRow(50, 100);
    const wide = fakeRow(120, 100);
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));

    applySharedFitFontSize([short, wide]);

    expect(short.style.fontSize).toBe(wide.style.fontSize);
    expect(parseFloat(wide.style.fontSize)).toBeLessThan(16);
    expect(rowOverflowsWidth(wide)).toBe(false);
  });

  it('keeps the CSS size when every row already fits', () => {
    const a = fakeRow(40, 100);
    const b = fakeRow(60, 100);
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      fontSize: (element as HTMLElement).style.fontSize || '16px',
    }));

    applySharedFitFontSize([a, b]);

    expect(a.style.fontSize).toBe('');
    expect(b.style.fontSize).toBe('');
  });
});

describe('groupRowsByFitGroup', () => {
  function rowIn(group: string | undefined): HTMLElement {
    return {
      closest: () => (group === undefined ? null : { dataset: { fitGroup: group } }),
    } as unknown as HTMLElement;
  }

  it('keeps ungrouped rows together and splits marked tiles into their own group', () => {
    const plain = [rowIn(undefined), rowIn(undefined)];
    const hero = [rowIn('hero'), rowIn('hero')];

    expect(groupRowsByFitGroup([hero[0], plain[0], hero[1], plain[1]])).toEqual([hero, plain]);
    expect(groupRowsByFitGroup(plain)).toEqual([plain]);
  });
});
