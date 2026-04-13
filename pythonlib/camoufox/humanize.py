"""
Human-like mouse movement engine -- Sigma-Lognormal model.

Generates trajectories that pass behavioral analysis by anti-bot systems.
Based on the Kinematic Theory of rapid human movements (Plamondon 1995):
each movement is a sum of N lognormal velocity kernels, one per
neuromuscular command. Produces asymmetric velocity profiles,
sub-movements, overshoot, and correction naturally.

Fitts's Law estimates total duration. Physiological tremor adds 8-12Hz noise.
Two-thirds power law (angular_velocity ~ curvature^(2/3)) is satisfied
by the coupling of velocity profile and path curvature.

The SFU SDK (Shopee) sends the last 21 mouse positions with timestamps.
The server derives velocity, acceleration, curvature, and entropy from
those raw (x, y, t) tuples. This engine ensures those derived metrics
match real human distributions.
"""

import contextvars
import math
import random
from typing import List, Optional, Tuple

PathPoint = Tuple[float, float, float]  # (x, y, delay_seconds)


# --- Session behavioral coherence (personality seed) ---
#
# All humanization dimensions should correlate within a session. A "sloppy"
# user has shaky mouse + imprecise clicks + variable typing + faster scroll.
# A "precise" user has steady mouse + tight clicks + consistent typing.
# Without coherence, ML classifiers detect the inconsistency.
#
# The personality is stored in a ContextVar so each asyncio task / worker
# has its own. Call set_personality() once per browser session at startup.

_personality: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "humanize_personality", default=None
)

_TRAITS = {
    "precise": {
        "tremor_amp": 0.55,
        "click_offset_sigma": 0.65,
        "fitts_noise": 0.75,
        "hover_delay": 0.85,
        "scroll_speed": 0.90,
        "keystroke_sigma": 0.80,
    },
    "normal": {
        "tremor_amp": 1.00,
        "click_offset_sigma": 1.00,
        "fitts_noise": 1.00,
        "hover_delay": 1.00,
        "scroll_speed": 1.00,
        "keystroke_sigma": 1.00,
    },
    "sloppy": {
        "tremor_amp": 1.55,
        "click_offset_sigma": 1.35,
        "fitts_noise": 1.30,
        "hover_delay": 1.20,
        "scroll_speed": 1.15,
        "keystroke_sigma": 1.25,
    },
}


def set_personality(trait: Optional[str] = None,
                    seed: Optional[int] = None) -> dict:
    """Set the humanization personality for the current async context.

    Call once per browser session at startup. All humanize functions called
    from this context (and child tasks) will use the trait multipliers,
    producing coherent behavior across mouse, keystroke, scroll, and timing.

    Args:
        trait: 'precise' (~25%), 'normal' (~50%), 'sloppy' (~25%).
               If None, randomly selected.
        seed: Optional seed for trait selection (deterministic per session).

    Returns: the personality state dict.
    """
    if trait is None:
        rng = random.Random(seed) if seed is not None else random
        r = rng.random()
        if r < 0.25:
            trait = "precise"
        elif r < 0.75:
            trait = "normal"
        else:
            trait = "sloppy"
    if trait not in _TRAITS:
        trait = "normal"
    state = {"trait": trait, "mult": _TRAITS[trait]}
    _personality.set(state)
    return state


def get_personality() -> dict:
    """Return current personality state, or 'normal' default."""
    state = _personality.get()
    if state is None:
        return {"trait": "normal", "mult": _TRAITS["normal"]}
    return state


def _mult(key: str) -> float:
    """Return personality multiplier for `key`, or 1.0 if no personality set."""
    state = _personality.get()
    if state is None:
        return 1.0
    return state["mult"].get(key, 1.0)


# --- Fitts's Law ---

def fitts_time(distance: float, width: float = 50.0) -> float:
    """Movement time from Fitts's Law: MT = a + b * log2(D/W + 1).
    Returns seconds. Adds gaussian noise scaled by personality."""
    if distance < 1:
        return 0.05
    a = 0.05
    b = 0.145
    mt = a + b * math.log2(distance / width + 1)
    mt *= random.gauss(1.0, 0.15 * _mult("fitts_noise"))
    return max(0.05, mt)


# --- Sigma-Lognormal velocity core ---

def _lognormal_pdf(t: float, t0: float, mu: float, sigma: float) -> float:
    """Lognormal PDF: velocity kernel for one neuromuscular sub-movement.
    v_i(t) = 1/(sigma*sqrt(2pi)*(t-t0)) * exp(-(ln(t-t0)-mu)^2 / (2*sigma^2))
    """
    dt = t - t0
    if dt <= 1e-10:
        return 0.0
    log_dt = math.log(dt)
    return math.exp(-(log_dt - mu) ** 2 / (2.0 * sigma * sigma)) / (
        sigma * _SQRT_2PI * dt
    )

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _generate_submovements(arc_length: float, duration: float,
                           distance: float) -> list:
    """Generate sigma-lognormal sub-movement parameters.

    Returns list of (t0, mu, sigma, D) tuples where:
    - t0: onset time of this neuromuscular command
    - mu, sigma: lognormal shape (derived so velocity peaks at correct time)
    - D: distance contribution (integral of D*pdf = D)
    """
    if distance < 100:
        n = random.choices([1, 2], weights=[0.55, 0.45])[0]
    elif distance < 300:
        n = random.choices([2, 3], weights=[0.6, 0.4])[0]
    elif distance < 500:
        n = random.choices([2, 3, 4], weights=[0.35, 0.40, 0.25])[0]
    else:
        n = random.choices([3, 4, 5], weights=[0.35, 0.40, 0.25])[0]

    subs = []
    t0 = random.uniform(0.003, 0.012)
    remaining_d = arc_length

    for i in range(n):
        sigma = max(0.18, min(0.55, random.gauss(0.30, 0.08)))

        if i == 0:
            # Primary sub-movement: 75-95% of distance
            d_frac = min(0.98, max(0.70, random.betavariate(8, 2)))
            D = arc_length * d_frac
            remaining_d -= D
            # Velocity peaks at 30-45% of movement duration
            peak_frac = max(0.25, min(0.50, random.gauss(0.37, 0.04)))
            peak_time = max(0.008, (duration - t0) * peak_frac)
        else:
            # Corrective sub-movements
            spacing = max(0.015, random.expovariate(1.0 / 0.08))
            t0 += spacing
            if t0 >= duration * 0.95:
                t0 = duration * random.uniform(0.6, 0.85)
            if i < n - 1:
                frac = random.uniform(0.3, 0.7)
                D = max(0.5, remaining_d * frac)
                remaining_d -= D
            else:
                D = max(0.5, remaining_d)
                remaining_d = 0
            remaining_time = max(0.02, duration * 1.05 - t0)
            peak_frac = max(0.20, min(0.55, random.gauss(0.35, 0.06)))
            peak_time = max(0.005, remaining_time * peak_frac)

        # mu derived so lognormal mode = peak_time
        # mode = exp(mu - sigma^2) => mu = ln(peak_time) + sigma^2
        mu = math.log(peak_time) + sigma * sigma
        subs.append((t0, mu, sigma, D))

    return subs


def _velocity_at(t: float, submovements: list) -> float:
    """Total speed at time t from all sub-movement velocity kernels."""
    v = 0.0
    for t0, mu, sigma, D in submovements:
        v += D * _lognormal_pdf(t, t0, mu, sigma)
    return v


# --- Arc-length parameterized Bezier curve ---

def _bezier_cubic(t: float, p0, p1, p2, p3):
    u = 1 - t
    return (
        u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
        u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1],
    )


def _control_points(sx, sy, ex, ey):
    """Generate 2 control points on one side of the path.
    Spread scaled to distance, 15-40% perpendicular offset."""
    dx, dy = ex - sx, ey - sy
    dist = math.sqrt(dx*dx + dy*dy) or 1.0
    px, py = -dy / dist, dx / dist
    side = random.choice([-1, 1])
    spread = dist * random.uniform(0.15, 0.40)
    off1 = side * random.uniform(spread * 0.3, spread * 0.8)
    c1 = (sx + dx * 0.25 + px * off1, sy + dy * 0.25 + py * off1)
    off2 = side * random.uniform(spread * 0.2, spread * 0.6)
    c2 = (sx + dx * 0.75 + px * off2, sy + dy * 0.75 + py * off2)
    return c1, c2


def _build_curve(sx, sy, ex, ey, n_samples=500):
    """Densely sample Bezier curve and compute cumulative arc lengths.
    Returns (points, arc_lengths)."""
    c1, c2 = _control_points(sx, sy, ex, ey)
    p0, p3 = (sx, sy), (ex, ey)

    points = []
    arc_lengths = [0.0]
    prev_x, prev_y = sx, sy

    for i in range(n_samples + 1):
        frac = i / n_samples
        x, y = _bezier_cubic(frac, p0, c1, c2, p3)
        points.append((x, y))
        if i > 0:
            d = math.sqrt((x - prev_x)**2 + (y - prev_y)**2)
            arc_lengths.append(arc_lengths[-1] + d)
        prev_x, prev_y = x, y

    return points, arc_lengths


def _lookup_position(curve_points, arc_lengths, target_s):
    """Find (x,y) on curve at arc-length position target_s via binary search."""
    total = arc_lengths[-1]
    target_s = max(0.0, min(total, target_s))

    lo, hi = 0, len(arc_lengths) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if arc_lengths[mid] <= target_s:
            lo = mid
        else:
            hi = mid

    if arc_lengths[hi] == arc_lengths[lo]:
        return curve_points[lo]

    frac = (target_s - arc_lengths[lo]) / (arc_lengths[hi] - arc_lengths[lo])
    x = curve_points[lo][0] + frac * (curve_points[hi][0] - curve_points[lo][0])
    y = curve_points[lo][1] + frac * (curve_points[hi][1] - curve_points[lo][1])
    return (x, y)


# --- Micro-tremor ---

def _tremor(t: float, freq: float, amplitude: float) -> Tuple[float, float]:
    """Physiological hand tremor: 8-12Hz oscillation, 0.3-1.5px amplitude.
    Sum of sinusoids at slightly different frequencies."""
    tx = (amplitude * math.sin(2 * math.pi * freq * t) +
          amplitude * 0.3 * math.sin(2 * math.pi * (freq * 1.37) * t + 1.2))
    ty = (amplitude * math.sin(2 * math.pi * (freq * 0.93) * t + 0.7) +
          amplitude * 0.25 * math.sin(2 * math.pi * (freq * 1.51) * t + 2.1))
    return tx, ty


# --- Sub-movement decomposition (spatial) ---

def _split_into_submovements(sx, sy, ex, ey) -> List[Tuple[float, float]]:
    """For long movements, decompose into 2-3 spatial sub-movements with waypoints."""
    dist = math.sqrt((ex - sx)**2 + (ey - sy)**2)
    if dist < 300:
        return [(ex, ey)]
    elif dist < 600:
        n = 2
    else:
        n = random.choice([2, 3])

    dx, dy = ex - sx, ey - sy
    px, py = -dy / dist, dx / dist

    waypoints = []
    for i in range(1, n):
        frac = i / n + random.gauss(0, 0.05)
        frac = max(0.2, min(0.8, frac))
        wander = random.gauss(0, dist * 0.03)
        wx = sx + dx * frac + px * wander
        wy = sy + dy * frac + py * wander
        waypoints.append((wx, wy))

    waypoints.append((ex, ey))
    return waypoints


# --- Overshoot ---

def _overshoot_target(sx, sy, ex, ey) -> Tuple[float, float]:
    """Overshoot in the direction of movement. 5-20px past target."""
    dx, dy = ex - sx, ey - sy
    dist = math.sqrt(dx*dx + dy*dy) or 1.0
    ux, uy = dx / dist, dy / dist
    px, py = -uy, ux
    forward = random.uniform(5, 20)
    lateral = random.gauss(0, 4)
    return (ex + ux * forward + px * lateral,
            ey + uy * forward + py * lateral)


# --- Segment generator (sigma-lognormal core) ---

def _generate_segment(sx: float, sy: float, ex: float, ey: float,
                      target_width: float = 50.0) -> List[PathPoint]:
    """Generate a single movement segment using sigma-lognormal velocity
    profile applied to a Bezier curve via arc-length parameterization."""
    dist = math.sqrt((ex - sx)**2 + (ey - sy)**2)
    if dist < 1:
        return [(ex, ey, 0.01)]

    # Build arc-length parameterized 2D curve
    curve_pts, arc_lens = _build_curve(sx, sy, ex, ey)
    total_arc = arc_lens[-1]

    # Duration from Fitts's Law
    duration = fitts_time(dist, target_width)

    # Generate sigma-lognormal sub-movements
    subs = _generate_submovements(total_arc, duration, dist)

    # Sample rate matching browser mousemove (55-75 Hz)
    sample_rate = random.uniform(55, 75)
    n_points = max(8, int(duration * sample_rate))

    # Numerically integrate velocity to get cumulative arc-length at each sample
    # Internal integration at 500 Hz for accuracy
    int_dt = 0.002
    int_steps = max(1, int(duration * 1.12 / int_dt))

    # Build s(t) via trapezoidal integration
    cum_s = 0.0
    s_at_t = [(0.0, 0.0)]  # (time, cumulative_arc_length)
    prev_v = _velocity_at(0.0, subs)
    for step in range(1, int_steps + 1):
        t = step * int_dt
        v = _velocity_at(t, subs)
        cum_s += 0.5 * (prev_v + v) * int_dt
        s_at_t.append((t, cum_s))
        prev_v = v

    # Scale so total integrated distance matches arc length
    actual_total = s_at_t[-1][1]
    scale = total_arc / actual_total if actual_total > 1e-6 else 1.0

    # Resample at output rate
    output_dt = duration / n_points
    tremor_freq = random.uniform(8, 12)
    tremor_amp = random.uniform(0.3, 1.2) * _mult("tremor_amp")

    points = []
    s_idx = 0  # pointer into s_at_t for interpolation

    for i in range(n_points + 1):
        t_target = i * output_dt

        # Find cumulative distance at t_target by walking s_at_t
        while s_idx < len(s_at_t) - 1 and s_at_t[s_idx + 1][0] <= t_target:
            s_idx += 1

        # Interpolate s between s_at_t[s_idx] and s_at_t[s_idx+1]
        if s_idx < len(s_at_t) - 1:
            t_lo, s_lo = s_at_t[s_idx]
            t_hi, s_hi = s_at_t[s_idx + 1]
            dt_span = t_hi - t_lo
            if dt_span > 1e-10:
                frac = (t_target - t_lo) / dt_span
            else:
                frac = 0.0
            s_now = (s_lo + frac * (s_hi - s_lo)) * scale
        else:
            s_now = total_arc

        # Look up 2D position on curve
        x, y = _lookup_position(curve_pts, arc_lens, s_now)

        # Tremor (decreases near target)
        progress = min(1.0, s_now / total_arc) if total_arc > 0 else 1.0
        t_scale = max(0.1, 1.0 - progress * 0.7)
        tx, ty = _tremor(t_target, tremor_freq, tremor_amp * t_scale)
        x += tx
        y += ty

        # Delay
        if i == 0:
            delay = random.uniform(0.003, 0.008)
        else:
            delay = output_dt + random.gauss(0, output_dt * 0.08)
            delay = max(0.004, delay)

        points.append((x, y, delay))

    # Snap last point to target
    if points:
        _, _, last_d = points[-1]
        points[-1] = (ex, ey, last_d)

    return points


# --- Main path generator ---

def generate_path(sx: float, sy: float, ex: float, ey: float,
                  target_width: float = 50.0) -> List[PathPoint]:
    """Generate a complete human-like mouse trajectory.

    Uses sigma-lognormal velocity on Bezier curves with spatial sub-movements,
    directional overshoot, and physiological tremor.
    Returns list of (x, y, delay) tuples ready for _follow_path().
    """
    dist = math.sqrt((ex - sx)**2 + (ey - sy)**2)
    if dist < 2:
        return [(ex, ey, 0.01)]

    # Overshoot for long distances
    do_overshoot = dist > 350 and random.random() < min(0.5, dist / 1200)

    if do_overshoot:
        ox, oy = _overshoot_target(sx, sy, ex, ey)
        path = _generate_segment(sx, sy, ox, oy, target_width)
        if path:
            lx, ly, _ = path[-1]
            path[-1] = (lx, ly, random.uniform(0.08, 0.15))
        correction = _generate_segment(ox, oy, ex, ey, target_width * 2)
        path.extend(correction)
        return path

    # Spatial sub-movements for long distances
    waypoints = _split_into_submovements(sx, sy, ex, ey)
    path = []
    cx, cy = sx, sy
    for i, (wx, wy) in enumerate(waypoints):
        segment = _generate_segment(cx, cy, wx, wy, target_width)
        if i < len(waypoints) - 1 and segment:
            lx, ly, _ = segment[-1]
            segment[-1] = (lx, ly, random.uniform(0.08, 0.20))
        path.extend(segment)
        if segment:
            cx, cy = segment[-1][0], segment[-1][1]

    return path


# --- Click helpers ---

def click_offset(element_width: float, element_height: float) -> Tuple[float, float]:
    """Gaussian offset for click position within an element.
    Slight bias toward upper-left (reading direction).
    Spread scales with personality (precise=tighter, sloppy=looser)."""
    spread = _mult("click_offset_sigma")
    sigma_x = (element_width / 6) * spread
    sigma_y = (element_height / 6) * spread
    ox = random.gauss(-element_width * 0.05, sigma_x)
    oy = random.gauss(-element_height * 0.05, sigma_y)
    ox = max(-element_width * 0.4, min(element_width * 0.4, ox))
    oy = max(-element_height * 0.4, min(element_height * 0.4, oy))
    return ox, oy


def hover_delay() -> float:
    """Delay between arriving at element and clicking (log-normal).
    Median ~180ms, scaled by personality."""
    base = random.lognormvariate(math.log(0.18), 0.4)
    return max(0.06, base * _mult("hover_delay"))


def inter_action_pause() -> float:
    """Pause between major actions (log-normal). Median ~500ms."""
    return max(0.15, random.lognormvariate(math.log(0.5), 0.5))


# --- Scroll helpers ---

def scroll_sequence(total_delta: float) -> List[Tuple[float, float]]:
    """Generate a human-like scroll sequence: bursts with momentum decay.
    Returns list of (delta_y, delay) tuples.
    Pause durations scale inversely with personality scroll_speed."""
    if abs(total_delta) < 10:
        return []

    sign = 1 if total_delta > 0 else -1
    remaining = abs(total_delta)
    events = []
    pause_scale = 1.0 / _mult("scroll_speed")

    while remaining > 5:
        burst_len = random.randint(3, 8)
        burst_base = random.uniform(60, 140)

        for j in range(burst_len):
            if remaining <= 5:
                break
            progress = j / max(1, burst_len - 1)
            intensity = math.sin(progress * math.pi) * 0.6 + 0.4
            delta = min(remaining, burst_base * intensity * random.uniform(0.7, 1.3))
            remaining -= delta
            events.append((delta * sign, random.uniform(0.015, 0.06)))

        if remaining > 5:
            decay_events = random.randint(2, 4)
            decay_delta = events[-1][0] if events else burst_base * sign * 0.5
            for k in range(decay_events):
                decay_delta *= random.uniform(0.4, 0.7)
                if abs(decay_delta) < 5:
                    break
                remaining -= abs(decay_delta)
                events.append((decay_delta, random.uniform(0.03, 0.08)))

        if remaining > 5:
            events.append((0, random.uniform(0.2, 2.0) * pause_scale))

    if len(events) > 5 and random.random() < 0.05:
        insert_at = random.randint(len(events) // 2, len(events) - 1)
        rev_delta = -sign * random.uniform(20, 60)
        events.insert(insert_at, (rev_delta, random.uniform(0.04, 0.1)))

    return events


def reading_scroll(page_height: float, viewport_height: float = 800,
                   coverage: Optional[float] = None
                   ) -> List[Tuple[float, float]]:
    """Generate a scroll sequence sized to the actual page content.

    Scrolls a proportion of the scrollable area, with reading pauses
    inserted proportionally to page length. Longer pages produce more
    reading pauses; short pages scroll less. Matches real users where
    dwell time and scroll depth correlate with content amount.

    Args:
        page_height: document.body.scrollHeight in CSS pixels
        viewport_height: window.innerHeight in CSS pixels
        coverage: fraction of scrollable area to traverse (0-1).
                  If None, randomly chosen from [0.3, 0.85].

    Returns: list of (delta_y, delay_seconds) events.
    """
    scrollable = max(0, page_height - viewport_height)
    if scrollable < 100:
        return []

    if coverage is None:
        coverage = random.uniform(0.30, 0.85)

    target = scrollable * coverage
    if target < 50:
        return []

    events = scroll_sequence(target)

    # Reading density: rough heuristic, more content -> more pauses
    # 1500px ~ 5% chance per event, 6000px ~ 25% chance per event
    read_density = max(0.0, min(0.30, page_height / 20000.0))
    pause_scale = 1.0 / _mult("scroll_speed")

    enhanced: List[Tuple[float, float]] = []
    for dy, delay in events:
        enhanced.append((dy, delay))
        if dy != 0 and random.random() < read_density:
            read_time = random.uniform(0.8, 2.5) * pause_scale
            enhanced.append((0.0, read_time))

    return enhanced


# --- Idle behavior ---

def idle_mouse_drift(cx: float, cy: float, duration: float,
                     viewport_w: float = 1400, viewport_h: float = 800
                     ) -> List[PathPoint]:
    """Generate mouse drift during page viewing (reading simulation).

    Ensures 2-5 mouse events per second (SFU SDK ring buffer is 21 events,
    server needs it full at all times). During "still" reading phases the
    hand is never actually still -- physiological tremor + postural
    adjustments produce micro-movements at 1-4Hz.
    """
    points = []
    elapsed = 0
    x, y = cx, cy

    while elapsed < duration:
        action = random.random()

        if action < 0.40:
            # Small directed drift (reading direction)
            dx = random.uniform(5, 40)
            dy = random.uniform(-5, 15)
            nx = max(50, min(viewport_w - 50, x + dx))
            ny = max(50, min(viewport_h - 50, y + dy))
            move_time = random.uniform(0.3, 0.8)
            steps = random.randint(3, 6)
            for s in range(steps):
                frac = (s + 1) / steps
                mx = x + (nx - x) * frac + random.gauss(0, 0.5)
                my = y + (ny - y) * frac + random.gauss(0, 0.5)
                points.append((mx, my, move_time / steps))
            x, y = nx, ny
            elapsed += move_time

        elif action < 0.60:
            # Larger repositioning
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
            # Reading pause with micro-drifts (2-4Hz) so SFU ring buffer
            # keeps filling. Hand never truly stops moving.
            pause_dur = random.uniform(1.5, 4.0)
            pause_elapsed = 0.0
            while pause_elapsed < pause_dur and elapsed + pause_elapsed < duration:
                # Tiny postural drift (~1-2px)
                dx = random.gauss(0, 1.2)
                dy = random.gauss(0, 0.9)
                nx = max(50, min(viewport_w - 50, x + dx))
                ny = max(50, min(viewport_h - 50, y + dy))
                # Most steps at 0.2-0.5s (2-5Hz), some freeze moments
                if random.random() < 0.15:
                    step_delay = random.uniform(0.85, 1.6)
                else:
                    step_delay = random.uniform(0.2, 0.5)
                points.append((nx, ny, step_delay))
                x, y = nx, ny
                pause_elapsed += step_delay
            elapsed += pause_elapsed

    return points


# --- Keystroke dynamics ---

# Common English/Portuguese digraphs (typed faster due to muscle memory)
_FAST_DIGRAPHS = frozenset([
    "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd",
    "ti", "es", "or", "te", "of", "ed", "is", "it", "al", "ar",
    "st", "to", "nt", "ng", "se", "ha", "as", "ou", "io", "le",
    "ve", "co", "me", "de", "hi", "ri", "ro", "ic", "ne", "ea",
    "ra", "ce", "li", "ch", "ll", "be", "ma", "si", "om", "ur",
    # Portuguese/Spanish common
    "ão", "qu", "os", "as", "em", "do", "da", "mo", "ss", "rr",
])

# Slow/awkward pairs (finger reach or alternation issues)
_SLOW_DIGRAPHS = frozenset([
    "qz", "zx", "xj", "jq", "vq", "kw", "fq", "pq", "jx", "wq",
])


def keystroke_delay(char: str, prev_char: str = "") -> float:
    """Inter-key delay for one keystroke.

    Log-normal distribution with digraph-based adjustment:
    - Common pairs (th, in, er...): ~100ms median
    - Normal pairs: ~150ms median
    - Slow pairs (qz, xj...): ~220ms median
    - Transitions (letter→space, punctuation→letter): +20-40ms
    - After a word boundary: slightly longer (thinking gap)
    """
    base_mu = math.log(0.15)  # 150ms median
    base_sigma = 0.38 * _mult("keystroke_sigma")

    if prev_char:
        digraph = (prev_char + char).lower()
        if digraph in _FAST_DIGRAPHS:
            base_mu = math.log(0.10)  # ~100ms
        elif digraph in _SLOW_DIGRAPHS:
            base_mu = math.log(0.22)  # ~220ms
            base_sigma = 0.45
        elif prev_char == " " and char.isalpha():
            # After a space, slight "thinking" delay
            base_mu = math.log(0.18)
        elif char == " ":
            # Before a space, slight pause
            base_mu = math.log(0.17)
        elif not prev_char.isalpha() and char.isalpha():
            base_mu = math.log(0.20)

    delay = random.lognormvariate(base_mu, base_sigma)
    return max(0.04, min(0.8, delay))


def typing_sequence(text: str) -> List[Tuple[str, float]]:
    """Generate (char, delay_after_char) pairs for human-like typing.

    Used by _Keyboard.type in rdp_api to send chars one-by-one with
    log-normal inter-key intervals. Occasionally inserts longer pauses
    at natural break points (after spaces, punctuation).
    """
    if not text:
        return []
    out = []
    prev = ""
    for i, ch in enumerate(text):
        d = keystroke_delay(ch, prev)
        # Occasional longer thinking pause after sentence-like boundary
        if prev in ".,!?;:" and ch == " " and random.random() < 0.3:
            d += random.uniform(0.15, 0.5)
        # Rare typo-like hesitation (word middle)
        elif random.random() < 0.008:
            d += random.uniform(0.2, 0.7)
        out.append((ch, d))
        prev = ch
    return out
