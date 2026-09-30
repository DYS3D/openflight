import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n';
import type { CameraCaptureSettings } from '../../stores/useCameraStore';
import { CameraFeed } from '../CameraFeed';

interface CameraPanelProps {
  captureSettings: CameraCaptureSettings;
  captureSettingsError: string | null;
  onUpdateCaptureSettings: (settings: Partial<CameraCaptureSettings>) => void;
  /** Initial ball-zone state; the panel owns it afterwards. Off by default, never persisted. */
  showBallZone?: boolean;
}

/** High-speed capture setup and preview panel. */
export function CameraPanel({
  captureSettings,
  captureSettingsError,
  onUpdateCaptureSettings,
  showBallZone: initialShowBallZone = false,
}: CameraPanelProps) {
  const { t } = useI18n();
  const [showBallZone, setShowBallZone] = useState(initialShowBallZone);

  return (
    <div className="panel camera-panel camera-panel--capture">
      <div className="panel__body camera-panel__body camera-panel__body--capture">
        <div className="camera-panel__toolbar">
          <button
            type="button"
            role="switch"
            aria-checked={showBallZone}
            className="menu-sheet__switch camera-panel__switch"
            onClick={() => setShowBallZone((on) => !on)}
          >
            <span className="menu-sheet__switch-label">{t('camera.showBallZone')}</span>
            <span className="menu-sheet__switch-track" aria-hidden="true">
              <span className="menu-sheet__switch-thumb" />
            </span>
          </button>
        </div>
        <CameraFeed
          captureSettings={captureSettings}
          captureSettingsError={captureSettingsError}
          onUpdateCaptureSettings={onUpdateCaptureSettings}
          showBallZone={showBallZone}
        />
      </div>
    </div>
  );
}
