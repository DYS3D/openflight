import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot } from '../../types/shot';
import { CarryTrend } from './CarryTrend';

function shot(carry: number, index: number): Shot {
  return {
    estimated_carry_yards: carry,
    carry_spin_adjusted: null,
    timestamp: `2026-10-03T10:00:0${index}Z`,
  } as Shot;
}

describe('CarryTrend', () => {
  it('renders nothing until there are two shots to compare', () => {
    expect(renderToString(<CarryTrend shots={[shot(200, 0)]} unitSystem="imperial" />)).toBe('');
  });

  it('shows the last six carries with the newest bar highlighted', () => {
    const shots = [180, 190, 200, 210, 220, 230, 240, 250].map(shot);
    const html = renderToString(<CarryTrend shots={shots} unitSystem="imperial" />).replace(/<!-- -->/g, '');

    expect(html.match(/carry-trend__bar[ "]/g)).toHaveLength(6);
    expect(html.match(/carry-trend__bar--latest/g)).toHaveLength(1);
    expect(html).toContain('Last 6');
    expect(html).toContain('Shot 1: 200 yds');
    expect(html).toContain('height:30%');
    expect(html).toContain('height:100%');
  });
});
