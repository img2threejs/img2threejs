# Capability: Multi-View Synthesis

## ADDED Requirements

### Requirement: Flexible View Input Processing
The system SHALL accept any number of reference images (1 to N) and process them into a unified 3D understanding.

#### Scenario: Single view provided
- **Given** the user provides 1 reference image
- **When** the intake stage processes the image
- **Then** the system SHALL skip multi-view synthesis
- **And** the system SHALL generate a minimal geometry brief with lower confidence scores
- **And** the system SHALL proceed with existing code-written geometry

#### Scenario: Two views provided
- **Given** the user provides 2 reference images
- **When** the intake stage processes the images
- **Then** the system SHALL detect features in both images
- **And** the system SHALL match features between the two views
- **And** the system SHALL generate a geometry brief with moderate confidence

#### Scenario: Three views provided
- **Given** the user provides 3 reference images
- **When** the intake stage processes the images
- **Then** the system SHALL detect features in all 3 images
- **And** the system SHALL match features across views
- **And** the system SHALL generate a geometry brief with good confidence

#### Scenario: Four or more views provided
- **Given** the user provides 4+ reference images
- **When** the intake stage processes the images
- **Then** the system SHALL detect features in all images
- **And** the system SHALL match features across all overlapping views
- **And** the system SHALL generate a geometry brief with high confidence

#### Scenario: Seven or more views provided
- **Given** the user provides 7+ reference images (including multiple angles of same view)
- **When** the intake stage processes the images
- **Then** the system SHALL group images by approximate viewing angle
- **And** the system SHALL use the best quality image per angle
- **And** the system SHALL optionally use multiple angles for refinement

#### Scenario: Named views provided
- **Given** the user provides images with explicit view names (e.g., "front", "back", "top")
- **When** the intake stage processes the images
- **Then** the system SHALL use provided names for view alignment
- **And** the system SHALL map named views to standard positions
- **And** the system SHALL prioritize named views over auto-detected views

#### Scenario: Unnamed views provided
- **Given** the user provides images without explicit view names
- **When** the intake stage processes the images
- **Then** the system SHALL auto-detect viewing angles from image content
- **And** the system SHALL cluster similar views together
- **And** the system SHALL generate view labels based on detected features

### Requirement: Feature Detection and Matching
The system SHALL detect visual features in each reference image and match them across views.

#### Scenario: Distinctive features present
- **Given** reference images contain distinctive visual features (buttons, seams, edges)
- **When** feature detection runs on each image
- **Then** the system SHALL detect at least 100 features per image
- **And** the system SHALL match features between overlapping views
- **And** the system SHALL compute feature match confidence scores

#### Scenario: Low-texture surfaces
- **Given** reference images contain large uniform surfaces (white shells)
- **When** feature detection runs on each image
- **Then** the system SHALL detect edge features along surface boundaries
- **And** the system SHALL use contour matching for uniform regions
- **And** the system SHALL report reduced confidence for matched features

### Requirement: 3D Geometry Extraction
The system SHALL extract 3D geometric information from matched features across views.

#### Scenario: Depth from parallax
- **Given** matched features between two views with known relative pose
- **When** triangulation runs on matched points
- **Then** the system SHALL compute depth values for matched points
- **And** the system SHALL generate depth maps for each view
- **And** the system SHALL identify occlusion boundaries

#### Scenario: Component dimension estimation
- **Given** features belonging to the same component across multiple views
- **When** 3D reconstruction runs on component features
- **Then** the system SHALL estimate component dimensions (width, height, depth)
- **And** the system SHALL compute dimension confidence based on view coverage
- **And** the system SHALL output dimensions in the geometry brief

### Requirement: Confidence Scoring
The system SHALL assign confidence scores to each reconstructed component based on view coverage and feature quality.

#### Scenario: High view coverage
- **Given** a component visible in 4+ views with high-quality features
- **When** confidence scoring runs
- **Then** the system SHALL assign confidence > 0.85
- **And** the system SHALL mark component as "high confidence"

#### Scenario: Low view coverage
- **Given** a component visible in only 1-2 views
- **When** confidence scoring runs
- **Then** the system SHALL assign confidence < 0.60
- **And** the system SHALL mark component as "low confidence"
- **And** the system SHALL suggest additional views or approximation

### Requirement: Geometry Brief Generation
The system SHALL output a structured geometry brief that the spec and build stages consume.

#### Scenario: Brief generation
- **Given** completed multi-view synthesis
- **When** geometry brief generation runs
- **Then** the system SHALL output a JSON brief with per-component dimensions
- **And** the system SHALL include curvature data for curved surfaces
- **And** the system SHALL include confidence scores for all values
- **And** the system SHALL include view coverage information

#### Scenario: Brief consumption
- **Given** a geometry brief from synthesis
- **When** the spec stage generates the sculpt spec
- **Then** the system SHALL incorporate brief dimensions into component definitions
- **And** the system SHALL use brief curvature for surface profiles
- **And** the system SHALL propagate confidence scores to the spec

## MODIFIED Requirements

### Requirement: Reference Image Handling
The system SHALL support both single and multiple reference images.

#### Scenario: Backwards compatibility
- **Given** an existing project with `referenceImage` (singular) field
- **When** the system processes the project
- **Then** the system SHALL use the single reference image
- **And** the system SHALL skip multi-view synthesis
- **And** the system SHALL maintain existing behavior

#### Scenario: Multiple references
- **Given** a project with `referenceImages` (plural) field
- **When** the system processes the project
- **Then** the system SHALL use all provided reference images
- **And** the system SHALL trigger multi-view synthesis
- **And** the system SHALL prefer plural field over singular field

### Requirement: Spec Generation
The system SHALL incorporate multi-view data into spec generation.

#### Scenario: Brief available
- **Given** a geometry brief from multi-view synthesis
- **When** the spec stage generates the sculpt spec
- **Then** the system SHALL include `multiViewBrief` field in the spec
- **And** the system SHALL use brief dimensions for component definitions
- **And** the system SHALL include confidence scores in the spec

#### Scenario: Brief unavailable
- **Given** no geometry brief (single-view input)
- **When** the spec stage generates the sculpt spec
- **Then** the system SHALL generate spec without brief
- **And** the system SHALL use existing code-written geometry logic
- **And** the system SHALL maintain backwards compatibility

## REMOVED Requirements

### Requirement: Single-View Only Processing
The system SHALL NOT restrict processing to a single reference image.

#### Scenario: Multiple views ignored
- **Given** a system with single-view-only processing
- **When** multiple views are provided
- **Then** the system SHALL NOT ignore additional views
- **And** the system SHALL process all available views
- **And** the system SHALL generate multi-view geometry data
