"""scipy is imported only when a spin function actually needs it."""

import subprocess
import sys
import textwrap


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_importing_rolling_buffer_processor_does_not_import_scipy():
    result = _run(
        """
        import sys

        import openflight.rolling_buffer.processor
        import openflight.rolling_buffer.multitaper
        import openflight.spin_estimate

        loaded = sorted(name for name in sys.modules if name.split(".")[0] == "scipy")
        assert not loaded, loaded
        """
    )

    assert result.returncode == 0, result.stderr


def test_spin_detection_imports_scipy_on_demand():
    result = _run(
        """
        import sys

        import numpy as np

        from openflight.rolling_buffer import IQCapture, RollingBufferProcessor

        t = np.arange(4096) / 30000.0
        phase = 2 * np.pi * 7000.0 * t
        amplitude = 500.0 * (1.0 + 0.05 * np.sin(2 * np.pi * 150.0 * t))
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=(2048 + amplitude * np.cos(phase)).astype(int).tolist(),
            q_samples=(2048 + amplitude * np.sin(phase)).astype(int).tolist(),
        )
        processor = RollingBufferProcessor()
        processor.process_standard(capture)
        assert "scipy" not in sys.modules

        result = processor.detect_spin_multitaper(
            capture, ball_speed_mph=100.0, ball_timestamp_ms=5.0
        )
        assert result.method == "multitaper_ungated"
        assert "scipy.signal" in sys.modules
        """
    )

    assert result.returncode == 0, result.stderr
