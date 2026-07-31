# Multi-View Synthesis

## Overview

Multi-view synthesis extracts 3D geometry information from multiple reference images. It supports variable view counts (1 to N views) with adaptive processing.

## Quick Start

### Single View (No Synthesis)

When only one reference image is provided, synthesis is skipped:

```python
from forge.stage1b_multi_view import run_synthesis_for_intake
from pathlib import Path

result = run_synthesis_for_intake(
    image_paths=[Path("front.png")],
    item_name="My Object",
)
# result["status"] == "skipped"
```

### Multiple Views (With Synthesis)

When multiple reference images are provided:

```python
from forge.stage1b_multi_view import run_synthesis_for_intake
from pathlib import Path

result = run_synthesis_for_intake(
    image_paths=[
        Path("front.png"),
        Path("back.png"),
        Path("top.png"),
        Path("bottom.png"),
        Path("left.png"),
        Path("right.png"),
    ],
    item_name="PS5 DualSense Controller",
)
# result["status"] == "evidence-only"
# result["synthesisMode"] == "full"
# confidence is derived from measured evidence and remains below 0.5 until
# calibrated metric geometry/component reconstruction is available
```

## View Count Modes

| Views | Mode | Description |
|-------|------|-------------|
| 1 | single-view | Skip synthesis, use code geometry |
| 2 | basic | Basic cross-view evidence extraction |
| 3-4 | standard | Broader cross-view evidence extraction |
| 5-6 | full | Comprehensive evidence coverage |
| 7+ | optimal | Multiple observations per angle |

Mode describes available coverage only. Confidence is calculated from feature
coverage, match coverage/quality, pose evidence and calibrated depth evidence;
it is never assigned from image count alone.

## Runtime capabilities

The shipped default is pure Python 3.10+ standard library. It always supports
view inventory, PNG edge evidence, bounded matching, and an `evidence-only`
brief. Metric pose/depth additionally requires verified camera calibration and
an environment that provides optional OpenCV and NumPy support; without either,
the pipeline remains `evidence-only` rather than inventing depth. Those optional
libraries are not a project or CI dependency.

## Named vs Unnamed Views

### Named Views

If images have descriptive names, they're automatically detected:

```
controller-front.png  → "front"
controller-back.png   → "back"
controller-top.png    → "top"
controller-iso-front-right.png → "three-quarter-front-right"
controller-side-elevation.png → "side" (orientation is not inferred)
```

### Unnamed Views

If images don't have descriptive names, angles are auto-detected:

```
image1.png  → "image1" (angle auto-detected)
photo2.jpg  → "photo2" (angle auto-detected)
```

## Integration Points

### With Intake

```python
from forge.stage1b_multi_view import (
    run_synthesis_for_intake,
    enhance_intake_record_with_synthesis,
)

# During intake processing
synthesis_result = run_synthesis_for_intake(image_paths, item_name)

# Enhance intake record
intake_record = enhance_intake_record_with_synthesis(
    intake_record,
    synthesis_result,
)
```

### With Spec Generation

```python
from forge.stage1b_multi_view import enhance_spec_with_brief

# Enhance spec with geometry brief
spec = enhance_spec_with_brief(spec, brief)
```

### With Build Process

```python
from forge.stage1b_multi_view import enhance_build_with_brief

# Enhance build configuration
build_config = enhance_build_with_brief(build_config, brief)
```

### With Review Process

```python
from forge.stage1b_multi_view import (
    enhance_review_with_multi_view,
    compare_multi_view,
    aggregate_multi_view_scores,
)
from forge.stage4_review.divine_eye import evaluate as evaluate_divine_eye

# Enhance review configuration
review_config = enhance_review_with_multi_view(review_config, brief)

# Compare against multiple views with the existing deterministic Divine Eye
comparison = compare_multi_view(
    render_paths,
    reference_paths,
    evaluator=evaluate_divine_eye,
)

# Missing/error views contribute zero. Inspect passed, complete, missingViews,
# failedViews, worstView and worstScore in addition to the aggregate.
aggregated_score = aggregate_multi_view_scores(comparison["perViewResults"])
```

## API Reference

### Core Functions

#### `synthesize_geometry_brief(image_paths, output_path=None)`

Main synthesis function. Returns geometry brief with per-component dimensions and confidence.

#### `run_synthesis_for_intake(image_paths, item_name, output_dir=None)`

Integration function for intake process. Returns synthesis result with status and mode.

#### `enhance_intake_record_with_synthesis(intake_record, synthesis_result)`

Enhances intake record with synthesis results.

#### `enhance_spec_with_brief(spec, brief)`

Enhances sculpt spec with multi-view geometry brief.

#### `enhance_build_with_brief(build_config, brief)`

Enhances build configuration with multi-view geometry brief.

#### `enhance_review_with_multi_view(review_config, brief)`

Enhances review configuration with multi-view data.

### View Counter Functions

#### `count_views(image_paths)`

Returns the number of provided views.

#### `detect_named_views(image_paths)`

Detects named views from image filenames.

#### `group_duplicate_views(image_paths, named_views)`

Groups duplicate views (multiple angles of same view).

### Feature Detection Functions

#### `detect_features(image_path, method="auto", max_features=1000)`

Detects features in an image. Supports SIFT, ORB, and edge detection.

### Feature Matching Functions

#### `match_features(all_features, ratio_threshold=0.75)`

Matches features across all view pairs.

### Pose Estimation Functions

#### `estimate_relative_poses(match_results)`

Estimates conservative image-space pose hints between all view pairs.
Uncalibrated results are explicitly marked as heuristic and cannot produce
metric depth.

### Depth Estimation Functions

#### `estimate_depth(match_results, pose_estimation)`

Estimates depth only when calibrated pose plus explicit focal length and
baseline are available. It does not invent camera parameters.

## Geometry Brief Schema

The geometry brief has the following structure:

```json
{
  "viewCount": 6,
  "synthesisMode": "full",
  "confidence": 0.85,
  "namedViews": {
    "front": "path/to/front.png",
    "back": "path/to/back.png"
  },
  "featureCount": {
    "front": 245,
    "back": 189
  },
  "matchCount": 1247,
  "components": {
    "body": {
      "visibleIn": ["front", "top", "left", "right"],
      "dimensions": {"width": 160, "height": 66, "depth": 106},
      "curvature": "butterfly profile",
      "confidence": 0.92
    }
  },
  "notes": "Multi-view synthesis with 6 views"
}
```

## Edge Cases

### Single View
- Synthesis is skipped
- Returns minimal brief with low confidence
- Uses code-written geometry

### Low-Texture Surfaces
- Uses edge detection instead of feature matching
- Relies on contour matching
- Reports reduced confidence

### Misaligned Views
- Reports reduced confidence
- Does not claim calibrated pose or depth without camera intrinsics

### Partial Overlap
- Identifies overlapping regions
- Only matches features in overlap areas
- Marks non-overlapping components as low confidence

## Testing

Run tests:

```bash
python3 -m unittest discover -s forge/tests
```

## References

- `forge/stage1b_multi_view/` - Main module
- `grimoire/intake/multi_view_analysis.md` - Analysis protocol
- `grimoire/multi_view/geometry_brief_schema.json` - Brief schema
