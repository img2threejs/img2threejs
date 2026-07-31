# Design: Multi-View Synthesis Stage

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    MULTI-VIEW SYNTHESIS                         │
└─────────────────────────────────────────────────────────────────┘

  INPUTS                          PROCESSING                     OUTPUTS
  ───────                         ──────────                     ───────

  ┌──────────┐                ┌──────────────┐             ┌──────────────┐
  │ front.png│───────────────▶│              │             │              │
  └──────────┘                │              │             │              │
  ┌──────────┐                │    View      │             │   Geometry   │
  │ back.png │───────────────▶│   Alignment  │────────────▶│    Brief     │
  └──────────┘                │              │             │              │
  ┌──────────┐                │              │             │              │
  │ top.png  │───────────────▶│              │             │              │
  └──────────┘                └──────┬───────┘             └──────────────┘
  ┌──────────┐                       │                            │
  │bottom.png│───────────────▶┌──────▼───────┐             ┌──────▼───────┐
  └──────────┘                │   Feature    │             │   Per-View   │
  ┌──────────┐                │  Extraction  │────────────▶│  Confidence  │
  │ left.png │───────────────▶│              │             │   Scores     │
  └──────────┘                │              │             │              │
  ┌──────────┐                │              │             │              │
  │right.png │───────────────▶│              │             │              │
  └──────────┘                └──────────────┘             └──────────────┘
```

## Data Flow

### 1. Input Schema

```typescript
interface MultiViewInput {
  views: {
    front?: ImageReference;
    back?: ImageReference;
    top?: ImageReference;
    bottom?: ImageReference;
    left?: ImageReference;
    right?: ImageReference;
  };
  metadata: {
    itemName: string;
    dimensions?: {
      width: number;
      height: number;
      depth: number;
      unit: 'mm' | 'cm' | 'in';
    };
  };
}

interface ImageReference {
  path: string;
  role: 'primary' | 'secondary' | 'critical' | 'profile';
  resolution: 'high' | 'medium' | 'low';
  features?: DetectedFeature[];
}
```

### 2. Processing Pipeline

```typescript
interface SynthesisResult {
  alignment: ViewAlignment;
  features: FeatureExtraction;
  confidence: ConfidenceScores;
  brief: GeometryBrief;
}

interface ViewAlignment {
  matchedFeatures: FeatureMatch[];
  relativePoses: CameraPose[];
  visibleComponents: ComponentVisibility[];
}

interface FeatureExtraction {
  components: ComponentGeometry[];
  crossViewDepth: DepthCues;
  occlusionBoundaries: Occlusion[];
}

interface ConfidenceScores {
  perComponent: Map<string, number>;
  overall: number;
  viewCoverage: Map<string, string[]>;
}

interface GeometryBrief {
  components: ComponentBrief[];
  assembly: AssemblyGuide;
  unknowns: UnknownGeometry[];
}
```

## Component Analysis by View

### View Contribution Matrix

| Component | Front | Back | Top | Bottom | Left | Right |
|-----------|-------|------|-----|--------|------|-------|
| White top shell | ✓ | ✓ | ✓ | - | ✓ | ✓ |
| White bottom shell | ✓ | ✓ | - | ✓ | ✓ | ✓ |
| Handle | ✓ | - | ✓ | ✓ | ✓ | ✓ |
| Touchpad | ✓ | - | ✓ | - | - | - |
| Thumbsticks | ✓ | - | ✓ | - | - | - |
| Triggers | ✓ | ✓ | - | - | ✓ | ✓ |
| Buttons (ABXY) | ✓ | - | - | - | - | - |
| D-pad | ✓ | - | - | - | - | - |
| USB-C port | ✓ | ✓ | - | - | - | - |
| Speaker grille | ✓ | ✓ | - | - | - | - |
| Charging contacts | - | ✓ | - | ✓ | - | - |

### Feature Extraction Rules

```python
# Front view provides:
- Component XY positions (buttons, thumbsticks, touchpad)
- Surface material boundaries
- Front-facing silhouette

# Back view provides:
- Rear shell curvature
- Trigger hinge geometry
- Charging contact positions
- USB-C port depth

# Top view provides:
- Shell width taper (critical for butterfly profile)
- Touchpad recess depth
- Shoulder button overhang
- Handle attachment angle

# Bottom view provides:
- Handle underside profile
- Grip texture zones
- Speaker grille depth
- Charging contact recess

# Left/Right profiles provide:
- Handle flare angle (15-20° from shoulder)
- Shell edge thickness
- Trigger travel depth
- Cross-section silhouette
```

## Algorithm: Cross-View Feature Matching

### Step 1: Feature Detection

```python
def detect_features(image: np.ndarray) -> List[Feature]:
    """Detect SIFT/ORB features in image."""
    detector = cv2.SIFT_create()
    keypoints, descriptors = detector.detectAndCompute(image, None)
    return [Feature(kp, desc) for kp, desc in zip(keypoints, descriptors)]
```

### Step 2: Feature Matching

```python
def match_features(
    features_a: List[Feature],
    features_b: List[Feature]
) -> List[FeatureMatch]:
    """Match features between two views."""
    bf = cv2.BFMatcher()
    matches = bf.knnMatch(features_a.descriptors, features_b.descriptors, k=2)
    # Apply ratio test
    good_matches = [m for m, n in matches if m.distance < 0.75 * n.distance]
    return good_matches
```

### Step 3: Pose Estimation

```python
def estimate_relative_pose(
    matches: List[FeatureMatch],
    image_size: Tuple[int, int]
) -> CameraPose:
    """Estimate relative camera pose between views."""
    # Use essential matrix
    E, mask = cv2.findEssentialMat(
        points_a, points_b, camera_matrix,
        cv2.RANSAC, 0.999, 1.0
    )
    _, R, t, mask = cv2.recoverPose(E, points_a, points_b, camera_matrix)
    return CameraPose(R, t)
```

### Step 4: Depth from Parallax

```python
def compute_depth_cues(
    pose_a: CameraPose,
    pose_b: CameraPose,
    matched_points: np.ndarray
) -> DepthCues:
    """Compute depth from stereo parallax."""
    # Triangulate points
    points_4d = cv2.triangulatePoints(
        pose_a.projection_matrix,
        pose_b.projection_matrix,
        matched_points[0],
        matched_points[1]
    )
    depths = points_4d[2] / points_4d[3]
    return DepthCues(depths, matched_points)
```

## Output: Geometry Brief Schema

```json
{
  "schemaVersion": "1.0",
  "generatedAt": "2026-07-29T08:45:00Z",
  "sourceViews": ["front", "back", "top", "left", "right"],
  "components": [
    {
      "id": "whiteTopShell",
      "name": "White Top Shell",
      "visibleIn": ["front", "top", "left", "right"],
      "dimensions": {
        "width": { "value": 160, "unit": "mm", "confidence": 0.95 },
        "depth": { "value": 106, "unit": "mm", "confidence": 0.90 },
        "height": { "value": 6, "unit": "mm", "confidence": 0.85 }
      },
      "curvature": {
        "profile": "butterfly",
        "hornPeaks": [-0.38, 0.38],
        "centerDip": 0.03,
        "confidence": 0.88
      },
      "materials": ["whiteShell"],
      "confidence": 0.92,
      "viewCoverage": {
        "front": "silhouette + surface",
        "top": "width taper + curvature",
        "left": "edge thickness + profile",
        "right": "edge thickness + profile"
      }
    },
    {
      "id": "handle",
      "name": "Handle (Left/Right)",
      "visibleIn": ["front", "top", "bottom", "left", "right"],
      "dimensions": {
        "length": { "value": 85, "unit": "mm", "confidence": 0.90 },
        "flareAngle": { "value": 17, "unit": "degrees", "confidence": 0.85 }
      },
      "crossSection": {
        "type": "asymmetrical_ellipse",
        "interiorFlatness": 0.7,
        "exteriorRoundness": 1.25,
        "confidence": 0.82
      },
      "taper": {
        "shoulderRadius": 14,
        "tipRadius": 4,
        "curveType": "catmullrom",
        "confidence": 0.88
      },
      "materials": ["whiteShell", "blackInner"],
      "confidence": 0.88,
      "viewCoverage": {
        "front": "flare direction + attachment",
        "top": "width + curve path",
        "bottom": "underside profile",
        "left": "cross-section + taper",
        "right": "cross-section + taper"
      }
    }
  ],
  "assembly": {
    "type": "sandwich",
    "description": "Central black chassis nested between two white outer plates",
    "partingLine": "Longitudinal seam along handle center",
    "confidence": 0.90
  },
  "unknowns": [
    {
      "component": "rearShellPalmRecess",
      "reason": "Only visible from back view, curvature ambiguous",
      "suggestedAction": "Approximate with concave surface, refine in form pass"
    }
  ]
}
```

## Integration Points

### 1. Intake → Synthesis

```python
# In forge/stage1_intake/intake.py
def run_intake(reference_images: List[str]) -> IntakeResult:
    # ... existing intake logic ...
    
    # NEW: Trigger multi-view synthesis if multiple views provided
    if len(reference_images) > 1:
        synthesis_result = run_multi_view_synthesis(reference_images)
        intake_result.synthesis = synthesis_result
    
    return intake_result
```

### 2. Synthesis → Spec

```python
# In forge/stage2_spec/new_sculpt_spec.py
def generate_spec(intake_result: IntakeResult) -> SculptSpec:
    spec = base_spec_from_intake(intake_result)
    
    # NEW: Incorporate multi-view brief if available
    if intake_result.synthesis:
        spec.multiViewBrief = intake_result.synthesis.brief
        spec.components = enhance_components_with_brief(
            spec.components,
            intake_result.synthesis.brief
        )
    
    return spec
```

### 3. Build → Multi-View-Aware Geometry

```typescript
// In src/createObjectModel.ts
function createModel(spec: SculptSpec): THREE.Group {
  const group = new THREE.Group();
  
  // NEW: Use multi-view brief for dimensional accuracy
  if (spec.multiViewBrief) {
    for (const component of spec.multiViewBrief.components) {
      const geometry = createComponentGeometry(component);
      group.add(geometry);
    }
  } else {
    // Fallback to existing code-written geometry
    return createModelFromCode(spec);
  }
  
  return group;
}
```

## Error Handling

### Single-View Fallback

```python
def synthesize_or_fallback(views: List[ImageReference]) -> SynthesisResult:
    """If only one view, skip synthesis and return minimal brief."""
    if len(views) == 1:
        return SynthesisResult(
            alignment=None,
            features=None,
            confidence={"overall": 0.5},
            brief=create_minimal_brief(views[0])
        )
    return run_full_synthesis(views)
```

### Feature Matching Failure

```python
def handle_matching_failure(views: List[ImageReference]) -> SynthesisResult:
    """If feature matching fails, fall back to agent-driven analysis."""
    logger.warning("Feature matching failed, falling back to agent analysis")
    return agent_driven_synthesis(views)
```

## Performance Considerations

| Operation | Time Complexity | Space Complexity |
|-----------|-----------------|------------------|
| Feature detection | O(n) per image | O(n) |
| Feature matching | O(m * n) | O(m * n) |
| Pose estimation | O(m) | O(m) |
| Triangulation | O(m) | O(m) |
| **Total** | **O(m * n)** | **O(m * n)** |

Where:
- n = number of features per image
- m = number of matched features

## Testing Strategy

1. **Unit tests**: Feature detection, matching, pose estimation
2. **Integration tests**: Full synthesis pipeline with known multi-view sets
3. **Regression tests**: Compare model quality before/after synthesis
4. **Edge cases**: Single view, mismatched views, low-quality images
