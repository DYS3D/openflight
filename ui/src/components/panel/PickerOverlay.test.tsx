import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PickerOverlay } from './PickerOverlay';
import { clubSections, profileSections } from './pickerSections';

describe('PickerOverlay', () => {
  it('doubles as the golfer picker: active profile selected, no group tabs', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select golfer"
        selectedId="p2"
        sections={profileSections([
          { id: 'p1', name: 'Justin', created_at: '', settings: {} },
          { id: 'p2', name: 'Lauren', created_at: '', settings: {} },
        ])}
        onSelect={() => {}}
        onClose={() => {}}
        wide
      />
    );

    expect(html).toContain('aria-label="Select golfer"');
    expect(html).not.toContain('picker-overlay__tabs');
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>Lauren</);
    expect(html).toMatch(/aria-pressed="false"[^>]*>Justin</);
  });

  it('opens the Woods tab for the default driver so DR is the selected tile', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toContain('aria-label="Groups"');
    expect(html).toMatch(/panel-action--primary[^>]*>Woods</);
    expect(html).toMatch(/panel-action--secondary[^>]*>Irons</);
    expect(html).toMatch(/panel-action--secondary[^>]*>Hybrids</);
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>DR</);
    expect(html).toContain('--picker-rows:3');
    expect(html).not.toContain('>7i<');
    expect(html).toContain('>3W<');
  });

  it('opens the Irons tab when a mid-iron is already selected', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="7-iron"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toMatch(/panel-action--primary[^>]*>Irons</);
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>7i</);
    expect(html).not.toContain('>DR<');
  });
});
