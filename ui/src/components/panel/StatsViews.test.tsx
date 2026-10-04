import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { makeTestFlight, makeTestSession, makeTestShot } from '../../test/shotFixtures';
import type { Shot } from '../../types/shot';
import { StatsPanel, type StatsView } from './StatsPanel';
import { StatsDispersionView } from './StatsDispersionView';
import { StatsFlightView } from './StatsFlightView';
import { StatsGappingView } from './StatsGappingView';

const css = readFileSync(fileURLToPath(new URL('./statsViews.css', import.meta.url)), 'utf8');
const chartsCss = readFileSync(fileURLToPath(new URL('../charts/charts.css', import.meta.url)), 'utf8');

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function count(html: string, needle: string): number {
  return html.split(needle).length - 1;
}

function withFlight(shots: Shot[], lateral = 0): Shot[] {
  return shots.map((shot, index) => ({
    ...shot,
    flight: makeTestFlight(shot.estimated_carry_yards, lateral + (index % 3) - 1),
  }));
}

const renderPanel = (shots: Shot[], initialView?: StatsView) =>
  text(
    renderToString(
      <StatsPanel shots={shots} activeClub="7-iron" profileId="james" profileName="James" initialView={initialView} />
    )
  );

describe('StatsPanel view switch', () => {
  it('opens on Summary with a four-way switch in the header', () => {
    const html = renderPanel(makeTestSession('7-iron', [150, 152]));
    const header = html.match(/<header class="panel-header">[\s\S]*?<\/header>/)?.[0] ?? '';

    expect(header).toContain('aria-label="Stats view"');
    for (const label of ['Summary', 'Flight', 'Dispersion', 'Gapping']) {
      expect(header).toContain(`>${label}</button>`);
    }
    expect(header).toMatch(/aria-pressed="true">Summary</);
    expect(html).toContain('stats-panel__grid--of-6');
    expect(html).toContain('stats-panel__chips');
  });

  it('shows only the chosen sub-view, scoped to the active profile', () => {
    const shots = [
      ...withFlight(makeTestSession('7-iron', [150, 152, 148])),
      makeTestShot({ profile_id: 'alex', club: 'driver', timestamp: 'z', flight: makeTestFlight(260) }),
    ];

    const gapping = renderPanel(shots, 'gapping');
    const dispersion = renderPanel(shots, 'dispersion');

    expect(gapping).toMatch(/aria-pressed="true">Gapping</);
    expect(gapping).not.toContain('stats-panel__grid');
    expect(gapping).not.toContain('stats-panel__chips');
    expect(gapping).toContain('stats-gapping__row');
    expect(gapping).not.toContain('>driver<');
    expect(dispersion).toContain('data-club="7-iron"');
    expect(dispersion).not.toContain('data-club="driver"');
  });

  it('keeps the header at the control height so Summary does not move', () => {
    expect(css).toMatch(/\.stats-panel__views \.segmented-control \{[^}]*padding: 0;/);
    expect(css).toMatch(
      /\.stats-panel__views \.segmented-control__button \{[^}]*min-height: var\(--panel-control-height, 44px\);/
    );
  });
});

describe('StatsFlightView', () => {
  it('explains the empty state when shots carry no flight', () => {
    const html = text(
      renderToString(<StatsFlightView shots={makeTestSession('7-iron', [150])} unitSystem="imperial" />)
    );

    expect(html).toContain('No flight data yet');
    expect(html).not.toContain('<svg');
  });

  it('draws side and top views of the latest flight with up to five ghosts of the same club', () => {
    const shots = [
      ...withFlight(makeTestSession('7-iron', [150, 151, 152, 153, 154, 155, 156])),
      makeTestShot({ club: 'driver', timestamp: '2026-09-30T09:00:00Z', flight: makeTestFlight(250) }),
    ].sort((a, b) => a.timestamp.localeCompare(b.timestamp));

    const html = text(renderToString(<StatsFlightView shots={shots} unitSystem="imperial" />));

    expect(html).toContain('aria-label="Side view of the latest ball flight"');
    expect(html).toContain('aria-label="Top view of the latest ball flight"');
    expect(count(html, 'class="flight-chart__latest"')).toBe(2);
    expect(count(html, 'class="flight-chart__ghost"')).toBe(10);
    expect(html).toContain('Previous 5');
    expect(html).toContain('Side view · yds');
    expect(html).toContain('stats-readout__value">156<');
  });

  it('labels axes and readouts in metres when the unit preference is metric', () => {
    const html = text(
      renderToString(<StatsFlightView shots={withFlight(makeTestSession('7-iron', [150]))} unitSystem="metric" />)
    );

    expect(html).toContain('Side view · m');
    expect(html).toContain('Top view · m');
    expect(html).toContain('stats-readout__value">137<');
    expect(html).not.toContain('Previous');
  });
});

describe('StatsDispersionView', () => {
  const shots = [
    ...withFlight(makeTestSession('7-iron', [150, 156, 144, 152]), 2),
    ...withFlight(makeTestSession('driver', [250, 255], {}, 20), -5),
  ];

  it('shows an empty state without flight data', () => {
    const html = text(
      renderToString(<StatsDispersionView shots={makeTestSession('7-iron', [150, 152, 148])} unitSystem="imperial" />)
    );

    expect(html).toContain('No landing data yet');
    expect(html).not.toContain('chart-legend');
  });

  it('plots each club in its own colour with a legend, an ellipse from three shots and a % inside readout', () => {
    const html = text(renderToString(<StatsDispersionView shots={shots} unitSystem="imperial" />));

    expect(html).toContain('aria-label="Landing positions by club"');
    expect(html).toContain('class="chart-series-1" data-club="7-iron"');
    expect(html).toContain('class="chart-series-2" data-club="driver"');
    expect(count(html, 'class="dispersion-chart__ellipse"')).toBe(1);
    // The last shot gets the bold ring.
    expect(count(html, 'class="dispersion-chart__latest"')).toBe(1);
    expect(html).toMatch(/chart-legend__club">7-iron<\/span><span class="chart-legend__detail">\d+% in oval</);
    expect(html).toContain('chart-legend__detail">2 shots<');
    expect(count(html, 'aria-pressed="false"')).toBe(2);
    expect(html).toContain('Oval = typical spread (1 SD, 3+ shots)');
  });

  it('highlights one club and fades the rest', () => {
    const html = text(
      renderToString(<StatsDispersionView shots={shots} unitSystem="imperial" initialHighlight="driver" />)
    );

    expect(html).toContain('chart-series-1 dispersion-chart__group--dimmed" data-club="7-iron"');
    expect(html).toContain('class="chart-series-2" data-club="driver"');
    expect(html).toMatch(/chart-legend__item chart-legend__item--active" aria-pressed="true"/);
  });

  it('keeps legend chips finger-sized and drag-scrollable', () => {
    expect(chartsCss).toMatch(/\.chart-legend \{[^}]*overflow-x: auto;[^}]*touch-action: none;/);
    expect(chartsCss).toMatch(/\.chart-legend__item \{[^}]*min-height: 44px;/);
  });
});

describe('StatsGappingView', () => {
  const session = [
    ...makeTestSession('7-iron', [150, 151, 149, 150, 152, 148, 150, 151, 149, 90], {}, 0),
    ...makeTestSession('driver', [240, 250, 260], {}, 20),
    ...makeTestSession('pw', [120], {}, 30),
  ];

  it('lists clubs longest first with average ± SD, range and gap', () => {
    const html = text(renderToString(<StatsGappingView shots={session} unitSystem="imperial" />));
    const clubs = [...html.matchAll(/stats-gapping__club-name">([^<]+)</g)].map((match) => match[1]);

    expect(clubs).toEqual(['driver', '7-iron', 'pw']);
    expect(html).toContain('Last 10 shots per club');
    expect(html).toContain('>250<span class="stats-gapping__sd-text"> ± 10</span>');
    expect(html).toContain('>240–260<');
    expect(html).toContain('>106<');
    expect(html).toContain('aria-pressed="false">Exclude mishits<');
    expect(count(html, 'class="stats-gapping__sd"')).toBe(2);
  });

  it('recomputes without mishits and says how many were dropped', () => {
    const html = text(renderToString(<StatsGappingView shots={session} unitSystem="imperial" initialExcludeMishits />));

    expect(html).toContain('aria-pressed="true">Exclude mishits<');
    expect(html).toContain('1 excluded · Mishit: carry more than 2 SD from the club average');
    expect(html).toContain('>148–152<');
    expect(html).toContain('>150<span class="stats-gapping__sd-text">');
  });

  it('shows an empty state for a swing-speed-only session', () => {
    const html = text(
      renderToString(
        <StatsGappingView shots={[makeTestShot({ mode: 'swing-speed', club: 'Swing Speed' })]} unitSystem="imperial" />
      )
    );

    expect(html).toContain('No shots yet');
  });
});
