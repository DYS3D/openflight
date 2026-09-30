import { describe, expect, it } from 'vitest';
import { displayLayoutFromSearch } from './displayLayout';

describe('displayLayoutFromSearch', () => {
  it('keeps the default display unless the TV layout is asked for', () => {
    expect(displayLayoutFromSearch('')).toBe('default');
    expect(displayLayoutFromSearch('?layout=grid')).toBe('default');
    expect(displayLayoutFromSearch('?layout=tv')).toBe('tv');
    expect(displayLayoutFromSearch('?theme=dark&layout=tv')).toBe('tv');
  });
});
