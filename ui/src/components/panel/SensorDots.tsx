import type { SensorDot, SensorLevel } from './sensorStatus';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';

const LEVEL_LABELS: Record<SensorLevel, MessageKey> = {
  ok: 'header.connected',
  warn: 'header.reconnecting',
  off: 'header.disconnected',
};

/** Copper header: compact OPS · TI · CAM · GSPRO link dots. */
export function SensorDots({ dots }: { dots: SensorDot[] }) {
  const { t } = useI18n();
  return (
    <ul className="sensor-dots" aria-label={t('header.sensors')}>
      {dots.map((dot) => (
        <li
          key={dot.id}
          className={`sensor-dots__item sensor-dots__item--${dot.level}`}
          aria-label={`${dot.label}: ${t(LEVEL_LABELS[dot.level])}`}
        >
          <span aria-hidden="true">{dot.label}</span>
          <span className="sensor-dots__dot" aria-hidden="true" />
        </li>
      ))}
    </ul>
  );
}
