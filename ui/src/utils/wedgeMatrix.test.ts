import { describe, expect, it } from 'vitest';
import { makeTestShot } from '../test/shotFixtures';
import type { Shot, SwingLength } from '../types/shot';
import { buildWedgeMatrix } from './wedgeMatrix';

function tagged(club: string, carry: number, swing: SwingLength | null): Shot {
  return { ...makeTestShot({ club, carry_spin_adjusted: carry, estimated_carry_yards: carry }), swing_length: swing };
}

describe('buildWedgeMatrix', () => {
  it('takes the median carry per wedge and swing length, ignoring untagged and non-wedge shots', () => {
    const rows = buildWedgeMatrix([
      tagged('gw', 80, 'full'),
      tagged('gw', 84, 'full'),
      tagged('gw', 90, 'full'),
      tagged('gw', 62, '3/4'),
      tagged('gw', 200, null),
      tagged('7-iron', 140, 'full'),
    ]);

    expect(rows.map((row) => row.club)).toEqual(['pw', 'gw', 'sw', 'lw']);
    const gw = rows[1].cells;
    expect(gw.full).toEqual({ median: 84, shots: 3 });
    expect(gw['3/4']).toEqual({ median: 62, shots: 1 });
    expect(gw['1/2']).toEqual({ median: null, shots: 0 });
    expect(rows[0].cells.full.shots).toBe(0);
  });
});
