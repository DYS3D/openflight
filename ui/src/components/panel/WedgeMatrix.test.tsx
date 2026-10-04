import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { makeTestShot } from '../../test/shotFixtures';
import { WedgeMatrixBoard } from './WedgeMatrix';

function render(club: string) {
  const shots = [
    { ...makeTestShot({ club: 'sw', estimated_carry_yards: 61, timestamp: 'a' }), swing_length: '1/2' as const },
    { ...makeTestShot({ club: 'sw', estimated_carry_yards: 65, timestamp: 'b' }), swing_length: '1/2' as const },
  ];
  return renderToString(
    <WedgeMatrixBoard shots={shots} club={club} unitSystem="imperial" swing="1/2" onSwing={() => {}} />
  ).replace(/<!-- -->/g, '');
}

describe('WedgeMatrixBoard', () => {
  it('shows each wedge × swing median and marks the cell being hit', () => {
    const html = render('sw');
    expect(html).toContain('aria-pressed="true">½<');
    expect(html).toContain('wedge-matrix__cell--current"><span class="wedge-matrix__carry">63');
    expect(html).toContain('2 shots');
    expect(html).toContain('Hitting SW: each shot is saved as this swing length');
  });

  it('asks for a wedge when another club is selected', () => {
    expect(render('7-iron')).toContain('Pick a wedge with Change Club, then a swing length');
  });
});
