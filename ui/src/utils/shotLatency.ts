import type { ShotLatency } from '../types/shot';
import { t, type MessageKey } from '../i18n';

const LATENCY_STAGE_LABELS: Record<string, MessageKey> = {
  trigger_to_ui: 'debug.latencyTriggerToUi',
  trigger_to_final: 'debug.latencyTriggerToFinal',
};

/** Stage rows in server order, skipping stages without a number. */
export function latencyRows(
  latency: ShotLatency | null | undefined
): Array<{ key: string; label: string; ms: number }> {
  if (!latency) return [];
  const rows: Array<{ key: string; label: string; ms: number }> = [];
  for (const [key, ms] of Object.entries(latency)) {
    if (typeof ms !== 'number' || !Number.isFinite(ms)) continue;
    const labelKey = LATENCY_STAGE_LABELS[key];
    rows.push({ key, label: labelKey ? t(labelKey) : key.replaceAll('_', ' '), ms });
  }
  return rows;
}
