import { useMemo, useState } from 'react';
import type { Shot } from '../../types/shot';
import { filterShotsByProfile } from '../../types/shot';
import { ballShots, carryYards } from '../../utils/shotAnalysis';
import {
  convertPracticeConfig,
  defaultPracticeConfig,
  LADDER_STEP,
  MAX_POINTS,
  playRound,
  ROUND_SHOTS,
  stepSetting,
  type PracticeConfig,
  type PracticeMode,
  type PracticeRound,
  type PracticeSetting,
} from '../../utils/practice';
import { convertDistanceFromYards, getDistanceUnit, type UnitSystem } from '../../utils/units';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useI18n } from '../../i18n/useI18n';
import { SegmentedControl } from '../ui/SegmentedControl';
import { PanelAction } from './PanelAction';
import { PanelHeader } from './PanelHeader';
import './PracticePanel.css';

interface PracticeSession {
  config: PracticeConfig;
  baseline: ReadonlySet<string>;
  profileId: string;
}

function newSeed(): number {
  return Math.floor(Math.random() * 2 ** 31);
}

function startSession(config: PracticeConfig, shots: readonly Shot[], profileId: string): PracticeSession {
  return { config, baseline: new Set(shots.map((shot) => shot.timestamp)), profileId };
}

interface PracticePanelProps {
  shots: Shot[];
  profileId: string;
  profileName: string;
}

/** Client-side only: scores the active profile's shots that arrive while the view is open. */
export function PracticePanel({ shots, profileId, profileName }: PracticePanelProps) {
  const { unitSystem } = useUnitPreference();
  const [session, setSession] = useState(() =>
    startSession(defaultPracticeConfig(unitSystem, newSeed()), shots, profileId)
  );

  // Another golfer or unit starts a fresh round (adjusted during render, not in an effect).
  if (session.profileId !== profileId || session.config.unitSystem !== unitSystem) {
    const config = { ...convertPracticeConfig(session.config, unitSystem), seed: session.config.seed + 1 };
    setSession(startSession(config, shots, profileId));
  }

  const carries = useMemo(
    () =>
      ballShots(filterShotsByProfile(shots, profileId))
        .filter((shot) => !session.baseline.has(shot.timestamp))
        .map(carryYards),
    [shots, profileId, session.baseline]
  );
  const round = playRound(session.config, carries);
  const restart = (config: PracticeConfig) =>
    setSession(startSession({ ...config, seed: newSeed() }, shots, profileId));

  return (
    <PracticeBoard
      config={session.config}
      round={round}
      profileName={profileName}
      unitSystem={unitSystem}
      onChangeConfig={restart}
      onNewRound={() => restart(session.config)}
    />
  );
}

interface PracticeBoardProps {
  config: PracticeConfig;
  round: PracticeRound;
  profileName: string;
  unitSystem: UnitSystem;
  onChangeConfig: (config: PracticeConfig) => void;
  onNewRound: () => void;
}

export function PracticeBoard({
  config,
  round,
  profileName,
  unitSystem,
  onChangeConfig,
  onNewRound,
}: PracticeBoardProps) {
  const { t } = useI18n();
  const unit = getDistanceUnit(unitSystem);
  const toUnit = (yards: number) => convertDistanceFromYards(yards, unitSystem);
  const last = round.attempts[round.attempts.length - 1];

  const stepper = (setting: PracticeSetting, label: string) => {
    const lower = stepSetting(config, setting, -1);
    const raise = stepSetting(config, setting, 1);
    return (
      <div className="practice-stepper" role="group" aria-label={label}>
        <span className="practice-stepper__label">{label}</span>
        <button
          type="button"
          className="practice-stepper__button"
          aria-label={t('practice.lower', { name: label })}
          disabled={lower[setting] === config[setting]}
          onClick={() => onChangeConfig(lower)}
        >
          −
        </button>
        <span className="practice-stepper__value">
          {config[setting]}
          <span className="practice__unit">{unit}</span>
        </span>
        <button
          type="button"
          className="practice-stepper__button"
          aria-label={t('practice.raise', { name: label })}
          disabled={raise[setting] === config[setting]}
          onClick={() => onChangeConfig(raise)}
        >
          +
        </button>
      </div>
    );
  };

  const stat = (id: string, label: string, value: string) => (
    <div key={id} className="practice__stat">
      <span className="practice__label">{label}</span>
      <span className="practice__stat-value">{value}</span>
    </div>
  );
  const totals = [
    stat('total', t('practice.total'), `${round.totalPoints}`),
    stat('shots', t('practice.shots'), `${round.attempts.length} / ${ROUND_SHOTS}`),
    stat('average', t('metric.average'), round.averagePoints === null ? '—' : round.averagePoints.toFixed(1)),
  ];

  return (
    <div className="panel practice-panel">
      <PanelHeader
        title={t('practice.title')}
        subtitle={profileName}
        actions={<PanelAction onClick={onNewRound}>{t('practice.newRound')}</PanelAction>}
      />
      <div className="panel__body practice">
        <div className="practice__controls">
          <div className="practice__mode">
            <SegmentedControl<PracticeMode>
              ariaLabel={t('practice.mode')}
              value={config.mode}
              options={[
                { id: 'target', label: t('practice.modeTarget') },
                { id: 'ladder', label: t('practice.modeLadder') },
              ]}
              onChange={(mode) => onChangeConfig({ ...config, mode })}
            />
          </div>
          {config.mode === 'target' ? (
            <>
              {stepper('minTarget', t('practice.shortest'))}
              {stepper('maxTarget', t('practice.longest'))}
            </>
          ) : (
            stepper('ladderStart', t('practice.start'))
          )}
          <span className="practice__hint">
            {config.mode === 'target'
              ? t('practice.targetHint')
              : t('practice.ladderHint', { step: LADDER_STEP, unit })}
          </span>
        </div>

        {round.complete ? (
          <div className="practice__play practice__play--summary" aria-live="polite">
            <div className="practice__card">
              <span className="practice__label">{t('practice.roundComplete')}</span>
              <span className="practice__big">
                {round.totalPoints}
                <span className="practice__unit">/ {ROUND_SHOTS * MAX_POINTS}</span>
              </span>
              <PanelAction onClick={onNewRound}>{t('practice.playAgain')}</PanelAction>
            </div>
            <div className="practice__totals">
              {stat('average', t('metric.average'), (round.averagePoints ?? 0).toFixed(1))}
              {stat('hits', t('practice.hits'), `${round.hits} / ${ROUND_SHOTS}`)}
              {stat(
                'best',
                t('practice.closest'),
                `${Math.min(...round.attempts.map((attempt) => attempt.errorPercent)).toFixed(1)}%`
              )}
            </div>
          </div>
        ) : (
          <div className="practice__play" aria-live="polite">
            <div className="practice__card">
              <span className="practice__label">{t('practice.target')}</span>
              <span className="practice__big">
                {round.nextTarget}
                <span className="practice__unit">{unit}</span>
              </span>
            </div>
            <div className="practice__card">
              <span className="practice__label">{t('practice.lastShot')}</span>
              {last ? (
                <>
                  <span className="practice__medium">
                    {toUnit(last.carryYards).toFixed(0)}
                    <span className="practice__unit">{unit}</span>
                  </span>
                  <span className="practice__detail">
                    {`${last.carryYards >= last.targetYards ? '+' : '−'}${Math.abs(toUnit(last.carryYards - last.targetYards)).toFixed(0)} ${unit} · ${last.errorPercent.toFixed(1)}%`}
                  </span>
                  <span className="practice__points">{t('practice.points', { points: last.points })}</span>
                </>
              ) : (
                <span className="practice__detail">{t('practice.waiting')}</span>
              )}
            </div>
            <div className="practice__totals">{totals}</div>
          </div>
        )}

        <ol className="practice__strip" aria-label={t('practice.historyAria')}>
          {Array.from({ length: ROUND_SHOTS }, (_, index) => {
            const attempt = round.attempts[index];
            const current = !round.complete && index === round.attempts.length;
            return (
              <li
                key={index}
                className={`practice__cell${current ? ' practice__cell--current' : ''}${attempt?.hit ? ' practice__cell--hit' : ''}`}
              >
                {attempt ? attempt.points : ''}
              </li>
            );
          })}
        </ol>
        <span className="practice__scoring">{t('practice.scoring')}</span>
      </div>
    </div>
  );
}
