# Proposal: Add Multi-View Synthesis Stage to Pipeline

## Problem Statement

The img2threejs pipeline currently only uses **one reference image** (the front view) for building 3D models, even when multiple views are provided. This results in incomplete reconstructions that miss critical geometry visible only from other angles.

### Current Behavior

When a user provides **any number of reference views** (1, 2, 3, 4, 5, 6, or more):

1. **Intake stage** records all views with metadata (roles: primary, secondary, critical, profile)
2. **Spec stage** only references the front view (`sourceImage: ...front.png`)
3. **Build stage** writes geometry from code assumptions, not multi-view analysis
4. **Review stage** only compares against the front view

### The Core Issue

The pipeline assumes a **fixed 6-view setup**, but real users provide **variable numbers of images**:

```
Single view:    [front.png]
Two views:      [front.png, back.png]
Three views:    [front.png, side.png, top.png]
Four views:     [front.png, back.png, left.png, right.png]
Six views:      [front.png, back.png, top.png, bottom.png, left.png, right.png]
Seven+ views:   [front.png, front-angle.png, back.png, ...]
```

**Current behavior**: Only the first/front view is used regardless of how many are provided.

### Evidence from PS5 DualSense Reconstruction

```
references/
├── ps5-dualsense-front.png    ← ONLY view used in spec + review
├── ps5-dualsense-back.png     ← UNUSED (shows rear shell, triggers, charging contacts)
├── ps5-dualsense-top.png      ← UNUSED (shows shell width taper, touchpad depth)
├── ps5-dualsense-bottom.png   ← UNUSED (shows handle underside, grip zones)
├── ps5-dualsense-left.png     ← UNUSED (shows handle flare angle, shell edge thickness)
└── ps5-dualsense-right.png    ← UNUSED (mirror of left profile)
```

**Registry** (`src/demos/registry.ts`):
```typescript
referenceImage: `${BASE}references/ps5-dualsense-front.png`,  // singular
```

**Spec** (`object-sculpt-spec.json`):
```json
"sourceImage": "/path/to/references/ps5-dualsense-front.png"  // singular
```

## Impact

- **Missing geometry**: Handle underside profile, rear shell curvature, trigger hinge placement unknown
- **Incorrect proportions**: Shell width taper only visible from top, handle flare angle only from side
- **Wasted effort**: User provides 6 views but 83% of information is discarded
- **Lower fidelity**: Models require 40+ iterations to approximate what multi-view analysis could provide in fewer passes

## Proposed Solution

Add a new pipeline stage between **intake** and **spec** that synthesizes information from **all provided views** (any number) into a unified 3D understanding.

### Pipeline Flow

```
CURRENT:
  intake → spec → build → review

PROPOSED:
  intake → multi-view-synthesis → spec → build → review
                   │
                   ├─ View count detection (1, 2, 3, ... N views)
                   ├─ View alignment (match features across views)
                   ├─ 3D feature extraction (depth from parallax, occlusion cues)
                   ├─ Per-view confidence scoring
                   └─ Unified geometry brief (dimensions, proportions, curvature per component)
```

### Handling Different View Counts

| Views | Behavior | Confidence | Use Case |
|-------|----------|------------|----------|
| **1 view** | Skip synthesis, use code-written geometry | Low | Quick prototype, single photo |
| **2 views** | Basic synthesis (front+back, or front+side) | Moderate | Simple objects, limited angles |
| **3-4 views** | Good synthesis (multiple angles) | Good | Most common, typical reference sets |
| **5-6 views** | Excellent synthesis (full coverage) | High | Complex objects, production quality |
| **7+ views** | Optimal synthesis (multiple per angle) | Very High | Professional photography, research |

### Adaptive Processing

The synthesis stage adapts based on input count:

```python
if view_count == 1:
    # Skip synthesis, proceed with code-written geometry
    brief = generate_minimal_brief(reference_image)
elif view_count == 2:
    # Basic synthesis: limited cross-view matching
    brief = basic_synthesis(views)
elif view_count <= 4:
    # Standard synthesis: good coverage
    brief = standard_synthesis(views)
elif view_count <= 6:
    # Full synthesis: comprehensive coverage
    brief = full_synthesis(views)
else:
    # Optimal synthesis: multiple per angle, refinement
    brief = optimal_synthesis(views)
```

### Stage Details

#### 1. View Alignment
- Match identifiable features across views (thumbstick positions, button clusters, seam lines)
- Compute relative camera poses between views (even without calibration)
- Identify which components are visible in which views

#### 2. 3D Feature Extraction
- **From front+back**: Shell thickness, component depth, trigger hinge geometry
- **From top+bottom**: Width taper, handle flare angle, underside profile
- **From left+right**: Handle curvature, shell edge thickness, profile silhouette
- **Cross-view**: Depth from parallax, occlusion boundaries

#### 3. Per-View Confidence Scoring
- Rate each component's reconstruction confidence based on view coverage
- Flag components that need additional views or approximation
- Guide the spec stage on which geometry can be exact vs inferred

#### 4. Unified Geometry Brief
Output a structured brief that the spec stage consumes:

```json
{
  "components": {
    "whiteTopShell": {
      "visibleIn": ["front", "top", "left", "right"],
      "dimensions": { "width": 160, "depth": 106, "height": 6 },
      "curvature": "butterfly profile with horn peaks at ±0.38",
      "confidence": 0.92
    },
    "handle": {
      "visibleIn": ["front", "left", "right"],
      "flareAngle": "15-20° from shoulder",
      "crossSection": "asymmetrical ellipse (flatter interior)",
      "confidence": 0.88
    },
    "rearShell": {
      "visibleIn": ["back"],
      "curvature": "concave recess for palm",
      "confidence": 0.75,
      "note": "single-view inference, may need refinement"
    }
  }
}
```

## Implementation Approach

### Option A: Agent-Driven Multi-View Analysis
- Extend the intake skill to analyze each view systematically
- Add a `multi_view_analysis.md` protocol to `grimoire/intake/`
- Agent extracts features from each view and synthesizes

**Pros**: No new dependencies, uses existing agent vision
**Cons**: Token-intensive, agent may miss cross-view correlations

### Option B: Deterministic Feature Matching
- Python script that detects SIFT/ORB features across views
- Computes homographies and relative poses
- Extracts depth cues from parallax

**Pros**: Deterministic, fast, reproducible
**Cons**: Needs calibration or known geometry assumptions

### Option C: Hybrid (Recommended)
- Deterministic feature matching for alignment
- Agent vision for semantic understanding ("this is the trigger hinge")
- Structured output that feeds directly into spec generation

## Files to Modify

1. **New**: `forge/stage1b_multi_view/synthesize.py` - Multi-view synthesis script
2. **New**: `grimoire/intake/multi_view_analysis.md` - Analysis protocol
3. **Modify**: `forge/stage2_spec/new_sculpt_spec.py` - Accept multi-view brief
4. **Modify**: `object-sculpt-spec.json` schema - Add `multiViewBrief` field
5. **Modify**: `src/demos/registry.ts` - Support `referenceImages` (plural)
6. **Modify**: `item-reconstruction-intake/SKILL.md` - Mandate multi-view synthesis

## Acceptance Criteria

- [ ] Pipeline processes **any number of provided views** (1, 2, 3, 4, 5, 6, 7+)
- [ ] Single-view inputs still work (backward compatible)
- [ ] Spec includes per-component view coverage and confidence
- [ ] Build stage receives dimensional data from multi-view analysis
- [ ] Review stage can compare against multiple views
- [ ] Model fidelity improves (fewer iterations to reach target quality)
- [ ] Confidence scores scale with view count (more views = higher confidence)
- [ ] System handles named views (front, back, top) and unnamed views (auto-detected)
- [ ] System handles duplicate/overlapping views (multiple angles of same view)

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Views processed | 1/6 (17%) | 100% of provided views |
| Iterations to pass Divine Eye | 40+ | 10-15 (with 3+ views) |
| Geometry confidence | Low (guessed) | Scales with view count |
| Component coverage | Partial | Complete |
| View flexibility | Fixed 6 only | 1 to N views |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Cross-view feature matching fails | High | Fallback to agent-driven analysis |
| Single-view objects waste time | Low | Skip synthesis if only 1 view provided |
| Token cost increases | Medium | Cache synthesis results, reuse across iterations |
| Variable view count causes confusion | Medium | Clear UI feedback on detected view count |
| Duplicate views cause redundancy | Low | Group by viewing angle, use best quality |
| Unnamed views misaligned | Medium | Auto-detect angles from image content |

## Related

- **GitHub Issue**: #58 (img2threejs/img2threejs)
- **Evidence**: PS5 DualSense reconstruction (40+ iterations without passing quality gates)
