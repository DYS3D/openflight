import { useEffect, useMemo } from 'react';
import type { Shot } from '../../types/shot';
import { filterShotsByProfile } from '../../types/shot';
import { ballShots, carryYards } from '../../utils/shotAnalysis';
import {
  COMBINE_REPEATS,
  COMBINE_TARGETS,
  convertPracticeConfig,
  defaultPracticeConfig,
  LADDER_STEP,
  MAX_POINTS,
  playRound,
  stepSetting,
  type PracticeConfig,
  type PracticeMode,
  type PracticeRound,
  type PracticeSetting,
} from '../../utils/practice';
import { convertDistanceFromYards, getDistanceUnit, type UnitSystem } from '../../utils/units';
import { useUnitPreference } from '../../state/useUnitPreference';
import { startPracticeSession, usePracticeStore } from '../../stores/usePracticeStore';
import { useI18n } from '../../i18n/useI18n';
import { SegmentedControl } from '../ui/SegmentedControl';
import { PanelAction } from './PanelAction';
import { PanelHeader } from './PanelHeader';
import './PracticePanel.css';

function newSeed(): number {
  return Math.floor(Math.random() * 2 ** 31);
}

interface PracticePanelProps {
  shots: Shot[];
  profileId: string;
  profileName: string;
}

/**
 * Client-side only: scores the active profile's shots hit since their round started.
 * Each golfer keeps their own round while the view is closed; a unit change restarts it.
 */
export function PracticePanel({ shots, profileId, profileName }: PracticePanelProps) {
  const { unitSystem } = useUnitPreference();
  const stored = usePracticeStore((state) => state.sessions[profileId]);
  const lastConfig = usePracticeStore((state) => state.lastConfig);
  const setSession = usePracticeStore((state) => state.setSession);

  const replacement = useMemo(() => {
    if (stored && stored.config.unitSystem === unitSystem) {
      return null;
    }
    const previous = stored?.config ?? lastConfig;
    const config = previous
      ? { ...convertPracticeConfig(previous, unitSystem), seed: newSeed() }
      : defaultPracticeConfig(unitSystem, newSeed());
    return startPracticeSession(config, shots);
  }, [stored, lastConfig, unitSystem, shots]);

  useEffect(() => {
    if (replacement) {
      setSession(profileId, replacement);
    }
  }, [replacement, profileId, setSession]);

  const session = replacement ?? stored!;

  const carries = useMemo(
    () =>
      ballShots(filterShotsByProfile(shots, profileId))
        .filter((shot) => !session.baseline.has(shot.timestamp))
        .map(carryYards),
    [shots, profileId, session.baseline]
  );
  const round = playRound(session.config, carries);
  const restart = (config: PracticeConfig) =>
    setSession(profileId, startPracticeSession({ ...config, seed: newSeed() }, shots));

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
  // The combine strip shows one pass (every distance once) at a time.
  const stripSize = config.mode === 'combine' ? COMBINE_TARGETS : round.shots;
  const pass = Math.min(Math.floor(round.attempts.length / stripSize), round.shots / stripSize - 1);
  const stripStart = pass * stripSize;
  const totals = [
    stat('total', t('practice.total'), `${round.totalPoints}`),
    config.mode === 'combine'
      ? stat('pass', t('practice.pass'), `${pass + 1} / ${COMBINE_REPEATS}`)
      : stat('shots', t('practice.shots'), `${round.attempts.length} / ${round.shots}`),
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
                { id: 'combine', label: t('practice.modeCombine') },
              ]}
              onChange={(mode) => onChangeConfig({ ...config, mode })}
            />
          </div>
          {config.mode !== 'ladder' ? (
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
              : config.mode === 'combine'
                ? t('practice.combineHint', { targets: COMBINE_TARGETS, repeats: COMBINE_REPEATS })
                : t('practice.ladderHint', { step: LADDER_STEP, unit })}
          </span>
        </div>

        {round.complete ? (
          <div className="practice__play practice__play--summary" aria-live="polite">
            <div className="practice__card">
              <span className="practice__label">
                {config.mode === 'combine' ? t('practice.combineScore') : t('practice.roundComplete')}
              </span>
              <span className="practice__big">
                {config.mode === 'combine' ? round.score : round.totalPoints}
                <span className="practice__unit">/ {config.mode === 'combine' ? 100 : round.shots * MAX_POINTS}</span>
              </span>
              <PanelAction onClick={onNewRound}>{t('practice.playAgain')}</PanelAction>
            </div>
            <div className="practice__totals">
              {stat('average', t('metric.average'), (round.averagePoints ?? 0).toFixed(1))}
              {stat('hits', t('practice.hits'), `${round.hits} / ${round.shots}`)}
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

        <ol
          className={`practice__strip${config.mode === 'combine' ? ' practice__strip--combine' : ''}`}
          aria-label={t('practice.historyAria')}
        >
          {Array.from({ length: stripSize }, (_, offset) => {
            const index = stripStart + offset;
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
