import type { SeriesShape } from '../../utils/shotAnalysis';

interface SeriesMarkerProps {
  shape: SeriesShape;
  cx: number;
  cy: number;
  size: number;
  className?: string;
}

export function SeriesMarker({ shape, cx, cy, size, className }: SeriesMarkerProps) {
  switch (shape) {
    case 'square':
      return (
        <rect
          className={className}
          x={cx - size * 0.88}
          y={cy - size * 0.88}
          width={size * 1.76}
          height={size * 1.76}
        />
      );
    case 'triangle':
      return (
        <path
          className={className}
          d={`M${cx},${cy - size * 1.15}L${cx + size * 1.05},${cy + size * 0.8}L${cx - size * 1.05},${cy + size * 0.8}Z`}
        />
      );
    case 'diamond':
      return (
        <path
          className={className}
          d={`M${cx},${cy - size * 1.2}L${cx + size * 1.2},${cy}L${cx},${cy + size * 1.2}L${cx - size * 1.2},${cy}Z`}
        />
      );
    default:
      return <circle className={className} cx={cx} cy={cy} r={size} />;
  }
}
