import { useShallow } from 'zustand/react/shallow';
import { useDebugStore } from '../stores/useDebugStore';
import { useSystemStore } from '../stores/useSystemStore';
import { socketService } from '../services/socketService';
import { useI18n } from '../i18n/useI18n';
import { DebugPanel } from './DebugPanel';
import { PanelAction, PanelHeader } from './panel';

export function DebugView() {
  const { t } = useI18n();
  const { debugMode, mockMode } = useSystemStore(
    useShallow((state) => ({ debugMode: state.debugMode, mockMode: state.mockMode }))
  );
  const { debugReadings, debugShotLogs, radarConfig, triggerDiagnostics, triggerStatus } = useDebugStore(
    useShallow((state) => ({
      debugReadings: state.debugReadings,
      debugShotLogs: state.debugShotLogs,
      radarConfig: state.radarConfig,
      triggerDiagnostics: state.triggerDiagnostics,
      triggerStatus: state.triggerStatus,
    }))
  );

  return (
    <div className="panel">
      <PanelHeader
        title={t('nav.debug')}
        subtitle={debugMode ? t('app.debugRecording') : t('app.debugIdle')}
        actions={
          <PanelAction variant="secondary" onClick={() => socketService.toggleDebug()}>
            {debugMode ? t('app.stopRecording') : t('app.record')}
          </PanelAction>
        }
      />
      <div className="panel__body panel-app__debug">
        <DebugPanel
          enabled={debugMode}
          readings={debugReadings}
          shotLogs={debugShotLogs}
          radarConfig={radarConfig}
          mockMode={mockMode}
          onToggle={() => socketService.toggleDebug()}
          onUpdateConfig={(config) => socketService.setRadarConfig(config)}
          triggerDiagnostics={triggerDiagnostics}
          triggerStatus={triggerStatus}
        />
      </div>
    </div>
  );
}
