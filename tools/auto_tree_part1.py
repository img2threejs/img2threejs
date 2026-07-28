#!/usr/bin/env python3
"""auto_tree.py - Template system for img2threejs pipeline."""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

def _eval(expr: str, dims: Dict[str, float]) -> float:
    """Evaluate a dimension expression in a sandboxed namespace."""
    try:
        return eval(expr, {"__builtins__": {}}, dims)
    except Exception as e:
        print(f"Warning: Failed to evaluate expression '{expr}': {e}")
        return 0.0

def _load_json(filepath: str) -> Dict[str, Any]:
    """Load JSON file with error handling."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {filepath}: {e}")
        sys.exit(1)

def _save_json(filepath: str, data: Dict[str, Any], indent: int = 2) -> None:
    """Save JSON file with error handling."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
    except IOError as e:
        print(f"Error: Failed to write to {filepath}: {e}")
        sys.exit(1)

class TemplateRegistry:
    """Registry of component tree templates for different object categories."""
    TEMPLATES: Dict[str, Dict[str, Any]] = {
        "CAR": {
            "categoryMatchers": ["automobile", "car", "hatchback", "sedan", "truck", "vehicle", "van", "suv", "coup", "roadster"],
            "description": "Car/automobile with body, wheels, windows, lights, mirrors, bumpers",
            "defaultDimensions": {"L": 4.0, "W": 1.7, "H": 1.5},
            "components": [
                {
                    "id": "body", "name": "Car body shell", "level": "macro", "role": "body",
                    "primitive": "box", "topologyClass": "assembled-solid",
                    "topologyRationale": "Main body shell with compound curves, panel gaps, character lines.",
                    "geometryDescriptor": {
                        "topologyIntent": "stylized reconstruction",
                        "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
                        "deformationStack": [], "uvStrategy": "generated procedural coordinates",
                        "normalStrategy": "smooth vertex normals"
                    },
                    "parent": "root",
                    "attachment": {
                        "parentSocket": "root", "contactType": "continuous",
                        "embedDepth": 0.01, "gapTolerance": 0.0,
                        "localStart": [0, 0, 0], "localEnd": [0, 0, 0]
                    },
                    "dimensions": {
                        "width": {"$eval": "L * 0.95"}, "height": {"$eval": "H * 0.7"},
                        "depth": {"$eval": "W * 0.95"}, "units": "meters", "confidence": 0.85
                    },
                    "transform": {
                        "position": [0, {"$eval": "H * 0.15"}, 0],
                        "rotation": [0, 0, 0], "scale": [1, 1, 1]
                    },
                    "actionProfile": {
                        "animationRole": "static",
                        "pivot": {"mode": "center", "localPosition": [0, 0, 0], "axis": [0, 1, 0], "confidence": 0.7},
                        "transformChannels": {
                            "translate": False, "rotate": False, "scale": False,
                            "bend": False, "twist": False, "detach": False,
                            "visibility": True, "materialState": False
                        },
                        "sockets": [],
                        "collider": {"type": "box", "offset": [0, 0, 0], "scale": [1, 1, 1], "isTrigger": False, "notes": "box proxy"},
                        "constraints": [],
                        "destruction": {"breakable": False, "fractureGroup": "", "seamRefs": [], "detachableFragments": [], "breakImpulse": 0.0, "debrisMaterial": "hidden"}
                    },
                    "material": "body-paint",
                    "materialLayers": ["body-paint"],
                    "deformations": [], "joints": [], "seams": [],
                    "localFeatures": [], "surfaceDetail": {
                        "macroRoughness": 0.0, "microRoughness": 0.0, "bumpAmplitude": 0.0,
                        "normalPattern": "", "displacementPattern": "", "occlusionPattern": "",
                        "edgeWearPattern": "", "notes": "", "roughnessMap": None, "normalMap": None
                    },
                    "evidenceRefs": ["full-object"], "details": [],
                    "fidelityTier": "blockout",
                    "colorMaterialRecipe": {
                        "dominantAlbedo": "rgba(200,200,200,1.0)",
                        "materialClass": "plastic", "materialClassConfidence": 0.8,
                        "secondaryAlbedo": "rgba(200,200,200,1.0)"
                    },
                    "children": ["windshield", "side-windows", "front-grille", "headlight-left", "headlight-right", "bumper"]
                }
            ],
            "materials": [
                {
                    "id": "body-paint",
                    "displayName": "Body paint (white metallic)",
                    "type": "standard",
                    "albedo": {"color": "rgba(220,220,230,1.0)", "type": "hex"},
                    "roughness": {"base": 0.3, "map": None},
                    "metalness": 0.8,
                    "clearcoat": 0.6,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                },
                {
                    "id": "glass",
                    "displayName": "Glass (clear)",
                    "type": "standard",
                    "albedo": {"color": "rgba(200,220,255,1.0)", "type": "hex"},
                    "roughness": {"base": 0.1, "map": None},
                    "metalness": 0.1,
                    "clearcoat": 0.3,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                },
                {
                    "id": "chrome",
                    "displayName": "Chrome trim",
                    "type": "standard",
                    "albedo": {"color": "rgba(220,220,220,1.0)", "type": "hex"},
                    "roughness": {"base": 0.15, "map": None},
                    "metalness": 0.95,
                    "clearcoat": 0.8,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                },
                {
                    "id": "plastic-trim",
                    "displayName": "Plastic trim",
                    "type": "standard",
                    "albedo": {"color": "rgba(180,180,180,1.0)", "type": "hex"},
                    "roughness": {"base": 0.5, "map": None},
                    "metalness": 0.2,
                    "clearcoat": 0.0,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                },
                {
                    "id": "alloy",
                    "displayName": "Alloy wheel",
                    "type": "standard",
                    "albedo": {"color": "rgba(150,150,150,1.0)", "type": "hex"},
                    "roughness": {"base": 0.4, "map": None},
                    "metalness": 0.9,
                    "clearcoat": 0.5,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                },
                {
                    "id": "headlight-mat",
                    "displayName": "Headlight material",
                    "type": "standard",
                    "albedo": {"color": "rgba(255,255,255,1.0)", "type": "hex"},
                    "roughness": {"base": 0.2, "map": None},
                    "metalness": 0.6,
                    "clearcoat": 0.4,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                },
                {
                    "id": "rubber",
                    "displayName": "Rubber tire",
                    "type": "standard",
                    "albedo": {"color": "rgba(40,40,40,1.0)", "type": "hex"},
                    "roughness": {"base": 0.8, "map": None},
                    "metalness": 0.1,
                    "clearcoat": 0.0,
                    "emissive": None,
                    "normalMap": None,
                    "bumpMap": None,
                    "occlusionMap": None,
                    "localOverrides": []
                }
            ],
            "repetitionSystems": [
                {
                    "id": "wheel-repetition",
                    "name": "Wheel repetition system",
                    "type": "instanced",
                    "components":
            "detailMappings": []
        }
    }

    def __init__(self):
        self.templates = self.TEMPLATES
    
    def match_template(self, primary_type: str) -> Tuple[str, Dict[str, Any]]:
        if not primary_type:
            return "CAR", self.templates["CAR"]
        primary_type_lower = primary_type.lower()
        best_match = None
        best_score = 0
        for template_key, template in self.templates.items():
            score = 0
            for matcher in template.get("categoryMatchers", []):
                if matcher in primary_type_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_match = template_key
        if best_match is None:
            print(f"No template matched '{primary_type}', using CAR template")
            return "CAR", self.templates["CAR"]
        return best_match, self.templates[best_match]


class ComponentInstantiator:
    """Instantiate component templates with parameterized dimensions."""
    
    def __init__(self, dimensions: Dict[str, float]):
        self.dimensions = dimensions
    
    def eval_expression(self, expr: Any) -> Any:
        """Evaluate $eval expressions with dimension context."""
        if isinstance(expr, dict) and "$eval" in expr:
            return self._eval_dim_expr(expr["$eval"])
        elif isinstance(expr, list):
            return [self.eval_expression(item) for item in expr]
        elif isinstance(expr, dict):
            return {k: self.eval_expression(v) for k, v in expr.items()}
        else:
            return expr
    
    def _eval_dim_expr(self, expr: str) -> float:
        """Evaluate a dimension expression."""
        return _eval(expr, self.dimensions)


class SpecEnricher:
    """Enrich skeleton spec with template components."""
    
    def __init__(self, template: Dict[str, Any], dimensions: Dict[str, float], assessment: Dict[str, Any]):
        self.template = template
        self.dimensions = dimensions
        self.instantiator = ComponentInstantiator(dimensions)
        self.assessment = assessment
    
    def instantiate_template(self) -> Dict[str, Any]:
        """Instantiate all components with evaluated dimensions."""
        components = []
        for comp_def in self.template.get("components", []):
            comp = self._instantiate_component(comp_def)
            components.append(comp)
        
        materials = self.template.get("materials", [])
        
        repetition_systems = self.template.get("repetitionSystems", [])
        
        lighting = self.template.get("lighting", {})
        
        feature_review_targets = self.template.get("featureReviewTargets", [])
        
        detail_mappings = self.template.get("detailMappings", [])
        
        return {
            "components": components,
            "materials": materials,
            "repetitionSystems": repetition_systems,
            "lighting": lighting,
            "featureReviewTargets": feature_review_targets,
            "detailMappings": detail_mappings
        }
    
    def _instantiate_component(self, comp_def: Dict[str, Any]) -> Dict[str, Any]:
        """Instantiate a single component."""
        comp = comp_def.copy()
        
        def eval_obj(obj):
            if isinstance(obj, dict) and "$eval" in obj:
                return self.instantiator._eval_dim_expr(obj["$eval"])
            elif isinstance(obj, list):
                return [eval_obj(item) for item in obj]
            elif isinstance(obj, dict):
                return {k: eval_obj(v) for k, v in obj.items()}
            else:
                return obj
        
        comp["dimensions"] = eval_obj(comp.get("dimensions", {}))
        comp["transform"] = eval_obj(comp.get("transform", {}))
        
        if "localStart" in comp and isinstance(comp["localStart"], list):
            comp["localStart"] = [eval_obj(v) for v in comp["localStart"]]
        if "localEnd" in comp and isinstance(comp["localEnd"], list):
            comp["localEnd"] = [eval_obj(v) for v in comp["localEnd"]]
        
        return comp
    
    def enrich_skeleton(self, skeleton: Dict[str, Any]) -> Dict[str, Any]:
        """Merge template into skeleton spec."""
        enriched = skeleton.copy()
        
        template_data = self.instantiate_template()
        
        enriched["components"] = template_data["components"]
        enriched["materials"] = template_data["materials"]
        enriched["repetitionSystems"] = template_data["repetitionSystems"]
        enriched["lighting"] = template_data["lighting"]
        enriched["featureReviewTargets"] = template_data["featureReviewTargets"]
        enriched["detailMappings"] = template_data["detailMappings"]
        
        return enriched


class SpecValidator:
    """Validate the enriched spec."""
    
    def __init__(self, spec: Dict[str, Any]):
        self.spec = spec
        self.errors = []
        self.warnings = []
    
    def validate(self) -> bool:
        """Run all validation checks."""
        self._validate_components()
        self._validate_materials()
        self._validate_repetition_systems()
        self._validate_lighting()
        self._validate_feature_review_targets()
        self._validate_detail_mappings()
        self._validate_attachment_contracts()
        self._validate_duplicate_ids()
        
        if self.errors:
            print("Validation Errors:")
            for error in self.errors:
                print(f"  - {error}")
        if self.warnings:
            print("\nWarnings:")
            for warning in self.warnings:
                print(f"  - {warning}")
        
        return len(self.errors) == 0
    
    def _validate_components(self):
        """Validate component structure."""
        components = self.spec.get("components", [])
        
        if not components:
            self.errors.append("No components found in spec")
            return
        
        for i, comp in enumerate(components):
            if not comp.get("id"):
                self.errors.append(f"Component {i} missing id")
            if not comp.get("name"):
                self.errors.append(f"Component {i} missing name")
            if not comp.get("primitive"):
                self.errors.append(f"Component {i} missing primitive")
            if not comp.get("material"):
                self.errors.append(f"Component {i} missing material")
            if not comp.get("parent"):
                self.errors.append(f"Component {i} missing parent")
    
    def _validate_materials(self):
        """Validate materials structure."""
        materials = self.spec.get("materials", [])
        
        for i, mat in enumerate(materials):
            if not mat.get("id"):
                self.errors.append(f"Material {i} missing id")
            if not mat.get("type"):
                self.errors.append(f"Material {i} missing type")
            if not mat.get("albedo"):
                self.errors.append(f"Material {i} missing albedo")
    
    def _validate_repetition_systems(self):
        """Validate repetition systems."""
        systems = self.spec.get("repetitionSystems", [])
        
        for i, sys in enumerate(systems):
            if not sys.get("id"):
                self.errors.append(f"Repetition system {i} missing id")
            if not sys.get("type"):
                self.errors.append(f"Repetition system {i} missing type")
    
    def _validate_lighting(self):
        """Validate lighting setup."""
        lighting = self.spec.get("lighting", {})
        
        if not lighting.get("setup"):
            self.warnings.append("Lighting setup not specified")
        if not lighting.get("keyLight"):
            self.warnings.append("Key light not specified")
        if not lighting.get
