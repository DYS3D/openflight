import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from '../App';
import type { Shot } from '../types/shot';
import { PostShotFeedback, PostShotTakeover } from './PostShotFeedback';

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 92,
    club_speed_mph: 68,
    smash_factor: 1.35,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: '2026-08-19T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    carry_spin_adjusted: 214,
    profile_id: 'james',
    ...overrides,
  };
}

describe('PostShotTakeover', () => {
  it('sizes the number in rem that follows the screen, never px', () => {
    const css = readFileSync(fileURLToPath(new URL('./PostShotFeedback.css', import.meta.url)), 'utf8');

    expect(css).toMatch(
      /\.post-shot-takeover__value-row \{[^}]*font-size: clamp\([\d.]+rem, min\([^)]*vh[^)]*vw\), [\d.]+rem\)/
    );
    expect(css).not.toMatch(/font-size: [\d.]+px/);
  });

  it('shows one big value with its label and unit', () => {
    const html = text(
      renderToString(
        <PostShotTakeover
          metric={{ id: 'ball_speed', label: 'Ball speed', value: '92.0', unit: 'mph' }}
          onDismiss={() => {}}
        />
      )
    );

    expect(html).toContain('<button type="button" class="post-shot-takeover"');
    expect(html).toContain('post-shot-takeover__label">Ball speed<');
    expect(html).toContain('post-shot-takeover__value">92.0<');
    expect(html).toContain('post-shot-takeover__unit">mph<');
    expect(html).not.toContain('metric-card__estimated');
  });

  it('marks an estimated value the way the Live tiles do', () => {
    const html = renderToString(
      <PostShotTakeover
        metric={{ id: 'carry', label: 'Carry', value: '214', unit: 'yds', estimated: true }}
        onDismiss={() => {}}
      />
    );

    expect(html).toContain('metric-card__estimated');
    expect(html).toContain('Estimated');
  });

  it('leaves out the unit for unitless metrics', () => {
    const html = renderToString(
      <PostShotTakeover metric={{ id: 'smash', label: 'Smash', value: '1.45' }} onDismiss={() => {}} />
    );

    expect(html).not.toContain('post-shot-takeover__unit');
  });
});

describe('PostShotFeedback', () => {
  it('renders nothing on first render, whatever the preference', () => {
    for (const bigNumberAfterShot of [false, true]) {
      const shot = makeShot();
      const html = renderToString(
        <PostShotFeedback
          shot={shot}
          shots={[shot]}
          profileId="james"
          heroMetricId="carry"
          shotVersion={3}
          isNewShot
          liveView
          bigNumberAfterShot={bigNumberAfterShot}
        />
      );

      expect(html).toBe('');
    }
  });

  it('adds nothing to the kiosk shell with preferences at their defaults', () => {
    expect(renderToString(<App />)).not.toContain('post-shot-takeover');
  });
});
