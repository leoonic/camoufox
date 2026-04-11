"""
Human-like mouse movement engine.

Generates trajectories that pass behavioral analysis by anti-bot systems.
Based on motor control research: Fitts's Law, minimum-jerk model,
two-thirds power law, and sub-movement decomposition.

The SFU SDK (Shopee) sends the last 21 mouse positions with timestamps.
The server derives velocity, acceleration, curvature, and entropy from
those raw (x, y, t) tuples. This engine ensures those derived metrics
match real human distributions.
"""

import math
import random
from typing import List, Tuple

# (x, y, delay_seconds)
PathPoint = Tuple[float, float, float]


# --- Fitts's Law ---

def fitts_time(distance: float, width: float = 50.0) -> float:
    """Movement time from Fitts's Law: MT = a + b * log2(D/W + 1).
    Returns seconds. Adds gaussian noise (~15% CV)."""
    if distance < 1:
        return 0.05
    a = 0.05   # base reaction/initiation time
    b = 0.145  # motor capacity constant
    mt = a + b * math.log2(distance / width + 1)
    # Human variability: ~15% coefficient of variation
    mt *= random.gauss(1.0, 0.15)
    return max(0.05, mt)


# --- Bezier path generation ---

def _bezier_cubic(t: float, p0, p1, p2, p3):
    u = 1 - t
    return (
        u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
        u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1],
    )


def _control_points(sx, sy, ex, ey):
    """Generate 2 control points on one side of the path (ghost-cursor style).
    Spread scaled to distance, 15-40% perpendicular offset."""
    dx, dy = ex - sx, ey - sy
    dist = math.sqrt(dx*dx + dy*dy) or 1.0
    # Perpendicular unit vector
    px, py = -dy / dist, dx / dist
    # Both points deviate to same side
    side = random.choice([-1, 1])
    # Spread: 15-40% of distance (much more natural than 5-10%)
    spread = dist * random.uniform(0.15, 0.40)
    # Control point at ~30% of path
    off1 = side * random.uniform(spread * 0.3, spread * 0.8)
    c1 = (sx + dx * 0.25 + px * off1, sy + dy * 0.25 + py * off1)
    # Control point at ~70% of path
    off2 = side * random.uniform(spread * 0.2, spread * 0.6)
    c2 = (sx + dx * 0.75 + px * off2, sy + dy * 0.75 + py * off2)
    return c1, c2


# --- Velocity profile ---

def _asymmetric_ease(t: float) -> float:
    """Asymmetric easing: maps [0,1] -> [0,1] monotonically.
    The DERIVATIVE (velocity) peaks at ~38% of movement time,
    matching human data where time-to-peak-velocity < time-from-peak-to-stop.
    Fast acceleration in first 38%, slower deceleration in remaining 62%."""
    peak = 0.38 + random.gauss(0, 0.03)
    peak = max(0.30, min(0.45, peak))
    if t < peak:
        # Fast acceleration phase: covers first 50% of distance
        return 0.5 * (t / peak) ** 1.7
    else:
        # Slow deceleration phase: covers remaining 50% of distance
        return 0.5 + 0.5 * (1.0 - ((1.0 - t) / (1.0 - peak)) ** 2.2)


# --- Micro-tremor ---

def _tremor(t: float, freq: float, amplitude: float) -> Tuple[float, float]:
    """Physiological hand tremor: 8-12Hz oscillation, 0.5-1.5px amplitude.
    Uses sum of sinusoids at slightly different frequencies for realism."""
    tx = (amplitude * math.sin(2 * math.pi * freq * t) +
          amplitude * 0.3 * math.sin(2 * math.pi * (freq * 1.37) * t + 1.2))
    ty = (amplitude * math.sin(2 * math.pi * (freq * 0.93) * t + 0.7) +
          amplitude * 0.25 * math.sin(2 * math.pi * (freq * 1.51) * t + 2.1))
    return tx, ty


# --- Sub-movement decomposition ---

def _split_into_submovements(sx, sy, ex, ey) -> List[Tuple[float, float]]:
    """For long movements, decompose into 2-3 sub-movements with waypoints.
    Each sub-movement has its own velocity peak. Short pauses between them."""
    dist = math.sqrt((ex - sx)**2 + (ey - sy)**2)
    if dist < 300:
        return [(ex, ey)]
    elif dist < 600:
        n = 2
    else:
        n = random.choice([2, 3])

    dx, dy = ex - sx, ey - sy
    px, py = -dy / dist, dx / dist  # perpendicular

    waypoints = []
    for i in range(1, n):
        frac = i / n + random.gauss(0, 0.05)
        frac = max(0.2, min(0.8, frac))
        # Slight perpendicular wander at waypoints
        wander = random.gauss(0, dist * 0.03)
        wx = sx + dx * frac + px * wander
        wy = sy + dy * frac + py * wander
        waypoints.append((wx, wy))

    waypoints.append((ex, ey))
    return waypoints


# --- Overshoot ---

def _overshoot_target(sx, sy, ex, ey) -> Tuple[float, float]:
    """Overshoot in the direction of movement (not random direction).
    Magnitude: 5-20px past target. Slight perpendicular deviation."""
    dx, dy = ex - sx, ey - sy
    dist = math.sqrt(dx*dx + dy*dy) or 1.0
    # Unit vector in movement direction
    ux, uy = dx / dist, dy / dist
    # Perpendicular
    px, py = -uy, ux
    # Overshoot: 5-20px forward, slight perpendicular
    forward = random.uniform(5, 20)
    lateral = random.gauss(0, 4)
    return (ex + ux * forward + px * lateral,
            ey + uy * forward + py * lateral)


# --- Main path generator ---

def generate_path(sx: float, sy: float, ex: float, ey: float,
                  target_width: float = 50.0) -> List[PathPoint]:
    """Generate a complete human-like mouse trajectory.

    Returns list of (x, y, delay) tuples ready for _follow_path().
    Handles sub-movements, overshoot, tremor, and timing.
    """
    dist = math.sqrt((ex - sx)**2 + (ey - sy)**2)
    if dist < 2:
        return [(ex, ey, 0.01)]

    # Decide on overshoot (probability increases with distance)
    do_overshoot = dist > 350 and random.random() < min(0.5, dist / 1200)

    if do_overshoot:
        ox, oy = _overshoot_target(sx, sy, ex, ey)
        # Main movement to overshoot point
        path = _generate_segment(sx, sy, ox, oy, target_width)
        # Brief pause at overshoot (80-150ms)
        if path:
            lx, ly, _ = path[-1]
            path[-1] = (lx, ly, random.uniform(0.08, 0.15))
        # Correction movement back to target
        correction = _generate_segment(ox, oy, ex, ey, target_width * 2)
        path.extend(correction)
        return path

    # Sub-movement decomposition for long distances
    waypoints = _split_into_submovements(sx, sy, ex, ey)
    path = []
    cx, cy = sx, sy
    for i, (wx, wy) in enumerate(waypoints):
        segment = _generate_segment(cx, cy, wx, wy, target_width)
        if i < len(waypoints) - 1 and segment:
            # Brief pause between sub-movements (80-200ms)
            lx, ly, _ = segment[-1]
            segment[-1] = (lx, ly, random.uniform(0.08, 0.20))
        path.extend(segment)
        if segment:
            cx, cy = segment[-1][0], segment[-1][1]

    return path


def _generate_segment(sx: float, sy: float, ex: float, ey: float,
                      target_width: float = 50.0) -> List[PathPoint]:
    """Generate a single sub-movement segment with Bezier path and timing."""
    dist = math.sqrt((ex - sx)**2 + (ey - sy)**2)
    if dist < 1:
        return [(ex, ey, 0.01)]

    c1, c2 = _control_points(sx, sy, ex, ey)
    p0, p3 = (sx, sy), (ex, ey)

    # Duration from Fitts's Law
    duration = fitts_time(dist, target_width)

    # ~60-80 points per second (browser mousemove rate)
    steps = max(8, int(duration * random.uniform(55, 75)))

    # Tremor parameters for this movement
    tremor_freq = random.uniform(8, 12)  # Hz
    tremor_amp = random.uniform(0.4, 1.2)  # px

    points = []
    prev_time = 0
    for i in range(steps):
        t = i / (steps - 1)

        # Asymmetric velocity profile (fast accel, slow decel)
        ease = _asymmetric_ease(t)

        # Bezier position
        bx, by = _bezier_cubic(ease, p0, c1, c2, p3)

        # Micro-tremor (decreases near target -- steadying hand)
        tremor_scale = 1.0 - t * 0.6
        tx, ty = _tremor(t * duration, tremor_freq, tremor_amp * tremor_scale)
        bx += tx
        by += ty

        # Timing: derived from velocity profile
        # Delay between this point and previous
        if i == 0:
            delay = random.uniform(0.003, 0.008)
        else:
            # Base: evenly spaced
            base = duration / steps
            # Modulate by velocity (inverse of ease derivative)
            # Slow at start/end, fast in middle
            if t < 0.1:
                factor = 1.8 + random.uniform(-0.2, 0.2)
            elif t > 0.9:
                factor = 1.5 + random.uniform(-0.2, 0.3)
            else:
                factor = random.uniform(0.7, 1.1)
            delay = base * factor
            # Small random jitter on timing
            delay += random.gauss(0, 0.002)
            delay = max(0.004, delay)

        points.append((bx, by, delay))

    # Ensure last point is exactly the target
    if points:
        _, _, last_delay = points[-1]
        points[-1] = (ex, ey, last_delay)

    return points


# --- Click helpers ---

def click_offset(element_width: float, element_height: float) -> Tuple[float, float]:
    """Gaussian offset for click position within an element.
    Humans don't click dead center -- slight bias toward upper-left (reading direction)."""
    sigma_x = element_width / 6
    sigma_y = element_height / 6
    # Slight upper-left bias
    ox = random.gauss(-element_width * 0.05, sigma_x)
    oy = random.gauss(-element_height * 0.05, sigma_y)
    # Clamp within element bounds
    ox = max(-element_width * 0.4, min(element_width * 0.4, ox))
    oy = max(-element_height * 0.4, min(element_height * 0.4, oy))
    return ox, oy


def hover_delay() -> float:
    """Delay between arriving at element and clicking (log-normal).
    Models visual confirmation + decision time. Median ~180ms."""
    return max(0.06, random.lognormvariate(math.log(0.18), 0.4))


def inter_action_pause() -> float:
    """Pause between major actions (log-normal). Median ~500ms."""
    return max(0.15, random.lognormvariate(math.log(0.5), 0.5))


# --- Scroll helpers ---

def scroll_sequence(total_delta: float) -> List[Tuple[float, float]]:
    """Generate a human-like scroll sequence: bursts with momentum decay.
    Returns list of (delta_y, delay) tuples."""
    if abs(total_delta) < 10:
        return []

    sign = 1 if total_delta > 0 else -1
    remaining = abs(total_delta)
    events = []

    while remaining > 5:
        # Burst of 3-8 scroll events
        burst_len = random.randint(3, 8)
        burst_base = random.uniform(60, 140)

        for j in range(burst_len):
            if remaining <= 5:
                break
            # Bell-shaped intensity within burst
            progress = j / max(1, burst_len - 1)
            intensity = math.sin(progress * math.pi) * 0.6 + 0.4
            delta = min(remaining, burst_base * intensity * random.uniform(0.7, 1.3))
            remaining -= delta
            events.append((delta * sign, random.uniform(0.015, 0.06)))

        # Momentum decay: 2-4 diminishing events
        if remaining > 5:
            decay_events = random.randint(2, 4)
            decay_delta = events[-1][0] if events else burst_base * sign * 0.5
            for k in range(decay_events):
                decay_delta *= random.uniform(0.4, 0.7)
                if abs(decay_delta) < 5:
                    break
                remaining -= abs(decay_delta)
                events.append((decay_delta, random.uniform(0.03, 0.08)))

        # Reading pause between bursts (200ms - 2s)
        if remaining > 5:
            events.append((0, random.uniform(0.2, 2.0)))

    # Occasional micro-reversal (5% chance)
    if len(events) > 5 and random.random() < 0.05:
        insert_at = random.randint(len(events) // 2, len(events) - 1)
        rev_delta = -sign * random.uniform(20, 60)
        events.insert(insert_at, (rev_delta, random.uniform(0.04, 0.1)))

    return events


# --- Idle behavior ---

def idle_mouse_drift(cx: float, cy: float, duration: float,
                     viewport_w: float = 1400, viewport_h: float = 800
                     ) -> List[PathPoint]:
    """Generate mouse drift during page viewing (reading simulation).
    Small, slow movements that follow a general reading direction
    (left-to-right, top-to-bottom) with pauses."""
    points = []
    elapsed = 0
    x, y = cx, cy

    while elapsed < duration:
        action = random.random()

        if action < 0.45:
            # Small drift in reading direction (right and slightly down)
            dx = random.uniform(5, 40)
            dy = random.uniform(-5, 15)
            nx = max(50, min(viewport_w - 50, x + dx))
            ny = max(50, min(viewport_h - 50, y + dy))
            # Slow movement (not instant)
            move_time = random.uniform(0.3, 0.8)
            steps = random.randint(3, 6)
            for s in range(steps):
                frac = (s + 1) / steps
                mx = x + (nx - x) * frac + random.gauss(0, 0.5)
                my = y + (ny - y) * frac + random.gauss(0, 0.5)
                points.append((mx, my, move_time / steps))
            x, y = nx, ny
            elapsed += move_time

        elif action < 0.65:
            # Larger repositioning (jump to different area)
            nx = random.gauss(viewport_w * 0.5, viewport_w * 0.2)
            ny = random.gauss(viewport_h * 0.4, viewport_h * 0.15)
            nx = max(50, min(viewport_w - 50, nx))
            ny = max(50, min(viewport_h - 50, ny))
            segment = _generate_segment(x, y, nx, ny, 100.0)
            points.extend(segment)
            x, y = nx, ny
            seg_time = sum(p[2] for p in segment)
            elapsed += seg_time

        else:
            # Still pause (no movement, just waiting)
            pause = random.uniform(1.0, 4.0)
            # Add a single point at current position to mark time
            points.append((x, y, pause))
            elapsed += pause

    return points
