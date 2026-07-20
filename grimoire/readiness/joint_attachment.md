# Attachment And Joint Correctness

Use this reference when an object has child parts attached to a parent: branches, limbs, handles, legs, horns, wings, cables, tubes, sockets, panels, hinged parts, or decorative appendages.

## Problem

Detached or floating child parts usually come from treating a child component as a free mesh with only `position`, `rotation`, and `scale`. For attached parts, that is not enough. The reconstruction must know where the child starts, where it ends, what parent socket it connects to, and how much it overlaps or embeds into the parent.

## Attachment Contract

Every child appendage, connector, limb, branch, tube, handle, leg, horn, wing, cable, or hinged part should define:

- `parent`: parent component id.
- `attachment.parentId`: same semantic parent id.
- `attachment.parentSocket`: named socket or contact region on the parent.
- `attachment.localStart`: child root point in parent-local coordinates.
- `attachment.localEnd`: child tip/end point in parent-local coordinates.
- `attachment.baseRadius` and `attachment.endRadius` when the child is tube/limb/branch-like.
- `attachment.embedDepth` or `attachment.overlap`: how far the child penetrates or blends into the parent.
- `attachment.contactType`: `embedded`, `socket`, `overlap`, `hinge`, `surface-contact`, or `glued`.
- `attachment.gapTolerance`: maximum acceptable visible gap.
- `attachment.evidenceRefs`: image regions that justify this attachment.

If the attachment point cannot be inferred from the image, record that as `request-input` or lower the fidelity target. Do not silently place the part in mid-air.

## Generator Rule

For endpoint-based parts, place the pivot group at `localStart`, generate the visible mesh from `localStart` to `localEnd`, and orient the mesh along that direction. Do not center the mesh at an arbitrary transform position.

## Screenshot Review

During `structural-pass` and `form-refinement`, inspect:

1. Are child roots visibly touching, overlapping, or embedded into the parent?
2. Are there floating joints or air gaps at branch roots, handles, limbs, legs, tubes, or connectors?
3. Does the child pivot sit at the semantic joint rather than at the center of the mesh?
4. Does deformation/bending originate from the joint/root?
5. Are parent sockets and child roots still aligned after rotation, scale, and animation?

If floating joints are visible, choose `refine-spec` when attachment data is missing, or `refine-code` when the spec has correct attachment data but the generated mesh does not use it.
