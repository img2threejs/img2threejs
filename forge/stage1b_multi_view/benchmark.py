from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from .feature_detector import clear_feature_cache
from .synthesize import synthesize_geometry_brief


def measure_synthesis(image_paths: list[Path]) -> dict[str, float | int]:
    clear_feature_cache()
    cold_start = perf_counter()
    synthesize_geometry_brief(image_paths)
    cold_seconds = perf_counter() - cold_start
    warm_start = perf_counter()
    synthesize_geometry_brief(image_paths)
    warm_seconds = perf_counter() - warm_start
    return {
        "viewCount": len(image_paths),
        "coldSeconds": round(cold_seconds, 4),
        "warmSeconds": round(warm_seconds, 4),
        "featureCacheImprovementSeconds": round(cold_seconds - warm_seconds, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark multi-view synthesis cold and warm runs.")
    parser.add_argument("images", nargs="+", type=Path, help="Readable reference image paths")
    args = parser.parse_args()
    missing = [str(path) for path in args.images if not path.is_file()]
    if missing:
        parser.error("image paths must exist: " + ", ".join(missing))
    print(json.dumps(measure_synthesis(args.images), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
