#!/usr/bin/env python3
"""Fill pre-spec assessment with car vision data, regenerate sculpt spec and factory."""

import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = Path.home() / "Documents" / "ZCodeProjects" / "ford-fiesta"
IMAGE_PATH = SKILL_DIR.parent / "agnes-free-image" / "outputs" / "agnes-free-image" / "4ca183cd2cad4e49928c2ed05cd3b170.png"
VISION_PATH = PROJECT_DIR / "vision-analysis.json"
ASSESSMENT_PATH = SKILL_DIR / "outputs" / "ford-fiesta-pre-spec-assessment.json"
SPEC_PATH = SKILL_DIR / "outputs" / "ford-fiesta-sculpt-spec.json"
MODEL_TS = PROJECT_DIR / "createFordFiestaModel.ts"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fill_assessment(assessment, vision):
    psa = assessment["preSpecAssessment"]

    oc = psa["objectClass"]
    oc["primaryType"] = "compact hatchback automobile"
    oc["primaryDomain"] = "object"
    oc["formLanguage"] = ["curvilinear", "sculpted", "aerodynamic", "geometric"]
    oc["structureKind"] = ["rigid-shell", "monocoque", "multi-panel"]
    oc["motionPotential"] = ["wheeled-vehicle", "ground-transport"]
    oc["materialFamilies"] = ["painted-metal", "glass", "plastic", "rubber", "alloy"]
    oc["notes"] = "Ford Fiesta Mk7 hatchback, 5-door, compact urban car."

    cx = psa["complexity"]
    cx["tier"] = "complex"
    cx["scores"] = {
        "silhouetteComplexity": 7, "componentCount": 8, "hierarchyDepth": 3,
        "repetitionDensity": 4, "materialLayerCount": 5, "localDetailDensity": 6,
        "occlusionRisk": 5, "actionReadinessNeed": 2,
    }
    cx["estimatedCounts"] = {
        "macroComponents": 8, "mesoComponents": 12, "microFeatureGroups": 10,
        "materialLayers": 5, "repetitionSystems": 3,
    }
    cx["reasoning"] = [
        "Car body has complex compound curves with multiple panel gaps.",
        "8+ major visible assemblies: body, windshield, side windows, front grille, headlights, bumper, wheels, mirrors.",
        "Materials vary: painted metal body, glass windows, plastic bumper/grille, rubber tires, alloy wheels.",
    ]

    sd = psa["specDepthDecision"]
    sd["requiredDepth"] = "complex"
    sd["minimumComponentLevels"] = ["macro", "meso", "micro"]
    sd["needsRepetitionSystems"] = True
    sd["needsMaterialLocalOverrides"] = True
    sd["needsMultipleReviewViews"] = True
    sd["needsActionReadyHierarchy"] = True
    sd["rationale"] = "Car requires multiple views to verify proportions. Wheel repetition system needed."

    qc = assessment["qualityContract"]
    qc["qualityBar"] = "moderate"

    di = psa["detailInventory"]
    di["targetMinDetails"] = 8
    di["details"] = [
        {"id": "grille-chrome-slats", "name": "Hexagonal grille with chrome slats",
         "location": "Front center", "scale": "meso", "component": "grille",
         "featureType": "geometry",
         "description": "Aston Martin-style hexagonal grille with horizontal chrome slats and Ford oval emblem."},
        {"id": "swept-headlamps", "name": "Swept-back headlamp shape",
         "location": "Front L/R", "scale": "meso", "component": "headlights",
         "featureType": "geometry",
         "description": "Sleek swept-back headlamps with clear lenses, aggressive styling."},
        {"id": "fog-light-bezels", "name": "Fog lights in black bezels",
         "location": "Lower bumper L/R", "scale": "micro", "component": "bumper",
         "featureType": "geometry",
         "description": "Round fog lights in black plastic bezels in lower bumper."},
        {"id": "multi-spoke-wheels", "name": "Multi-spoke alloy wheels",
         "location": "Four corners", "scale": "meso", "component": "wheels",
         "featureType": "geometry",
         "description": "16in multi-spoke alloy wheels with low-profile tires."},
        {"id": "body-character-line", "name": "Side character line",
         "location": "Side panels", "scale": "macro", "component": "body",
         "featureType": "geometry",
         "description": "Crease line from headlamp to tailgate along side panels."},
        {"id": "turn-signal-mirrors", "name": "Turn signal mirrors",
         "location": "Door mirrors", "scale": "meso", "component": "mirrors",
         "featureType": "geometry",
         "description": "Body-colored mirrors with integrated turn signals."},
        {"id": "black-bumper-inserts", "name": "Black bumper inserts",
         "location": "Lower front bumper", "scale": "meso", "component": "bumper",
         "featureType": "material",
         "description": "Black polymer inserts for contrast and scrape protection."},
        {"id": "white-metallic-paint", "name": "White metallic paint",
         "location": "All body panels", "scale": "macro", "component": "body",
         "featureType": "material",
         "description": "White metallic finish with glossy lacquer coating."},
    ]

    psa["unknownsToResolveBeforeImplementation"] = [
        "Rear end geometry (not visible in reference)",
        "Undercarriage/suspension layout",
        "Interior details through windows",
    ]

    return assessment


def main():
    print("🔍 Loading vision analysis...")
    vision = load_json(VISION_PATH)

    print("🔍 Loading existing pre-spec assessment...")
    assessment = load_json(ASSESSMENT_PATH)

    print("✏️  Filling assessment fields with car data...")
    assessment = fill_assessment(assessment, vision)
    ASSESSMENT_PATH.write_text(json.dumps(assessment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"✅ Saved filled assessment ({len(json.dumps(assessment))} bytes)")

    print("🔧 Regenerating sculpt spec with assessment...")
    result = subprocess.run(
        [sys.executable,
         str(SKILL_DIR / "forge" / "stage2_spec" / "new_sculpt_spec.py"),
         "ford-fiesta", "--image", str(IMAGE_PATH),
         "--assessment", str(ASSESSMENT_PATH),
         "--out", str(SPEC_PATH), "--force"],
        cwd=SKILL_DIR, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        print("⚠️  Sculpt spec generation had issues, continuing anyway")

    print("🔧 Validating spec...")
    subprocess.run(
        [sys.executable,
         str(SKILL_DIR / "forge" / "stage2_spec" / "validate_sculpt_spec.py"),
         str(SPEC_PATH)],
        cwd=SKILL_DIR,
    )

    print("🔧 Regenerating Three.js factory code...")
    result = subprocess.run(
        [sys.executable,
         str(SKILL_DIR / "forge" / "stage3_build" / "generate_threejs_factory.py"),
         str(SPEC_PATH), "--out", str(MODEL_TS), "--force"],
        cwd=SKILL_DIR, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        print("❌ Factory generation failed")
        sys.exit(1)

    spec = load_json(SPEC_PATH)
    comps = spec.get("componentTree", [])
    print(f"\n✅ Done! Component tree has {len(comps)} components:")
    for c in comps:
        kids = c.get("children", [])
        print(f"   - {c.get('id')} ({c.get('primitive')}) kids={len(kids)}")

    print(f"\n📦 Model: {MODEL_TS}")
    print(f"   Size: {MODEL_TS.stat().st_size} bytes, {len(MODEL_TS.read_text().splitlines())} lines")


if __name__ == "__main__":
    main()
