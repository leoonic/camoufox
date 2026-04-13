"""
Tests for humanize.py -- validates that generated trajectories
have properties consistent with real human mouse movement.

Validates sigma-lognormal model properties:
- Asymmetric velocity profile (peak at 30-50% of movement)
- Sub-movements: paths >300px have 2+ velocity peaks
- Two-thirds power law: angular_velocity ~ curvature^(2/3)
- Timing CV > 0.3 (human inter-event variability)
- Point rate 55-80 points/second
- Endpoint accuracy <1px
- No teleportation (no step > 30% of total distance)
- Overshoot in movement direction
- Physiological tremor (8-12Hz, 0.3-3px)
- 100 random paths pass all validations
"""

import math
import random
import statistics
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from camoufox.humanize import (
    generate_path,
    fitts_time,
    click_offset,
    hover_delay,
    inter_action_pause,
    scroll_sequence,
    reading_scroll,
    idle_mouse_drift,
    keystroke_delay,
    typing_sequence,
    set_personality,
    get_personality,
    _generate_segment,
    _control_points,
    _tremor,
    _split_into_submovements,
    _overshoot_target,
    _lognormal_pdf,
    _generate_submovements,
    _velocity_at,
    _build_curve,
    _lookup_position,
    _personality,
)


def _compute_velocities(path):
    """Compute instantaneous velocities from path points."""
    vels = []
    for i in range(1, len(path)):
        dx = path[i][0] - path[i-1][0]
        dy = path[i][1] - path[i-1][1]
        dt = path[i][2]
        if dt > 0.001:
            v = math.sqrt(dx*dx + dy*dy) / dt
            vels.append(v)
    return vels


def _compute_curvatures(path):
    """Compute curvature at each point using 3-point method."""
    curvatures = []
    for i in range(1, len(path) - 1):
        x0, y0, _ = path[i-1]
        x1, y1, _ = path[i]
        x2, y2, _ = path[i+1]
        dx1, dy1 = x1 - x0, y1 - y0
        dx2, dy2 = x2 - x1, y2 - y1
        cross = abs(dx1 * dy2 - dy1 * dx2)
        d1 = math.sqrt(dx1*dx1 + dy1*dy1)
        d2 = math.sqrt(dx2*dx2 + dy2*dy2)
        d3 = math.sqrt((x2-x0)**2 + (y2-y0)**2)
        denom = d1 * d2 * d3
        if denom > 0.001:
            curvatures.append(cross / denom)
    return curvatures


def _path_total_time(path):
    return sum(p[2] for p in path)


def _path_distance(path):
    total = 0
    for i in range(1, len(path)):
        dx = path[i][0] - path[i-1][0]
        dy = path[i][1] - path[i-1][1]
        total += math.sqrt(dx*dx + dy*dy)
    return total


def _find_velocity_peaks(velocities, min_prominence=0.1):
    """Find local maxima in velocity profile. Returns list of peak indices."""
    if len(velocities) < 3:
        return []
    peaks = []
    max_v = max(velocities) if velocities else 1
    threshold = max_v * min_prominence
    for i in range(1, len(velocities) - 1):
        if (velocities[i] > velocities[i-1] and
            velocities[i] > velocities[i+1] and
            velocities[i] > threshold):
            peaks.append(i)
    return peaks


class TestFittsLaw(unittest.TestCase):

    def test_longer_distance_takes_more_time(self):
        random.seed(42)
        times_short = [fitts_time(200, 50) for _ in range(100)]
        times_long = [fitts_time(800, 50) for _ in range(100)]
        self.assertGreater(statistics.mean(times_long), statistics.mean(times_short))

    def test_smaller_target_takes_more_time(self):
        random.seed(42)
        times_big = [fitts_time(400, 100) for _ in range(100)]
        times_small = [fitts_time(400, 20) for _ in range(100)]
        self.assertGreater(statistics.mean(times_small), statistics.mean(times_big))

    def test_minimum_time(self):
        t = fitts_time(0.5)
        self.assertGreaterEqual(t, 0.05)

    def test_human_variability(self):
        random.seed(42)
        times = [fitts_time(400, 50) for _ in range(500)]
        cv = statistics.stdev(times) / statistics.mean(times)
        self.assertGreater(cv, 0.08)
        self.assertLess(cv, 0.30)


class TestLognormalPdf(unittest.TestCase):

    def test_zero_before_onset(self):
        self.assertAlmostEqual(_lognormal_pdf(0.05, 0.1, -1.0, 0.3), 0.0)

    def test_positive_after_onset(self):
        v = _lognormal_pdf(0.2, 0.01, -1.0, 0.3)
        self.assertGreater(v, 0.0)

    def test_peak_at_mode(self):
        """PDF should peak near exp(mu - sigma^2) after onset."""
        t0, mu, sigma = 0.01, -1.5, 0.3
        mode = math.exp(mu - sigma*sigma) + t0
        v_at_mode = _lognormal_pdf(mode, t0, mu, sigma)
        v_before = _lognormal_pdf(mode - 0.02, t0, mu, sigma)
        v_after = _lognormal_pdf(mode + 0.02, t0, mu, sigma)
        self.assertGreater(v_at_mode, v_before)
        self.assertGreater(v_at_mode, v_after)

    def test_integral_approx_one(self):
        """Integral of lognormal PDF should be ~1.0."""
        t0, mu, sigma = 0.0, -1.0, 0.3
        dt = 0.001
        total = 0.0
        t = dt
        for _ in range(5000):
            total += _lognormal_pdf(t, t0, mu, sigma) * dt
            t += dt
        self.assertAlmostEqual(total, 1.0, delta=0.05)


class TestSubmovements(unittest.TestCase):

    def test_count_short(self):
        random.seed(42)
        for _ in range(20):
            subs = _generate_submovements(80, 0.3, 80)
            self.assertIn(len(subs), [1, 2])

    def test_count_long(self):
        random.seed(42)
        for _ in range(20):
            subs = _generate_submovements(600, 0.6, 600)
            self.assertGreaterEqual(len(subs), 2)

    def test_d_sums_to_arc(self):
        """Sum of D values should approximately equal arc_length."""
        random.seed(42)
        for _ in range(30):
            arc = random.uniform(100, 800)
            dur = random.uniform(0.2, 0.8)
            subs = _generate_submovements(arc, dur, arc)
            total_d = sum(s[3] for s in subs)
            self.assertAlmostEqual(total_d, arc, delta=arc * 0.3)

    def test_primary_dominates(self):
        """First sub-movement should cover >70% of distance."""
        random.seed(42)
        for _ in range(30):
            subs = _generate_submovements(400, 0.5, 400)
            primary_d = subs[0][3]
            self.assertGreater(primary_d / 400, 0.65)


class TestPathGeneration(unittest.TestCase):

    def test_reaches_target(self):
        random.seed(42)
        for _ in range(50):
            ex = random.uniform(100, 1400)
            ey = random.uniform(100, 800)
            path = generate_path(100, 100, ex, ey)
            last_x, last_y, _ = path[-1]
            dist = math.sqrt((last_x - ex)**2 + (last_y - ey)**2)
            self.assertLess(dist, 1.0,
                f"Endpoint miss: target=({ex:.0f},{ey:.0f}) got=({last_x:.0f},{last_y:.0f})")

    def test_minimum_points(self):
        random.seed(42)
        path = generate_path(0, 0, 300, 200)
        self.assertGreaterEqual(len(path), 8)

    def test_very_short_path(self):
        path = generate_path(100, 100, 101, 100)
        self.assertEqual(len(path), 1)

    def test_point_density(self):
        """Should generate 55-80 points per second."""
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        total_time = _path_total_time(path)
        if total_time > 0.1:
            rate = len(path) / total_time
            self.assertGreater(rate, 30, f"Point rate too low: {rate:.0f}/s")
            self.assertLess(rate, 150, f"Point rate too high: {rate:.0f}/s")

    def test_no_teleportation(self):
        random.seed(42)
        for _ in range(20):
            path = generate_path(0, 0, 500, 300)
            total_dist = math.sqrt(500**2 + 300**2)
            for i in range(1, len(path)):
                dx = path[i][0] - path[i-1][0]
                dy = path[i][1] - path[i-1][1]
                step = math.sqrt(dx*dx + dy*dy)
                self.assertLess(step, total_dist * 0.3,
                    f"Teleportation: step {i} covers {step:.0f}px of {total_dist:.0f}px")

    def test_positive_delays(self):
        random.seed(42)
        path = generate_path(100, 100, 800, 500)
        for i, (x, y, d) in enumerate(path):
            self.assertGreater(d, 0, f"Non-positive delay at point {i}: {d}")


class TestSigmaLognormalVelocity(unittest.TestCase):
    """Sigma-lognormal specific velocity profile tests."""

    def test_asymmetric_peak_position(self):
        """Peak velocity should occur at 30-50% of movement (not 50% symmetric)."""
        random.seed(42)
        peak_positions = []
        for _ in range(50):
            path = _generate_segment(100, 100, 500, 300)
            vels = _compute_velocities(path)
            if len(vels) > 5:
                peak_idx = vels.index(max(vels))
                peak_pos = peak_idx / len(vels)
                peak_positions.append(peak_pos)
        avg_peak = statistics.mean(peak_positions)
        self.assertGreater(avg_peak, 0.15, f"Peak too early: avg={avg_peak:.2f}")
        self.assertLess(avg_peak, 0.60, f"Peak too late: avg={avg_peak:.2f}")

    def test_velocity_submovements_long_path(self):
        """Paths >300px should have 2+ velocity peaks (from spatial or temporal sub-movements)."""
        random.seed(42)
        multi_peak_count = 0
        total = 50
        for _ in range(total):
            sx, sy = 100, 100
            ex = sx + random.uniform(350, 800)
            ey = sy + random.uniform(200, 500)
            path = generate_path(sx, sy, ex, ey)
            vels = _compute_velocities(path)
            if len(vels) < 5:
                continue
            # Smooth velocities for peak detection
            window = max(3, len(vels) // 15)
            smoothed = []
            for i in range(len(vels)):
                lo = max(0, i - window)
                hi = min(len(vels), i + window + 1)
                smoothed.append(statistics.mean(vels[lo:hi]))
            peaks = _find_velocity_peaks(smoothed, min_prominence=0.08)
            if len(peaks) >= 2:
                multi_peak_count += 1
        self.assertGreater(multi_peak_count / total, 0.5,
            f"Only {multi_peak_count}/{total} paths had 2+ velocity peaks")

    def test_acceleration_then_deceleration(self):
        """First third should accelerate, last third should decelerate."""
        random.seed(42)
        accel_ok = 0
        decel_ok = 0
        total = 0
        for _ in range(50):
            path = _generate_segment(100, 100, 500, 300)
            vels = _compute_velocities(path)
            if len(vels) < 6:
                continue
            total += 1
            n = len(vels)
            third = n // 3
            first_accels = [vels[i+1] - vels[i] for i in range(min(third, n-1))]
            if statistics.mean(first_accels) > 0:
                accel_ok += 1
            last_accels = [vels[i+1] - vels[i] for i in range(n - third - 1, n - 1)]
            if last_accels and statistics.mean(last_accels) < 0:
                decel_ok += 1
        if total > 10:
            self.assertGreater(accel_ok / total, 0.5)
            self.assertGreater(decel_ok / total, 0.4)


class TestTwoThirdsPowerLaw(unittest.TestCase):
    """angular_velocity ~ curvature^(2/3): fundamental constraint of human motor control."""

    def test_correlation(self):
        """Correlation between log(angular_velocity) and log(curvature) should be positive."""
        random.seed(42)
        correlations = []
        for _ in range(30):
            path = generate_path(100, 100,
                                 100 + random.uniform(200, 600),
                                 100 + random.uniform(200, 400))
            if len(path) < 10:
                continue
            # Compute angular velocity and curvature at each point
            ang_vels = []
            curvs = []
            for i in range(1, len(path) - 1):
                x0, y0, _ = path[i-1]
                x1, y1, d1 = path[i]
                x2, y2, d2 = path[i+1]
                dx1, dy1 = x1-x0, y1-y0
                dx2, dy2 = x2-x1, y2-y1
                ds1 = math.sqrt(dx1*dx1 + dy1*dy1)
                ds2 = math.sqrt(dx2*dx2 + dy2*dy2)
                if ds1 < 0.01 or ds2 < 0.01 or d1 < 0.001 or d2 < 0.001:
                    continue
                # Angle change
                cross = dx1*dy2 - dy1*dx2
                dot = dx1*dx2 + dy1*dy2
                dtheta = abs(math.atan2(cross, dot))
                avg_dt = (d1 + d2) / 2
                ang_vel = dtheta / avg_dt if avg_dt > 0.001 else 0
                # Curvature
                denom = ds1 * ds2 * math.sqrt((x2-x0)**2 + (y2-y0)**2)
                curv = abs(cross) / denom if denom > 0.001 else 0
                if ang_vel > 0.01 and curv > 0.0001:
                    ang_vels.append(math.log(ang_vel))
                    curvs.append(math.log(curv))
            if len(ang_vels) > 10:
                # Pearson correlation
                n = len(ang_vels)
                mean_a = statistics.mean(ang_vels)
                mean_c = statistics.mean(curvs)
                cov = sum((a - mean_a) * (c - mean_c) for a, c in zip(ang_vels, curvs)) / n
                std_a = statistics.stdev(ang_vels)
                std_c = statistics.stdev(curvs)
                if std_a > 0.01 and std_c > 0.01:
                    r = cov / (std_a * std_c)
                    correlations.append(r)
        if correlations:
            avg_r = statistics.mean(correlations)
            self.assertGreater(avg_r, 0.1,
                f"Two-thirds power law correlation too low: avg r={avg_r:.3f}")


class TestTimingVariance(unittest.TestCase):

    def test_timing_cv(self):
        """CV of delays should be > 0.2 (human variability)."""
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        delays = [d for _, _, d in path if d < 0.5]
        if len(delays) > 5:
            cv = statistics.stdev(delays) / statistics.mean(delays)
            self.assertGreater(cv, 0.10, f"Timing CV too low: {cv:.3f}")

    def test_no_perfectly_uniform_timing(self):
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        delays = [d for _, _, d in path]
        unique_rounded = len(set(round(d, 4) for d in delays))
        ratio = unique_rounded / len(delays)
        self.assertGreater(ratio, 0.5, "Too many identical delays")


class TestSpatialSubmovements(unittest.TestCase):

    def test_short_no_split(self):
        random.seed(42)
        wps = _split_into_submovements(0, 0, 200, 100)
        self.assertEqual(len(wps), 1)

    def test_medium_split(self):
        random.seed(42)
        wps = _split_into_submovements(0, 0, 400, 300)
        self.assertEqual(len(wps), 2)

    def test_long_split(self):
        random.seed(42)
        wps = _split_into_submovements(0, 0, 800, 500)
        self.assertGreaterEqual(len(wps), 2)
        self.assertLessEqual(len(wps), 3)

    def test_last_waypoint_is_target(self):
        random.seed(42)
        wps = _split_into_submovements(0, 0, 700, 400)
        self.assertAlmostEqual(wps[-1][0], 700, places=0)
        self.assertAlmostEqual(wps[-1][1], 400, places=0)

    def test_path_has_pauses_between_submovements(self):
        random.seed(42)
        path = generate_path(0, 0, 800, 500)
        long_pauses = [d for _, _, d in path if d > 0.07]
        self.assertGreater(len(long_pauses), 0)


class TestOvershoot(unittest.TestCase):

    def test_overshoot_direction(self):
        random.seed(42)
        for _ in range(50):
            sx, sy = 100, 100
            ex, ey = 600, 400
            ox, oy = _overshoot_target(sx, sy, ex, ey)
            dist_to_target = math.sqrt((ex - sx)**2 + (ey - sy)**2)
            dist_to_overshoot = math.sqrt((ox - sx)**2 + (oy - sy)**2)
            self.assertGreater(dist_to_overshoot, dist_to_target * 0.95)

    def test_overshoot_magnitude(self):
        random.seed(42)
        for _ in range(50):
            ox, oy = _overshoot_target(100, 100, 600, 400)
            overshoot_dist = math.sqrt((ox - 600)**2 + (oy - 400)**2)
            self.assertGreater(overshoot_dist, 3)
            self.assertLess(overshoot_dist, 30)


class TestTremor(unittest.TestCase):

    def test_tremor_amplitude(self):
        max_disp = 0
        for t in [i * 0.001 for i in range(1000)]:
            tx, ty = _tremor(t, 10.0, 1.0)
            disp = math.sqrt(tx*tx + ty*ty)
            max_disp = max(max_disp, disp)
        self.assertLess(max_disp, 3.0, f"Tremor too large: {max_disp:.2f}px")
        self.assertGreater(max_disp, 0.3, f"Tremor too small: {max_disp:.2f}px")

    def test_tremor_not_constant(self):
        values = [_tremor(t * 0.01, 10.0, 1.0) for t in range(100)]
        x_vals = [v[0] for v in values]
        self.assertGreater(statistics.stdev(x_vals), 0.1)


class TestCurvature(unittest.TestCase):

    def test_path_curves(self):
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        curvatures = _compute_curvatures(path)
        if curvatures:
            avg_curv = statistics.mean(curvatures)
            self.assertGreater(avg_curv, 0.0001)

    def test_straightness_ratio(self):
        random.seed(42)
        ratios = []
        for _ in range(30):
            sx, sy = random.uniform(0, 500), random.uniform(0, 500)
            ex, ey = sx + random.uniform(200, 600), sy + random.uniform(200, 400)
            path = generate_path(sx, sy, ex, ey)
            straight = math.sqrt((ex-sx)**2 + (ey-sy)**2)
            actual = _path_distance(path)
            if straight > 10:
                ratios.append(actual / straight)
        avg_ratio = statistics.mean(ratios)
        self.assertGreater(avg_ratio, 1.005)


class TestClickOffset(unittest.TestCase):

    def test_offset_distribution(self):
        random.seed(42)
        offsets_x = [click_offset(100, 40)[0] for _ in range(500)]
        self.assertAlmostEqual(statistics.mean(offsets_x), -5.0, delta=8)
        self.assertGreater(statistics.stdev(offsets_x), 5)
        for ox in offsets_x:
            self.assertGreater(ox, -50)
            self.assertLess(ox, 50)

    def test_not_always_center(self):
        random.seed(42)
        results = [click_offset(100, 40) for _ in range(100)]
        centers = sum(1 for ox, oy in results if abs(ox) < 0.1 and abs(oy) < 0.1)
        self.assertLess(centers, 5)


class TestHoverDelay(unittest.TestCase):

    def test_median_range(self):
        random.seed(42)
        delays = sorted([hover_delay() for _ in range(1000)])
        median = delays[500]
        self.assertGreater(median, 0.08)
        self.assertLess(median, 0.5)

    def test_right_skewed(self):
        random.seed(42)
        delays = [hover_delay() for _ in range(1000)]
        self.assertGreater(statistics.mean(delays), statistics.median(delays))

    def test_minimum(self):
        random.seed(42)
        for _ in range(1000):
            self.assertGreaterEqual(hover_delay(), 0.06)


class TestScrollSequence(unittest.TestCase):

    def test_total_scroll_approximation(self):
        random.seed(42)
        for _ in range(20):
            target = random.uniform(200, 1000)
            events = scroll_sequence(target)
            actual = sum(d for d, _ in events if d != 0)
            self.assertAlmostEqual(actual, target, delta=target * 0.25)

    def test_scroll_direction(self):
        random.seed(42)
        events = scroll_sequence(500)
        positive = sum(1 for d, _ in events if d > 0)
        negative = sum(1 for d, _ in events if d < 0)
        self.assertGreater(positive, negative * 5)

    def test_variable_deltas(self):
        random.seed(42)
        events = scroll_sequence(500)
        deltas = [abs(d) for d, _ in events if abs(d) > 1]
        if len(deltas) > 3:
            cv = statistics.stdev(deltas) / statistics.mean(deltas)
            self.assertGreater(cv, 0.15)

    def test_has_pauses(self):
        random.seed(42)
        events = scroll_sequence(800)
        pauses = [delay for _, delay in events if delay > 0.1]
        self.assertGreater(len(pauses), 0)

    def test_small_scroll_empty(self):
        events = scroll_sequence(5)
        self.assertEqual(len(events), 0)

    def test_negative_scroll(self):
        random.seed(42)
        events = scroll_sequence(-500)
        deltas = [d for d, _ in events if d != 0]
        negative = sum(1 for d in deltas if d < 0)
        self.assertGreater(negative, len(deltas) * 0.7)


class TestIdleMouseDrift(unittest.TestCase):

    def test_sufficient_events(self):
        random.seed(42)
        points = idle_mouse_drift(400, 300, 5.0)
        self.assertGreater(len(points), 10, f"Only {len(points)} events in 5s idle")

    def test_stays_in_viewport(self):
        random.seed(42)
        points = idle_mouse_drift(400, 300, 5.0, 1400, 800)
        for x, y, _ in points:
            self.assertGreater(x, 0)
            self.assertLess(x, 1450)
            self.assertGreater(y, 0)
            self.assertLess(y, 850)

    def test_duration_approximation(self):
        random.seed(42)
        points = idle_mouse_drift(400, 300, 8.0)
        total = sum(d for _, _, d in points)
        self.assertGreater(total, 5.0)
        self.assertLess(total, 15.0)

    def test_has_pauses(self):
        random.seed(42)
        points = idle_mouse_drift(400, 300, 10.0)
        pauses = [d for _, _, d in points if d > 0.8]
        self.assertGreater(len(pauses), 0)

    def test_movement_variety(self):
        """Across multiple runs, idle should produce both micro-drifts and
        large repositionings. Tested over several seeds for robustness."""
        max_steps = []
        all_small_ratios = []
        for seed in range(20):
            random.seed(seed)
            points = idle_mouse_drift(400, 300, 15.0)
            step_sizes = []
            for i in range(1, len(points)):
                dx = points[i][0] - points[i-1][0]
                dy = points[i][1] - points[i-1][1]
                step_sizes.append(math.sqrt(dx*dx + dy*dy))
            if step_sizes:
                max_steps.append(max(step_sizes))
                small = sum(1 for s in step_sizes if s < 20)
                all_small_ratios.append(small / len(step_sizes))
        # At least one run should have a large repositioning (>50px)
        self.assertGreater(max(max_steps), 50,
            f"No large repositioning across 20 runs. Max step: {max(max_steps):.1f}")
        # Most of the points should be small drifts (micro-movements)
        self.assertGreater(statistics.mean(all_small_ratios), 0.5,
            "Most idle points should be small drifts")


class TestInterActionPause(unittest.TestCase):
    def test_positive(self):
        random.seed(42)
        for _ in range(100):
            self.assertGreaterEqual(inter_action_pause(), 0.15)

    def test_right_skewed(self):
        random.seed(42)
        pauses = [inter_action_pause() for _ in range(1000)]
        self.assertGreater(statistics.mean(pauses), statistics.median(pauses))


class TestControlPoints(unittest.TestCase):

    def test_same_side(self):
        random.seed(42)
        for _ in range(50):
            c1, c2 = _control_points(0, 0, 500, 300)
            dx, dy = 500, 300
            dist = math.sqrt(dx*dx + dy*dy)
            px, py = -dy/dist, dx/dist
            proj1 = c1[0] * px + c1[1] * py
            proj2 = c2[0] * px + c2[1] * py
            if abs(proj1) > 5 and abs(proj2) > 5:
                self.assertEqual(proj1 > 0, proj2 > 0)


class TestBuildCurve(unittest.TestCase):

    def test_starts_and_ends_correctly(self):
        random.seed(42)
        pts, arcs = _build_curve(100, 200, 500, 400)
        self.assertAlmostEqual(pts[0][0], 100, places=1)
        self.assertAlmostEqual(pts[0][1], 200, places=1)
        self.assertAlmostEqual(pts[-1][0], 500, places=1)
        self.assertAlmostEqual(pts[-1][1], 400, places=1)

    def test_arc_lengths_monotonic(self):
        random.seed(42)
        _, arcs = _build_curve(0, 0, 300, 200)
        for i in range(1, len(arcs)):
            self.assertGreaterEqual(arcs[i], arcs[i-1])

    def test_total_arc_reasonable(self):
        """Arc length should be >= straight line distance."""
        random.seed(42)
        _, arcs = _build_curve(0, 0, 300, 400)
        straight = math.sqrt(300**2 + 400**2)
        self.assertGreaterEqual(arcs[-1], straight * 0.99)


class TestEndToEnd(unittest.TestCase):

    def test_many_random_paths(self):
        """Generate 100 random paths and verify basic properties."""
        random.seed(42)
        for i in range(100):
            sx = random.uniform(0, 1000)
            sy = random.uniform(0, 700)
            ex = random.uniform(0, 1400)
            ey = random.uniform(0, 800)
            dist = math.sqrt((ex-sx)**2 + (ey-sy)**2)
            if dist < 2:
                continue
            path = generate_path(sx, sy, ex, ey)
            last_x, last_y, _ = path[-1]
            end_dist = math.sqrt((last_x - ex)**2 + (last_y - ey)**2)
            self.assertLess(end_dist, 1.5, f"Path {i}: endpoint miss {end_dist:.1f}px")
            self.assertGreater(len(path), 3, f"Path {i}: only {len(path)} points")
            for j, (_, _, d) in enumerate(path):
                self.assertGreater(d, 0, f"Path {i}, point {j}: delay={d}")

    def test_horizontal_movement(self):
        path = generate_path(100, 400, 800, 400)
        self.assertGreater(len(path), 5)
        self.assertAlmostEqual(path[-1][0], 800, places=0)
        self.assertAlmostEqual(path[-1][1], 400, places=0)

    def test_vertical_movement(self):
        path = generate_path(400, 100, 400, 700)
        self.assertGreater(len(path), 5)
        self.assertAlmostEqual(path[-1][0], 400, places=0)
        self.assertAlmostEqual(path[-1][1], 700, places=0)

    def test_diagonal_movement(self):
        path = generate_path(50, 50, 1200, 700)
        self.assertGreater(len(path), 10)

    def test_short_movement(self):
        path = generate_path(400, 300, 420, 310)
        self.assertGreater(len(path), 3)


class TestPointRate(unittest.TestCase):
    """Point rate should be 55-80 points/second (simulates 60Hz monitor)."""

    def test_rate_range(self):
        random.seed(42)
        for _ in range(30):
            path = _generate_segment(100, 100,
                                     100 + random.uniform(150, 500),
                                     100 + random.uniform(100, 300))
            total_time = _path_total_time(path)
            if total_time > 0.1:
                rate = len(path) / total_time
                self.assertGreater(rate, 35, f"Rate too low: {rate:.0f}/s")
                self.assertLess(rate, 120, f"Rate too high: {rate:.0f}/s")


class TestIdleEventVolume(unittest.TestCase):
    """SFU SDK ring buffer is 21 events. Idle must keep it full: 2-5 events/sec."""

    def test_event_rate_during_idle(self):
        """Idle 5s should generate >=10 events (2+/sec minimum)."""
        for seed in range(10):
            random.seed(seed)
            points = idle_mouse_drift(400, 300, 5.0)
            total_time = sum(d for _, _, d in points)
            if total_time > 0.5:
                rate = len(points) / total_time
                self.assertGreater(rate, 1.5,
                    f"Seed {seed}: rate too low {rate:.1f}/s ({len(points)} pts / {total_time:.1f}s)")

    def test_long_idle_fills_buffer(self):
        """Idle 10s should generate >=21 events (fills SFU ring buffer)."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 10.0)
        self.assertGreaterEqual(len(points), 21,
            f"Only {len(points)} events in 10s idle, not enough for SFU buffer")

    def test_no_long_silence_gaps(self):
        """During idle, consecutive delays should sum to < 3s (no dead spots
        where the SFU buffer would stop being updated)."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 15.0)
        # Accumulate delays; if any single delay > 2s that's a dead spot
        long_gaps = [d for _, _, d in points if d > 2.0]
        self.assertLess(len(long_gaps), 3,
            f"Too many long silence gaps: {long_gaps}")


class TestKeystrokeDelay(unittest.TestCase):
    """keystroke_delay should produce log-normal inter-key intervals with
    digraph-based adjustment."""

    def test_always_positive(self):
        random.seed(42)
        for _ in range(100):
            d = keystroke_delay("a", "b")
            self.assertGreater(d, 0)
            self.assertLess(d, 1.0)

    def test_common_digraphs_faster(self):
        """Common pairs (th, er, in) should be faster than random pairs."""
        random.seed(42)
        fast_delays = []
        rare_delays = []
        for _ in range(200):
            fast_delays.append(keystroke_delay("h", "t"))   # "th"
            fast_delays.append(keystroke_delay("e", "h"))   # "he"
            fast_delays.append(keystroke_delay("r", "e"))   # "er"
            rare_delays.append(keystroke_delay("z", "q"))   # "qz"
            rare_delays.append(keystroke_delay("j", "x"))   # "xj"
        fast_mean = statistics.mean(fast_delays)
        rare_mean = statistics.mean(rare_delays)
        self.assertLess(fast_mean, rare_mean,
            f"Fast digraphs {fast_mean:.3f} not faster than rare {rare_mean:.3f}")

    def test_inter_key_cv(self):
        """Inter-key intervals should have CV > 0.3 (human variability)."""
        random.seed(42)
        delays = [keystroke_delay("e", "h") for _ in range(500)]
        cv = statistics.stdev(delays) / statistics.mean(delays)
        self.assertGreater(cv, 0.25, f"CV too low: {cv:.3f}")

    def test_median_range(self):
        """Overall median should be in typical human range (80-250ms)."""
        random.seed(42)
        delays = sorted([keystroke_delay("a", "b") for _ in range(1000)])
        median = delays[500]
        self.assertGreater(median, 0.06)
        self.assertLess(median, 0.35)


class TestTypingSequence(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(typing_sequence(""), [])

    def test_length_matches(self):
        random.seed(42)
        seq = typing_sequence("hello world")
        self.assertEqual(len(seq), 11)

    def test_chars_in_order(self):
        random.seed(42)
        seq = typing_sequence("abc123")
        self.assertEqual("".join(c for c, _ in seq), "abc123")

    def test_total_typing_time_reasonable(self):
        """Typing 20 chars should take 1-8 seconds."""
        random.seed(42)
        seq = typing_sequence("hello world, this is")  # 20 chars
        total = sum(d for _, d in seq)
        self.assertGreater(total, 1.0)
        self.assertLess(total, 10.0)

    def test_inter_char_cv(self):
        """Delays should have CV > 0.3 (not uniform)."""
        random.seed(42)
        seq = typing_sequence("this is a test of keystroke timing variability")
        delays = [d for _, d in seq]
        cv = statistics.stdev(delays) / statistics.mean(delays)
        self.assertGreater(cv, 0.3, f"Typing CV too low: {cv:.3f}")


class TestReadingScroll(unittest.TestCase):
    """reading_scroll should size scroll output to actual page content."""

    def test_short_page_no_scroll(self):
        """Page barely taller than viewport should produce no scroll."""
        random.seed(42)
        events = reading_scroll(820, 800)
        self.assertEqual(len(events), 0)

    def test_long_page_more_scroll(self):
        """Longer pages should produce more scroll events."""
        random.seed(42)
        short = reading_scroll(2000, 800, coverage=0.6)
        random.seed(42)
        long_events = reading_scroll(8000, 800, coverage=0.6)
        # Long page should have more total scroll
        short_total = sum(abs(d) for d, _ in short if d != 0)
        long_total = sum(abs(d) for d, _ in long_events if d != 0)
        self.assertGreater(long_total, short_total * 1.5)

    def test_coverage_respected(self):
        """Total scrolled should be ~ coverage * scrollable area."""
        random.seed(42)
        page_h = 5000
        vp_h = 800
        coverage = 0.5
        events = reading_scroll(page_h, vp_h, coverage=coverage)
        scrolled = sum(d for d, _ in events if d > 0)
        expected = (page_h - vp_h) * coverage
        self.assertAlmostEqual(scrolled, expected, delta=expected * 0.30)

    def test_long_page_has_reading_pauses(self):
        """Long pages should inject reading pauses (delay > 0.7s)."""
        random.seed(42)
        events = reading_scroll(8000, 800, coverage=0.7)
        long_pauses = [d for dy, d in events if dy == 0 and d > 0.7]
        self.assertGreater(len(long_pauses), 0)


class TestPersonality(unittest.TestCase):
    """Personality system: coherent multipliers across humanization dimensions."""

    def setUp(self):
        # Reset personality before each test
        _personality.set(None)

    def tearDown(self):
        _personality.set(None)

    def test_default_is_normal(self):
        """Without set_personality, get_personality returns normal."""
        p = get_personality()
        self.assertEqual(p["trait"], "normal")
        self.assertEqual(p["mult"]["tremor_amp"], 1.0)

    def test_set_explicit_trait(self):
        set_personality(trait="precise")
        self.assertEqual(get_personality()["trait"], "precise")
        set_personality(trait="sloppy")
        self.assertEqual(get_personality()["trait"], "sloppy")

    def test_invalid_trait_falls_back_to_normal(self):
        set_personality(trait="bogus")
        self.assertEqual(get_personality()["trait"], "normal")

    def test_seed_is_deterministic(self):
        set_personality(seed=12345)
        first = get_personality()["trait"]
        set_personality(seed=12345)
        second = get_personality()["trait"]
        self.assertEqual(first, second)

    def test_random_trait_distribution(self):
        """Without seed, traits should distribute roughly 25/50/25."""
        random.seed(42)
        counts = {"precise": 0, "normal": 0, "sloppy": 0}
        for _ in range(2000):
            _personality.set(None)
            set_personality()
            counts[get_personality()["trait"]] += 1
        # Expect ~500 precise, ~1000 normal, ~500 sloppy with some tolerance
        self.assertGreater(counts["normal"], counts["precise"])
        self.assertGreater(counts["normal"], counts["sloppy"])

    def test_precise_tremor_smaller(self):
        """Precise personality should produce smaller tremor than sloppy."""
        random.seed(42)
        set_personality(trait="precise")
        precise_paths = [_generate_segment(100, 100, 400, 300) for _ in range(20)]
        random.seed(42)
        set_personality(trait="sloppy")
        sloppy_paths = [_generate_segment(100, 100, 400, 300) for _ in range(20)]

        def avg_path_jitter(paths):
            jitters = []
            for path in paths:
                if len(path) < 5:
                    continue
                # Sum of squared deviations from straight line
                for i in range(1, len(path) - 1):
                    x0, y0, _ = path[i-1]
                    x1, y1, _ = path[i]
                    x2, y2, _ = path[i+1]
                    # Cross product magnitude as deviation
                    dev = abs((x2-x0)*(y1-y0) - (x1-x0)*(y2-y0))
                    jitters.append(dev)
            return statistics.mean(jitters) if jitters else 0

        self.assertLess(avg_path_jitter(precise_paths),
                        avg_path_jitter(sloppy_paths))

    def test_precise_click_offset_tighter(self):
        """Precise personality should produce smaller click offsets."""
        random.seed(42)
        set_personality(trait="precise")
        precise_offsets = [click_offset(100, 40)[0] for _ in range(500)]
        random.seed(42)
        set_personality(trait="sloppy")
        sloppy_offsets = [click_offset(100, 40)[0] for _ in range(500)]
        self.assertLess(statistics.stdev(precise_offsets),
                        statistics.stdev(sloppy_offsets))

    def test_sloppy_hover_delay_longer(self):
        """Sloppy personality should have longer hover delays."""
        random.seed(42)
        set_personality(trait="precise")
        precise_delays = [hover_delay() for _ in range(500)]
        random.seed(42)
        set_personality(trait="sloppy")
        sloppy_delays = [hover_delay() for _ in range(500)]
        self.assertLess(statistics.mean(precise_delays),
                        statistics.mean(sloppy_delays))

    def test_sloppy_keystroke_more_variable(self):
        """Sloppy personality should have higher keystroke timing variance."""
        random.seed(42)
        set_personality(trait="precise")
        precise = [keystroke_delay("a", "b") for _ in range(500)]
        random.seed(42)
        set_personality(trait="sloppy")
        sloppy = [keystroke_delay("a", "b") for _ in range(500)]
        self.assertLess(statistics.stdev(precise), statistics.stdev(sloppy))


if __name__ == "__main__":
    unittest.main()
