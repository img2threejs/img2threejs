# Multi-View Analysis Protocol

## Overview

This protocol defines how to handle multiple reference images for procedural 3D reconstruction. The key insight: **images are independent evidence sources, NOT a fused multi-view set**.

## Core Principle

When you have a front image and a back image of the same object:

- **Front image** → front-facing geometry + front texture
- **Back image** → back-facing geometry + back texture
- **Do NOT fuse them** — they are independent evidence for different parts of the mesh
- **Depth (Z) comes from procedural parameters**, not from image analysis

## When To Use

Use this protocol when:
- Multiple reference images are provided (2+ views)
- The object has different surface patterns on different sides (e.g., CS2 Gamma Doppler)
- You need to texture both sides of a thin object (knife, blade, plate)

## What Each Image Gives You

### Front Image
- **2D silhouette** (X, Y coordinates) — the profile of the object
- **Front texture** — the pixels for front-facing polygons
- **Proportions** — relative sizes of components

### Back Image
- **Confirms the silhouette** — if the object is symmetric, the back matches the front
- **Back texture** — the pixels for back-facing polygons
- **Unique details** — any features only visible from the back

### What Neither Image Gives You
- **Z-dimension (depth/thickness)** — this comes from your knowledge of the object
- **Side profile** — without a side view, the side is a "guess" (straight extrusion)
- **Cross-section shape** — you define this (diamond for knife, rectangle for plate)

## Protocol Steps

### Step 1: Identify View Roles

For each image, determine its role:
- **Primary reference** — the main view (usually front) — drives silhouette and proportions
- **Secondary reference** — confirms silhouette, provides back/side texture
- **Supplementary** — additional angles for complex geometry

### Step 2: Extract Silhouette from Primary

From the primary reference (front):
1. Trace the 2D outline — this defines X, Y coordinates
2. Establish the coordinate system — where is (0, 0, 0)?
3. Set the scale — how many pixels = 1 Three.js unit?

### Step 3: Define Depth Procedurally

The Z-dimension does NOT come from the images. You define it:

```
Blade thickness at spine: 5mm (thick part)
Blade thickness at edge: 0mm (sharp part)
Guard thickness: 6mm
Grip diameter: 11mm
```

These values come from:
- Your knowledge of the object (a knife blade is thin)
- Reference photos that show thickness (3/4 angle, side view)
- Standard dimensions for the object type

### Step 4: Create Face-Specific Texturing

For a thin object with different patterns on each side:

1. **Create front faces** at Z = +thickness → apply front texture
2. **Create back faces** at Z = -thickness → apply back texture
3. **Bridge the edges** → connect front silhouette edges to back silhouette edges

The UV mapping must NOT mirror — front and back textures occupy distinct areas.

### Step 5: Define Cross-Section (for blades)

For a knife blade, the cross-section is a diamond shape:
- Spine (top): thick
- Edge (bottom): thin (0mm)
- Left face: front texture
- Right face: back texture

This is the "section curve" in the Volcano technique.

## Edge Cases

### Single View Only
- Use the front image for silhouette + texture
- Estimate depth from object knowledge
- The back is inferred (often mirrored)

### Symmetric Object (front = back)
- Still create separate front and back faces
- Can use the same texture for both sides
- But the geometry should still be two-sided

### Asymmetric Object (front ≠ back)
- Front image → front faces
- Back image → back faces
- Each side has its own texture
- The "bridge" between them defines the side profile

### No Side View
- The side is a straight extrusion (front edge → back edge)
- For a blade, add a grind (taper from spine to edge)
- Document that the side is approximated

## Quality Gates

Before proceeding to spec:
- [ ] Primary silhouette extracted correctly
- [ ] Front and back images are aligned (same scale, same object position)
- [ ] Depth values are defined (even if approximated)
- [ ] Face-specific texturing plan is documented
- [ ] Cross-section shape is defined (for blades)

## Integration Points

### With Intake
- Record each image's role (primary, secondary, supplementary)
- Do NOT fuse images into a single multi-view synthesis
- Store each image path separately

### With Spec
- Use primary silhouette for componentTree dimensions
- Use front image for front-facing material
- Use back image for back-facing material
- Document depth as procedural parameter

### With Build
- Create geometry using tapered curves (spine + taper + section)
- Apply front texture to front faces
- Apply back texture to back faces
- Bridge edges between front and back silhouettes

### With Review
- Compare front render against front reference
- Compare back render against back reference
- Compare side render against expectation (even without side reference)
- Report per-face confidence
