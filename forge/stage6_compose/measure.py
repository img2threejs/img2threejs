"""Independently render/measure the exact composed candidate with host-pinned Chrome.

API: measure_scene(workspace, *, scene=REF, inventory=REF, reference=REF,
 placement=REF, chrome=HOST_REF, modules=MAP, output_dir=REL,
 viewport={width,height,devicePixelRatio:1}, background=RGB, timeout_seconds=30,
 reference_mask=REF|null). Explicit reference masks bind the reference digest,
producer, width/height and Boolean pixels; parent authenticates their provenance.
Rendered foreground is recomputed from actual object-ID pixels, never supplied.
The base must call this itself, then assess_scene(result, expected_object_ids=...).
Saved JSON, input digests and producer names do not authenticate an execution.
The frozen engineering size bands are not independent corpus calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import quote

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)
from stage6_compose.compose import CompositionError, _canonical, _json, _path, _read, _write_output
from stage6_compose.geometry import bounds, box_iou, contact_gap, intersection_count, triangles
from stage4_review import capture_delivered_glb as capture
from stage1_intake.extract_pbr_evidence import read_png, build_foreground_mask
from _shared.jpeg import decode_jpeg, is_jpeg


class Inputs:
    def __init__(self, workspace):
        try:
            self.fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise CompositionError('unsafe-path', 'workspace') from exc
        self.snapshots = {}

    def read(self, ref):
        capture._reference(ref)
        previous = self.snapshots.get(ref['path'])
        if previous is not None and previous[0] != ref:
            raise CompositionError('conflicting-reference', ref['path'])
        data = _read(self.fd, ref['path'])
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise CompositionError('hash-mismatch', ref['path'])
        self.snapshots[ref['path']] = (dict(ref), data)
        return data

    def record(self, ref):
        value = _json(self.read(ref), ref['path'])
        if type(value) is not dict:
            raise CompositionError('invalid-record', ref['path'])
        return value

    def recheck(self):
        for ref, data in list(self.snapshots.values()):
            if self.read(ref) != data:
                raise CompositionError('input-changed', ref['path'])

    def close(self):
        os.close(self.fd)


def _prior(row, artifact):
    if row.get('role') == 'scene-container':
        height = artifact.get('roomDimensions', {}).get('height')
        if type(height) not in (float, int) or not math.isfinite(height) or height <= 0:
            raise CompositionError('missing-room-dimensions', row['objectId'])
        return {'min': height, 'max': height}
    frozen = json.loads(Path(_FORGE, '_shared/size_priors.json').read_text(encoding='utf-8'))
    band = frozen['classes'].get(row.get('classId'))
    if band is None:
        raise CompositionError('unknown-size-prior-class', row['objectId'])
    return {'min': band['minHeight'], 'max': band['maxHeight']}


def _inventory(value):
    rows = value.get('objects')
    if (value.get('kind') != 'img2.object-inventory' or value.get('version') != 1
            or type(rows) is not list or not rows
            or any(type(row) is not dict or type(row.get('objectId')) is not str
                   or type(row.get('artifactObjectId')) is not str for row in rows)
            or len({row['objectId'] for row in rows}) != len(rows)):
        raise CompositionError('invalid-inventory', 'objects')
    return rows


def _template_data(module):
    template = Path(__file__).with_name('runtime.mjs').read_bytes()
    prefix, suffix = template.split(b'__COMPOSITION_JSON__')
    if not module.startswith(prefix) or not module.endswith(suffix):
        raise CompositionError('untrusted-scene-module', 'module')
    data = _json(module[len(prefix):len(module)-len(suffix)], 'composition data')
    if (type(data) is not dict or set(data) != {'files', 'instances', 'camera', 'environment', 'baseContainer'}
            or module != prefix + _canonical(data) + suffix):
        raise CompositionError('untrusted-scene-module', 'module')
    return data


def _capture(inputs, *, data, scene_hash, reference_hash, inventory, chrome, modules,
             viewport, background, timeout_seconds):
    capture._number(timeout_seconds, 1, 120, 'timeout_seconds')
    deadline = time.monotonic() + timeout_seconds
    capture._keys(viewport, ('width', 'height', 'devicePixelRatio'), 'viewport')
    for key in ('width', 'height'):
        if type(viewport[key]) is not int or not 32 <= viewport[key] <= 2048:
            raise CompositionError('invalid-viewport', key)
    if viewport['devicePixelRatio'] != 1 or type(viewport['devicePixelRatio']) is bool:
        raise CompositionError('invalid-viewport', 'devicePixelRatio')
    capture._color(background, 'background')
    if not math.isclose(data['camera']['aspect'], viewport['width'] / viewport['height'], abs_tol=1e-12):
        raise CompositionError('camera-viewport-mismatch', 'aspect')
    if type(modules) is not dict or not capture.REQUIRED_MODULES <= modules.keys() or not modules.keys() <= capture.MODULE_URLS:
        raise CompositionError('unsupported-module-map', 'modules')
    module_bytes = {url: capture._host_ref(ref, 8 * 1024 * 1024)[0] for url, ref in modules.items()}
    executable, mode = capture._host_ref(chrome)
    if Path(chrome['path']).name not in capture._CHROME_NAMES or not mode & 0o111 or executable[:4] not in capture._NATIVE_MAGIC:
        raise CompositionError('unsupported-executable', 'chrome')
    # Execute only a byte-for-byte program template with strictly serialized DATA.
    # URL rewriting changes transport only; each input retains its original hash.
    transported = dict(data, files=[dict(entry, url='/inputs/' + quote(entry['path'], safe='/')) for entry in data['files']])
    template = Path(__file__).with_name('runtime.mjs').read_bytes()
    composed = template.replace(b'__COMPOSITION_JSON__', _canonical(transported))
    runtime = Path(__file__).with_name('measure_runtime.mjs').read_bytes()
    config = {'moduleUrl': '/composition.mjs', 'sceneSha256': scene_hash, 'referenceSha256': reference_hash,
              'inventory': inventory, 'viewport': viewport, 'background': background}
    html = b'<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,"><style>html,body{margin:0;padding:0;overflow:hidden}canvas{display:block}</style><script type="importmap" nonce="fixed-capture-map">{"imports":{"three":"/deps/three.module.js","three/addons/":"/deps/"}}</script><script type="module" src="/runtime.mjs"></script>'
    routes = {'/': (html, 'text/html; charset=utf-8'), '/runtime.mjs': (runtime, 'text/javascript; charset=utf-8'),
              '/composition.mjs': (composed, 'text/javascript; charset=utf-8'), '/config.json': (_canonical(config), 'application/json')}
    for entry in data['files']:
        payload = inputs.read({'path': entry['path'], 'sha256': entry['sha256']})
        routes['/inputs/' + quote(entry['path'], safe='/')] = (payload, 'model/gltf-binary' if entry['path'].lower().endswith('.glb') else 'application/octet-stream')
    first = data['instances'][0]['modelPath']
    routes['/delivery.glb'] = (inputs.snapshots[first][1], 'model/gltf-binary')
    routes.update({url: (payload, 'text/javascript; charset=utf-8') for url, payload in module_bytes.items()})
    png, report, browser, flags, requested = capture._browser(chrome['path'], routes, config, deadline)
    capture._png_dimensions(png, [viewport['width'], viewport['height']])
    if (report.get('kind') != 'img2.scene-runtime-measurement' or report.get('sceneSha256') != scene_hash
            or report.get('referenceSha256') != reference_hash or report.get('viewport') != viewport):
        raise CompositionError('runtime-binding-mismatch', 'report')
    inputs.recheck()
    capture._host_ref(chrome)
    for ref in modules.values():
        capture._host_ref(ref, 8 * 1024 * 1024)
    capture._remaining(deadline)
    renderer = {'chrome': chrome, 'modules': modules, 'browser': browser, 'flags': flags,
                'requestedPaths': requested, 'runtimeSha256': hashlib.sha256(runtime).hexdigest(),
                'transportModuleSha256': hashlib.sha256(composed).hexdigest(), 'threeRevision': report.get('threeRevision')}
    return png, report, renderer


def _image(data, name, explicit_mask=None):
    # The stdlib decoder reads immutable bytes, not a mutable workspace pathname.
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        dimensions = [int.from_bytes(data[start:start+4], 'big') for start in (16, 20)]
        if min(dimensions) <= 0 or dimensions[0]*dimensions[1] > 16_777_216:
            raise CompositionError('resource-limit', name)
        capture._png_dimensions(data, dimensions)
    with tempfile.TemporaryDirectory(prefix='img2-scene-image-') as temporary:
        path = Path(temporary, name)
        path.write_bytes(data)
        try:
            width, height, pixels = decode_jpeg(data) if is_jpeg(data) else read_png(path)
        except (ValueError, RuntimeError) as exc:
            raise CompositionError('unsupported-image', name) from exc
        warnings = []
    if width * height > 16_777_216:
        raise CompositionError('resource-limit', 'image')
    if explicit_mask is not None:
        mask = explicit_mask.get('mask') if type(explicit_mask) is dict else None
        if (type(mask) is not list or len(mask) != width*height
                or explicit_mask.get('width') != width or explicit_mask.get('height') != height
                or any(type(value) is not bool for value in mask) or not any(mask)):
            raise CompositionError('empty-or-invalid-explicit-mask', name)
        return width, height, pixels, mask
    alpha = [pixel[3] > 127 for pixel in pixels]
    if any(pixel[3] < 128 for pixel in pixels):
        mask = alpha
    else:
        mask, _, extra = build_foreground_mask(width, height, pixels)
        warnings += extra
    if not any(mask) or sum(mask) > len(mask)*0.9 or warnings:
        raise CompositionError('empty-or-unreliable-foreground', name)
    return width, height, pixels, mask


def _lattice(image):
    width, height, pixels, mask = image
    indices = [index for index, solid in enumerate(mask) if solid]
    x0, x1 = min(i % width for i in indices), max(i % width for i in indices) + 1
    y0, y1 = min(i // width for i in indices), max(i // width for i in indices) + 1
    values, solid = [], []
    for gy in range(192):
        ys = y0 + gy * (y1-y0) // 192
        ye = max(y0 + (gy+1) * (y1-y0) // 192, ys+1)
        for gx in range(192):
            xs = x0 + gx * (x1-x0) // 192
            xe = max(x0 + (gx+1) * (x1-x0) // 192, xs+1)
            cells = [y*width+x for y in range(ys, min(ye, height)) for x in range(xs, min(xe, width))]
            values.append(sum(sum(pixels[i][:3]) for i in cells) / (3 * len(cells)))
            solid.append(sum(mask[i] for i in cells) > len(cells)/2)
    return values, solid


def image_metrics(reference_bytes, screenshot_bytes, *, reference_mask=None, render_mask=None):
    """Foreground-bbox-aligned 192-cell silhouette + interior measurement."""
    baseline, a = _lattice(_image(reference_bytes, 'reference.png', reference_mask))
    rendered, b = _lattice(_image(screenshot_bytes, 'capture.png', render_mask))
    shared = [index for index in range(192*192) if a[index] and b[index]]
    union = sum(x or y for x, y in zip(a, b))
    if not shared or not union:
        raise CompositionError('no-overlapping-figure-cells', 'images')
    return {'silhouetteIoU': len(shared) / union,
            'interiorDifference': sum(abs(baseline[i]-rendered[i]) for i in shared) / len(shared) / 255,
            'grid': 192, 'cellsCompared': len(shared)}


def _bound(record, bindings, field):
    if any(record.get(key) != value for key, value in bindings.items()):
        raise CompositionError('stale-placement-evidence', field)


def _scene_sources(inputs, workspace, placements, rows, inventory, reference, data):
    from stage6_compose.placement import solve_camera, _rooted_artifacts, _source_rows, validate_metric_anchor
    from stage6_compose.geometry import _sub, _dot
    from stage6_compose.compose import _number, _vector
    if type(placements.get('preparation')) is not dict:
        raise CompositionError('missing-placement-preparation', 'placement')
    plan = inputs.record(placements['preparation'])
    arguments = plan.get('arguments')
    if type(arguments) is not dict or arguments.get('inventory') != inventory or arguments.get('reference') != reference:
        raise CompositionError('stale-placement-preparation', 'bindings')
    camera_raw = inputs.record(arguments['camera_solve'])
    camera, _ = solve_camera(camera_raw)
    if inputs.record(placements['camera']) != camera or data['camera'] != {k: v for k, v in camera.items() if k != 'type'}:
        raise CompositionError('camera-solve-mismatch', 'scene.camera')
    environment = inputs.record(placements['environment'])
    if data['environment'] != {k: environment[k] for k in ('skyColor', 'groundColor', 'intensity')}:
        raise CompositionError('scene-environment-mismatch', 'environment')
    base = inputs.record(placements['baseContainer']) if placements['baseContainer'] is not None else None
    if base is not None:
        base = {name: {key: base[name][key] for key in ('width', 'height', 'position', 'rotation', 'color')} for name in ('ground', 'backdrop')}
    if data['baseContainer'] != base:
        raise CompositionError('scene-container-mismatch', 'baseContainer')
    artifacts, _ = _rooted_artifacts(inputs, workspace, arguments['artifacts'], rows)
    validate_metric_anchor(camera_raw, rows, artifacts)
    raw_depth = inputs.record(arguments['depth'])
    raw_support = inputs.record(arguments['support'])
    binding = {'inventorySha256': inventory['sha256'], 'referenceSha256': reference['sha256'],
               'cameraSolveSha256': arguments['camera_solve']['sha256']}
    for field, record in (('depth', raw_depth), ('support', raw_support)):
        _bound(record, binding, field)
        if type(record.get('producer')) is not str or not record['producer'].strip():
            raise CompositionError('missing-producer', field)
    ids = {row['objectId'] for row in rows}
    depths = _source_rows(raw_depth, ids, 'depth')
    supports = _source_rows(raw_support, ids, 'support')
    anchor = raw_depth.get('anchor')
    if type(anchor) is not dict:
        raise CompositionError('missing-metric-depth-anchor', 'depth.anchor')
    point = _vector(anchor.get('worldPoint'), 'depth.anchor.worldPoint')
    if not any(item['world'] == point for item in camera_raw['correspondences']):
        raise CompositionError('unbound-metric-depth-anchor', 'depth.anchor.worldPoint')
    anchor_depth = _dot(_sub(point, camera['position']), _sub(camera['target'], camera['position']))
    anchor_relative = _number(anchor.get('relativeDepth'), 'anchor.relativeDepth', True)
    if anchor_depth <= camera['near']:
        raise CompositionError('invalid-metric-depth-anchor', 'anchor')
    metric_depths = {oid: _number(row.get('relativeDepth'), oid + '.relativeDepth', True)/anchor_relative*anchor_depth for oid, row in depths.items()}
    placed = placements.get('objects')
    if type(placed) is not list or len({row['objectId'] for row in placed}) != len(placed):
        raise CompositionError('inventory-mismatch', 'placement.objects')
    by_id = {row['objectId']: row for row in rows}
    instances = {row['objectId']: row for row in data['instances']}
    if len(instances) != len(data['instances']) or set(instances) != {row['objectId'] for row in placed}:
        raise CompositionError('inventory-mismatch', 'scene.instances')
    for row in placed:
        oid = row['objectId']
        if oid not in by_id:
            raise CompositionError('inventory-mismatch', oid)
        artifact = artifacts[by_id[oid]['artifactObjectId']]
        expected = {'objectId': oid, 'artifactObjectId': by_id[oid]['artifactObjectId'],
                    'artifactSha256': artifact['ref']['sha256'], 'modelPath': artifact['modelPath'],
                    'position': row['position'], 'scale': [v*artifact['artifact']['scaleEstimate'] for v in row['scale']],
                    'yawBin': row['yawBin']}
        if instances[oid] != expected:
            raise CompositionError('scene-transform-mismatch', oid)
    return {oid: item['artifact'] for oid, item in artifacts.items()}, metric_depths, supports


def measure_scene(workspace, *, scene, inventory, reference, placement, chrome, modules,
                  output_dir, viewport, background=0xffffff, timeout_seconds=30, reference_mask=None):
    """Produce fresh D12 evidence, never acceptance. Caller scores are not inputs."""
    inputs = Inputs(workspace)
    try:
        manifest = inputs.record(scene)
        rows = _inventory(inputs.record(inventory))
        reference_bytes = inputs.read(reference)
        admitted_mask = inputs.record(reference_mask) if reference_mask is not None else None
        if admitted_mask is not None and (admitted_mask.get('kind') != 'img2.reference-mask'
                or admitted_mask.get('version') != 1 or admitted_mask.get('referenceSha256') != reference['sha256']
                or type(admitted_mask.get('producer')) is not str or not admitted_mask['producer'].strip()):
            raise CompositionError('stale-or-invalid-reference-mask', 'reference_mask')
        placements = inputs.record(placement)
        if (manifest.get('kind') != 'img2.composed-scene' or manifest.get('inventorySha256') != inventory['sha256']
                or manifest.get('referenceSha256') != reference['sha256']):
            raise CompositionError('stale-scene', 'scene')
        module = inputs.read(manifest['module'])
        if hashlib.sha256(module).hexdigest() != manifest.get('sceneSha256'):
            raise CompositionError('hash-mismatch', 'sceneSha256')
        data = _template_data(module)
        if data['instances'] != manifest.get('instances'):
            raise CompositionError('scene-instance-mismatch', 'instances')
        retained = {(entry['path'], entry['sha256']) for entry in data['files']}
        for ref in (inventory, reference, placement):
            if (ref['path'], ref['sha256']) not in retained:
                raise CompositionError('scene-input-mismatch', ref['path'])
        expected = {row['objectId'] for row in rows}
        if {row['id'] for row in manifest.get('objects', [])} != expected or len(manifest.get('objects', [])) != len(expected):
            raise CompositionError('inventory-mismatch', 'scene.objects')
        artifacts, metric_depths, source_supports = _scene_sources(inputs, workspace, placements, rows, inventory, reference, data)
        png, report, renderer = _capture(inputs, data=data, scene_hash=manifest['sceneSha256'],
            reference_hash=reference['sha256'], inventory=rows, chrome=chrome, modules=modules,
            viewport=viewport, background=background, timeout_seconds=timeout_seconds)
        runtime_rows = report.get('objects')
        if (type(runtime_rows) is not list or len(runtime_rows) != len(expected)
                or {row.get('id') for row in runtime_rows} != expected):
            raise CompositionError('inventory-mismatch', 'runtime.objects')
        geometry = {row['id']: triangles(row['triangles']) for row in runtime_rows}
        runtime = {row['id']: row for row in runtime_rows}
        placement_rows = {row['objectId']: row for row in placements['objects']}
        scene_hash = manifest['sceneSha256']
        def measurement(value):
            return {'value': value, 'producer': 'base:stage6_compose.measure:v1',
                    'sceneSha256': scene_hash, 'referenceSha256': reference['sha256']}
        objects = []
        for row in rows:
            oid = row['objectId']
            placed = placement_rows.get(oid, placements.get('embeddedEvidence', {}).get(oid))
            if placed is None:
                raise CompositionError('missing-placement', oid)
            artifact = artifacts[row['artifactObjectId']]
            band = _prior(row, artifact)
            height = bounds(geometry[oid])[1][1] - bounds(geometry[oid])[0][1]
            actual = runtime[oid]
            # Even an accurately projected hidden/offscreen object has no visual evidence.
            visible = type(actual.get('visiblePixels')) is int and actual['visiblePixels'] > 0 and actual.get('clipped') is False
            iou = box_iou(actual['projectedBox'], row['approxBox']) if visible else 0.0
            evidence = placed['evidence']
            support_record = inputs.record(evidence['support'])
            depth_record = inputs.record(evidence['depth'])
            binding = {'inventorySha256': inventory['sha256'], 'referenceSha256': reference['sha256'],
                       'artifactSha256': placed['artifactSha256'], 'objectId': placed['objectId']}
            _bound(support_record, binding, oid + '.support')
            _bound(depth_record, binding, oid + '.depth')
            if depth_record.get('cameraDepth') != metric_depths[oid]:
                raise CompositionError('depth-calibration-mismatch', oid)
            original_support = source_supports[oid]
            if (support_record.get('mode') != original_support.get('mode')
                    or support_record.get('supportObjectId') != original_support.get('supportObjectId')):
                raise CompositionError('support-source-mismatch', oid)
            if type(depth_record.get('cameraDepth')) not in (int, float) or not math.isfinite(depth_record['cameraDepth']) or depth_record['cameraDepth'] <= 0:
                raise CompositionError('missing-metric-depth', oid)
            mode = support_record.get('mode')
            if mode not in ('surface', 'free'):
                raise CompositionError('missing-support-classification', oid)
            obj = {'id': oid, 'boxIoU': measurement(iou), 'height': measurement(height),
                   'scale': measurement(height), 'scalePrior': band, 'supported': mode == 'surface'}
            if mode == 'surface':
                support_id = support_record.get('supportObjectId')
                if support_id == 'base-ground':
                    support_mesh = triangles(report['support']['ground'])
                elif support_id in geometry and support_id != oid:
                    support_mesh = geometry[support_id]
                else:
                    raise CompositionError('missing-support-geometry', oid)
                obj['contactGap'] = measurement(contact_gap(geometry[oid], support_mesh))
            objects.append(obj)
        pairs = []
        for index, a in enumerate(rows):
            for b in rows[index+1:]:
                aid, bid = a['objectId'], b['objectId']
                pairs.append({'objects': [aid, bid], 'count': measurement(intersection_count(geometry[aid], geometry[bid]))})
        pixels = image_metrics(reference_bytes, png, reference_mask=admitted_mask,
            render_mask={'width': viewport['width'], 'height': viewport['height'], 'mask': report.get('objectMask')})
        result = {'kind': 'img2.scene-measurements', 'version': 1, 'sceneSha256': scene_hash,
                  'referenceSha256': reference['sha256'], 'inventorySha256': inventory['sha256'],
                  'objects': objects, 'intersections': pairs, 'silhouetteIoU': measurement(pixels['silhouetteIoU']),
                  'interiorDifference': measurement(pixels['interiorDifference']), 'lightRigCount': measurement(report['lightRigCount']),
                  'referenceMask': reference_mask, 'renderMaskSource': 'runtime-object-id-pixels',
                  'imageSampling': {'grid': 192, 'cellsCompared': pixels['cellsCompared']},
                  'scope': 'actual-composer-scene-reference-view-rest-pose', 'deliverable': False}
        output = _path(output_dir)
        for path in inputs.snapshots:
            if _path(path).is_relative_to(output):
                raise CompositionError('output-overlaps-input', path)
        inputs.recheck()
        contents = {'measurements.json': _canonical(result) + b'\n', 'capture.png': png,
                    'renderer.json': _canonical(renderer) + b'\n', 'runtime.json': _canonical(report) + b'\n'}
        _write_output(inputs.fd, output, contents)
        return result
    finally:
        inputs.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--request', required=True, help='Workspace-relative strict JSON measurement arguments')
    args = parser.parse_args(argv)
    inputs = Inputs(args.workspace)
    try:
        arguments = _json(_read(inputs.fd, str(_path(args.request))), 'request')
    finally:
        inputs.close()
    try:
        result = measure_scene(args.workspace, **arguments)
    except (CompositionError, capture.CaptureError) as exc:
        print(json.dumps({'status': 'unevaluated', 'reasonCode': exc.reason_code, 'field': exc.field}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
