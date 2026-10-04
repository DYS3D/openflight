import { useEffect } from 'react';
import type { Shot, SwingLength } from '../../types/shot';
import { ALL_CLUBS } from '../../data/clubs';
import { socketService } from '../../services/socketService';
import { usePracticeStore } from '../../stores/usePracticeStore';
import { buildWedgeMatrix, SWING_LENGTHS, WEDGES } from '../../utils/wedgeMatrix';
import { convertDistanceFromYards, getDistanceUnit, type UnitSystem } from '../../utils/units';
import { useI18n } from '../../i18n/useI18n';
import { SegmentedControl } from '../ui/SegmentedControl';

const SWING_LABELS: Record<SwingLength, string> = { full: 'Full', '3/4': '¾', '1/2': '½' };

function clubLabel(club: string): string {
  return ALL_CLUBS.find((entry) => entry.id === club)?.label ?? club.toUpperCase();
}

/**
 * Tags every shot hit while it is open with the chosen swing length, and shows the
 * median carry for each wedge at each length. Leaving it stops the tagging.
 */
export function WedgeMatrix({ shots, club, unitSystem }: { shots: Shot[]; club: string; unitSystem: UnitSystem }) {
  const swing = usePracticeStore((state) => state.swingLength);
  const setSwing = usePracticeStore((state) => state.setSwingLength);

  useEffect(() => {
    socketService.setSwingLength(swing);
    return () => socketService.setSwingLength(null);
  }, [swing]);

  return <WedgeMatrixBoard shots={shots} club={club} unitSystem={unitSystem} swing={swing} onSwing={setSwing} />;
}

interface WedgeMatrixBoardProps {
  shots: Shot[];
  club: string;
  unitSystem: UnitSystem;
  swing: SwingLength;
  onSwing: (swing: SwingLength) => void;
}

export function WedgeMatrixBoard({ shots, club, unitSystem, swing, onSwing }: WedgeMatrixBoardProps) {
  const { t } = useI18n();
  const unit = getDistanceUnit(unitSystem);
  const rows = buildWedgeMatrix(shots);
  const isWedge = (WEDGES as readonly string[]).includes(club);

  return (
    <div className="wedge-matrix">
      <div className="wedge-matrix__controls practice__mode">
        <SegmentedControl<SwingLength>
          ariaLabel={t('wedges.swing')}
          value={swing}
          options={SWING_LENGTHS.map((id) => ({ id, label: SWING_LABELS[id] }))}
          onChange={onSwing}
        />
        <span className="practice__hint">
          {isWedge ? t('wedges.hint', { club: clubLabel(club) }) : t('wedges.pickWedge')}
        </span>
      </div>
      <table className="wedge-matrix__table" aria-label={t('wedges.title')}>
        <thead>
          <tr>
            <th scope="col" />
            {SWING_LENGTHS.map((id) => (
              <th key={id} scope="col" className={id === swing ? 'wedge-matrix__col--active' : undefined}>
                {SWING_LABELS[id]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.club}>
              <th scope="row">{clubLabel(row.club)}</th>
              {SWING_LENGTHS.map((id) => {
                const cell = row.cells[id];
                const current = row.club === club && id === swing;
                return (
                  <td key={id} className={current ? 'wedge-matrix__cell--current' : undefined}>
                    {cell.median === null ? (
                      '—'
                    ) : (
                      <>
                        <span className="wedge-matrix__carry">
                          {convertDistanceFromYards(cell.median, unitSystem).toFixed(0)}
                          <span className="practice__unit">{unit}</span>
                        </span>
                        <span className="wedge-matrix__count">{t('wedges.shots', { count: cell.shots })}</span>
                      </>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
