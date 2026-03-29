"""
Standalone fingerprint profile generator for Camoufox RDP workers.
Generates coherent CAMOU_CONFIG profiles from real hardware data.
No BrowserForge dependency.

Usage (one-time generation):
    from camoufox.fpgen import generate_batch, save_profiles
    profiles = generate_batch(
        count=10,
        ff_version="149",
        timezones=["America/Argentina/Buenos_Aires"] * 10,
        max_threads=8,
    )
    save_profiles(profiles, "fingerprint_profiles")

Usage (runtime, per worker):
    from camoufox.fpgen import load_profile
    profile = load_profile("fingerprint_profiles/profile_1.json")
    async with RDPBrowser(fingerprint=profile, ...) as browser:
        ...
"""

import json
import random
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PKG = Path(__file__).parent
_WEBGL_DB = _PKG / "webgl" / "webgl_data.db"
_FONTS_JSON = _PKG / "fonts.json"
_VOICES_JSON = _PKG / "voices.json"
_PROFILES_JSON = _PKG / "device_profiles" / "profiles.json"

_cache: Dict[str, Any] = {}

# GPU tier classification: substrings matched against WebGL renderer strings.
# Maps GPU model substring -> hardware tier for coherent thread/resolution selection.
_GPU_TIERS = {
    "Intel 945GM": "integrated",
    "Intel(R) HD Graphics 400": "integrated",
    "Intel(R) HD Graphics Direct3D11 vs_4_0": "integrated",
    "Intel(R) HD Graphics Direct3D11 vs_4_1": "integrated",
    "Intel(R) HD Graphics Direct3D11 vs_5_0": "integrated",
    "Microsoft Basic Render Driver": "integrated",
    "SwiftShader": "integrated",
    "Radeon HD 3200": "low",
    "GeForce GTX 480": "low",
    "GeForce 8800": "low",
    "Radeon R9 200": "mid",
    "GeForce GTX 980": "mid",
}

# Resolution distributions per GPU tier (Steam Hardware Survey 2025)
_RESOLUTIONS = {
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

_DPR = {
    "integrated": [(1.0, 65), (1.25, 15), (1.5, 12), (2.0, 8)],
    "low": [(1.0, 70), (1.25, 12), (1.5, 10), (2.0, 8)],
    "mid": [(1.0, 55), (1.25, 15), (1.5, 15), (2.0, 15)],
}

# Software renderers to skip (unrealistic for real users)
_SOFTWARE_RENDERERS = {"SwiftShader", "Microsoft Basic Render Driver"}

# Essential Windows fonts (OS markers + core system fonts).
# CreepJS uses Segoe UI, Cambria Math, Nirmala UI to verify Windows OS claim.
_ESSENTIAL_WIN_FONTS = [
    "Arial", "Times New Roman", "Courier New", "Verdana", "Georgia",
    "Trebuchet MS", "Tahoma", "Segoe UI", "Calibri", "Cambria Math",
    "Nirmala UI", "Consolas",
]

# Timezone -> (language, region) for coherent locale generation
_TZ_LOCALE = {
    "America/New_York": ("en", "US"),
    "America/Chicago": ("en", "US"),
    "America/Denver": ("en", "US"),
    "America/Los_Angeles": ("en", "US"),
    "America/Toronto": ("en", "CA"),
    "America/Vancouver": ("en", "CA"),
    "America/Mexico_City": ("es", "MX"),
    "America/Argentina/Buenos_Aires": ("es", "AR"),
    "America/Sao_Paulo": ("pt", "BR"),
    "America/Bogota": ("es", "CO"),
    "America/Lima": ("es", "PE"),
    "America/Santiago": ("es", "CL"),
    "America/Montevideo": ("es", "UY"),
    "America/Caracas": ("es", "VE"),
    "America/Guayaquil": ("es", "EC"),
    "America/Asuncion": ("es", "PY"),
    "America/La_Paz": ("es", "BO"),
    "Europe/London": ("en", "GB"),
    "Europe/Paris": ("fr", "FR"),
    "Europe/Berlin": ("de", "DE"),
    "Europe/Madrid": ("es", "ES"),
    "Europe/Rome": ("it", "IT"),
    "Europe/Amsterdam": ("nl", "NL"),
    "Europe/Brussels": ("fr", "BE"),
    "Europe/Zurich": ("de", "CH"),
    "Europe/Vienna": ("de", "AT"),
    "Europe/Warsaw": ("pl", "PL"),
    "Europe/Prague": ("cs", "CZ"),
    "Europe/Moscow": ("ru", "RU"),
    "Europe/Istanbul": ("tr", "TR"),
    "Europe/Lisbon": ("pt", "PT"),
    "Europe/Stockholm": ("sv", "SE"),
    "Europe/Helsinki": ("fi", "FI"),
    "Europe/Copenhagen": ("da", "DK"),
    "Europe/Oslo": ("nb", "NO"),
    "Europe/Bucharest": ("ro", "RO"),
    "Europe/Athens": ("el", "GR"),
    "Asia/Tokyo": ("ja", "JP"),
    "Asia/Seoul": ("ko", "KR"),
    "Asia/Shanghai": ("zh", "CN"),
    "Asia/Taipei": ("zh", "TW"),
    "Asia/Hong_Kong": ("zh", "HK"),
    "Asia/Singapore": ("en", "SG"),
    "Asia/Kolkata": ("hi", "IN"),
    "Asia/Bangkok": ("th", "TH"),
    "Asia/Jakarta": ("id", "ID"),
    "Asia/Ho_Chi_Minh": ("vi", "VN"),
    "Asia/Manila": ("en", "PH"),
    "Asia/Kuala_Lumpur": ("ms", "MY"),
    "Asia/Dubai": ("ar", "AE"),
    "Asia/Riyadh": ("ar", "SA"),
    "Asia/Jerusalem": ("he", "IL"),
    "Australia/Sydney": ("en", "AU"),
    "Australia/Melbourne": ("en", "AU"),
    "Pacific/Auckland": ("en", "NZ"),
    "Africa/Johannesburg": ("en", "ZA"),
    "Africa/Lagos": ("en", "NG"),
    "Africa/Cairo": ("ar", "EG"),
}


# --- Helpers ---

def _wchoice(options):
    values, weights = zip(*options)
    return random.choices(values, weights=weights, k=1)[0]


def _wchoice_dict(d: dict):
    keys = list(d.keys())
    weights = [d[k] for k in keys]
    return int(random.choices(keys, weights=weights, k=1)[0])


def _load_json(key: str, path: Path):
    if key not in _cache:
        with open(path, "r", encoding="utf-8") as f:
            _cache[key] = json.load(f)
    return _cache[key]


# --- Data loaders ---

def _load_all_webgl_win() -> List[Dict]:
    """Load all unique Windows WebGL entries from DB, excluding software renderers."""
    conn = sqlite3.connect(str(_WEBGL_DB))
    cur = conn.cursor()
    cur.execute(
        "SELECT vendor, renderer, data, win "
        "FROM webgl_fingerprints WHERE win > 0 ORDER BY win DESC"
    )
    rows = cur.fetchall()
    conn.close()

    entries = []
    seen = set()
    for vendor, renderer, data_str, weight in rows:
        if renderer in seen:
            continue
        seen.add(renderer)
        # Skip software renderers
        if any(sw in renderer for sw in _SOFTWARE_RENDERERS):
            continue
        data = json.loads(data_str)
        entries.append({
            "db_vendor": vendor,
            "db_renderer": renderer,
            "data": data,
            "weight": weight,
        })
    return entries


def _classify_renderer(renderer: str) -> str:
    for pattern, tier in _GPU_TIERS.items():
        if pattern in renderer:
            return tier
    return "mid"


def _gpu_short_name(renderer: str) -> str:
    """Extract short GPU name from ANGLE renderer string."""
    if "," in renderer:
        gpu = renderer.split(",")[1].strip()
        gpu = gpu.split(" Direct3D")[0].split(" Vulkan")[0]
        if gpu.endswith(")"):
            gpu = gpu[:-1]
        return gpu
    return renderer[:40]


# --- Component generators ---

def _pick_threads(tier: str, max_threads: int, min_threads: int = 2) -> int:
    """Pick thread count from Blender Open Data, capped at max_threads, floored at min_threads."""
    if _PROFILES_JSON.exists():
        profiles = _load_json("profiles", _PROFILES_JSON)
        tier_data = profiles.get("tiers", {}).get(tier)
        if tier_data:
            dist = tier_data["thread_counts"]
            filtered = {k: v for k, v in dist.items()
                        if min_threads <= int(k) <= max_threads}
            if filtered:
                return _wchoice_dict(filtered)
    return min(max(random.choice([4, 8]), min_threads), max_threads)


def _pick_resolution(tier: str) -> Tuple[int, int]:
    options = _RESOLUTIONS.get(tier, _RESOLUTIONS["mid"])
    entry = _wchoice([(r, r[2]) for r in options])
    return entry[0], entry[1]


def _pick_dpr(tier: str, width: int) -> float:
    if width <= 1600:
        return _wchoice([(1.0, 80), (1.25, 15), (1.5, 5)])
    return _wchoice(_DPR.get(tier, _DPR["mid"]))


def _build_screen(width: int, height: int, dpr: float) -> Dict[str, Any]:
    """Generate all screen/window fields with realistic jitter."""
    taskbar = random.choice([30, 40, 48])
    avail_w = width
    avail_h = height - taskbar

    if random.random() < 0.75:
        outer_w, outer_h = avail_w, avail_h
    else:
        outer_w = avail_w - random.randint(40, 200)
        outer_h = avail_h - random.randint(40, 150)

    chrome_h = random.randint(70, 90)
    chrome_w = random.randint(14, 18)

    return {
        "screen.width": width,
        "screen.height": height,
        "screen.availWidth": avail_w,
        "screen.availHeight": avail_h,
        "screen.availTop": 0,
        "screen.availLeft": 0,
        "screen.colorDepth": 24,
        "screen.pixelDepth": 24,
        "window.outerWidth": outer_w,
        "window.outerHeight": outer_h,
        "window.innerWidth": outer_w - chrome_w,
        "window.innerHeight": outer_h - chrome_h,
        "window.screenX": 0,
        "window.screenY": 0,
        "window.devicePixelRatio": dpr,
    }


def _build_font_subset() -> List[str]:
    """Random 30-78% of non-essential Windows fonts + all essential fonts."""
    all_fonts = _load_json("fonts", _FONTS_JSON)
    win_fonts = all_fonts.get("win", [])
    essential = set(_ESSENTIAL_WIN_FONTS)

    result = [f for f in win_fonts if f in essential]
    non_essential = [f for f in win_fonts if f not in essential]

    pct = 30 + int(random.random() * 49)
    count = round((pct / 100) * len(non_essential))
    if count < len(non_essential):
        selected = random.sample(non_essential, count)
    else:
        selected = list(non_essential)
    result.extend(selected)
    return result


def _build_voices() -> List[str]:
    """All Windows voices (too few to subset meaningfully)."""
    all_voices = _load_json("voices", _VOICES_JSON)
    win_voices = all_voices.get("win", [])
    return [v.split(":")[0] for v in win_voices]


def _accept_language(lang: str, region: str) -> str:
    """Build Accept-Language header in Firefox format."""
    tag = f"{lang}-{region}"
    if lang == "en":
        return f"{tag},{lang};q=0.5"
    return f"{tag},{lang};q=0.8,en-US;q=0.5,en;q=0.3"


def _nav_languages(lang: str, region: str) -> List[str]:
    """Build navigator.languages array."""
    tag = f"{lang}-{region}"
    if lang == "en":
        return [tag, lang]
    return [tag, lang, "en-US", "en"]


# --- Main generators ---

def generate_profile(
    webgl_entry: Dict,
    ff_version: str = "149",
    timezone: str = "America/New_York",
    max_threads: int = 8,
    locale_language: Optional[str] = None,
    locale_region: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a complete CAMOU_CONFIG dict for one browser instance.

    Args:
        webgl_entry: Pre-selected WebGL entry from _load_all_webgl_win()
        ff_version: Firefox major version (must match the binary)
        timezone: IANA timezone (should match IP geolocation)
        max_threads: Max hardwareConcurrency (set to real CPU cores of the machine)
        locale_language: Override language (e.g. "pt"). If None, derived from timezone.
        locale_region: Override region (e.g. "BR"). If None, derived from timezone.

    Returns:
        Complete CAMOU_CONFIG dict ready for env var injection.
    """
    config: Dict[str, Any] = {}

    # 1. WebGL: merge full parameter set (vendor, renderer, extensions, params, shaders)
    webgl_data = dict(webgl_entry["data"])
    webgl_data.pop("webGl2Enabled", None)
    renderer = webgl_data.get("webGl:renderer", "")
    config.update(webgl_data)

    # 2. GPU tier -> coherent hardware
    tier = _classify_renderer(renderer)
    threads = _pick_threads(tier, max_threads)
    width, height = _pick_resolution(tier)
    dpr = _pick_dpr(tier, width)

    # 3. Navigator identity (Windows + Firefox)
    ua = (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{ff_version}.0) "
        f"Gecko/20100101 Firefox/{ff_version}.0"
    )
    config["navigator.userAgent"] = ua
    config["headers.User-Agent"] = ua
    config["navigator.platform"] = "Win32"
    config["navigator.oscpu"] = "Windows NT 10.0; Win64; x64"
    config["navigator.hardwareConcurrency"] = threads
    config["navigator.maxTouchPoints"] = 0

    if random.random() < 0.15:
        config["navigator.doNotTrack"] = "1"

    # 4. Screen + Window
    config.update(_build_screen(width, height, dpr))

    # 5. Locale: explicit override or derived from timezone
    if locale_language and locale_region:
        lang, region = locale_language, locale_region
    else:
        lang, region = _TZ_LOCALE.get(timezone, ("en", "US"))
    config["timezone"] = timezone
    config["locale:language"] = lang
    config["locale:region"] = region
    config["navigator.language"] = f"{lang}-{region}"
    config["navigator.languages"] = _nav_languages(lang, region)
    config["headers.Accept-Language"] = _accept_language(lang, region)

    # 6. Unique seeds (uint32, never 0)
    config["fonts:spacing_seed"] = random.randint(1, 4_294_967_295)
    config["audio:seed"] = random.randint(1, 4_294_967_295)
    config["canvas:seed"] = random.randint(1, 4_294_967_295)

    # 7. Fonts (random subset) + Voices (all Windows)
    config["fonts"] = _build_font_subset()
    config["voices"] = _build_voices()

    # 8. Battery (realistic desktop state)
    if random.random() < 0.65:
        config["battery:charging"] = True
        config["battery:chargingTime"] = 0.0
        config["battery:level"] = round(random.uniform(0.50, 1.0), 2)
    else:
        config["battery:charging"] = False
        config["battery:dischargingTime"] = round(random.uniform(3600, 28800), 0)
        config["battery:level"] = round(random.uniform(0.15, 0.95), 2)

    # 9. Media devices (typical desktop: 1 mic, 1 cam, 1-2 speakers)
    config["mediaDevices:micros"] = random.choice([0, 1, 1, 1])
    config["mediaDevices:webcams"] = random.choice([0, 1, 1, 1])
    config["mediaDevices:speakers"] = random.choice([1, 1, 2, 2])

    # 10. Behavioral
    config["humanize"] = True

    return config


def generate_batch(
    count: int = 10,
    ff_version: str = "149",
    timezones: Optional[List[str]] = None,
    max_threads: int = 8,
    locale_language: Optional[str] = None,
    locale_region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Generate N profiles with unique WebGL renderers.

    Picks the top N most common renderers (by real-world probability),
    then shuffles assignment so profile_1 doesn't always get the most common GPU.

    Args:
        count: Number of profiles to generate
        ff_version: Firefox version (must match binary)
        timezones: One timezone per profile. If None, defaults to America/New_York.
        max_threads: Real CPU cores of the server machine
        locale_language: Override language for all profiles. If None, derived from timezone.
        locale_region: Override region for all profiles. If None, derived from timezone.

    Returns:
        List of CAMOU_CONFIG dicts with _meta field for human review.
    """
    webgl_entries = _load_all_webgl_win()
    if count > len(webgl_entries):
        raise ValueError(
            f"Requested {count} profiles but only {len(webgl_entries)} "
            f"unique hardware WebGL renderers available"
        )

    selected = webgl_entries[:count]
    random.shuffle(selected)

    if timezones is None:
        timezones = ["America/New_York"] * count
    elif len(timezones) < count:
        timezones = timezones + [timezones[-1]] * (count - len(timezones))

    profiles = []
    for i, (entry, tz) in enumerate(zip(selected, timezones)):
        profile = generate_profile(
            entry, ff_version, tz, max_threads, locale_language, locale_region
        )

        renderer = entry["data"].get("webGl:renderer", "")
        tier = _classify_renderer(renderer)
        gpu = _gpu_short_name(renderer)

        profile["_meta"] = {
            "profile_id": i + 1,
            "gpu": gpu,
            "tier": tier,
            "threads": profile["navigator.hardwareConcurrency"],
            "resolution": f"{profile['screen.width']}x{profile['screen.height']}",
            "dpr": profile["window.devicePixelRatio"],
            "timezone": tz,
            "locale": f"{profile['locale:language']}-{profile['locale:region']}",
            "fonts_count": len(profile.get("fonts", [])),
            "generated_at": datetime.now().isoformat()[:19],
        }
        profiles.append(profile)

    return profiles


def save_profiles(profiles: List[Dict], output_dir: str) -> List[str]:
    """Save profiles to numbered JSON files. Returns file paths."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    paths = []
    for i, profile in enumerate(profiles):
        path = Path(output_dir) / f"profile_{i + 1}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        paths.append(str(path))
    return paths


def load_profile(path: str) -> Dict[str, Any]:
    """Load a saved profile. Strips _meta before returning."""
    with open(path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    profile.pop("_meta", None)
    return profile


def print_summary(profiles: List[Dict]) -> None:
    """Print a human-readable summary of generated profiles."""
    print(f"\n{'#':>3} {'GPU':30s} {'Tier':12s} {'Threads':>7} {'Resolution':>12} {'DPR':>5} {'TZ':25s} {'Locale':8s} {'Fonts':>5}")
    print("-" * 120)
    for p in profiles:
        m = p.get("_meta", {})
        print(
            f"{m.get('profile_id', '?'):>3} "
            f"{m.get('gpu', '?'):30s} "
            f"{m.get('tier', '?'):12s} "
            f"{m.get('threads', '?'):>7} "
            f"{m.get('resolution', '?'):>12} "
            f"{m.get('dpr', '?'):>5} "
            f"{m.get('timezone', '?'):25s} "
            f"{m.get('locale', '?'):8s} "
            f"{m.get('fonts_count', '?'):>5}"
        )
    print()
