Create a high-fidelity procedural Three.js 3D reconstruction of:

    ★ Classic Knife | Fade
    Wear condition: Minimal Wear
    Item family: knife
    Subtype: classic-knife

    The goal is maximum observable agreement with the supplied reference images,
    including:

    - blade silhouette and proportions;
    - spine, cutting edge, tip and bevel profile;
    - guard, handle, pommel and hardware placement;
    - metal base colour and exposed metal;
    - Fade colour gradient, transition zones and pattern placement;
    - roughness, metallic response, clearcoat and edge wear;
    - front/back consistency;
    - camera framing and object orientation.

    The FRONT and BACK images below are the only visual ground truth. Treat them as
    two views of the same physical item, not as separate objects.

    Workflow:

    1. Validate both images before reconstruction.
       - Check that both images contain the same knife.
       - Check for duplicate views, unreadable regions, cropping, empty background
       and inconsistent scale.
       - Record the role of each image as `front` or `back`.

    2. Analyze the images before authoring geometry.
       - Separate observation from inference.
       - Identify visible geometry, material regions, wear, lighting artifacts and
       hidden areas.
       - Do not use the filename, skin name or prior model as a substitute for image
       evidence.

    3. Build one geometry contract from both views.
       - Reconcile front and back silhouette, proportions and orientation.
       - Use a dedicated Classic Knife adapter.
       - Do not substitute a Bayonet, Karambit, Navaja, Flip Knife or generic knife
       model.
       - Do not use a generic knife tree when a Classic Knife-specific detail is
       visible.

    4. Build the surface/material contract.
       - Separate blade coating, exposed metal, handle, guard, hardware and wear
       regions.
       - Preserve the actual Fade colour placement from the images.
       - Use de-lit reference projection for the visible pattern whenever
       technically possible.
       - Do not replace the observed Fade pattern with a generic procedural gradient
       unless projection is unavailable.
       - Keep albedo, roughness, metalness, normal/height, AO, mask and wear as
       independent channels.
       - Never reuse albedo as roughness, normal, AO or metalness.

    5. Handle missing evidence honestly.
       - Do not invent paint seed, float value, UV layout or unseen-side details.
       - Mark hidden or inferred geometry with per-region confidence.
       - If the two views are insufficient to resolve an important feature, return
       `request-input`.
       - Do not claim exact geometry or exact texture where the images do not
       provide evidence.

    6. Exactness policy:
       - If only the two images are available, use `exactnessTier: image-only`.
       - If the skin name is verified with reliable metadata but exact paint seed/
       float/texture maps are unavailable, use `exactnessTier: metadata-assisted`.
       - Use `exactnessTier: exact-texture` only when exact texture maps and valid
       provenance are available.
       - Knowing the skin name alone is not enough for `exact-texture`.
       - Keep `route` and `exactnessTier` independent.
       - If projection falls back to procedural-finish, preserve the original
       evidence tier and record the approximation warning.

    7. Existing project context:
       - You may inspect `builds/` and `runtime/cs2-preview/` to understand
       available implementation layers.
       - Treat those directories as implementation context only.
       - Do not copy their geometry, materials, colours, proportions or fixture data
       when they conflict with the uploaded images.
       - Do not treat an existing preview fixture as evidence that the
       reconstruction is correct.
       - The uploaded FRONT and BACK images always have priority.

    8. Review requirements:
       - Render a fixed view aligned to the primary reference view.
       - Render at least two meaningful orbit views.
       - Reject degenerate views where the model is only a flat plane.
       - Review silhouette, proportions, material response, Fade placement, wear and
       hardware separately.
       - A good global score must not override a failed critical feature.

    9. Final report must include:
       - geometry features matched;
       - material and colour features matched;
       - front/back consistency;
       - inferred or hidden regions;
       - approximation notes;
       - selected route;
       - exactness tier;
       - unresolved differences;
       - recommended next action: `continue`, `refine-spec`, `refine-code`,
       `request-input`, or `stop`.

    Do not claim that the model is exact unless the evidence supports that claim.

    FRONT IMAGE:
  FRONT IMAGE: [Image #2]

  BACK IMAGE: [Image #3]
[Image #2]
[Image #3]

I'll start by loading the img2threejs skill, which is purpose-built for this task.

       technically possible.
       - Do not replace the observed Fade pattern with a generic procedural gradient
       unless projection is unavailable.
       - Keep albedo, roughness, metalness, normal/height, AO, mask and wear as
       independent channels.
       - Never reuse albedo as roughness, normal, AO or metalness.

    5. Handle missing evidence honestly.
       - Do not invent paint seed, float value, UV layout or unseen-side details.
       - Mark hidden or inferred geometry with per-region confidence.
       - If the two views are insufficient to resolve an important feature, return
       `request-input`.
       - Do not claim exact geometry or exact texture where the images do not
       provide evidence.

    6. Exactness policy:
       - If only the two images are available, use `exactnessTier: image-only`.
       - If the skin name is verified with reliable metadata but exact paint seed/
       float/texture maps are unavailable, use `exactnessTier: metadata-assisted`.
       - Use `exactnessTier: exact-texture` only when exact texture maps and valid
       provenance are available.
       - Knowing the skin name alone is not enough for `exact-texture`.
       - Keep `route` and `exactnessTier` independent.
       - If projection falls back to procedural-finish, preserve the original
       evidence tier and record the approximation warning.

    7. Existing project context:
       - You may inspect `builds/` and `runtime/cs2-preview/` to understand
       available implementation layers.
       - Treat those directories as implementation context only.
       - Do not copy their geometry, materials, colours, proportions or fixture data
       when they conflict with the uploaded images.
       - Do not treat an existing preview fixture as evidence that the
       reconstruction is correct.
       - The uploaded FRONT and BACK images always have priority.

    8. Review requirements:
       - Render a fixed view aligned to the primary reference view.
       - Render at least two meaningful orbit views.
       - Reject degenerate views where the model is only a flat plane.
       - Review silhouette, proportions, material response, Fade placement, wear and
       hardware separately.
       - A good global score must not override a failed critical feature.

    9. Final report must include:
       - geometry features matched;
       - material and colour features matched;
       - front/back consistency;
       - inferred or hidden regions;
       - approximation notes;
       - selected route;
       - exactness tier;
       - unresolved differences;
       - recommended next action: `continue`, `refine-spec`, `refine-code`,
       `request-input`, or `stop`.

    Do not claim that the model is exact unless the evidence supports that claim.