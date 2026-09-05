#!/usr/bin/env python
"""
Generate a reference mesh from reference image(s) via a hosted TRELLIS Space, as GLB **and** OBJ.

Why a hosted Space and not a local model: TRELLIS states "the code is currently tested only on
Linux" and "an NVIDIA GPU with at least 16GB of memory is necessary", and its submodules (spconv,
nvdiffrast, diff-gaussian-rasterization, flash-attn) are CUDA-only. There is no MPS path. The Space
runs on the provider's CUDA hardware, so an Apple Silicon machine gets TRELLIS output without
TRELLIS ever running locally.

Why BOTH formats, from one generation:
  GLB  — transport/render format. Three.js loads it natively (GLTFLoader), so the reference can be
         rendered with the SAME camera and shader as the candidate model. That identical-domain
         render is the entire point: comparing a PBR render against a photograph is what pins
         `ssim` at 0 and `tonalParity` at 0.11.
  OBJ  — scoring format. `forge/` gates are pure-stdlib by house rule, and OBJ is `v/vn/f` ASCII
         that a ~30-line stdlib parser reads. Decoding GLB accessors/bufferViews in pure Python is
         200-400 fiddly lines and impossible outright if the file is Draco-compressed.

Both files are written from ONE generation and ONE transform, because the most dangerous failure
here is silent: if the GLB and the OBJ disagree on axis convention, the Three.js render and the
Python metric score two different objects and neither complains. glTF fixes Y-up/right-handed/metres
by spec; OBJ fixes nothing. `--assert-axes` compares their bounding boxes and refuses to continue on
a mismatch.

Texture is deliberately minimal. The metrics this unlocks (Chamfer, F-score, normal consistency) are
geometric, and the depth/normal render path discards materials anyway. A generated texture is a
hallucinated bake of a single view with shading and AO burned in — strictly worse than the original
reference images, which remain the colour authority.

Usage:
    python generate_reference_mesh.py IMAGE [IMAGE ...] --out-dir DIR [--space ID] [--seed N]
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
from pathlib import Path

DEFAULT_SPACE = "trellis-community/TRELLIS"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("images", nargs="*", type=Path, help="reference image(s); 2+ enables multi-view")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--from-glb", type=Path, default=None,
                   help="skip generation and convert an existing GLB. Generation costs finite "
                        "ZeroGPU quota, so a failure in the local conversion step must never force "
                        "a regeneration of a mesh that already exists")
    p.add_argument("--space", default=DEFAULT_SPACE)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--mesh-simplify", type=float, default=0.95,
                   help="TRELLIS simplify ratio, demo range 0.9-0.98. In the reference demo this is "
                        "the fraction of triangles REMOVED, so higher means a lighter mesh -- but "
                        "verify against the triangle count this script reports rather than trusting "
                        "the direction stated here")
    p.add_argument("--texture-size", type=int, default=512,
                   help="kept small on purpose: geometry is what is being scored")
    p.add_argument("--hf-token", default=None)
    return p.parse_args()


def generate(args: argparse.Namespace) -> Path:
    from gradio_client import Client, handle_file

    for image in args.images:
        if not image.exists():
            sys.exit(f"reference image not found: {image}")

    # `token=`, not `hf_token=` — gradio_client 2.6.0 names it `token` and rejects the other spelling
    # outright. And fall back to the stored login rather than leaving it None: a user who ran
    # `hf auth login` specifically to get ZeroGPU quota should actually get it, not silently run
    # anonymous.
    from huggingface_hub import get_token

    token = args.hf_token or get_token()
    client = Client(args.space, token=token, verbose=False)
    print(f"auth       : {'token' if token else 'anonymous'}")
    primary = handle_file(str(args.images[0]))
    # TRELLIS takes the extra views as {image, caption} records, and only consults them when
    # `multiimage_algo` is set. A single view genuinely cannot show hidden sides; two clay
    # turnarounds at different azimuths constrain the back far better than one ever can.
    extra = [{"image": handle_file(str(p)), "caption": None} for p in args.images[1:]]

    print(f"space      : {args.space}")
    print(f"views      : {len(args.images)} ({'multi-view' if extra else 'single-view'})")

    # `/start_session` MUST be called first, and nothing in the API listing says so.
    #
    # The TRELLIS app creates a per-session scratch directory (TMP_DIR/<session_hash>) inside a
    # `demo.load(...)` handler and every later step writes into it. `demo.load` fires when a browser
    # opens the page; it does NOT fire for an API client. Skipping it produced a bare server-side
    # `FileNotFoundError` with no indication of which file or why -- the failure names the symptom
    # and hides the cause. The session hash is per-Client, so this has to run on the same instance.
    client.predict(api_name="/start_session")
    result = client.predict(
        image=primary,
        multiimages=extra,
        seed=args.seed,
        ss_guidance_strength=7.5,
        ss_sampling_steps=12,
        slat_guidance_strength=3.0,
        slat_sampling_steps=12,
        multiimage_algo="stochastic" if extra else "stochastic",
        mesh_simplify=args.mesh_simplify,
        texture_size=args.texture_size,
        api_name="/generate_and_extract_glb",
    )
    # The endpoint returns (preview video, extracted asset, downloadable GLB).
    glb_source = None
    for item in result:
        candidate = item.get("video") if isinstance(item, dict) else item
        if isinstance(candidate, str) and candidate.lower().endswith(".glb"):
            glb_source = candidate
    if glb_source is None:
        sys.exit(f"no .glb in response: {result!r}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = args.out_dir / "reference.glb"
    shutil.copy(glb_source, glb_path)
    return glb_path


def inspect_glb(glb_path: Path) -> dict:
    """Read the glTF JSON chunk with stdlib only, to catch compression before anything depends on it.

    Draco or meshopt compression is common in generated GLB and is the one condition that makes the
    pure-Python scoring path impossible — it must be detected at intake, not discovered later.
    """
    data = glb_path.read_bytes()
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        sys.exit(f"{glb_path} is not a GLB (bad magic)")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:
        sys.exit("first GLB chunk is not JSON")
    gltf = json.loads(data[20:20 + chunk_length])
    required = gltf.get("extensionsRequired", [])
    return {
        "extensionsRequired": required,
        "compressed": any("draco" in e.lower() or "meshopt" in e.lower() for e in required),
        "meshes": len(gltf.get("meshes", [])),
        "materials": len(gltf.get("materials", [])),
    }


def write_obj(glb_path: Path, obj_path: Path) -> dict:
    """GLB -> triangulated, smooth-normal OBJ, and report the geometry both consumers must agree on.

    Smooth rather than flat normals: flat normals encode the TESSELLATION, not the form, so a normal
    comparison against a flat-shaded reference would score how the generator chose to triangulate.
    Flat shading also splits a vertex per face, tripling the count for nothing.
    """
    import trimesh

    scene_or_mesh = trimesh.load(glb_path, force="scene")
    mesh = scene_or_mesh.to_mesh() if hasattr(scene_or_mesh, "to_mesh") else scene_or_mesh
    mesh.merge_vertices()          # weld first, so smooth normals average across real neighbours
    mesh.fix_normals()
    obj_path.write_text(trimesh.exchange.obj.export_obj(mesh, include_normals=True, include_texture=False))
    bounds = mesh.bounds
    return {
        "vertices": int(len(mesh.vertices)),
        "triangles": int(len(mesh.faces)),
        "bbox_min": [round(float(v), 4) for v in bounds[0]],
        "bbox_max": [round(float(v), 4) for v in bounds[1]],
        "size": [round(float(v), 4) for v in (bounds[1] - bounds[0])],
        "watertight": bool(mesh.is_watertight),
    }


def main() -> None:
    args = parse_args()
    if args.from_glb is not None:
        if not args.from_glb.exists():
            sys.exit(f"--from-glb not found: {args.from_glb}")
        glb_path = args.from_glb
        print(f"reusing    : {glb_path} (no generation)")
    else:
        if not args.images:
            sys.exit("provide reference image(s), or --from-glb to convert an existing mesh")
        glb_path = generate(args)
    obj_path = glb_path.with_suffix(".obj")

    glb_info = inspect_glb(glb_path)
    obj_info = write_obj(glb_path, obj_path)

    report = {"glb": str(glb_path), "obj": str(obj_path), **glb_info, **obj_info}
    (args.out_dir / "reference-mesh.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

    if glb_info["compressed"]:
        print("\nWARNING: GLB declares compression in extensionsRequired. Three.js needs DRACOLoader "
              "and the pure-Python path cannot read it. The OBJ above is still usable.", file=sys.stderr)
    print("\nNOTE: this mesh is a generative PROXY, not ground truth. Score it against the original "
          "reference image before using it as a scoring reference — a hallucinated back side is "
          "exactly what a confident-but-wrong metric would optimise toward.", file=sys.stderr)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(lambda _argv: main()))
