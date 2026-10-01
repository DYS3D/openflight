"""OPS243-A rolling-buffer radar profiles (``--radar-profile``).

The rolling buffer always holds ``BUFFER_SAMPLES`` I/Q pairs split into
``BUFFER_SEGMENTS`` segments of ``SEGMENT_SAMPLES``; ``S#n`` sets how many
segments are kept from before the HOST_INT trigger. The sample rate therefore
sets both the physical span of the buffer and how long the radar keeps
sampling after impact before it can dump. A capture is the same fixed-size
dump whatever the rate, so the serial link is not a constraint here.
"""

from dataclasses import dataclass

RADAR_PROFILES = ("standard", "low-latency")
DEFAULT_RADAR_PROFILE = "standard"

BUFFER_SAMPLES = 4096
SEGMENT_SAMPLES = 128
BUFFER_SEGMENTS = BUFFER_SAMPLES // SEGMENT_SAMPLES
LOW_LATENCY_SAMPLE_RATE_KSPS = 50
# S#20 at 50 ksps = 51.2 ms pre / 30.7 ms post: enough post-impact ball
# signal for the 20 ms spin minimum. A 68 ms pre-trigger would leave 12.8 ms.
LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS = 20
# 30 ksps is the rate every bin-count constant in the processor was tuned at.
REFERENCE_SAMPLE_RATE_KSPS = 30


@dataclass(frozen=True)
class RadarProfileSettings:
    """Resolved OPS243 rolling-buffer settings for one profile."""

    profile: str
    sample_rate_ksps: int
    pre_trigger_segments: int
    # Scale the processor's 30 ksps bin-count constants so the DC mask and
    # peak separation keep their mph meaning at the profile's rate.
    scale_speed_band: bool

    @property
    def segment_ms(self) -> float:
        return SEGMENT_SAMPLES / self.sample_rate_ksps

    @property
    def pre_trigger_ms(self) -> float:
        return self.pre_trigger_segments * self.segment_ms

    @property
    def post_trigger_ms(self) -> float:
        return (BUFFER_SEGMENTS - self.pre_trigger_segments) * self.segment_ms

    @property
    def buffer_ms(self) -> float:
        return BUFFER_SEGMENTS * self.segment_ms


def resolve_radar_profile(
    profile: str,
    *,
    sample_rate_ksps: int,
    pre_trigger_segments: int,
) -> RadarProfileSettings:
    """Turn ``--radar-profile`` plus the explicit radar flags into settings.

    ``standard`` passes the flags through untouched. ``low-latency`` samples
    at 50 ksps and re-splits the buffer so the pre-trigger span keeps the
    physical duration the standard profile would have had, capped at
    ``LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS`` so the shorter buffer still
    keeps ~31 ms after the trigger.
    """
    if profile not in RADAR_PROFILES:
        raise ValueError(f"radar profile must be one of {RADAR_PROFILES}, got {profile!r}")
    if profile == "standard":
        return RadarProfileSettings(
            profile=profile,
            sample_rate_ksps=sample_rate_ksps,
            pre_trigger_segments=pre_trigger_segments,
            scale_speed_band=False,
        )

    standard_pre_trigger_ms = pre_trigger_segments * SEGMENT_SAMPLES / sample_rate_ksps
    fast_segment_ms = SEGMENT_SAMPLES / LOW_LATENCY_SAMPLE_RATE_KSPS
    fast_pre_trigger = round(standard_pre_trigger_ms / fast_segment_ms)
    fast_pre_trigger = max(0, min(LOW_LATENCY_MAX_PRE_TRIGGER_SEGMENTS, fast_pre_trigger))
    return RadarProfileSettings(
        profile=profile,
        sample_rate_ksps=LOW_LATENCY_SAMPLE_RATE_KSPS,
        pre_trigger_segments=fast_pre_trigger,
        scale_speed_band=True,
    )
