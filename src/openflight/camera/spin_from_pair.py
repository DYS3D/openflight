"""EXPERIMENTAL: ball spin from two images of a marked ball.

Given two views of the same ball a known time apart, either a
double-exposure frame holding two ball discs or two consecutive frames,
find both discs, locate a high-contrast mark (painted line or dots) on
each, lift the mark pixels onto the unit sphere and solve for the
rotation that carries the first mark onto the second. Spin follows from
angle / gap.

Camera-frame conventions: x right, y down, z toward the camera,
orthographic projection over the disc. For a camera behind the tee,
backspin is rotation about -x (the top of the ball moves toward the
camera), sidespin about y and rifle spin about z. The sign of the
sidespin component relative to fade/draw depends on the mount and is a
calibration matter, not fixed here.

This cannot work with the current 1 ms continuous-light exposure: the
ball moves ~67 mm at 150 mph in that time, so no mark is resolvable. It
needs the strobe hardware listed in :mod:`openflight.camera.strobe` and
a marked ball. With plain 300 fps frames the ball turns ~54 deg per frame
at 2,700 rpm, at the edge of what the mark tracking tolerates, and much
more for wedges.
"""

# pylint: disable=no-member  # cv2 is a compiled module pylint cannot introspect

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the camera extra
    CV2_AVAILABLE = False

LIMB_EXCLUSION = 0.9
LIMB_TRUNCATION = 0.8
MARK_DARKNESS_RATIO = 0.6
MIN_MARK_PIXELS = 8
# Second/third eigenvalue ratio of the mark's 3-D scatter above which the
# mark is treated as a line (its great-circle plane is recoverable).
LINE_ELONGATION = 4.0
BACKSPIN_AXIS = np.array([-1.0, 0.0, 0.0])
SIDESPIN_AXIS = np.array([0.0, 1.0, 0.0])
RIFLE_AXIS = np.array([0.0, 0.0, 1.0])


@dataclass(frozen=True)
class Disc:
    """One ball image in pixel coordinates."""

    cx: float
    cy: float
    r: float


@dataclass
class MarkPose:
    """A mark on one ball disc, lifted onto the unit sphere."""

    angle_deg: float
    offset: Tuple[float, float]
    center: np.ndarray
    normal: Optional[np.ndarray]
    pixel_count: int
    elongation: float
    quality: float


@dataclass
class SpinFromPair:
    """Spin estimate; ``status`` is "accepted" or the reason nothing was estimated."""

    status: str
    angle_deg: Optional[float] = None
    spin_rpm: Optional[float] = None
    axis: Optional[Tuple[float, float, float]] = None
    axis_deg: Optional[float] = None
    backspin_rpm: Optional[float] = None
    sidespin_rpm: Optional[float] = None
    rifle_rpm: Optional[float] = None
    confidence: float = 0.0
    discs: List[Disc] = field(default_factory=list)


def _require_cv2() -> None:
    if not CV2_AVAILABLE:
        raise RuntimeError("OpenCV not available. Install the camera extra.")


def _gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    return frame


def find_discs(
    frame: np.ndarray,
    *,
    min_radius: float = 6.0,
    max_radius: float = 150.0,
    threshold: Optional[int] = None,
    max_discs: int = 2,
) -> List[Disc]:
    """Bright ball discs in a frame, left to right.

    Separate discs come from their filled contours; a blob whose fill
    ratio says it holds two touching or overlapping discs is split with
    a Hough circle search.
    """
    _require_cv2()
    gray = _gray(np.asarray(frame))
    if threshold is None:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    else:
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    discs: List[Disc] = []
    min_area = math.pi * min_radius**2
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        fill = area / (math.pi * radius**2)
        if fill >= 0.85 and min_radius <= radius <= max_radius:
            discs.append(Disc(float(cx), float(cy), float(radius)))
            continue
        if fill < 0.4:
            continue
        discs.extend(_split_blob(binary, contour, min_radius, max_radius))

    discs.sort(key=lambda d: d.cx)
    return discs[:max_discs]


def _split_blob(
    binary: np.ndarray, contour: np.ndarray, min_radius: float, max_radius: float
) -> List[Disc]:
    x, y, w, h = cv2.boundingRect(contour)
    roi = np.zeros_like(binary)
    cv2.drawContours(roi, [contour], -1, 255, -1)
    roi = roi[y : y + h, x : x + w]
    blurred = cv2.GaussianBlur(roi, (5, 5), 0)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.0,
        minDist=max(min_radius, 4.0),
        param1=50,
        param2=12,
        minRadius=int(min_radius),
        maxRadius=int(min(max_radius, max(w, h))),
    )
    if circles is None:
        return []
    found: List[Disc] = []
    for cx, cy, r in circles[0][:2]:
        found.append(Disc(float(cx) + x, float(cy) + y, float(r)))
    return found


def extract_disc(frame: np.ndarray, disc: Disc, margin: int = 2) -> Tuple[np.ndarray, Disc]:
    """Square crop around ``disc`` and the disc's position inside the crop."""
    gray = _gray(np.asarray(frame))
    h, w = gray.shape[:2]
    half = int(math.ceil(disc.r)) + margin
    x0 = max(0, int(round(disc.cx)) - half)
    y0 = max(0, int(round(disc.cy)) - half)
    x1 = min(w, int(round(disc.cx)) + half + 1)
    y1 = min(h, int(round(disc.cy)) + half + 1)
    crop = gray[y0:y1, x0:x1]
    return crop, Disc(disc.cx - x0, disc.cy - y0, disc.r)


def mark_pose(
    disc_img: np.ndarray,
    disc: Optional[Disc] = None,
    *,
    exclude: Sequence[Disc] = (),
) -> Optional[MarkPose]:
    """Locate the dark mark on one disc.

    ``disc`` defaults to the largest circle inscribed in the crop.
    ``exclude`` masks other discs (the overlap region of a double
    exposure). Returns None when no mark is found.
    """
    gray = _gray(np.asarray(disc_img)).astype(np.float32)
    h, w = gray.shape[:2]
    if disc is None:
        disc = Disc((w - 1) / 2.0, (h - 1) / 2.0, min(h, w) / 2.0 - 1.0)
    if disc.r <= 1.0:
        return None
    ys, xs = np.mgrid[0:h, 0:w]
    u = (xs - disc.cx) / disc.r
    v = (ys - disc.cy) / disc.r
    inside = u**2 + v**2 <= LIMB_EXCLUSION**2
    for other in exclude:
        inside &= (xs - other.cx) ** 2 + (ys - other.cy) ** 2 > other.r**2
    if inside.sum() < MIN_MARK_PIXELS * 4:
        return None
    ball_level = float(np.median(gray[inside]))
    mark = inside & (gray < ball_level * MARK_DARKNESS_RATIO)
    count = int(mark.sum())
    if count < MIN_MARK_PIXELS:
        return None

    mu = u[mark]
    mv = v[mark]
    mw = np.sqrt(np.clip(1.0 - mu**2 - mv**2, 0.0, None))
    points = np.column_stack([mu, mv, mw])
    # Image pixels sample the sphere at density w, so weight by 1/w to get
    # centroid and scatter per unit of ball surface, not per pixel.
    weights = 1.0 / np.maximum(mw, 0.2)
    weights /= weights.sum()
    center = weights @ points
    center /= np.linalg.norm(center)

    scatter = (points * weights[:, None]).T @ points
    eigvals, eigvecs = np.linalg.eigh(scatter)
    elongation = float(eigvals[1] / max(eigvals[0], 1e-12))
    normal: Optional[np.ndarray] = None
    if elongation >= LINE_ELONGATION:
        normal = eigvecs[:, 0].copy()
        normal -= float(normal @ center) * center
        norm = np.linalg.norm(normal)
        normal = normal / norm if norm > 1e-9 else None

    du = mu - mu.mean()
    dv = mv - mv.mean()
    angle_deg = math.degrees(
        0.5 * math.atan2(2.0 * float((du * dv).mean()), float((du**2 - dv**2).mean()))
    )
    radial = float(mu.mean() ** 2 + mv.mean() ** 2)
    # Pixels near the limb mean the mark may run past the visible edge, which
    # drags its centroid inward and biases the rotation.
    limb_fraction = float(np.mean(mu**2 + mv**2 > LIMB_TRUNCATION**2))
    quality = min(1.0, count / 30.0) * max(0.0, 1.0 - radial) * (1.0 - limb_fraction)
    return MarkPose(
        angle_deg=angle_deg % 180.0,
        offset=(float(mu.mean()), float(mv.mean())),
        center=center,
        normal=normal,
        pixel_count=count,
        elongation=elongation,
        quality=quality,
    )


def _frame(m: np.ndarray, n: np.ndarray) -> np.ndarray:
    return np.column_stack([m, n, np.cross(m, n)])


def _rotation_angle_axis(rotation: np.ndarray) -> Tuple[float, np.ndarray]:
    cos_theta = (np.trace(rotation) - 1.0) / 2.0
    theta = math.acos(max(-1.0, min(1.0, cos_theta)))
    if theta < 1e-9:
        return 0.0, BACKSPIN_AXIS.copy()
    axis = np.array(
        [
            rotation[2, 1] - rotation[1, 2],
            rotation[0, 2] - rotation[2, 0],
            rotation[1, 0] - rotation[0, 1],
        ]
    ) / (2.0 * math.sin(theta))
    return theta, axis / np.linalg.norm(axis)


def _minimal_rotation(m1: np.ndarray, m2: np.ndarray) -> Tuple[float, np.ndarray]:
    cross = np.cross(m1, m2)
    sin_theta = float(np.linalg.norm(cross))
    theta = math.atan2(sin_theta, float(m1 @ m2))
    if sin_theta < 1e-9:
        return theta, BACKSPIN_AXIS.copy()
    return theta, cross / sin_theta


def estimate_spin(discs: Sequence[MarkPose], gap_s: float) -> SpinFromPair:
    """Rotation between two mark poses observed ``gap_s`` apart.

    With a line mark on both discs the full rotation is solved from the
    (mark centre, plane normal) frames, trying both normal signs and
    keeping the smaller rotation. With a dot mark only the minimal
    rotation carrying one centre onto the other is available (any spin
    about the mark itself is invisible), reported at reduced confidence.
    """
    if gap_s <= 0:
        return SpinFromPair(status="invalid_gap")
    if len(discs) != 2:
        return SpinFromPair(status="one_disc" if len(discs) == 1 else "no_discs")
    first, second = discs
    m1 = first.center / np.linalg.norm(first.center)
    m2 = second.center / np.linalg.norm(second.center)
    method_weight = 1.0
    if first.normal is not None and second.normal is not None:
        theta, axis = None, None
        for sign in (1.0, -1.0):
            rotation = _frame(m2, sign * second.normal) @ _frame(m1, first.normal).T
            candidate_theta, candidate_axis = _rotation_angle_axis(rotation)
            if theta is None or candidate_theta < theta:
                theta, axis = candidate_theta, candidate_axis
    else:
        theta, axis = _minimal_rotation(m1, m2)
        method_weight = 0.6

    angle_deg = math.degrees(theta)
    spin_rpm = angle_deg / 360.0 * 60.0 / gap_s
    omega = spin_rpm * axis
    backspin = float(omega @ BACKSPIN_AXIS)
    sidespin = float(omega @ SIDESPIN_AXIS)
    rifle = float(omega @ RIFLE_AXIS)
    if angle_deg < 5.0 or angle_deg > 90.0:
        plausibility = 0.2
    elif angle_deg > 60.0:
        plausibility = 0.5
    else:
        plausibility = 1.0
    confidence = math.sqrt(first.quality * second.quality) * plausibility * method_weight
    return SpinFromPair(
        status="accepted",
        angle_deg=angle_deg,
        spin_rpm=spin_rpm,
        axis=(float(axis[0]), float(axis[1]), float(axis[2])),
        axis_deg=math.degrees(math.atan2(sidespin, backspin)),
        backspin_rpm=backspin,
        sidespin_rpm=sidespin,
        rifle_rpm=rifle,
        confidence=confidence,
    )


def _pose_for(frame: np.ndarray, disc: Disc, others: Sequence[Disc]) -> Optional[MarkPose]:
    crop, local = extract_disc(frame, disc)
    dx = local.cx - disc.cx
    dy = local.cy - disc.cy
    exclude = [Disc(o.cx + dx, o.cy + dy, o.r) for o in others]
    return mark_pose(crop, local, exclude=exclude)


def estimate_spin_from_double_exposure(frame: np.ndarray, gap_s: float) -> SpinFromPair:
    """Both ball images in one strobed frame; the left disc is taken as first."""
    discs = find_discs(frame, max_discs=2)
    if len(discs) < 2:
        result = SpinFromPair(status="one_disc" if discs else "no_discs")
        result.discs = list(discs)
        return result
    poses = [_pose_for(frame, disc, [o for o in discs if o is not disc]) for disc in discs]
    if any(p is None for p in poses):
        return SpinFromPair(status="no_mark", discs=list(discs))
    result = estimate_spin([p for p in poses if p is not None], gap_s)
    result.discs = list(discs)
    return result


def estimate_spin_from_frames(
    frame_a: np.ndarray, frame_b: np.ndarray, gap_s: float
) -> SpinFromPair:
    """Two consecutive frames, one ball disc each (the largest disc is used)."""
    discs: List[Disc] = []
    poses: List[MarkPose] = []
    for frame in (frame_a, frame_b):
        found = find_discs(frame, max_discs=4)
        if not found:
            return SpinFromPair(status="no_discs", discs=discs)
        disc = max(found, key=lambda d: d.r)
        discs.append(disc)
        pose = _pose_for(frame, disc, [])
        if pose is None:
            return SpinFromPair(status="no_mark", discs=discs)
        poses.append(pose)
    result = estimate_spin(poses, gap_s)
    result.discs = discs
    return result
