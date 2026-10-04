import { useEffect } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { socketService } from '../../services/socketService';
import { useBannerStore } from '../../stores/useBannerStore';
import type { LevelStatus } from '../../types/socket';
import { bubblePosition, bubbleRangeDeg, formatLevelDegrees, levelHint } from '../../utils/bubbleLevel';
import { PanelHeader } from './PanelHeader';

interface LevelPanelProps {
  /** Omit to read the banner store; pass it in tests. */
  status?: LevelStatus | null;
}

const VIAL_RADIUS = 90;

function levelHintText(hint: ReturnType<typeof levelHint>, t: ReturnType<typeof useI18n>['t']): string {
  return hint ? t(hint) : t('level.instructions');
}
const BUBBLE_RADIUS = 14;

function BubbleLevel({ status }: { status: LevelStatus }) {
  const { t } = useI18n();
  const { x, y, level } = bubblePosition(status.pitch_deg, status.roll_deg, status.threshold_deg);
  const usable = VIAL_RADIUS - BUBBLE_RADIUS;
  const thresholdRadius = (status.threshold_deg / bubbleRangeDeg(status.threshold_deg)) * usable + BUBBLE_RADIUS;
  const cx = 100 + (x / 100) * usable;
  const cy = 100 + (y / 100) * usable;

  return (
    <svg
      className={`level-vial ${level ? 'level-vial--level' : 'level-vial--off'}`}
      viewBox="0 0 200 200"
      role="img"
      aria-label={t('level.bubbleAria', {
        pitch: formatLevelDegrees(status.pitch_deg),
        roll: formatLevelDegrees(status.roll_deg),
      })}
    >
      <circle className="level-vial__rim" cx="100" cy="100" r={VIAL_RADIUS} />
      <circle className="level-vial__target" cx="100" cy="100" r={thresholdRadius} />
      <line className="level-vial__axis" x1="10" y1="100" x2="190" y2="100" />
      <line className="level-vial__axis" x1="100" y1="10" x2="100" y2="190" />
      <circle className="level-vial__bubble" cx={cx} cy={cy} r={BUBBLE_RADIUS} />
    </svg>
  );
}

/**
 * Bubble level fed by `level_status` (server flags `--inclinometer
 * --level-warning-deg`). Opens from the menu's System block; no footer tab.
 */
export function LevelPanel({ status: statusProp }: LevelPanelProps) {
  const { t } = useI18n();
  const storeStatus = useBannerStore((state) => state.levelStatus);
  const status = statusProp === undefined ? storeStatus : statusProp;

  // Live readings only flow while this screen is open.
  useEffect(() => {
    socketService.watchLevel(true);
    return () => socketService.watchLevel(false);
  }, []);
  const level = status?.level ?? false;
  const subtitle = status ? (level ? t('level.ok') : t('level.notLevel')) : undefined;
  const header = <PanelHeader title={t('level.title')} subtitle={subtitle} />;

  if (!status) {
    return (
      <div className="panel level-panel">
        {header}
        <div className="panel__body panel__body--empty">
          <span className="panel__empty-title">{t('level.empty')}</span>
          <span className="panel__empty-detail">{t('level.emptyDetail')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="panel level-panel">
      {header}
      <div className={`panel__body level-panel__body ${level ? 'level-panel__body--level' : 'level-panel__body--off'}`}>
        <BubbleLevel status={status} />
        <div className="level-panel__readouts">
          <div className="level-panel__readout">
            <span className="level-panel__readout-label">{t('level.pitch')}</span>
            <span className="level-panel__readout-value">
              {formatLevelDegrees(status.pitch_deg)}
              <span className="level-panel__readout-unit">°</span>
            </span>
          </div>
          <div className="level-panel__readout">
            <span className="level-panel__readout-label">{t('level.roll')}</span>
            <span className="level-panel__readout-value">
              {formatLevelDegrees(status.roll_deg)}
              <span className="level-panel__readout-unit">°</span>
            </span>
          </div>
          <span className="level-panel__state">{level ? t('level.ok') : t('level.notLevel')}</span>
          <span className="level-panel__threshold">
            {t('level.threshold', { deg: formatLevelDegrees(status.threshold_deg) })}
          </span>
          {level ? (
            <p className="level-panel__instructions">{t('level.instructions')}</p>
          ) : (
            <p className="level-panel__hint">{levelHintText(levelHint(status), t)}</p>
          )}
          <p className="level-panel__frame">{t('level.feetFrame')}</p>
        </div>
      </div>
    </div>
  );
}
