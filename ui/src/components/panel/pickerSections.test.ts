import { describe, expect, it } from 'vitest';
import type { Profile } from '../../types/profile';
import {
  clubSections,
  initialPickerSection,
  pickerGridRows,
  profileSections,
  trainingImplementSections,
} from './pickerSections';

describe('profileSections', () => {
  it('offers every profile by name in one tab-less section', () => {
    const profiles: Profile[] = [
      { id: 'p1', name: 'Justin', created_at: '', settings: {} },
      { id: 'p2', name: 'Lauren', created_at: '', settings: {} },
    ];

    expect(profileSections(profiles)).toEqual([
      {
        name: 'profiles',
        options: [
          { id: 'p1', label: 'Justin' },
          { id: 'p2', label: 'Lauren' },
        ],
      },
    ]);
  });
});

describe('initialPickerSection', () => {
  const clubs = clubSections();

  it('opens Woods when the driver is selected', () => {
    expect(initialPickerSection(clubs, 'driver')).toBe('Woods');
  });

  it('opens Irons when a wedge is selected', () => {
    expect(initialPickerSection(clubs, 'pw')).toBe('Irons');
  });

  it('falls back to the first section when the id is unknown', () => {
    expect(initialPickerSection(clubs, 'not-a-club')).toBe('Irons');
  });

  it('opens the matching training group', () => {
    const groups = trainingImplementSections();
    expect(initialPickerSection(groups, 'stack-160g')).toBe('TheStack');
  });
});

describe('pickerGridRows', () => {
  it('uses three rows for the iron set so woods tiles match that cube size', () => {
    expect(pickerGridRows(clubSections())).toBe(3);
  });

  it('uses the densest training group so every tab fits', () => {
    expect(pickerGridRows(trainingImplementSections())).toBe(4);
  });
});
