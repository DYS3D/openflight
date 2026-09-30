import { describe, expect, it } from 'vitest';
import { makeTestFlight, makeTestSession, makeTestShot } from '../test/shotFixtures';
import {
  buildDispersion,
  buildGapping,
  carryYards,
  clubSeries,
  excludeMishits,
  flightExtent,
  flightTraces,
  isValidFlight,
} from './shotAnalysis';

describe('carryYards', () => {
  it('matches the Live panel: spin-adjusted carry first', () => {
    expect(carryYards(makeTestShot({ estimated_carry_yards: 150, carry_spin_adjusted: 158 }))).toBe(158);
    expect(carryYards(makeTestShot({ estimated_carry_yards: 150, carry_spin_adjusted: null }))).toBe(150);
  });
});

describe('isValidFlight', () => {
  it('rejects missing or malformed flight payloads', () => {
    expect(isValidFlight(undefined)).toBe(false);
    expect(isValidFlight(null)).toBe(false);
    expect(isValidFlight({ ...makeTestFlight(150), points: [[0, 0, 0]] })).toBe(false);
    expect(
      isValidFlight({
        ...makeTestFlight(150),
        points: [
          [0, 0, 0],
          [1, Number.NaN, 2],
        ],
      })
    ).toBe(false);
    expect(isValidFlight(makeTestFlight(150))).toBe(true);
  });
});

describe('flightTraces', () => {
  it('returns null until a shot carries a flight', () => {
    expect(flightTraces([makeTestShot()])).toBeNull();
  });

  it('pairs the latest flight with up to five earlier flights of the same club', () => {
    const irons = makeTestSession('7-iron', [150, 151, 152, 153, 154, 155, 156], {}).map((shot, index) => ({
      ...shot,
      flight: makeTestFlight(150 + index),
    }));
    const driver = makeTestShot({ club: 'driver', timestamp: '2026-09-30T09:00:00Z', flight: makeTestFlight(250) });
    const noFlight = makeTestShot({ club: '7-iron', timestamp: '2026-09-30T11:00:00Z' });

    const traces = flightTraces([driver, ...irons, noFlight])!;

    expect(traces.latest.flight.carry_yards).toBe(156);
    expect(traces.ghosts.map((shot) => shot.flight.carry_yards)).toEqual([151, 152, 153, 154, 155]);
  });

  it('never borrows ghosts from another club', () => {
    const driver = makeTestShot({ club: 'driver', timestamp: 'a', flight: makeTestFlight(250) });
    const iron = makeTestShot({ club: '7-iron', timestamp: 'b', flight: makeTestFlight(150) });

    expect(flightTraces([driver, iron])!.ghosts).toEqual([]);
  });

  it('measures the space the traces need', () => {
    expect(flightExtent([makeTestFlight(150, -12, 30), makeTestFlight(160, 4, 25)])).toEqual({
      downrange: 160,
      height: 30,
      lateral: 12,
    });
  });
});

describe('clubSeries', () => {
  it('assigns colours in first-shot order and keeps them as clubs are added', () => {
    const before = clubSeries([makeTestShot({ club: '7-iron' }), makeTestShot({ club: 'driver' })]);
    const after = clubSeries([
      makeTestShot({ club: '7-iron' }),
      makeTestShot({ club: 'driver' }),
      makeTestShot({ club: 'pw' }),
    ]);

    expect(before.get('7-iron')).toEqual(after.get('7-iron'));
    expect(before.get('driver')).toEqual(after.get('driver'));
    expect(after.get('pw')).toEqual({ colour: 3, shape: 'triangle' });
  });

  it('gives a ninth club a new colour and shape pair instead of a duplicate', () => {
    const clubs = ['driver', '3-wood', '5-wood', '4-iron', '5-iron', '6-iron', '7-iron', '8-iron', '9-iron'];
    const series = clubSeries(clubs.map((club) => makeTestShot({ club })));
    const pairs = [...series.values()].map((entry) => `${entry.colour}-${entry.shape}`);

    expect(new Set(pairs).size).toBe(clubs.length);
    expect(series.get('9-iron')).toEqual({ colour: 1, shape: 'square' });
  });

  it('ignores swing-speed readings', () => {
    const series = clubSeries([makeTestShot({ mode: 'swing-speed', club: 'Swing Speed' })]);

    expect(series.size).toBe(0);
  });
});

describe('buildDispersion', () => {
  it('groups landing points by club and skips shots without a flight', () => {
    const shots = [
      makeTestShot({ club: '7-iron', flight: makeTestFlight(150, -4) }),
      makeTestShot({ club: '7-iron', flight: makeTestFlight(156, 3) }),
      makeTestShot({ club: 'driver', flight: makeTestFlight(250, 12) }),
      makeTestShot({ club: 'pw' }),
    ];

    const groups = buildDispersion(shots);

    expect(groups.map((group) => group.club)).toEqual(['7-iron', 'driver']);
    expect(groups[0].points).toEqual([
      { x: 150, y: -4 },
      { x: 156, y: 3 },
    ]);
    expect(groups[0].ellipse).toBeNull();
    expect(groups[0].insideFraction).toBeNull();
  });

  it('adds a 1-SD ellipse and the share inside it from three shots', () => {
    const shots = [
      [140, -6],
      [150, 0],
      [160, 6],
      [150, 8],
      [150, -8],
    ].map(([carry, lateral]) => makeTestShot({ flight: makeTestFlight(carry, lateral) }));

    const [group] = buildDispersion(shots);

    expect(group.ellipse).not.toBeNull();
    expect(group.ellipse!.cx).toBe(150);
    expect(group.insideFraction).toBeGreaterThan(0);
    expect(group.insideFraction).toBeLessThan(1);
  });
});

describe('excludeMishits', () => {
  it('drops a carry more than 2 SD from the mean', () => {
    const { kept, excluded } = excludeMishits([150, 151, 149, 150, 152, 148, 150, 151, 149, 90]);

    expect(excluded).toBe(1);
    expect(kept).not.toContain(90);
  });

  it('keeps everything when the spread is even or too small to judge', () => {
    expect(excludeMishits([140, 150, 160])).toEqual({ kept: [140, 150, 160], excluded: 0 });
    expect(excludeMishits([150, 150, 150])).toEqual({ kept: [150, 150, 150], excluded: 0 });
    expect(excludeMishits([150])).toEqual({ kept: [150], excluded: 0 });
  });
});

describe('buildGapping', () => {
  it('sorts clubs by average carry and reports the gap to the next club', () => {
    const shots = [
      ...makeTestSession('7-iron', [150, 154], {}, 0),
      ...makeTestSession('driver', [240, 250, 260], {}, 10),
      ...makeTestSession('pw', [120], {}, 20),
    ];

    const rows = buildGapping(shots);

    expect(rows.map((row) => row.club)).toEqual(['driver', '7-iron', 'pw']);
    expect(rows[0]).toMatchObject({ average: 250, stdDev: 10, min: 240, max: 260, shots: 3, gapToNext: 98 });
    expect(rows[1]).toMatchObject({ average: 152, gapToNext: 32 });
    expect(rows[2]).toMatchObject({ average: 120, stdDev: null, gapToNext: null });
  });

  it('uses only each club’s last ten shots', () => {
    const shots = makeTestSession('7-iron', [100, 100, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150]);

    const [row] = buildGapping(shots);

    expect(row.shots).toBe(10);
    expect(row.average).toBe(150);
    expect(row.min).toBe(150);
  });

  it('recomputes averages without mishits only when asked', () => {
    const shots = makeTestSession('7-iron', [150, 151, 149, 150, 152, 148, 150, 151, 149, 90]);

    const withMishits = buildGapping(shots)[0];
    const withoutMishits = buildGapping(shots, { excludeMishits: true })[0];

    expect(withMishits.min).toBe(90);
    expect(withMishits.excluded).toBe(0);
    expect(withoutMishits).toMatchObject({ shots: 9, excluded: 1, min: 148, max: 152, average: 150 });
  });

  it('leaves swing-speed readings out', () => {
    expect(buildGapping([makeTestShot({ mode: 'swing-speed', club: 'Swing Speed' })])).toEqual([]);
  });
});
