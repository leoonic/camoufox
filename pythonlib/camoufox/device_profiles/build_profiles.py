"""
Build device profile lookup table from Blender Open Data.
Run once: python -m device_profiles.build_profiles
Input: device_profiles/blender_raw.zip (316K+ JSONL entries)
Output: device_profiles/profiles.json
"""
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BLENDER_ZIP = Path(__file__).parent / "blender_raw.zip"
OUTPUT = Path(__file__).parent / "profiles.json"

# GPU family -> tier mapping
GPU_TIER_PATTERNS = {
    # NVIDIA
    r"RTX\s*[45]0[89]0": "high",
    r"RTX\s*[34]0[789]0": "high",
    r"RTX\s*[34]060": "mid",
    r"RTX\s*20[789]0": "mid",
    r"RTX\s*2060": "mid",
    r"GTX\s*1[07]80": "mid",
    r"GTX\s*1[07]70": "mid",
    r"GTX\s*1[07]60": "mid",
    r"GTX\s*1[07]50": "low",
    r"GTX\s*9[8765]0": "mid",
    r"GTX\s*[67][5-9]0": "low",
    r"GTX\s*[45][5-9]0": "low",
    r"GeForce\s+[89]": "low",
    r"Quadro": "mid",
    r"Tesla": "high",
    # AMD
    r"RX\s*[67][89]00": "high",
    r"RX\s*[67][67]00": "mid",
    r"RX\s*[56][56]00": "mid",
    r"RX\s*[56][0-4]0": "low",
    r"RX\s*[45][89]0": "low",
    r"Radeon.*VII": "high",
    r"Radeon.*R9\s*[23]": "mid",
    r"Radeon.*R7": "low",
    r"Radeon.*HD\s*[789]": "low",
    r"Radeon.*HD\s*[3456]": "low",
    r"Vega": "mid",
    # Intel
    r"Arc\s*[AB]": "mid",
    r"Iris\s*Xe": "integrated",
    r"Iris\s*Plus": "integrated",
    r"Iris\s*Pro": "integrated",
    r"UHD\s*Graphics": "integrated",
    r"HD\s*Graphics": "integrated",
    r"Intel.*945": "integrated",
    # Apple (skip, Windows only)
    r"Apple\s*M": "high",
}


def classify_blender_gpu(gpu_name: str) -> str:
    for pattern, tier in GPU_TIER_PATTERNS.items():
        if re.search(pattern, gpu_name, re.IGNORECASE):
            return tier
    # Fallback heuristics
    if "intel" in gpu_name.lower():
        return "integrated"
    if "radeon" in gpu_name.lower():
        return "low"
    return "mid"


def parse_blender_zip(zip_path: Path):
    """Parse JSONL from Blender Open Data zip. Yield (tier, threads, os)."""
    count = 0
    errors = 0
    seen = set()
    with zipfile.ZipFile(zip_path) as z:
        jsonl_files = [n for n in z.namelist() if n.endswith(".jsonl")]
        for name in jsonl_files:
            print(f"  Reading {name}...")
            with z.open(name) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        errors += 1
                        continue

                    # Blender wraps benchmarks in data[]
                    raw_data = entry.get("data", [entry])
                    if not isinstance(raw_data, list):
                        continue
                    data_list = raw_data
                    for benchmark in data_list:
                        si = benchmark.get("system_info", {})
                        os_name = si.get("system", "")
                        if "windows" not in os_name.lower():
                            continue

                        threads = si.get("num_cpu_threads", 0)
                        if not threads or threads > 128:
                            continue

                        devices = si.get("devices", [])
                        gpu_found = False
                        for dev in devices:
                            dev_name = dev.get("name", "")
                            dev_type = dev.get("type", "")
                            # Skip CPU entries, keep GPU (CUDA, OPTIX, OPENCL, HIP, METAL, ONEAPI)
                            if not dev_name or dev_type == "CPU":
                                continue
                            tier = classify_blender_gpu(dev_name)
                            # Deduplicate per-system (same GPU+threads = same machine)
                            key = (dev_name, threads)
                            if key not in seen:
                                seen.add(key)
                                yield tier, threads, os_name
                                count += 1
                            gpu_found = True
                            break
                        if not gpu_found:
                            # No GPU, use integrated tier
                            key = ("integrated", threads)
                            if key not in seen:
                                seen.add(key)
                                yield "integrated", threads, os_name
                                count += 1

    print(f"Parsed {count} unique Windows device combos ({errors} parse errors)")


def build():
    if not BLENDER_ZIP.exists():
        print(f"ERROR: {BLENDER_ZIP} not found. Download from https://opendata.blender.org/snapshots/opendata-latest.zip")
        return

    print(f"Parsing {BLENDER_ZIP}...")
    tier_threads = defaultdict(Counter)
    total = 0

    for tier, threads, _ in parse_blender_zip(BLENDER_ZIP):
        tier_threads[tier][str(threads)] += 1
        total += 1

    print(f"\nTotal Windows entries: {total}")
    for tier, counter in sorted(tier_threads.items()):
        print(f"  {tier}: {sum(counter.values())} entries, top threads: {counter.most_common(5)}")

    profiles = {
        "version": 1,
        "generated_at": datetime.now().isoformat(),
        "source": "Blender Open Data",
        "source_count": total,
        "tiers": {},
    }

    # Max realistic thread counts per tier
    tier_max_threads = {"integrated": 16, "low": 24, "mid": 32, "high": 64}

    for tier, counter in tier_threads.items():
        max_t = tier_max_threads.get(tier, 32)
        filtered = {k: v for k, v in counter.items() if int(k) <= max_t}
        if not filtered:
            filtered = {"8": 1}
        profiles["tiers"][tier] = {
            "thread_counts": filtered,
            "entry_count": sum(filtered.values()),
        }

    # Ensure all expected tiers exist
    for t in ("integrated", "low", "mid", "high"):
        if t not in profiles["tiers"]:
            profiles["tiers"][t] = {"thread_counts": {"8": 1}, "entry_count": 0}

    with open(OUTPUT, "w") as f:
        json.dump(profiles, f, indent=2)

    print(f"\nWrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
