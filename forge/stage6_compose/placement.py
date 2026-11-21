"""Base placement preparation + journal-verified finalization, without provider calls.

prepare_placement(workspace, *, inventory, reference, artifacts, camera_solve,
 depth, support, environment, base_container, chrome, modules, output_dir,
 timeout_seconds=30) renders exact authored GLBs and freezes priors before choices.
It returns preparation.json content; each object has sizePrior, sizeOptions,
 yawOptions, and decisionBindings usable with placement_context().

produce_placement(workspace, *, preparation=REF, decisions={objectId:{
 size:{context:REF,record:REF},yaw:{context:REF,record:REF}}},
 verify_answer=CALLBACK, output_dir, timeout_seconds=30) recomputes preparation,
 then consumes each answer through a trusted base callback(context,record) that
 calls consume_placement(journal_verify,context,record). A decider string is never
 authority. The callback must remain live while publishing under the parent lease.

Raw depth/support each bind inventorySha256,referenceSha256,cameraSolveSha256 and
producer. Depth has anchor:{worldPoint,relativeDepth} and objects [{objectId,
relativeDepth}]. Anchor worldPoint must be an admitted metric camera landmark.
camera_solve.metricAnchor binds two admitted worldPoints to roomDimensions and its
artifact digest (mode room-dimensions, axis width/height/depth), or to a frozen
class height (mode size-prior, classId, heightMeters). The two worldPoints must
span that metric axis. Raw depth/support cover embedded inventory instances too.
Support objects have mode:'surface',supportObjectId or mode:'free'. These input
observations require parent provenance review; hashes do not authenticate them.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import sys
from types import SimpleNamespace

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)
_BASE = str(Path(__file__).resolve().parents[2])
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
from _shared.route_artifact import validate_artifact
from stage6_compose.compose import CompositionError, RUN_UNAVAILABLE, _canonical, _path, _write_output, _glb, _vector, _number, _color
from stage6_compose.geometry import bounds, box_iou, contact_gap, ray_distance, triangles, _cross, _dot, _sub
from stage6_compose.measure import Inputs, _capture, _inventory, _prior, _bound
from forge.stage1_intake.solve_camera_pose import fit_camera_to_correspondences
from forge.stage1_intake import camera_fitting_types as camera_types


def solve_camera(record):
    """Re-run stage1 fitting; preserve its full projection convention in Three."""
    initial = record.get('initialCamera')
    points = record.get('correspondences')
    if type(initial) is not dict or type(points) is not list:
        raise CompositionError('missing-camera-correspondences', 'cameraSolve')
    try:
        camera = SimpleNamespace(**{snake: initial[camel] for snake, camel in (
            ('image_width', 'imageWidth'), ('image_height', 'imageHeight'),
            ('fov_degrees', 'fovDegrees'), ('yaw_degrees', 'yawDegrees'),
            ('pitch_degrees', 'pitchDegrees'), ('roll_degrees', 'rollDegrees'), ('position', 'position'))})
        fit = fit_camera_to_correspondences([SimpleNamespace(name=p['name'], world=p['world'], observed=p['observed']) for p in points], initial_camera=camera)
    except (KeyError, TypeError, ValueError, AttributeError,
            camera_types.InsufficientCorrespondencesError, camera_types.DegenerateCorrespondencesError,
            camera_types.InvalidInitialCameraError, camera_types.InvalidCameraDimensionsError,
            camera_types.NonFiniteCameraInputError) as exc:
        raise CompositionError('invalid-camera-correspondences', 'cameraSolve') from exc
    # A numerical fit is candidate camera evidence, not a convergence/quality
    # assertion. Preserve stalled/max-iterations diagnostics; D12 measures the
    # actual projected boxes and pixels instead of inventing a fit threshold.
    params = fit['fit']['cameraParameters']
    yaw, pitch, roll = (math.radians(params[key]) for key in ('yawDegrees', 'pitchDegrees', 'rollDegrees'))
    right = [math.cos(yaw), math.sin(pitch)*math.sin(yaw), -math.cos(pitch)*math.sin(yaw)]
    up = [0, math.cos(pitch), math.sin(pitch)]
    forward = [-math.sin(yaw), math.sin(pitch)*math.cos(yaw), -math.cos(pitch)*math.cos(yaw)]
    rolled_up = [math.sin(roll)*r + math.cos(roll)*u for r, u in zip(right, up)]
    position = params['position']
    near = _number(record.get('near'), 'camera.near', True)
    far = _number(record.get('far'), 'camera.far', True)
    if far <= near:
        raise CompositionError('invalid-camera', 'near/far')
    result = {'type': 'perspective', 'position': position,
              'target': [p+f for p, f in zip(position, forward)], 'up': rolled_up,
              'fov': params['fovDegrees'], 'aspect': camera.image_width/camera.image_height, 'near': near, 'far': far}
    return result, fit


def _rooted_artifacts(inputs, workspace, refs, rows):
    result = {}
    expected = {row['artifactObjectId'] for row in rows}
    files = {}
    for ref in refs:
        raw = inputs.record(ref)
        rooted = deepcopy(raw)
        directory = _path(ref['path']).parent
        rooted['files'] = [dict(entry, path=str(directory / _path(entry['path']))) for entry in raw['files']]
        if 'entrypoint' in raw:
            rooted['entrypoint'] = str(directory / _path(raw['entrypoint']))
        artifact = validate_artifact(workspace, rooted)
        oid = artifact['objectId']
        if oid not in expected or oid in result:
            raise CompositionError('inventory-mismatch', 'artifacts')
        owned = [row for row in rows if row['artifactObjectId'] == oid]
        if set(artifact.get('ownedObjectIds', [])) != {row['objectId'] for row in owned}:
            raise CompositionError('ownership-mismatch', oid)
        if any(row.get('ownerRoute') != artifact.get('ownerRoute') for row in owned):
            raise CompositionError('ownership-mismatch', oid)
        glbs = [entry for entry in artifact['files'] if entry['path'].lower().endswith('.glb')]
        selected = artifact.get('entrypoint', glbs[0]['path'] if len(glbs) == 1 else None)
        selected_files = [entry for entry in glbs if entry['path'] == selected]
        if len(selected_files) != 1:
            raise CompositionError('ambiguous-entrypoint', oid)
        for entry in artifact['files']:
            payload = inputs.read({'path': entry['path'], 'sha256': entry['sha256']})
            files[entry['path']] = {'path': entry['path'], 'sha256': entry['sha256']}
            if entry['path'] == selected:
                _glb(payload, entry['path'])
        result[oid] = {'artifact': artifact, 'ref': ref, 'modelPath': selected}
    if set(result) != expected:
        raise CompositionError('inventory-mismatch', 'artifacts')
    return result, list(files.values())


def _source_rows(record, expected, field):
    rows = record.get('objects')
    if (type(rows) is not list or any(type(row) is not dict or type(row.get('objectId')) is not str for row in rows)
            or len(rows) != len(expected) or {row['objectId'] for row in rows} != expected):
        raise CompositionError('inventory-mismatch', field)
    return {row['objectId']: row for row in rows}


def _ref(output, name, data):
    return {'path': str(output / name), 'sha256': hashlib.sha256(data).hexdigest()}


def validate_metric_anchor(camera_raw, rows, artifacts):
    """Check the camera world scale against explicit admitted metric landmarks."""
    anchor = camera_raw.get('metricAnchor')
    if type(anchor) is not dict:
        raise CompositionError('missing-camera-metric-anchor', 'cameraSolve.metricAnchor')
    points = anchor.get('worldPoints')
    if type(points) is not list or len(points) != 2:
        raise CompositionError('invalid-camera-metric-anchor', 'worldPoints')
    a, b = (_vector(point, 'metricAnchor.worldPoints') for point in points)
    if not all(any(item.get('world') == point for item in camera_raw['correspondences']) for point in (a, b)):
        raise CompositionError('unbound-camera-metric-anchor', 'worldPoints')
    containers = [row for row in rows if row.get('role') == 'scene-container']
    if containers:
        room = artifacts[containers[0]['artifactObjectId']]
        dimensions = room['artifact']['roomDimensions']
        axis = anchor.get('axis')
        if (anchor.get('mode') != 'room-dimensions' or axis not in ('width', 'height', 'depth')
                or anchor.get('roomDimensions') != dimensions
                or anchor.get('containerArtifactSha256') != room['ref']['sha256']):
            raise CompositionError('invalid-camera-metric-anchor', 'roomDimensions')
        index, span = ('width', 'height', 'depth').index(axis), dimensions[axis]
    else:
        class_id = anchor.get('classId')
        member = next((row for row in rows if row.get('classId') == class_id), None)
        if anchor.get('mode') != 'size-prior' or member is None:
            raise CompositionError('invalid-camera-metric-anchor', 'classId')
        prior = _prior(member, artifacts[member['artifactObjectId']]['artifact'])
        span = _number(anchor.get('heightMeters'), 'metricAnchor.heightMeters', True)
        if not prior['min'] <= span <= prior['max']:
            raise CompositionError('scale-outside-prior', 'metricAnchor.heightMeters')
        index = 1
    if (not math.isclose(abs(a[index]-b[index]), span, rel_tol=1e-8, abs_tol=1e-8)
            or any(not math.isclose(a[k], b[k], rel_tol=0, abs_tol=1e-8) for k in range(3) if k != index)):
        raise CompositionError('metric-landmark-span-mismatch', 'worldPoints')


def _prepare(inputs, workspace, arguments):
    inventory, reference = arguments['inventory'], arguments['reference']
    rows = _inventory(inputs.record(inventory))
    inputs.read(reference)
    camera_raw = inputs.record(arguments['camera_solve'])
    camera, fit = solve_camera(camera_raw)
    environment = inputs.record(arguments['environment'])
    if environment.get('type') != 'hemisphere':
        raise CompositionError('unsupported-environment', 'environment')
    _color(environment.get('skyColor'), 'environment.skyColor')
    _color(environment.get('groundColor'), 'environment.groundColor')
    _number(environment.get('intensity'), 'environment.intensity', True)
    artifacts, model_files = _rooted_artifacts(inputs, workspace, arguments['artifacts'], rows)
    containers = [row for row in rows if row.get('role') == 'scene-container']
    if len(containers) > 1:
        raise CompositionError('multiple-containers', 'inventory')
    validate_metric_anchor(camera_raw, rows, artifacts)
    container = containers[0]['objectId'] if containers else None
    if container and any(row['artifactObjectId'] == container and row['objectId'] != container and row.get('instanceOf') for row in rows):
        raise CompositionError('invalid-container-instance', container)
    base = inputs.record(arguments['base_container']) if arguments['base_container'] is not None else None
    if (container is None) == (base is None):
        raise CompositionError('missing-or-duplicate-container', 'baseContainer')
    if base is not None:
        base = {name: {key: base[name][key] for key in ('width', 'height', 'position', 'rotation', 'color')} for name in ('ground', 'backdrop')}
        for name, plane in base.items():
            for key in ('width', 'height'):
                _number(plane[key], 'baseContainer.' + name + '.' + key, True)
            for key in ('position', 'rotation'):
                _vector(plane[key], 'baseContainer.' + name + '.' + key)
            _color(plane['color'], 'baseContainer.' + name + '.color')
    placed = [row for row in rows if row['artifactObjectId'] != container or row['objectId'] == container]
    expected = {row['objectId'] for row in placed}
    all_ids = {row['objectId'] for row in rows}
    for row in rows:
        _prior(row, artifacts[row['artifactObjectId']]['artifact'])
    bindings = {'inventorySha256': inventory['sha256'], 'referenceSha256': reference['sha256'],
                'cameraSolveSha256': arguments['camera_solve']['sha256']}
    depth = inputs.record(arguments['depth'])
    support = inputs.record(arguments['support'])
    for name, evidence in (('depth', depth), ('support', support)):
        _bound(evidence, bindings, name)
        if type(evidence.get('producer')) is not str or not evidence['producer'].strip():
            raise CompositionError('missing-producer', name)
    depths = _source_rows(depth, all_ids, 'depth')
    supports = _source_rows(support, all_ids, 'support')
    anchor = depth.get('anchor')
    if type(anchor) is not dict:
        raise CompositionError('missing-metric-depth-anchor', 'depth.anchor')
    anchor_world = _vector(anchor.get('worldPoint'), 'depth.anchor.worldPoint')
    if not any(point.get('world') == anchor_world for point in camera_raw['correspondences']):
        raise CompositionError('unbound-metric-depth-anchor', 'depth.anchor.worldPoint')
    anchor_relative = _number(anchor.get('relativeDepth'), 'depth.anchor.relativeDepth', True)
    forward = _sub(camera['target'], camera['position'])
    anchor_depth = _dot(_sub(anchor_world, camera['position']), forward)
    if anchor_depth <= camera['near']:
        raise CompositionError('invalid-metric-depth-anchor', 'depth.anchor')
    instances = [{'objectId': row['objectId'], 'artifactObjectId': row['artifactObjectId'],
                  'artifactSha256': artifacts[row['artifactObjectId']]['ref']['sha256'],
                  'modelPath': artifacts[row['artifactObjectId']]['modelPath'], 'position': [0, 0, 0],
                  'scale': [artifacts[row['artifactObjectId']]['artifact']['scaleEstimate']] * 3, 'yawBin': 0} for row in placed]
    data = {'files': model_files, 'instances': instances, 'camera': {k: v for k, v in camera.items() if k != 'type'},
            'environment': {k: environment[k] for k in ('skyColor', 'groundColor', 'intensity')}, 'baseContainer': base}
    # Keep original aspect using its admitted dimensions. The host capture caps
    # dimensions and never silently stretches or reframes the reference camera.
    viewport = {'width': fit['imageWidth'], 'height': fit['imageHeight'], 'devicePixelRatio': 1}
    _, report, renderer = _capture(inputs, data=data, scene_hash=hashlib.sha256(_canonical(data)).hexdigest(),
        reference_hash=reference['sha256'], inventory=rows, chrome=arguments['chrome'], modules=arguments['modules'],
        viewport=viewport, background=0xffffff, timeout_seconds=arguments.get('timeout_seconds', 30))
    geometry = {item['id']: triangles(item['triangles']) for item in report['objects']}
    if set(geometry) != {row['objectId'] for row in rows} or len(report['objects']) != len(rows):
        raise CompositionError('inventory-mismatch', 'authored.geometry')
    output = _path(arguments['output_dir'])
    contents = {}
    def put(name, value):
        payload = _canonical(value) + b'\n'
        contents[name] = payload
        return _ref(output, name, payload)
    camera_ref = put('camera.json', camera)
    fit_ref = put('camera-fit.json', fit)
    geometry_ref = put('geometry.json', {oid: mesh for oid, mesh in sorted(geometry.items())})
    objects, embedded = [], []
    for row_index, row in enumerate(rows):
        oid, aid = row['objectId'], row['artifactObjectId']
        artifact = artifacts[aid]['artifact']
        band = _prior(row, artifact)
        box = bounds(geometry[oid])
        height = box[1][1] - box[0][1]
        if height <= 0:
            raise CompositionError('invalid-authored-height', oid)
        # Uniform correction options keep all three axes in the physical band.
        low, high = band['min']/height, band['max']/height
        prior = {'objectId': oid, 'minScale': [low]*3, 'maxScale': [high]*3,
                 'classId': row.get('classId'), 'heightBand': band,
                 'authoredMetricHeight': height, 'policyVersion': 'size-priors-v1'}
        prefix = f'object-{row_index}-'
        prior_ref = put(prefix + 'prior.json', prior)
        relative = _number(depths[oid].get('relativeDepth'), oid + '.relativeDepth', True)
        camera_depth = relative / anchor_relative * anchor_depth
        if not camera['near'] < camera_depth < camera['far']:
            raise CompositionError('depth-outside-camera', oid)
        common = {'objectId': oid, 'artifactSha256': artifacts[aid]['ref']['sha256'],
                  'inventorySha256': inventory['sha256'], 'referenceSha256': reference['sha256'],
                  'producer': 'base:stage6_compose.placement:v1'}
        depth_ref = put(prefix + 'depth.json', dict(common, cameraDepth=camera_depth, relativeDepth=relative,
            source=arguments['depth'], cameraSolve=arguments['camera_solve'], camera=camera_ref, metricLandmark=anchor_world))
        mode = supports[oid].get('mode')
        support_id = supports[oid].get('supportObjectId')
        if mode not in ('surface', 'free') or (mode == 'surface' and (support_id == oid or support_id not in all_ids | {'base-ground'})):
            raise CompositionError('missing-support-classification', oid)
        if support_id == 'base-ground' and base is None:
            raise CompositionError('missing-support-geometry', oid)
        support_ref = put(prefix + 'support.json', dict(common, mode=mode, supportObjectId=support_id, source=arguments['support']))
        metric = dict(common, source=arguments['camera_solve'], worldLandmark=anchor_world)
        if container:
            metric.update(mode='room-dimensions', containerArtifactSha256=artifacts[container]['ref']['sha256'],
                          roomDimensions=artifacts[container]['artifact']['roomDimensions'])
        else:
            metric.update(mode='size-prior', sizePriorSha256=prior_ref['sha256'])
        metric_ref = put(prefix + 'metric-anchor.json', metric)
        sizes = sorted(set((low, (low+high)/2, high)))
        decision_bindings = [{'role': role, **ref} for role, ref in (
            ('inventory', inventory), ('reference', reference), ('artifact', artifacts[aid]['ref']),
            ('camera', camera_ref), ('size-prior', prior_ref), ('depth', depth_ref), ('support', support_ref))]
        target = objects if oid in expected else embedded
        target.append({'objectId': oid, 'artifactSha256': artifacts[aid]['ref']['sha256'], 'sizePrior': prior_ref,
            'sizeOptions': [{'id': 'size-' + str(i), 'value': [scale]*3} for i, scale in enumerate(sizes)],
            'yawOptions': [{'id': 'yaw-' + str(i), 'value': i} for i in range(8)],
            'decisionBindings': decision_bindings, 'evidence': {'metricAnchor': metric_ref, 'depth': depth_ref, 'support': support_ref}})
    plan = {'kind': 'img2.placement-preparation', 'version': 1, 'arguments': arguments,
            'inventorySha256': inventory['sha256'], 'referenceSha256': reference['sha256'],
            'camera': camera_ref, 'cameraFit': fit_ref, 'geometry': geometry_ref, 'objects': objects,
            'embeddedObjects': embedded,
            'scope': 'actual-authored-rest-pose-metric-geometry-not-placement-approval', 'deliverable': False}
    put('renderer.json', renderer)
    return plan, contents, geometry, report, camera, rows, artifacts


def prepare_placement(workspace, *, inventory, reference, artifacts, camera_solve, depth, support,
                      environment, base_container, chrome, modules, output_dir, timeout_seconds=30):
    """Freeze actual dimensions and bounded choices before journal consultations."""
    arguments = dict(inventory=inventory, reference=reference, artifacts=artifacts, camera_solve=camera_solve,
                     depth=depth, support=support, environment=environment, base_container=base_container,
                     chrome=chrome, modules=modules, output_dir=output_dir, timeout_seconds=timeout_seconds)
    inputs = Inputs(workspace)
    try:
        plan, contents, *_ = _prepare(inputs, workspace, arguments)
        output = _path(output_dir)
        for path in inputs.snapshots:
            if _path(path).is_relative_to(output):
                raise CompositionError('output-overlaps-input', path)
        inputs.recheck()
        contents['preparation.json'] = _canonical(plan) + b'\n'
        _write_output(inputs.fd, output, contents)
        return plan
    finally:
        inputs.close()


def _position(camera, box, depth):
    box_iou(box, box)  # validate normalized nonempty corners
    forward = _sub(camera['target'], camera['position'])
    up = camera['up']
    right = _cross(forward, up)
    tangent = math.tan(math.radians(camera['fov'])/2)
    x, y = ((box[0]+box[2])/2 * 2 - 1), 1 - box[3]*2
    return [p + depth*(f + x*tangent*camera['aspect']*r + y*tangent*u)
            for p, f, r, u in zip(camera['position'], forward, right, up)]


def _transform(mesh, scale, yaw, position):
    c, s = math.cos(yaw*math.pi/4), math.sin(yaw*math.pi/4)
    return [tuple((position[0]+c*p[0]*scale[0]+s*p[2]*scale[2],
                   position[1]+p[1]*scale[1], position[2]-s*p[0]*scale[0]+c*p[2]*scale[2]) for p in tri) for tri in mesh]


def produce_placement(workspace, *, preparation, decisions, verify_answer, output_dir, timeout_seconds=30):
    """Recompute source geometry and consume journal authority; emit composer v1."""
    if not callable(verify_answer):
        raise CompositionError('placement-authority-required', 'verify_answer')
    inputs = Inputs(workspace)
    try:
        plan = inputs.record(preparation)
        if plan.get('kind') != 'img2.placement-preparation' or plan.get('version') != 1:
            raise CompositionError('invalid-record', 'preparation')
        arguments = deepcopy(plan['arguments'])
        arguments['timeout_seconds'] = timeout_seconds
        recomputed, contents, geometry, report, camera, rows, artifacts = _prepare(inputs, workspace, arguments)
        # Timeout is an execution deadline, not semantic prepared input identity.
        recomputed['arguments']['timeout_seconds'] = plan['arguments']['timeout_seconds']
        if recomputed != plan:
            raise CompositionError('stale-placement-preparation', 'preparation')
        for ref in (plan['camera'], plan['cameraFit'], plan['geometry']):
            payload = inputs.read(ref)
            if contents[_path(ref['path']).name] != payload:
                raise CompositionError('stale-placement-preparation', ref['path'])
        expected = {row['objectId'] for row in plan['objects']}
        if type(decisions) is not dict or set(decisions) != expected:
            raise CompositionError('inventory-mismatch', 'decisions')
        output = _path(output_dir)
        generated = {}
        def put(name, value):
            payload = _canonical(value) + b'\n'
            generated[name] = payload
            return _ref(output, name, payload)
        inventory_rows = {row['objectId']: row for row in rows}
        placement_rows, world = {}, {}
        model_session = set()
        for index, prepared in enumerate(plan['objects']):
            oid = prepared['objectId']
            source = inventory_rows[oid]
            answers = {}
            for question in ('size', 'yaw'):
                item = decisions[oid][question]
                context, record = inputs.record(item['context']), inputs.record(item['record'])
                if (context.get('subject', {}).get('objectId') != oid
                        or context.get('requirements', {}).get('placement') != {'question': question}
                        or context.get('bindings') != prepared['decisionBindings']
                        or context.get('candidates') != prepared[question + 'Options']):
                    raise CompositionError('placement-decision-context-mismatch', oid + '.' + question)
                answer = verify_answer(deepcopy(context), deepcopy(record))
                if (type(answer) is not dict or answer.get('objectId') != oid or answer.get('question') != question
                        or answer.get('artifactSha256') != prepared['artifactSha256']
                        or answer.get('inventorySha256') != plan['inventorySha256']
                        or answer.get('referenceSha256') != plan['referenceSha256']
                        or answer.get('decisionRef') != record.get('recordId')):
                    raise CompositionError('invalid-verified-placement-answer', oid)
                if not any(candidate['value'] == answer.get('value') for candidate in context['candidates']):
                    raise CompositionError('out-of-set', oid)
                answer['decisionEvidence'] = {'context': item['context'], 'record': item['record']}
                reason = answer.get('reasonCode')
                answer['sessionAtDecision'] = 'unavailable:' + reason if reason in RUN_UNAVAILABLE else 'healthy'
                if answer['decider'].startswith('model:'):
                    reason = answer['reasonCode']
                    model_session.add(reason)
                    answer['unavailableReceipt'] = put(f'object-{index}-{question}-receipt.json', {
                        'kind': 'img2.placement-unavailable-receipt', 'version': 1, 'reasonCode': reason,
                        'decisionRef': answer['decisionRef'], 'context': item['context'], 'record': item['record']})
                answers[question] = answer
            # Retained prior/evidence bytes must equal the independently recomputed ones.
            for ref in (prepared['sizePrior'], *prepared['evidence'].values()):
                if inputs.read(ref) != contents[_path(ref['path']).name]:
                    raise CompositionError('stale-placement-preparation', ref['path'])
            depth = inputs.record(prepared['evidence']['depth'])['cameraDepth']
            position = [0, 0, 0] if source.get('role') == 'scene-container' else _position(camera, source['approxBox'], depth)
            scale, yaw = answers['size']['value'], answers['yaw']['value']
            world[oid] = _transform(geometry[oid], scale, yaw, position)
            placement_rows[oid] = dict(prepared, position=position, scale=scale, yawBin=yaw,
                sizeAnswer=put(f'object-{index}-size.json', answers['size']),
                yawAnswer=put(f'object-{index}-yaw.json', answers['yaw']))
        for embedded in plan['embeddedObjects']:
            oid = embedded['objectId']
            for ref in (embedded['sizePrior'], *embedded['evidence'].values()):
                if inputs.read(ref) != contents[_path(ref['path']).name]:
                    raise CompositionError('stale-placement-preparation', ref['path'])
            parent = placement_rows[inventory_rows[oid]['artifactObjectId']]
            world[oid] = _transform(geometry[oid], parent['scale'], parent['yawBin'], parent['position'])
        pending = set(expected)
        while pending:
            advanced = False
            for oid in sorted(pending):
                support = inputs.record(placement_rows[oid]['evidence']['support'])
                if support['mode'] == 'surface':
                    support_id = support['supportObjectId']
                    dependency = support_id if support_id in expected else inventory_rows.get(support_id, {}).get('artifactObjectId')
                    if dependency in pending:
                        continue
                    surface = triangles(report['support']['ground']) if support_id == 'base-ground' else world[support_id]
                    bottom = bounds(world[oid])[0][1]
                    top = bounds(surface)[1][1] + max(1, bounds(world[oid])[1][1]-bottom)
                    hits = []
                    for p in {p for tri in world[oid] for p in tri if abs(p[1]-bottom) < 1e-8}:
                        distances = [d for tri in surface if (d := ray_distance((p[0], top, p[2]), (0, -1, 0), tri)) is not None]
                        if distances:
                            hits.append(top-min(distances))
                    if not hits:
                        raise CompositionError('missing-support-ray-hit', oid)
                    shift = max(hits)-bottom
                    placement_rows[oid]['position'][1] += shift
                    world[oid] = [tuple((p[0], p[1]+shift, p[2]) for p in tri) for tri in world[oid]]
                    contact_gap(world[oid], surface)
                    for embedded in plan['embeddedObjects']:
                        eid = embedded['objectId']
                        if inventory_rows[eid]['artifactObjectId'] == oid:
                            world[eid] = _transform(geometry[eid], placement_rows[oid]['scale'],
                                                    placement_rows[oid]['yawBin'], placement_rows[oid]['position'])
                pending.remove(oid)
                advanced = True
            if not advanced:
                raise CompositionError('cyclic-support-dependency', 'support')
        for row in placement_rows.values():
            for key in ('sizeOptions', 'yawOptions', 'decisionBindings'):
                row.pop(key)
        # Group-scoped outages keep a healthy run. Only true run-wide outages may
        # become unavailable:<reason>; contradictory session reasons fail closed.
        outages = model_session & RUN_UNAVAILABLE
        if len(outages) > 1:
            raise CompositionError('inconsistent-placement-session', 'jevSession')
        session = 'unavailable:' + next(iter(outages)) if outages else 'healthy'
        result = {'kind': 'img2.scene-placement', 'version': 1, 'inventorySha256': plan['inventorySha256'],
                  'referenceSha256': plan['referenceSha256'], 'jevSession': session, 'camera': plan['camera'],
                  'environment': arguments['environment'], 'baseContainer': arguments['base_container'],
                  'objects': list(placement_rows.values()), 'preparation': preparation,
                  'embeddedEvidence': {row['objectId']: {'artifactSha256': row['artifactSha256'],
                      'objectId': row['objectId'], 'sizePrior': row['sizePrior'], 'evidence': row['evidence']}
                      for row in plan['embeddedObjects']}, 'deliverable': False}
        for path in inputs.snapshots:
            if _path(path).is_relative_to(output):
                raise CompositionError('output-overlaps-input', path)
        inputs.recheck()
        # The trusted callback is invoked again at publication, not just before
        # expensive geometry processing. It may enclose a live transaction verifier.
        for oid in expected:
            for question in ('size', 'yaw'):
                item = decisions[oid][question]
                verify_answer(inputs.record(item['context']), inputs.record(item['record']))
        generated['placement.json'] = _canonical(result) + b'\n'
        _write_output(inputs.fd, output, generated)
        return result
    finally:
        inputs.close()


