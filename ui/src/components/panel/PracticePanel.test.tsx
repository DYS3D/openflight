import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { makeTestSession } from '../../test/shotFixtures';
import { defaultPracticeConfig, playRound, randomTarget, type PracticeConfig } from '../../utils/practice';
import { PracticeBoard, PracticePanel } from './PracticePanel';

const css = readFileSync(fileURLToPath(new URL('./PracticePanel.css', import.meta.url)), 'utf8');

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

const config: PracticeConfig = defaultPracticeConfig('imperial', 11);

function renderBoard(carries: number[], overrides: Partial<PracticeConfig> = {}) {
  const roundConfig = { ...config, ...overrides };
  return text(
    renderToString(
      <PracticeBoard
        config={roundConfig}
        round={playRound(roundConfig, carries)}
        profileName="James"
        unitSystem={roundConfig.unitSystem}
        onChangeConfig={() => {}}
        onNewRound={() => {}}
      />
    )
  );
}

describe('PracticePanel', () => {
  it('does not score shots hit before the view opened', () => {
    const html = text(
      renderToString(
        <PracticePanel shots={makeTestSession('7-iron', [150, 151, 152])} profileId="james" profileName="James" />
      )
    );

    expect(html).toContain('panel-header__title">Practice<');
    expect(html).toContain('Hit a shot to score it');
    expect(html).toContain('>0 / 10<');
    expect(html).toContain('aria-pressed="true">Target<');
  });
});

describe('PracticeBoard', () => {
  it('shows the next target, the 50–150 default range and an empty 10-shot strip', () => {
    const html = renderBoard([]);

    expect(html).toContain(`practice__big">${randomTarget(11, 0, 50, 150)}<span class="practice__unit">yds</span>`);
    expect(html).toContain('practice-stepper__value">50<');
    expect(html).toContain('practice-stepper__value">150<');
    expect(html.match(/class="practice__cell/g)).toHaveLength(10);
    expect(html).toContain('practice__cell practice__cell--current');
  });

  it('reports the last shot, running total, shots taken and average', () => {
    const first = randomTarget(11, 0, 50, 150);
    const second = randomTarget(11, 1, 50, 150);

    const html = renderBoard([first, second * 1.08]);

    expect(html).toContain('Last shot');
    expect(html).toContain('3 pts');
    expect(html).toContain('8.0%');
    expect(html).toContain('practice__stat-value">8<');
    expect(html).toContain('>2 / 10<');
    expect(html).toContain('>4.0<');
    expect(html).toContain('practice__cell practice__cell--hit">5<');
  });

  it('shows a summary once ten shots are in', () => {
    const carries = Array.from({ length: 10 }, (_, index) => randomTarget(11, index, 50, 150));

    const html = renderBoard(carries);

    expect(html).toContain('Round complete');
    expect(html).toContain('practice__big">50<span class="practice__unit">/ 50</span>');
    expect(html).toContain('>10 / 10<');
    expect(html).toContain('Play again');
    expect(html).not.toContain('practice__cell--current');
  });

  it('switches the controls to a start distance for the ladder', () => {
    const html = renderBoard([], { mode: 'ladder', ladderStart: 80 });

    expect(html).toContain('aria-pressed="true">Ladder<');
    expect(html).toContain('aria-label="Start"');
    expect(html).toContain('practice__big">80<');
    expect(html).toContain('Up 10 yds after each hit (within 10%)');
    expect(html).not.toContain('aria-label="Min"');
  });

  it('plays the combine: min/max controls, 27 cells and a score out of 100 at the end', () => {
    const live = renderBoard([], { mode: 'combine', minTarget: 60, maxTarget: 180 });
    expect(live).toContain('aria-pressed="true">Combine<');
    expect(live).toContain('aria-label="Min"');
    expect(live).toContain('9 distances from Min to Max, 3 shots each, random order');
    expect(live.match(/class="practice__cell/g)).toHaveLength(9);
    expect(live).toContain('1 / 3');

    const secondPass = renderBoard(
      Array.from({ length: 10 }, () => 100),
      { mode: 'combine', minTarget: 60, maxTarget: 180 }
    );
    expect(secondPass).toContain('2 / 3');

    const done = renderBoard(
      Array.from({ length: 27 }, () => 1),
      { mode: 'combine', minTarget: 60, maxTarget: 180 }
    );
    expect(done).toContain('Combine score');
    expect(done).toContain('practice__big">0<span class="practice__unit">/ 100</span>');
  });

  it('follows the metric unit preference', () => {
    const html = renderBoard([], { unitSystem: 'metric', mode: 'ladder', ladderStart: 60 });

    expect(html).toContain('practice__big">60<span class="practice__unit">m</span>');
    expect(html).toContain('Up 10 m after each hit');
  });

  it('disables a stepper at the edge of its range', () => {
    const html = renderBoard([], { minTarget: 10, maxTarget: 20 });

    expect(html).toMatch(/aria-label="Lower Min" disabled=""/);
    expect(html).toMatch(/aria-label="Raise Min" disabled=""/);
    expect(html).not.toMatch(/aria-label="Raise Max" disabled=""/);
  });

  it('keeps stepper buttons finger-sized', () => {
    expect(css).toMatch(/\.practice-stepper__button \{[^}]*width: 44px;[^}]*height: 44px;/);
  });
});
