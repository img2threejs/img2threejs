"""Exercise copied standalone CS2 consumers; synthetic protocol data, not image QA.

The parent supplies only a temporary tools directory, fixture JSON and workspace.
Run with -I -B: neither installed plugin imports nor bytecode writes are needed.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, sys.argv[1])
from cs2_manifest import build_manifest
from cs2_routing import decision_digest


def reference_png(path):
    """Same 128px grey rectangle as standalone test_cs2_manifest.write_png."""
    rows = []
    for y in range(128):
        row = bytearray()
        for x in range(128):
            value = 40 if 28 <= x < 100 and 12 <= y < 116 else 240
            row.extend((value, value, value))
        rows.append(b"\x00" + bytes(row))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", 128, 128, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def main():
    fixture = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["cs2"]
    reference = Path(sys.argv[3]) / "look-alike.png"
    reference_png(reference)
    pin = "a" * 40
    candidate = "profile:cs2@" + pin
    # Deliberately plausible but synthetic. A matching digest proves no authority.
    decision = {
        "kind": "img2.route-decision", "version": 1, "runId": "fixture", "revision": 1,
        "phase": "decided", "suspendedAt": None,
        "imageSha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "request": {"expectedOutput": "3d", "intent": [], "explicitRoute": None},
        "inputsDigest": "b" * 64, "inventorySha256": "c" * 64,
        "objects": [{"objectId": "prop", "boxSha256": "d" * 64,
                     "eligible": [candidate], "rejected": [], "consultations": [],
                     "decider": "jev:fixture", "status": "resolved", "selected": [candidate],
                     "execution": {"mode": "single", "n": 1}}],
        "jevSession": "healthy", "probes": [], "policyVersion": "fixture-v1",
        "versions": {"harness": "fixture", "baseSkill": "fixture", "cards": {}},
        "consentSha256": "e" * 64,
        "identity": {"itemFamily": "knife", "confidence": 1.0},
    }
    common = {"admission_artifact": fixture["admission"], "probe_artifact": fixture["probe"]}
    routed = dict(common, route_decision=decision,
                  validated_route_sha256=decision_digest(decision), object_id="prop", resolved_sha=pin,
                  explicit_identity={"itemFamily": "knife", "name": "CS2"})
    results = {}
    for case in fixture["rejectedClassifications"]:
        record = deepcopy(fixture["classification"])
        if "record" in case:
            record = case["record"]
        else:
            record.update(case["patch"])
        results[case["name"]] = build_manifest(reference, record, **routed)
    # Controls prove the consumer really received classification and route inputs;
    # 'proceed' is intake state, not independent identity or delivery validation.
    results["classified-control"] = build_manifest(reference, fixture["classification"], **common)
    results["routed-control"] = build_manifest(reference, fixture["classification"], **routed)
    results["unadmitted"] = build_manifest(
        reference, fixture["classification"], **dict(routed, admission_artifact={"admitted": False}))
    print(json.dumps(results, allow_nan=False))


if __name__ == "__main__":
    main()
