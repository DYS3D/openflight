import type { ReactNode } from 'react';
import type { SpinQuality } from '../../types/shot';
import { t, type MessageKey } from '../../i18n';
import type { ConsistencyBand } from '../panel/consistency';
import type { RangeStatus } from '../panel/launchWindows';
import './MetricCard.css';

export interface MetricCardProps {
  value: string | number;
  unit?: string;
  label: string;
  subtext?: string;
  detail?: string;
  variant?: 'default' | 'emphasis';
  size?: 'standard' | 'hero';
  /**
   * Instrument-panel layouts (design doc 6a / 7a) lead with the label and put
   * the number underneath; the original card leads with the number.
   */
  labelPosition?: 'below' | 'above';
  /** Modeled value. Shown as an ≈ mark; measured values have no mark. */
  estimated?: boolean;
  confidence?: SpinQuality | null;
  /** Override confidence copy while preserving its dot level. */
  confidenceLabel?: string;
  /** Renders the card as a button. Used by 6a's tap-a-tile-to-promote-it grid. */
  onClick?: () => void;
  /** Marks an interactive card as the currently promoted one (`aria-pressed`). */
  selected?: boolean;
  consistency?: ConsistencyBand;
  /** Copper Live: where the value sits against the club's window. */
  range?: RangeStatus | null;
  /** Copper Live: extra content beside the number, e.g. the carry trend. */
  aside?: ReactNode;
  /** Tiles sharing a group get one fitted number size (Copper hero tiles). */
  fitGroup?: string;
}

const RANGE_LABELS: Record<RangeStatus, MessageKey> = {
  low: 'metric.rangeLow',
  in: 'metric.rangeIn',
  high: 'metric.rangeHigh',
};

const CONSISTENCY_LABELS: Record<ConsistencyBand, MessageKey> = {
  good: 'metric.consistencyGood',
  fair: 'metric.consistencyFair',
  poor: 'metric.consistencyPoor',
};

export function EstimatedMark() {
  return (
    <span className="metric-card__estimated" title={t('metric.estimated')}>
      <svg viewBox="0 0 16 10" aria-hidden="true">
        <path d="M1 3.1c2.2-1.6 4.4 1.6 6.6 0s4.4-1.6 6.6 0" />
        <path d="M1 7.4c2.2-1.6 4.4 1.6 6.6 0s4.4-1.6 6.6 0" />
      </svg>
      <span className="metric-card__estimated-label">{t('metric.estimated')}</span>
    </span>
  );
}

export function MetricCard({
  value,
  unit,
  label,
  subtext,
  detail,
  variant = 'default',
  size = 'standard',
  labelPosition = 'below',
  confidence,
  confidenceLabel,
  onClick,
  selected,
  estimated,
  consistency,
  range,
  aside,
  fitGroup,
}: MetricCardProps) {
  const classes = ['metric-card', `metric-card--${variant}`, `metric-card--label-${labelPosition}`];
  if (size === 'hero') {
    classes.push('metric-card--hero');
  }
  if (onClick) {
    classes.push('metric-card--interactive');
  }
  if (selected) {
    classes.push('metric-card--selected');
  }
  if (consistency) {
    classes.push(`metric-card--consistency-${consistency}`);
  }
  if (aside) {
    classes.push('metric-card--with-aside');
  }

  const label_ = (
    <span className="metric-card__label">
      {label}
      {estimated ? <EstimatedMark /> : null}
      {consistency ? (
        <span className="metric-card__consistency-label">{t(CONSISTENCY_LABELS[consistency])}</span>
      ) : null}
    </span>
  );
  const meta = (
    <>
      {subtext ? <span className="metric-card__subtext metric-card__confidence-label">{subtext}</span> : null}
      {detail ? <span className="metric-card__subtext metric-card__confidence-label">{detail}</span> : null}
      {confidence ? (
        <div className={`metric-card__confidence metric-card__confidence--${confidence}`}>
          {confidence !== 'experimental' ? (
            <span className="metric-card__confidence-dots">
              <span className="dot filled" />
              <span className={`dot ${confidence === 'medium' || confidence === 'high' ? 'filled' : ''}`} />
              <span className={`dot ${confidence === 'high' ? 'filled' : ''}`} />
            </span>
          ) : null}
          <span className="metric-card__confidence-label">{confidenceLabel ?? confidence}</span>
        </div>
      ) : null}
      {range ? (
        <span className={`metric-card__range metric-card__range--${range}`}>
          <span className="metric-card__range-bar" aria-hidden="true" />
          {t(RANGE_LABELS[range])}
        </span>
      ) : null}
    </>
  );
  const body = (
    <>
      {labelPosition === 'above' ? label_ : null}
      <div className="metric-card__value-row">
        <span className="metric-card__value">{value}</span>
        {unit ? <span className="metric-card__unit">{unit}</span> : null}
      </div>
      {labelPosition === 'below' ? label_ : null}
      {labelPosition === 'above' ? <div className="metric-card__meta">{meta}</div> : meta}
      {aside ? <div className="metric-card__aside">{aside}</div> : null}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        className={classes.join(' ')}
        aria-pressed={selected ?? false}
        data-fit-group={fitGroup}
        onClick={onClick}
      >
        {body}
      </button>
    );
  }

  return (
    <div className={classes.join(' ')} data-fit-group={fitGroup}>
      {body}
    </div>
  );
}
