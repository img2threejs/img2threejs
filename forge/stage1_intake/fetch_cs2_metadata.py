#!/usr/bin/env python3
"""Resolve CS2 skin metadata (paint index, float range, rarity, CDN image) from the CSGO-API
skins index -- an optional exactness upgrade over the image-only default. Never guesses: a
no-match or an ambiguous multi-match is an error, not a silent pick. See
grimoire/intake/cs2_texture_acquisition.md and openspec add-cs2-item-reconstruction task 5.2.

The index is the public CSGO-API `skins.json` shape: a list of records like
    {"name": "Karambit | Doppler (Phase 2)", "weapon": {"name": "Karambit"},
     "paint_index": 419, "min_float": 0.0, "max_float": 0.08,
     "rarity": {"name": "Covert"}, "image": "https://.../419.png"}
Load it locally with --index-file (no network) or fetch it with --index-url.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def load_index(index_file: Path | None, index_url: str | None) -> list[dict]:
    if index_file is not None:
        data = json.loads(index_file.expanduser().read_text(encoding="utf-8"))
    elif index_url is not None:
        with urllib.request.urlopen(index_url, timeout=30) as response:  # noqa: S310 (user-supplied trusted URL)
            data = json.loads(response.read().decode("utf-8"))
    else:
        raise ValueError("provide --index-file or --index-url")
    if isinstance(data, dict):  # some CSGO-API mirrors key by id
        data = list(data.values())
    if not isinstance(data, list):
        raise ValueError("index must be a JSON list (or id-keyed object) of skin records")
    return [record for record in data if isinstance(record, dict)]


def _weapon_name(record: dict) -> str:
    weapon = record.get("weapon")
    if isinstance(weapon, dict):
        return str(weapon.get("name") or "")
    return str(weapon or "")


def _rarity_name(record: dict) -> str:
    rarity = record.get("rarity")
    if isinstance(rarity, dict):
        return str(rarity.get("name") or "")
    return str(rarity or "")


def match_records(records: list[dict], weapon: str, skin: str, phase: str | None,
                  paint_index: int | None = None) -> list[dict]:
    weapon_l, skin_l = weapon.lower(), skin.lower()
    phase_l = phase.lower() if phase else None
    matches = []
    for record in records:
        name_l = str(record.get("name") or "").lower()
        if _weapon_name(record).lower() != weapon_l:
            continue
        if skin_l not in name_l:
            continue
        if phase_l is not None and phase_l not in name_l:
            continue
        if paint_index is not None and str(record.get("paint_index")) != str(paint_index):
            continue
        matches.append(record)
    return matches


def to_metadata(record: dict, source: str | None = None) -> dict:
    metadata = {
        "name": record.get("name"),
        "weapon": _weapon_name(record),
        "paintIndex": record.get("paint_index"),
        "minFloat": record.get("min_float"),
        "maxFloat": record.get("max_float"),
        "floatRange": {"min": record.get("min_float"), "max": record.get("max_float")},
        "rarity": _rarity_name(record),
        "imageUrl": record.get("image"),
    }
    if source:
        metadata["source"] = source
    metadata["provenance"] = {"kind": "metadata-index", "source": source or "unspecified"}
    return metadata


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weapon", required=True, help="Weapon name, e.g. 'Karambit'")
    parser.add_argument("--skin", required=True, help="Skin/paint kit name substring, e.g. 'Doppler'")
    parser.add_argument("--phase", help="Disambiguating phase substring, e.g. 'Phase 2' or 'Emerald'")
    parser.add_argument("--paint-index", type=int, dest="paint_index",
                        help="Exact paint_index to disambiguate when names collide (e.g. CSGO-API lists "
                             "every Doppler phase as the same name; --paint-index 419 picks Phase 2).")
    parser.add_argument("--index-file", type=Path, help="Local CSGO-API skins JSON")
    parser.add_argument("--index-url", help="Remote CSGO-API skins JSON URL")
    parser.add_argument("--out", type=Path, help="Write the resolved metadata JSON here")
    parser.add_argument("--force", action="store_true", help="Overwrite --out if it exists")
    parser.add_argument("--download-image", type=Path,
                        help="Directory to download the resolved CDN image into (optional)")
    args = parser.parse_args(argv)

    try:
        records = load_index(args.index_file, args.index_url)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: could not load skin index: {exc}", file=sys.stderr)
        return 2

    matches = match_records(records, args.weapon, args.skin, args.phase, args.paint_index)
    if not matches:
        print(f"error: no match for weapon={args.weapon!r} skin={args.skin!r} "
              f"phase={args.phase!r}; refine the query (nothing guessed)", file=sys.stderr)
        return 2
    if len(matches) > 1:
        names = "; ".join(f"{m.get('name')} (paint_index={m.get('paint_index')})" for m in matches)
        print(f"error: ambiguous match ({len(matches)}): {names}. Add --phase or --paint-index to "
              "disambiguate (nothing guessed).", file=sys.stderr)
        return 2

    source = str(args.index_file.expanduser().resolve()) if args.index_file else args.index_url
    metadata = to_metadata(matches[0], source)

    if args.download_image and metadata.get("imageUrl"):
        args.download_image.expanduser().mkdir(parents=True, exist_ok=True)
        target = args.download_image.expanduser() / f"{metadata.get('paintIndex')}.png"
        try:
            urllib.request.urlretrieve(metadata["imageUrl"], target)  # noqa: S310
            metadata["imagePath"] = str(target)
        except OSError as exc:
            print(f"warning: image download failed ({exc}); metadata still resolved", file=sys.stderr)

    payload = json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        output = args.out.expanduser().resolve()
        if output.exists() and not args.force:
            print(f"error: {output} exists (use --force)", file=sys.stderr)
            return 2
        output.write_text(payload, encoding="utf-8")
        print(f"wrote {output}")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
