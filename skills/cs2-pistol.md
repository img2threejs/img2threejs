reate a high-fidelity procedural Three.js 3D reconstruction of:

      ★ Glock-18 | Ghost Protocol
      Wear condition: Well-Worn
      Item family: weapon
      Subtype: glock-18

  The goal is maximum observable agreement with the supplied FRONT and BACK reference images, covering:
  - Slide silhouette, frame/grip ergonomics, ejection port, trigger guard, and internal barrel/breech geometry.
  - Sights, trigger shoe, safety blade, magazine extension, and pin placement.
  - Translucent red polymer PBR parameters (transmission, IOR, thickness, internal occlusion), metallic breech block response, and surface abrasions.
  - Ghost Protocol cybernetic circuit linework, decal positioning ("GHOST (*)", "PROTOCOL", ">_"), and refraction placement.
  - Front/back consistency, camera framing, and 3D orientation.

  Treat the provided FRONT and BACK images as two views of the same physical item.

  ### Workflow & Protocol:

  1. Image Validation: Verify front/back alignment, scale consistency, readable resolution, and reject inconsistent/duplicate inputs.
  2. Layered Observation: Isolate visible polymer frame, PBR transmission, visible internal sub-meshes, and wear features. Separate observation from inference.
  3. Geometry Contract: Reconcile front/back silhouettes using a dedicated glock-18 adapter (never substitute generic pistol models).
  4. Material Contract: Use de-lit reference projection for translucent shell linework and decals. Keep PBR channels (Albedo, Transmission, Roughness, Metalness, Normal, AO) strictly independent.
  5. Evidence Honesty: Mark hidden internal geometry with per-region confidence scores. Return `request-input` if crucial features are ambiguous.
  6. Exactness Tiers:
     - `image-only`: Derived strictly from the 2D reference images.
     - `metadata-assisted`: Verified skin/float metadata without raw texture maps.
     - `exact-texture`: Full raw texture maps and provenance available.
  7. Context Priority: The uploaded FRONT and BACK images strictly override any existing preview fixtures or legacy code in `builds/`.
  8. Review Gates: Render fixed reference view + ≥2 orbit views. Reject degenerate flat planes. A high global score cannot bypass a failed critical feature.
  9. Output Report: Provide matched geometry/material features, front/back consistency, exactness tier, and recommended action (`continue` | `refine-spec` | `refine-code` | `request-input` | `stop`).

  FRONT: [Image #9]
  BACK: [Image #10]

  Do not claim that the model is exact unless the evidence supports that claim.