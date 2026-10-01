import { createRoot } from 'react-dom/client';
import { StatsPanel, type StatsView } from '../../../src/components/panel/StatsPanel';
import { LevelPanel } from '../../../src/components/panel/LevelPanel';
import { PracticePanel } from '../../../src/components/panel/PracticePanel';
import { TvDisplay } from '../../../src/components/TvDisplay';
import { makeTestFlight, makeTestShot } from '../../../src/test/shotFixtures';
import { applyTheme } from '../../../src/theme/theme';
import type { Shot } from '../../../src/types/shot';
import '../../../src/components/panel/panel.css';

const params = new URLSearchParams(window.location.search);
const view = params.get('view') ?? 'dispersion';
applyTheme(params.get('theme') === 'light' ? 'light' : 'dark');

const BAG: ReadonlyArray<readonly [string, number]> = [
  ['driver', 250],
  ['3-wood', 230],
  ['5-wood', 215],
  ['3-hybrid', 200],
  ['4-iron', 188],
  ['5-iron', 177],
  ['6-iron', 166],
  ['7-iron', 155],
  ['8-iron', 144],
  ['9-iron', 133],
  ['pw', 122],
  ['gw', 108],
  ['sw', 94],
  ['lw', 80],
];
const SPREAD: ReadonlyArray<readonly [number, number]> = [
  [0, 0],
  [6, -4],
  [-5, 3],
  [3, 6],
  [-2, -7],
];

const allShots: Shot[] = BAG.flatMap(([club, carry], clubIndex) =>
  SPREAD.map(([long, lateral], shotIndex) =>
    makeTestShot({
      club,
      profile_id: 'james',
      estimated_carry_yards: carry + long,
      ball_speed_mph: 60 + carry / 2,
      timestamp: `2026-09-30T10:${String(clubIndex).padStart(2, '0')}:${String(shotIndex).padStart(2, '0')}Z`,
      flight: makeTestFlight(carry + long, lateral + clubIndex / 3, carry / 7),
    })
  )
);
/** `shots=N` keeps the first N shots (0, 1, 30…); the default is the whole bag. */
const shotCount = params.get('shots');
const shots = shotCount === null ? allShots : allShots.slice(0, Number(shotCount));

const fixture =
  view === 'tv' ? (
    <TvDisplay connected shots={shots} profileId="james" profileName="James" />
  ) : (
    <div className="panel-app">
      <main className="panel-app__main">
        {view === 'level' ? (
          <LevelPanel status={{ pitch_deg: 1.25, roll_deg: -0.4, level: false, threshold_deg: 1 }} />
        ) : view === 'practice' ? (
          <PracticePanel shots={shots} profileId="james" profileName="James" />
        ) : (
          <StatsPanel
            shots={shots}
            activeClub="7-iron"
            profileId="james"
            profileName="James"
            initialView={view as StatsView}
          />
        )}
      </main>
    </div>
  );

createRoot(document.getElementById('root')!).render(fixture);
