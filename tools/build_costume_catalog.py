"""Audit all 2D costumes against an unpacked v2.4.0 master and resource pack.

The decrypted game manifest is a developer input. No user paths or filenames
are retained in the generated catalog; all identifiers come from game data.
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def table(path):
    # Master CSVs begin with field names, types and array dimensions.
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))[2:]


def build(master_dir, manifest_path, cache_root, catalog_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundles = {row["m_identifier"].lower(): row for row in manifest["m_bundleList"]}
    live2d = {row["id"]: row for row in table(master_dir / "live2d.bytes")}
    checked = {}

    def missing(identifier, ancestors=()):
        identifier = identifier.lower()
        if identifier in checked:
            return checked[identifier]
        if identifier in ancestors:
            return [identifier + ":dependency_cycle"]
        row = bundles.get(identifier)
        if row is None:
            return [identifier + ":not_in_manifest"]
        path = cache_root / row["m_hash"] / (row["m_name"] + ".unity3d")
        errors = []
        if not path.is_file():
            errors.append(identifier + ":missing")
        elif path.stat().st_size != int(row["m_fileSize"]):
            errors.append(identifier + ":size_mismatch")
        for dependency in row.get("m_dependencies", []):
            errors.extend(missing(dependency, (*ancestors, identifier)))
        checked[identifier] = sorted(set(errors))
        return checked[identifier]

    inventory = defaultdict(list)
    costumes = {}
    for row in table(master_dir / "costume.bytes"):
        live = live2d.get(row["master_live2d_id"])
        errors = missing("live2d/" + live["prefab_name"]) if live else ["unknown_live2d"]
        errors = list(errors) + missing("textures/costumeicon/" + row["icon_sprite_name"] + ".png")
        character_id, costume_id = int(row["master_character_id"]), int(row["id"])
        costumes[str(costume_id)] = {
            "master_character_id": character_id,
            "master_live2d_id": int(row["master_live2d_id"]),
            "unlock_route": row["unlock_route"],
            "available": not errors,
            "unavailable_resources": sorted(set(errors)),
        }
        if not errors:
            inventory[character_id].append(costume_id)

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["source"] = "NanaOn v2.4.0 CostumeMst/Live2DMst and packaged bundle dependency/size audit"
    catalog["costumes"] = dict(sorted(costumes.items(), key=lambda item: int(item[0])))
    catalog["available_costumes"] = [
        {"master_character_id": char, "master_costume_ids": sorted(ids)}
        for char, ids in sorted(inventory.items())
    ]
    for card in catalog["cards"].values():
        card["available_costume_ids"] = [
            item for item in card["costume_ids"] if costumes.get(str(item), {}).get("available")
        ]
        card["unavailable_costume_ids"] = [
            item for item in card["costume_ids"] if item not in card["available_costume_ids"]
        ]
    counts = catalog["counts"]
    counts["available_unique_costumes"] = sum(len(ids) for ids in inventory.values())
    counts["total_unique_costumes"] = len(costumes)
    counts["unavailable_unique_costumes"] = len(costumes) - counts["available_unique_costumes"]
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle-cache", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path(__file__).resolve().parent.parent / "data/card_costume_catalog.json")
    args = parser.parse_args()
    print(json.dumps(build(args.master_dir, args.manifest, args.bundle_cache, args.catalog), indent=2))
