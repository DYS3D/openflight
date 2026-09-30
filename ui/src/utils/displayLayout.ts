export type DisplayLayout = 'default' | 'tv';

export function displayLayoutFromSearch(search: string): DisplayLayout {
  return new URLSearchParams(search).get('layout') === 'tv' ? 'tv' : 'default';
}
