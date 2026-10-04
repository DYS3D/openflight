import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { makeTestFlight, makeTestSession, makeTestShot } from '../test/shotFixtures';
import type { Shot } from '../types/shot';
import { TvDisplay } from './TvDisplay';

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function render(shots: Shot[], unitSystem: 'imperial' | 'metric' = 'imperial', connected = true) {
  return text(
    renderToString(
      <TvDisplay connected={connected} shots={shots} profileId="james" profileName="James" unitSystem={unitSystem} />
    )
  );
}

const session = [
  ...makeTestSession('driver', [240, 250], { ball_speed_mph: 150 }, 0),
  ...makeTestSession('7-iron', [150, 152, 148, 154], { ball_speed_mph: 118 }, 10),
].map((shot) => ({ ...shot, flight: makeTestFlight(shot.estimated_carry_yards, 2) }));

describe('TvDisplay', () => {
  it('leads with the copper Live screen for the latest shot', () => {
    const html = render(session);

    expect(html).toContain('aria-label="TV display"');
    expect(html).toContain('live-panel__grid--copper');
    expect(html.match(/class="metric-card /g)).toHaveLength(10);
    expect(html).toContain('>154<');
    expect(html).toContain('>118.0<');
    expect(html).toContain('7 Iron');
    expect(html).toContain('James');
    expect(html).not.toContain('Socket disconnected');
  });

  it('draws the latest side view with ghosts and the dispersion mini-plot', () => {
    const html = render(session);

    expect(html).toContain('aria-label="Side view of the latest ball flight"');
    expect(html).not.toContain('Top view');
    expect(html.match(/class="flight-chart__ghost"/g)).toHaveLength(3);
    expect(html).toContain('aria-label="Landing positions by club"');
    expect(html).toContain('data-club="driver"');
    // The TV is for watching: no controls beyond the Live tiles' own selection.
    expect(html).not.toContain('Change club');
  });

  it('strips the last five shots, newest first, with club, ball speed and carry', () => {
    const html = render(session);
    const strip = html.slice(html.indexOf('tv-display__recent'));
    const clubs = [...strip.matchAll(/tv-display__shot-club">([^<]+)</g)].map((match) => match[1]);

    expect(clubs).toEqual(['7 Iron', '7 Iron', '7 Iron', '7 Iron', 'Driver']);
    expect(strip).toContain('tv-display__shot-stat">118<');
    expect(strip).toContain('tv-display__shot-stat tv-display__shot-stat--carry">154<');
  });

  it('follows the active profile and the unit preference', () => {
    const shots = [
      ...session,
      makeTestShot({ profile_id: 'alex', club: 'pw', timestamp: 'z', estimated_carry_yards: 90 }),
    ];

    const html = render(shots, 'metric');

    expect(html).not.toContain('>pw<');
    expect(html).toContain(
      'tv-display__shot-stat tv-display__shot-stat--carry">141<span class="tv-display__unit">m</span>'
    );
    expect(html).toContain('Side view · m');
  });

  it('waits politely before the first shot and flags a lost connection', () => {
    const html = render([], 'imperial', false);

    expect(html).toContain('live-panel');
    expect(html).toContain('No flight data yet');
    expect(html).toContain('No landing data yet');
    expect(html).toContain('Recent shots will appear here');
    expect(html).toContain('Socket disconnected');
  });
});
