# Tasks: Multi-View Synthesis Stage

## Phase 1: Foundation (Week 1)

### Task 1.1: Create Multi-View Synthesis Module
**Priority**: High  
**Estimate**: 3 days  
**Dependencies**: None

- [x] Create `forge/stage1b_multi_view/` directory structure
- [x] Implement `synthesize.py` - main entry point
- [x] Implement `feature_detector.py` - SIFT/ORB feature detection
- [x] Implement `feature_matcher.py` - cross-view feature matching
- [x] Implement `pose_estimator.py` - relative camera pose estimation
- [x] Implement `depth_estimator.py` - depth from parallax
- [x] Add unit tests for each module

**Acceptance Criteria**:
- [x] Can detect features in a single image
- [x] Can match features between two views
- [x] Can estimate relative pose between views
- [x] Can compute depth cues from matched features when calibration is provided
- [x] All unit tests pass

### Task 1.2: Define Geometry Brief Schema
**Priority**: High  
**Estimate**: 1 day  
**Dependencies**: None

- [x] Create `grimoire/multi_view/geometry_brief_schema.json`
- [x] Define component geometry structure
- [x] Define confidence scoring structure
- [x] Define view coverage structure
- [x] Add schema validation

**Acceptance Criteria**:
- [x] Schema validates sample geometry briefs
- [x] Schema is backwards compatible with existing specs
- [x] Documentation is complete

### Task 1.3: Update Intake Record Schema
**Priority**: Medium  
**Estimate**: 1 day  
**Dependencies**: Task 1.2

- [x] Add optional `synthesis` field in `grimoire/multi_view/intake_record_schema.json`
- [x] Add optional `multiViewBrief` extension in `grimoire/multi_view/object_sculpt_spec_extension_schema.json`
- [x] Update validation scripts

**Acceptance Criteria**:
- [x] New fields are optional (backwards compatible)
- [x] Validation passes with and without new fields
- [x] Documentation is updated

### Task 1.4: Implement View Count Detection
**Priority**: High  
**Estimate**: 1 day  
**Dependencies**: None

- [x] Create `view_counter.py` - detect number of provided views
- [x] Implement named view detection (front, back, top, etc.)
- [x] Implement unnamed view auto-detection (readable PNG signature clustering into non-semantic `auto-*` labels)
- [x] Implement duplicate view grouping (multiple angles of same view)
- [x] Add unit tests for different view counts and unnamed/duplicate handling

**Acceptance Criteria**:
- [x] Correctly counts 1 to N views
- [x] Handles named views (front, back, top, left, right, bottom)
- [x] Handles unnamed readable PNG views (signature clusters; no semantic pose claim)
- [x] Groups duplicate views (multiple front angles)
- [x] All unit tests pass

## Phase 2: Integration (Week 2)

### Task 2.1: Integrate Synthesis into Intake Pipeline
**Priority**: High  
**Estimate**: 2 days  
**Dependencies**: Task 1.1, Task 1.3

- [x] Modify the actual intake boundary, `forge/stage2_spec/new_pre_spec_assessment.py`, to call synthesis
- [x] Add synthesis result to the assessment record
- [x] Handle single-view fallback (skip synthesis)
- [x] Handle variable view count (1 to N)
- [x] Return `request-input` for unreadable multi-view local paths; synthesis failures remain fail-closed

**Acceptance Criteria**:
- [x] Intake with 1 view skips synthesis gracefully
- [x] Intake with 2+ views triggers bounded evidence-only synthesis
- [x] Metric depth envelopes require calibrated capture; uncalibrated input remains evidence-only
- [x] Synthesis failures are logged and handled
- [x] Integration tests pass

### Task 2.2: Update Spec Generation to Use Brief
**Priority**: High  
**Estimate**: 2 days  
**Dependencies**: Task 1.2, Task 2.1

- [x] Modify `forge/stage2_spec/new_sculpt_spec.py` to accept brief
- [x] Map matching component dimensions from the brief into `multiViewDimensions`
- [x] Update generated scale from brief dimensions
- [x] Update curvature data from brief
- [x] Preserve synthesis and brief confidence in the spec

**Acceptance Criteria**:
- [x] Spec generation uses brief when available
- [x] Spec falls back to existing logic when brief is missing
- [x] Component dimensions match brief values
- [x] Unit tests pass

### Task 2.3: Update Registry for Multiple References
**Priority**: Medium  
**Estimate**: 1 day  
**Dependencies**: Task 1.3

- [x] Add `referenceImages` (plural) field to `DemoEntry` type
- [x] Update `src/demos/registry.ts` to support multiple images
- [x] Maintain backwards compatibility with `referenceImage` (singular)

**Acceptance Criteria**:
- [x] Registry accepts both singular and plural reference fields
- [x] Plural field takes precedence when both are provided
- [x] TypeScript compilation passes

## Phase 3: Build Integration (Week 3)

### Task 3.1: Update Build to Use Brief Dimensions
**Priority**: High  
**Estimate**: 3 days  
**Dependencies**: Task 2.2

- [x] Modify `generate_threejs_factory.py` to use brief dimensions
- [x] Update generated component scale from `multiViewDimensions`
- [x] Update curvature calculations from brief
- [x] Carry multi-view brief/provenance into generated runtime evidence

**Acceptance Criteria**:
- [x] Generated Three.js code uses brief dimensions
- [x] Unit test verifies the emitted scale values
- [ ] Browser visual dimension validation needs a tracked demo consumer and reference render set

### Task 3.2: Update Review to Compare Multiple Views
**Priority**: Medium  
**Estimate**: 2 days  
**Dependencies**: Task 2.3

- [x] Modify `divine_eye.py` to accept ordered multiple reference/render pairs
- [x] Compare silhouette IoU and proportion delta for every provided view
- [x] Aggregate fidelity fail-closed across all views
- [x] Add per-view scoring

**Acceptance Criteria**:
- [x] Review compares against all provided views
- [x] Scores are aggregated across views
- [x] Review output includes per-view breakdown
- [x] Unit tests pass

## Phase 4: Agent Integration (Week 4)

### Task 4.1: Create Multi-View Analysis Protocol
**Priority**: Medium  
**Estimate**: 2 days  
**Dependencies**: Task 1.1

- [x] Create `grimoire/intake/multi_view_analysis.md`
- [x] Define agent-driven analysis steps
- [x] Define feature extraction guidelines
- [x] Define bounded evidence-only synthesis workflow

**Acceptance Criteria**:
- [x] Protocol is clear and actionable
- [x] Protocol distinguishes calibrated reconstruction from evidence-only input
- [x] Protocol covers single-view and unreadable-input edge cases

### Task 4.2: Update Intake Skill
**Priority**: Medium  
**Estimate**: 1 day  
**Dependencies**: Task 4.1

- [x] Update `item-reconstruction-intake/SKILL.md`
- [x] Add multi-view synthesis mandate
- [x] Update workflow steps
- [x] Add examples

**Acceptance Criteria**:
- [x] Skill mandates synthesis for multi-view inputs
- [x] Skill provides clear instructions
- [x] Examples are provided

### Task 4.3: Create Hybrid Synthesis Mode
**Priority**: Low  
**Estimate**: 3 days  
**Dependencies**: Task 1.1, Task 4.1

- [x] Implement agent-driven synthesis fallback
- [x] Combine deterministic and agent results
- [x] Add confidence weighting
- [x] Handle conflicts between methods

**Acceptance Criteria**:
- [x] Hybrid mode works when deterministic fails
- [x] Results are merged intelligently
- [x] Confidence scores reflect method quality
- [x] Unit tests pass

## Phase 5: Testing & Documentation (Week 5)

### Task 5.1: Create Test Suite
**Priority**: High  
**Estimate**: 3 days  
**Dependencies**: All previous tasks

- [x] Add multi-view tests under the repository's `forge/tests/` convention
- [x] Add unit tests for feature detection
- [x] Add unit tests for feature matching
- [x] Add integration tests for full pipeline
- [x] Add regression tests with generated known multi-view sets and a PS5 CLI smoke run

**Acceptance Criteria**:
- [x] Targeted unit and integration tests pass
- [x] Code coverage > 80% (85% for `forge.stage1b_multi_view`)
- [x] No regressions in existing tests

### Task 5.2: Create Documentation
**Priority**: Medium  
**Estimate**: 2 days  
**Dependencies**: All previous tasks

- [x] Update `grimoire/scripts.md` with multi-view commands
- [x] Update `grimoire/intake/multi_view_analysis.md` as the user guide
- [x] Document CLI and data-flow APIs in the scripts guide
- [x] Update README with a multi-view example

**Acceptance Criteria**:
- [x] Documentation matches the implemented bounded behavior
- [x] Examples are provided
- [x] CLI/API entry points are documented
- [x] User guide is clear

### Task 5.3: Performance Optimization
**Priority**: Low  
**Estimate**: 2 days  
**Dependencies**: Task 5.1

- [x] Profile synthesis pipeline
- [x] Optimize feature detection
- [x] Optimize feature matching
- [x] Add caching for repeated views

**Acceptance Criteria**:
- [x] Synthesis completes in < 5 seconds for the six-view synthetic regression set
- [x] Memory usage is bounded by the feature cap and in-process cache key
- [x] Caching improves repeated feature extraction
- [x] Benchmarks are documented

## Milestones

| Milestone | Target Date | Dependencies |
|-----------|-------------|--------------|
| Foundation Complete | Week 1 | - |
| Integration Complete | Week 2 | Foundation |
| Build Integration Complete | Week 3 | Integration |
| Agent Integration Complete | Week 4 | Build Integration |
| Testing & Documentation Complete | Week 5 | Agent Integration |
| **Release** | **Week 6** | All |

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Feature matching fails on real images | Medium | High | Implement agent-driven fallback |
| Performance too slow | Low | Medium | Add caching, optimize algorithms |
| Backwards compatibility breaks | Low | High | Extensive testing, optional fields |
| Agent synthesis inconsistent | Medium | Medium | Hybrid mode, confidence weighting |

## Success Criteria

- [x] Pipeline processes all supplied views for the PS5 DualSense CLI smoke set
- [ ] Model iterations reduced from 40+ to 10-15 (requires comparative production reconstruction evidence)
- [x] Divine Eye multi-view gates pass in integration tests
- [x] Backwards compatible with single-view inputs
- [x] Documentation is complete
- [x] All tests pass
