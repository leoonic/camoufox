"""
Runtime fingerprint validator.
Ensures hardware coherence using Blender Open Data distributions.
Corrects: hardwareConcurrency, maxTouchPoints, screen, DPR.
"""
import json
import random
from pathlib import Path
from typing import Dict, Optional, Tuple

from browserforge.fingerprints import Fingerprint

PROFILES_PATH = Path(__file__).parent / "profiles.json"
_CACHE: Optional[dict] = None

# GPU tier classification for Camoufox's 11 Windows renderers
GPU_TIERS = {
    "Intel 945GM": "integrated",
    "Intel(R) HD Graphics 400": "integrated",
    "Intel(R) HD Graphics Direct3D11 vs_4_1": "integrated",
    "Intel(R) HD Graphics Direct3D11 vs_5_0": "integrated",
    "Microsoft Basic Render Driver": "integrated",
    "Radeon HD 3200": "low",
    "GeForce GTX 480": "low",
    "GeForce 8800": "low",
    "Radeon R9 200": "mid",
    "GeForce GTX 980": "mid",
}

# Resolution distributions per tier (from Steam Hardware Survey)
RESOLUTIONS = {
    "integrated": [
        (1920, 1080, 55), (1366, 768, 20), (1600, 900, 10),
        (1280, 800, 5), (1440, 900, 5), (2560, 1440, 5),
    ],
    "low": [
        (1920, 1080, 65), (1366, 768, 12), (1600, 900, 8),
        (1280, 1024, 5), (2560, 1440, 7), (1680, 1050, 3),
    ],
    "mid": [
        (1920, 1080, 55), (2560, 1440, 22), (1600, 900, 5),
        (1366, 768, 5), (3440, 1440, 5), (3840, 2160, 8),
    ],
}

# DPR distributions per tier
DPR_OPTIONS = {
    "integrated": [(1.0, 65), (1.25, 15), (1.5, 12), (2.0, 8)],
    "low": [(1.0, 70), (1.25, 12), (1.5, 10), (2.0, 8)],
    "mid": [(1.0, 55), (1.25, 15), (1.5, 15), (2.0, 15)],
}

# Fallback thread count distributions per tier (if profiles.json not available)
FALLBACK_THREADS = {
    "integrated": [(4, 40), (8, 35), (2, 10), (6, 10), (12, 5)],
    "low": [(4, 30), (8, 35), (6, 10), (12, 15), (16, 10)],
    "mid": [(8, 30), (12, 25), (16, 20), (6, 10), (4, 10), (24, 5)],
}


def _load_profiles() -> Optional[dict]:
    global _CACHE
    if _CACHE is None and PROFILES_PATH.exists():
        with open(PROFILES_PATH, "r") as f:
            _CACHE = json.load(f)
    return _CACHE


def _weighted_choice(options):
    """Pick from [(value, weight), ...] using weighted random."""
    values, weights = zip(*options)
    return random.choices(values, weights=weights, k=1)[0]


def _weighted_choice_dict(d: dict):
    """Pick from {value_str: weight} dict."""
    keys = list(d.keys())
    weights = [d[k] for k in keys]
    return int(random.choices(keys, weights=weights, k=1)[0])


def classify_renderer(renderer: str) -> str:
    """Classify a Camoufox WebGL renderer into a GPU tier."""
    for pattern, tier in GPU_TIERS.items():
        if pattern in renderer:
            return tier
    return "mid"


def pre_sample_webgl() -> Tuple[str, str]:
    """Pre-sample a WebGL vendor/renderer from Camoufox's DB for Windows."""
    from camoufox.webgl import sample_webgl
    result = sample_webgl("win")
    return result["webGl:vendor"], result["webGl:renderer"]


def pick_thread_count(tier: str) -> int:
    """Pick a realistic thread count for the GPU tier."""
    profiles = _load_profiles()
    if profiles and tier in profiles.get("tiers", {}):
        return _weighted_choice_dict(profiles["tiers"][tier]["thread_counts"])
    return _weighted_choice(FALLBACK_THREADS.get(tier, FALLBACK_THREADS["mid"]))


def pick_resolution(tier: str) -> Tuple[int, int]:
    """Pick a realistic screen resolution for the GPU tier."""
    options = RESOLUTIONS.get(tier, RESOLUTIONS["mid"])
    w, h, _ = zip(*options) if False else (None, None, None)
    entry = _weighted_choice([(r, r[2]) for r in options])
    return entry[0], entry[1]


def pick_dpr(tier: str, width: int) -> float:
    """Pick a realistic devicePixelRatio."""
    # High DPR unlikely on low resolutions
    if width <= 1600:
        return _weighted_choice([(1.0, 80), (1.25, 15), (1.5, 5)])
    return _weighted_choice(DPR_OPTIONS.get(tier, DPR_OPTIONS["mid"]))


def apply_resolution(screen, width: int, height: int, dpr: float):
    """Fix all screen fields for internal consistency."""
    screen.width = width
    screen.height = height
    screen.colorDepth = 24
    screen.pixelDepth = 24

    taskbar = random.choice([30, 40, 48])
    screen.availWidth = width
    screen.availHeight = height - taskbar
    screen.availTop = 0
    screen.availLeft = 0

    # 75% maximized
    if random.random() < 0.75:
        screen.outerWidth = screen.availWidth
        screen.outerHeight = screen.availHeight
    else:
        screen.outerWidth = screen.availWidth - random.randint(40, 200)
        screen.outerHeight = screen.availHeight - random.randint(40, 150)

    chrome_h = random.randint(70, 90)
    chrome_w = random.randint(14, 18)
    screen.innerWidth = screen.outerWidth - chrome_w
    screen.innerHeight = screen.outerHeight - chrome_h
    screen.devicePixelRatio = dpr
    screen.screenX = 0
    screen.pageXOffset = 0
    screen.pageYOffset = 0


def validate_fingerprint(fingerprint: Fingerprint, renderer: str) -> Fingerprint:
    """
    Validate and fix hardware coherence of a fingerprint
    against a specific Camoufox WebGL renderer.
    """
    tier = classify_renderer(renderer)

    # Fix hardwareConcurrency
    fingerprint.navigator.hardwareConcurrency = pick_thread_count(tier)

    # Fix maxTouchPoints (desktop Windows = 0)
    fingerprint.navigator.maxTouchPoints = 0

    # Fix doNotTrack (most users don't enable it)
    if random.random() < 0.85:
        fingerprint.navigator.doNotTrack = None

    # Fix screen resolution and DPR
    w, h = pick_resolution(tier)
    dpr = pick_dpr(tier, w)
    apply_resolution(fingerprint.screen, w, h, dpr)

    return fingerprint
