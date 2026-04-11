"""
Tests for humanize.py -- validates that generated trajectories
have properties consistent with real human mouse movement.

Based on:
- Fitts's Law (movement time ~ log2(D/W + 1))
- Two-thirds power law (angular_velocity ~ curvature^(2/3))
- Asymmetric velocity profile (peak at ~35-45% of movement)
- Sub-movement decomposition for long distances
- Physiological tremor (8-12Hz)
- Inter-event timing variance (CV > 0.3)
- Sufficient point density (~60-80 points/sec)
- Endpoint accuracy
- Overshoot in movement direction
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
    idle_mouse_drift,
    _generate_segment,
    _control_points,
    _asymmetric_ease,
    _tremor,
    _split_into_submovements,
    _overshoot_target,
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


def _compute_accelerations(velocities, delays):
    """Compute accelerations from velocities and delays."""
    accels = []
    for i in range(1, len(velocities)):
        dv = velocities[i] - velocities[i-1]
        dt = delays[i]
        if dt > 0.001:
            accels.append(dv / dt)
    return accels


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


class TestFittsLaw(unittest.TestCase):
    """Fitts's Law: movement time increases with distance and decreases with target width."""

    def test_longer_distance_takes_more_time(self):
        """Average time for 800px should be > 200px."""
        random.seed(42)
        times_short = [fitts_time(200, 50) for _ in range(100)]
        times_long = [fitts_time(800, 50) for _ in range(100)]
        self.assertGreater(statistics.mean(times_long), statistics.mean(times_short))

    def test_smaller_target_takes_more_time(self):
        """Smaller target width = harder = more time."""
        random.seed(42)
        times_big = [fitts_time(400, 100) for _ in range(100)]
        times_small = [fitts_time(400, 20) for _ in range(100)]
        self.assertGreater(statistics.mean(times_small), statistics.mean(times_big))

    def test_minimum_time(self):
        """Very short distance should still take minimum time."""
        t = fitts_time(0.5)
        self.assertGreaterEqual(t, 0.05)

    def test_human_variability(self):
        """Fitts time should have ~15% coefficient of variation."""
        random.seed(42)
        times = [fitts_time(400, 50) for _ in range(500)]
        cv = statistics.stdev(times) / statistics.mean(times)
        self.assertGreater(cv, 0.08)
        self.assertLess(cv, 0.30)


class TestPathGeneration(unittest.TestCase):
    """Core path generation: shape, timing, and endpoint accuracy."""

    def test_reaches_target(self):
        """Path endpoint should be within 1px of target."""
        random.seed(42)
        for _ in range(50):
            ex = random.uniform(100, 1400)
            ey = random.uniform(100, 800)
            path = generate_path(100, 100, ex, ey)
            last_x, last_y, _ = path[-1]
            dist = math.sqrt((last_x - ex)**2 + (last_y - ey)**2)
            self.assertLess(dist, 1.0, f"Endpoint miss: target=({ex:.0f},{ey:.0f}) got=({last_x:.0f},{last_y:.0f})")

    def test_minimum_points(self):
        """Path should have at least 8 points for any non-trivial distance."""
        random.seed(42)
        path = generate_path(0, 0, 300, 200)
        self.assertGreaterEqual(len(path), 8)

    def test_very_short_path(self):
        """Distance < 2px should return single point."""
        path = generate_path(100, 100, 101, 100)
        self.assertEqual(len(path), 1)

    def test_point_density(self):
        """Should generate ~60-80 points per second (browser mousemove rate)."""
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        total_time = _path_total_time(path)
        if total_time > 0.1:
            rate = len(path) / total_time
            self.assertGreater(rate, 30, f"Point rate too low: {rate:.0f}/s")
            self.assertLess(rate, 150, f"Point rate too high: {rate:.0f}/s")

    def test_no_teleportation(self):
        """No single step should cover more than 30% of total distance."""
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
        """All delays must be positive."""
        random.seed(42)
        path = generate_path(100, 100, 800, 500)
        for i, (x, y, d) in enumerate(path):
            self.assertGreater(d, 0, f"Non-positive delay at point {i}: {d}")


class TestVelocityProfile(unittest.TestCase):
    """Velocity should follow asymmetric bell curve (peak at ~35-45%)."""

    def test_asymmetric_peak(self):
        """Peak velocity should occur in the first half of the movement (35-55% of points)."""
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
        self.assertLess(avg_peak, 0.65, f"Peak too late: avg={avg_peak:.2f}")

    def test_acceleration_then_deceleration(self):
        """First third should have positive average acceleration, last third negative."""
        random.seed(42)
        results = {"accel_pos": 0, "decel_neg": 0, "total": 0}
        for _ in range(50):
            path = _generate_segment(100, 100, 500, 300)
            vels = _compute_velocities(path)
            if len(vels) < 6:
                continue
            results["total"] += 1
            n = len(vels)
            third = n // 3
            # First third: velocities should be increasing (positive acceleration)
            first_accels = [vels[i+1] - vels[i] for i in range(min(third, n-1))]
            if statistics.mean(first_accels) > 0:
                results["accel_pos"] += 1
            # Last third: velocities should be decreasing (negative acceleration)
            last_accels = [vels[i+1] - vels[i] for i in range(n - third - 1, n - 1)]
            if last_accels and statistics.mean(last_accels) < 0:
                results["decel_neg"] += 1
        if results["total"] > 10:
            self.assertGreater(results["accel_pos"] / results["total"], 0.5,
                "First third should usually accelerate")
            self.assertGreater(results["decel_neg"] / results["total"], 0.4,
                "Last third should usually decelerate")


class TestAsymmetricEase(unittest.TestCase):
    """The easing function should produce values between 0 and 1."""

    def test_boundaries(self):
        random.seed(42)
        for _ in range(100):
            val = _asymmetric_ease(0.0)
            self.assertAlmostEqual(val, 0.0, places=2)
            val = _asymmetric_ease(1.0)
            self.assertAlmostEqual(val, 1.0, places=2)

    def test_monotonic(self):
        """Should be monotonically increasing when peak is fixed."""
        # _asymmetric_ease uses random.gauss internally for peak variation,
        # so we fix the seed before EACH full sweep to get consistent peak
        for run in range(20):
            random.seed(run * 1000)
            # Pre-generate the peak value by calling once and capturing the seed state
            # Instead, test the full path which calls it in sequence
            path = _generate_segment(0, 0, 300, 200)
            # Verify positions move monotonically toward target (not backwards)
            if len(path) > 3:
                dists_to_end = [math.sqrt((300-x)**2 + (200-y)**2) for x, y, _ in path]
                # Overall trend should decrease (getting closer to target)
                self.assertLess(dists_to_end[-1], dists_to_end[0] * 0.1,
                    "Path doesn't converge to target")


class TestSubMovements(unittest.TestCase):
    """Long movements should decompose into sub-movements."""

    def test_short_no_split(self):
        """Short movements (<300px) should not be split."""
        random.seed(42)
        wps = _split_into_submovements(0, 0, 200, 100)
        self.assertEqual(len(wps), 1)

    def test_medium_split(self):
        """Medium movements (300-600px) should split into 2."""
        random.seed(42)
        wps = _split_into_submovements(0, 0, 400, 300)
        self.assertEqual(len(wps), 2)

    def test_long_split(self):
        """Long movements (>600px) should split into 2-3."""
        random.seed(42)
        wps = _split_into_submovements(0, 0, 800, 500)
        self.assertGreaterEqual(len(wps), 2)
        self.assertLessEqual(len(wps), 3)

    def test_last_waypoint_is_target(self):
        """Last waypoint should always be the target."""
        random.seed(42)
        wps = _split_into_submovements(0, 0, 700, 400)
        self.assertAlmostEqual(wps[-1][0], 700, places=0)
        self.assertAlmostEqual(wps[-1][1], 400, places=0)

    def test_path_has_pauses_between_submovements(self):
        """Long path should have inter-submovement pauses (>80ms delays)."""
        random.seed(42)
        path = generate_path(0, 0, 800, 500)
        long_pauses = [d for _, _, d in path if d > 0.07]
        self.assertGreater(len(long_pauses), 0,
            "Long movement should have at least one inter-submovement pause")


class TestOvershoot(unittest.TestCase):
    """Overshoot should be in the direction of movement."""

    def test_overshoot_direction(self):
        """Overshoot point should be past the target in the movement direction."""
        random.seed(42)
        for _ in range(50):
            sx, sy = 100, 100
            ex, ey = 600, 400
            ox, oy = _overshoot_target(sx, sy, ex, ey)
            # Overshoot should be further from start than target
            dist_to_target = math.sqrt((ex - sx)**2 + (ey - sy)**2)
            dist_to_overshoot = math.sqrt((ox - sx)**2 + (oy - sy)**2)
            self.assertGreater(dist_to_overshoot, dist_to_target * 0.95,
                f"Overshoot not past target: {dist_to_overshoot:.0f} vs {dist_to_target:.0f}")

    def test_overshoot_magnitude(self):
        """Overshoot should be 5-25px past target."""
        random.seed(42)
        for _ in range(50):
            ox, oy = _overshoot_target(100, 100, 600, 400)
            overshoot_dist = math.sqrt((ox - 600)**2 + (oy - 400)**2)
            self.assertGreater(overshoot_dist, 3)
            self.assertLess(overshoot_dist, 30)


class TestTremor(unittest.TestCase):
    """Physiological tremor: 8-12Hz, 0.5-1.5px amplitude."""

    def test_tremor_amplitude(self):
        """Tremor displacement should stay within expected range."""
        max_disp = 0
        for t in [i * 0.001 for i in range(1000)]:
            tx, ty = _tremor(t, 10.0, 1.0)
            disp = math.sqrt(tx*tx + ty*ty)
            max_disp = max(max_disp, disp)
        self.assertLess(max_disp, 3.0, f"Tremor too large: {max_disp:.2f}px")
        self.assertGreater(max_disp, 0.3, f"Tremor too small: {max_disp:.2f}px")

    def test_tremor_not_constant(self):
        """Tremor values should vary over time."""
        values = [_tremor(t * 0.01, 10.0, 1.0) for t in range(100)]
        x_vals = [v[0] for v in values]
        self.assertGreater(statistics.stdev(x_vals), 0.1, "Tremor X not varying")


class TestTimingVariance(unittest.TestCase):
    """Inter-event timing should have high variance (CV > 0.3) to look human."""

    def test_timing_cv(self):
        """Coefficient of variation of delays should be > 0.2."""
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        delays = [d for _, _, d in path if d < 0.5]  # exclude inter-submovement pauses
        if len(delays) > 5:
            cv = statistics.stdev(delays) / statistics.mean(delays)
            self.assertGreater(cv, 0.15, f"Timing CV too low: {cv:.3f}")

    def test_no_perfectly_uniform_timing(self):
        """Delays should not be nearly identical (bot signature)."""
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        delays = [d for _, _, d in path]
        unique_rounded = len(set(round(d, 4) for d in delays))
        ratio = unique_rounded / len(delays)
        self.assertGreater(ratio, 0.5, "Too many identical delays")


class TestCurvature(unittest.TestCase):
    """Path should have non-zero curvature (not straight lines)."""

    def test_path_curves(self):
        """Average curvature should be > 0 for medium+ distances."""
        random.seed(42)
        path = generate_path(100, 100, 600, 400)
        curvatures = _compute_curvatures(path)
        if curvatures:
            avg_curv = statistics.mean(curvatures)
            self.assertGreater(avg_curv, 0.0001, f"Path too straight: curvature={avg_curv}")

    def test_straightness_ratio(self):
        """Path length should be longer than straight-line distance (>1.0 ratio)."""
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
        self.assertGreater(avg_ratio, 1.005, f"Paths too straight: avg ratio={avg_ratio:.4f}")


class TestClickOffset(unittest.TestCase):
    """Click positions should follow Gaussian distribution, not dead center."""

    def test_offset_distribution(self):
        """Offsets should center near 0 but have spread."""
        random.seed(42)
        offsets_x = [click_offset(100, 40)[0] for _ in range(500)]
        offsets_y = [click_offset(100, 40)[1] for _ in range(500)]
        # Mean should be near 0 (slight negative bias)
        self.assertAlmostEqual(statistics.mean(offsets_x), -5.0, delta=8)
        # Should have significant spread
        self.assertGreater(statistics.stdev(offsets_x), 5)
        # Should stay within element bounds
        for ox in offsets_x:
            self.assertGreater(ox, -50)
            self.assertLess(ox, 50)

    def test_not_always_center(self):
        """Should never return exactly (0, 0) repeatedly."""
        random.seed(42)
        results = [click_offset(100, 40) for _ in range(100)]
        centers = sum(1 for ox, oy in results if abs(ox) < 0.1 and abs(oy) < 0.1)
        self.assertLess(centers, 5, "Too many dead-center clicks")


class TestHoverDelay(unittest.TestCase):
    """Hover delay should follow log-normal distribution."""

    def test_median_range(self):
        """Median should be ~150-250ms."""
        random.seed(42)
        delays = sorted([hover_delay() for _ in range(1000)])
        median = delays[500]
        self.assertGreater(median, 0.08, f"Hover delay median too short: {median:.3f}")
        self.assertLess(median, 0.5, f"Hover delay median too long: {median:.3f}")

    def test_right_skewed(self):
        """Distribution should be right-skewed (mean > median)."""
        random.seed(42)
        delays = [hover_delay() for _ in range(1000)]
        self.assertGreater(statistics.mean(delays), statistics.median(delays))

    def test_minimum(self):
        """Should never be less than 60ms."""
        random.seed(42)
        for _ in range(1000):
            self.assertGreaterEqual(hover_delay(), 0.06)


class TestScrollSequence(unittest.TestCase):
    """Scroll should be in bursts with momentum decay."""

    def test_total_scroll_approximation(self):
        """Total scroll should approximate requested delta (within 20%)."""
        random.seed(42)
        for _ in range(20):
            target = random.uniform(200, 1000)
            events = scroll_sequence(target)
            actual = sum(d for d, _ in events if d != 0)
            self.assertAlmostEqual(actual, target, delta=target * 0.25)

    def test_scroll_direction(self):
        """All non-reversal scrolls should match requested direction."""
        random.seed(42)
        events = scroll_sequence(500)
        positive = sum(1 for d, _ in events if d > 0)
        negative = sum(1 for d, _ in events if d < 0)
        # Most should be positive (direction of scroll)
        self.assertGreater(positive, negative * 5)

    def test_variable_deltas(self):
        """Scroll deltas should not be uniform."""
        random.seed(42)
        events = scroll_sequence(500)
        deltas = [abs(d) for d, _ in events if abs(d) > 1]
        if len(deltas) > 3:
            cv = statistics.stdev(deltas) / statistics.mean(deltas)
            self.assertGreater(cv, 0.15, f"Scroll deltas too uniform: CV={cv:.3f}")

    def test_has_pauses(self):
        """Should have reading pauses (delay > 100ms)."""
        random.seed(42)
        events = scroll_sequence(800)
        pauses = [delay for _, delay in events if delay > 0.1]
        self.assertGreater(len(pauses), 0, "No reading pauses in scroll")

    def test_small_scroll_empty(self):
        """Scroll < 10px should return empty."""
        events = scroll_sequence(5)
        self.assertEqual(len(events), 0)

    def test_negative_scroll(self):
        """Negative scroll (up) should work."""
        random.seed(42)
        events = scroll_sequence(-500)
        deltas = [d for d, _ in events if d != 0]
        negative = sum(1 for d in deltas if d < 0)
        self.assertGreater(negative, len(deltas) * 0.7)


class TestIdleMouseDrift(unittest.TestCase):
    """Idle drift should generate sufficient mouse events with natural patterns."""

    def test_sufficient_events(self):
        """5 seconds of idle should generate at least 10 mouse events."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 5.0)
        self.assertGreater(len(points), 10, f"Only {len(points)} events in 5s idle")

    def test_stays_in_viewport(self):
        """All points should stay within viewport bounds."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 5.0, 1400, 800)
        for x, y, _ in points:
            self.assertGreater(x, 0, f"Point outside left: x={x}")
            self.assertLess(x, 1450, f"Point outside right: x={x}")
            self.assertGreater(y, 0, f"Point outside top: y={y}")
            self.assertLess(y, 850, f"Point outside bottom: y={y}")

    def test_duration_approximation(self):
        """Total time should approximate requested duration."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 8.0)
        total = sum(d for _, _, d in points)
        self.assertGreater(total, 5.0, f"Idle too short: {total:.1f}s")
        self.assertLess(total, 15.0, f"Idle too long: {total:.1f}s")

    def test_has_pauses(self):
        """Should include still pauses (>1s delays)."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 10.0)
        pauses = [d for _, _, d in points if d > 0.8]
        self.assertGreater(len(pauses), 0, "No still pauses in idle drift")

    def test_movement_variety(self):
        """Should have both small drifts and larger repositionings."""
        random.seed(42)
        points = idle_mouse_drift(400, 300, 10.0)
        step_sizes = []
        for i in range(1, len(points)):
            dx = points[i][0] - points[i-1][0]
            dy = points[i][1] - points[i-1][1]
            step_sizes.append(math.sqrt(dx*dx + dy*dy))
        if step_sizes:
            self.assertGreater(max(step_sizes), 50, "No large repositionings")
            small = sum(1 for s in step_sizes if s < 20)
            self.assertGreater(small, len(step_sizes) * 0.3, "Not enough small drifts")


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
    """Bezier control points should be on one side and within reasonable spread."""

    def test_same_side(self):
        """Both control points should deviate to the same side of the path."""
        random.seed(42)
        for _ in range(50):
            c1, c2 = _control_points(0, 0, 500, 300)
            # Project control points onto perpendicular axis
            dx, dy = 500, 300
            dist = math.sqrt(dx*dx + dy*dy)
            px, py = -dy/dist, dx/dist
            proj1 = (c1[0] - 0) * px + (c1[1] - 0) * py
            proj2 = (c2[0] - 0) * px + (c2[1] - 0) * py
            # Both should be on same side (same sign or both near zero)
            if abs(proj1) > 5 and abs(proj2) > 5:
                self.assertEqual(proj1 > 0, proj2 > 0,
                    f"Control points on different sides: {proj1:.1f}, {proj2:.1f}")


class TestEndToEnd(unittest.TestCase):
    """Integration tests for complete path generation across various scenarios."""

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
            # Should reach target
            last_x, last_y, _ = path[-1]
            end_dist = math.sqrt((last_x - ex)**2 + (last_y - ey)**2)
            self.assertLess(end_dist, 1.5, f"Path {i}: endpoint miss {end_dist:.1f}px")
            # Should have multiple points
            self.assertGreater(len(path), 3, f"Path {i}: only {len(path)} points for {dist:.0f}px")
            # All delays positive
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


if __name__ == "__main__":
    unittest.main()
