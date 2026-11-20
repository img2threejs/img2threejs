"""Base-owned D11/D18 GLB composition; Python 3.10, standard library only.

compose_scene(workspace, request, *, output_dir) writes a NEW candidate directory.
It does not consult a provider, own a lease, advance state, or accept delivery: the
parent must check its lease and terminal child gates before calling this library.
2D returns a bypass without reading manifests or creating files.

Request v1: {kind:'img2.scene-compose-request', version:1, expectedOutput:'3d',
 inventory:REF, artifacts:[REF], placement:REF, reference:REF}. REF is
{path:canonical workspace-relative POSIX path, sha256:lowercase SHA-256}.
Inventory is {kind:'img2.object-inventory',version:1,objects:[{objectId,role,
 ownerRoute,artifactObjectId,instanceOf?}]}. artifactObjectId names the child whose
artifact owns that row; a container can own embedded props. Repeated separately
placed instances must share a nonempty instanceOf, ownerRoute and artifactObjectId.
Artifact manifests use validate_artifact's D18 schema plus ownerRoute,
ownedObjectIds (exact inventory coverage), and, for the container, excludeComponents
(exact list of all independently routed instances). A unique GLB is required; an
optional entrypoint explicitly selects among multiple GLBs. Source-only factories
fail closed: no compiled and verified source consumer exists here.

Placement: {kind:'img2.scene-placement',version:1,inventorySha256,
 referenceSha256,jevSession:'healthy'|'unavailable:<reason>',camera:REF,
 environment:REF,baseContainer:REF|null,objects:[{objectId,artifactSha256,
 position:[x,y,z],scale:[x,y,z],yawBin:0..7,sizeAnswer:REF,yawAnswer:REF,
 sizePrior:REF,evidence:{metricAnchor:REF,depth:REF,support:REF}}]}.
Only independently placed artifact owners / repeated instances have placements.
Position is metres; scale is a positive dimensionless correction multiplied by
D18 scaleEstimate (metres per authored unit). Rotation is only the recorded yaw.
Each answer is {kind:'img2.placement-answer',version:1,objectId,question:'size'|'yaw',
 inventorySha256,referenceSha256,artifactSha256,decider:'jev:<id>'|'model:<id>',
 reasonCode,value, ...}; model answers additionally require unavailableReceipt:REF.
For a healthy session model fallback needs reasonCode in REQUEST_FAILURES. No
answer is created here. Receipts/evidence are retained, NOT authenticated here.
The parent must establish controller provenance/consent and prior-policy authority.
sizePrior is {objectId,minScale:[x,y,z],maxScale:[x,y,z]}; correction factors must
fall within it. Missing placement producers fail rather than inventing positions.
Each evidence record binds objectId, artifactSha256, inventorySha256 and
referenceSha256 and names a nonempty producer. metricAnchor additionally records
mode:'room-dimensions', containerArtifactSha256 and exact roomDimensions, or
mode:'size-prior' and sizePriorSha256 without a container. Producer authenticity,
actual support/depth measurements and semantic exclusion from container geometry
are prerequisites for parent review, not facts established by metadata checks.

Camera record: {type:'perspective',position:[x,y,z],target:[x,y,z],fov,aspect,
 near,far}. Environment: {type:'hemisphere',skyColor:0..0xffffff,
 groundColor:0..0xffffff,intensity:positive}. No light is inferred. Without a
container baseContainer explicitly records ground and backdrop, each {width,height,
 position:[x,y,z],rotation:[x,y,z],color:0..0xffffff}. These are parent-generated
planes with recorded dimensions, not guessed geometry. With a container it is null.
All input bytes are hash checked again at browser load. GLBs must be self-contained;
external resources, embedded lights and unsupported decoders fail closed. Output
quality/runtime/rig parity stay unevaluated pending independent measurements and
actual browser + screenshot gates. No imported plugin code or shell execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
from urllib.parse import quote

from _shared.route_artifact import validate_artifact

REQUEST_FAILURES = frozenset({'provider-error', 'timeout', 'malformed', 'out-of-set', 'capability-unavailable'})
RUN_UNAVAILABLE = frozenset({'jev-not-installed', 'login-declined', 'consent-declined', 'budget-declined'})
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')


class CompositionError(ValueError):
    def __init__(self, reason_code, field):
        self.reason_code = reason_code
        self.field = field
        super().__init__(f'{reason_code}: {field}')


def _fail(reason, field):
    raise CompositionError(reason, field)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CompositionError('invalid-json', 'input') from exc


def _json(data, field):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail('invalid-json', field)
            result[key] = value
        return result
    def constant(value):
        _fail('invalid-json', field)
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        _canonical(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CompositionError):
            raise
        raise CompositionError('invalid-json', field) from exc

def _path(value):
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _fail('unsafe-path', 'path')
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(p in ('.', '..') for p in path.parts):
        _fail('unsafe-path', value)
    return path


def _read(root_fd, path):
    parts = _path(path).parts
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 1:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail('unsafe-path', path)
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read()
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            _fail('evidence-changed', path)
        if not data:
            _fail('missing-evidence', path)
        return data
    except OSError as exc:
        reason = 'missing-evidence' if isinstance(exc, FileNotFoundError) else 'unsafe-path'
        raise CompositionError(reason, path) from exc
    finally:
        os.close(fd)


def _record(value, kind):
    if type(value) is not dict or value.get('kind') != kind or value.get('version') != 1 or type(value.get('version')) is not int:
        _fail('invalid-record', kind)
    return value


def _number(value, field, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        _fail('invalid-transform', field)
    return value


def _vector(value, field, positive=False):
    if type(value) is not list or len(value) != 3:
        _fail('invalid-transform', field)
    return [_number(x, field, positive) for x in value]


def _color(value, field):
    if type(value) is not int or not 0 <= value <= 0xffffff:
        _fail('invalid-environment', field)
    return value


def _ids(value, expected, field):
    if (type(value) is not list or any(type(x) is not str for x in value)
            or len(value) != len(set(value)) or set(value) != expected):
        _fail('inventory-mismatch', field)


def _glb(data, path):
    size = struct.unpack_from('<I', data, 12)[0]
    document = _json(data[20:20 + size], path)
    used = document.get('extensionsUsed', [])
    required = document.get('extensionsRequired', [])
    if type(used) is not list or type(required) is not list:
        _fail('invalid-glb', path)
    if 'KHR_lights_punctual' in used + required:
        _fail('child-light-rig', path)
    if any(x in used + required for x in ('KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'KHR_texture_basisu')):
        _fail('decoder-unavailable', path)
    def visit(value):
        if type(value) is dict:
            for key, item in value.items():
                if key == 'KHR_lights_punctual':
                    _fail('child-light-rig', path)
                if key in ('KHR_draco_mesh_compression', 'EXT_meshopt_compression', 'KHR_texture_basisu'):
                    _fail('decoder-unavailable', path)
                if key == 'uri' and (type(item) is not str or not item.startswith('data:')):
                    _fail('external-glb-resource', path)
                visit(item)
        elif type(value) is list:
            for item in value:
                visit(item)
    visit(document)
    scenes = document.get('scenes')
    index = document.get('scene', 0)
    if (type(scenes) is not list or type(index) is not int or not 0 <= index < len(scenes)
            or type(scenes[index]) is not dict or not scenes[index].get('nodes')):
        _fail('missing-gltf-scene', path)


def compose_scene(workspace, request, *, output_dir):
    """Write scene.mjs + scene.json in an exclusive new directory; return manifest."""
    request = _record(_json(_canonical(request), 'request'), 'img2.scene-compose-request')
    if request.get('expectedOutput') == '2d':
        return {'status': 'bypassed', 'reasonCode': 'explicit-2d', 'files': []}
    if request.get('expectedOutput') != '3d':
        _fail('unsupported-output-kind', 'expectedOutput')
    output = _path(output_dir)
    root = Path(workspace)
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CompositionError('unsafe-path', 'workspace') from exc
    files = {}
    def read_ref(ref):
        if type(ref) is not dict or set(ref) != {'path', 'sha256'} or type(ref.get('sha256')) is not str or not _HASH.fullmatch(ref['sha256']):
            _fail('invalid-reference', 'reference')
        path = str(_path(ref['path']))
        data = _read(fd, path)
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            _fail('hash-mismatch', path)
        if path in files and files[path]['sha256'] != ref['sha256']:
            _fail('hash-mismatch', path)
        files[path] = dict(ref)
        return data
    def record_ref(ref):
        result = _json(read_ref(ref), ref['path'])
        if type(result) is not dict:
            _fail('invalid-record', ref['path'])
        return result
    try:
        inventory = _record(record_ref(request.get('inventory')), 'img2.object-inventory')
        placements = _record(record_ref(request.get('placement')), 'img2.scene-placement')
        read_ref(request.get('reference'))
        bindings = {'inventorySha256': request['inventory']['sha256'], 'referenceSha256': request['reference']['sha256']}
        for key, digest in bindings.items():
            if placements.get(key) != digest:
                _fail('stale-placement', key)
        objects = inventory.get('objects')
        if type(objects) is not list or not objects:
            _fail('missing-evidence', 'inventory.objects')
        by_id, groups = {}, {}
        for row in objects:
            if type(row) is not dict or type(row.get('objectId')) is not str or not _ID.fullmatch(row['objectId']):
                _fail('invalid-inventory', 'objectId')
            oid = row['objectId']
            if oid in by_id:
                _fail('duplicate-object', oid)
            if (row.get('role') not in ('scene-container', 'subject', 'prop', 'set-dressing')
                    or type(row.get('ownerRoute')) is not str or not row['ownerRoute']
                    or type(row.get('artifactObjectId')) is not str):
                _fail('invalid-inventory', oid)
            by_id[oid] = row
            groups.setdefault(row['artifactObjectId'], []).append(row)
        containers = [row for row in objects if row['role'] == 'scene-container']
        if len(containers) > 1:
            _fail('multiple-containers', 'inventory')
        container_id = containers[0]['objectId'] if containers else None
        if container_id is not None and by_id[container_id]['artifactObjectId'] != container_id:
            _fail('ownership-mismatch', container_id)
        for aid, rows in groups.items():
            if aid not in by_id or by_id[aid]['artifactObjectId'] != aid:
                _fail('inventory-mismatch', aid)
            if len({row['ownerRoute'] for row in rows}) != 1:
                _fail('ownership-mismatch', aid)
            if aid != container_id and len(rows) > 1:
                instance = rows[0].get('instanceOf')
                if type(instance) is not str or not instance or any(row.get('instanceOf') != instance for row in rows):
                    _fail('duplicate-routed-object', aid)
        artifacts = {}
        model_hashes = {}
        refs = request.get('artifacts')
        if type(refs) is not list:
            _fail('missing-evidence', 'artifacts')
        for ref in refs:
            original = record_ref(ref)
            artifact = validate_artifact(root, original)
            aid = artifact['objectId']
            if aid in artifacts:
                _fail('duplicate-artifact', aid)
            if aid not in groups:
                _fail('inventory-mismatch', aid)
            if artifact.get('ownerRoute') != by_id[aid]['ownerRoute']:
                _fail('ownership-mismatch', aid)
            _ids(artifact.get('ownedObjectIds'), {row['objectId'] for row in groups[aid]}, aid + '.ownedObjectIds')
            if (artifact['outputKind'] == '3d-scene') != (aid == container_id):
                _fail('container-kind-mismatch', aid)
            if aid == container_id:
                _ids(artifact.get('excludeComponents'), set(by_id) - {r['objectId'] for r in groups[aid]}, aid + '.excludeComponents')
            glbs = [entry for entry in artifact['files'] if entry['path'].lower().endswith('.glb')]
            if not glbs:
                _fail('source-consumer-unavailable', aid)
            selected = artifact.get('entrypoint')
            if selected is None and len(glbs) == 1:
                selected = glbs[0]['path']
            matches = [entry for entry in glbs if entry['path'] == selected]
            if len(matches) != 1:
                _fail('ambiguous-entrypoint', aid)
            model = matches[0]
            if model['sha256'] in model_hashes:
                _fail('duplicate-routed-artifact', aid)
            model_hashes[model['sha256']] = aid
            for entry in artifact['files']:
                data = read_ref({'path': entry['path'], 'sha256': entry['sha256']})
                if entry['path'] == selected:
                    _glb(data, selected)
            artifacts[aid] = (artifact, ref['sha256'], model['path'])
        if set(artifacts) != set(groups):
            _fail('inventory-mismatch', 'artifacts')
        expected = {oid for oid, row in by_id.items() if row['artifactObjectId'] != container_id or oid == container_id}
        instances = []
        seen = set()
        session = placements.get('jevSession')
        if session != 'healthy' and (type(session) is not str or not session.startswith('unavailable:') or session[12:] not in RUN_UNAVAILABLE):
            _fail('invalid-session', 'jevSession')
        rows = placements.get('objects')
        if type(rows) is not list:
            _fail('missing-evidence', 'placement.objects')
        for row in rows:
            if (type(row) is not dict or type(row.get('objectId')) is not str
                    or row['objectId'] not in expected):
                _fail('inventory-mismatch', 'placement.objects')
            oid = row['objectId']
            if oid in seen:
                _fail('duplicate-object', oid)
            seen.add(oid)
            artifact, digest, model_path = artifacts[by_id[oid]['artifactObjectId']]
            if row.get('artifactSha256') != digest:
                _fail('stale-placement', oid)
            position = _vector(row.get('position'), oid + '.position')
            scale = _vector(row.get('scale'), oid + '.scale', True)
            yaw = row.get('yawBin')
            if type(yaw) is not int or not 0 <= yaw <= 7:
                _fail('invalid-transform', oid + '.yawBin')
            for question, value in (('size', scale), ('yaw', yaw)):
                answer = _record(record_ref(row.get(question + 'Answer')), 'img2.placement-answer')
                if question == 'size':
                    _vector(answer.get('value'), oid + '.sizeAnswer', True)
                elif type(answer.get('value')) is not int:
                    _fail('invalid-transform', oid + '.yawAnswer')
                if any(answer.get(key) != val for key, val in dict(bindings, objectId=oid, artifactSha256=digest, question=question, value=value).items()):
                    _fail('stale-placement-answer', oid + '.' + question)
                decider = answer.get('decider')
                if type(decider) is not str or not re.fullmatch(r'(jev|model):[^\s]+', decider):
                    _fail('invalid-decider', oid)
                if decider.startswith('model:'):
                    record_ref(answer.get('unavailableReceipt'))
                    if session == 'healthy' and answer.get('reasonCode') not in REQUEST_FAILURES:
                        _fail('invalid-fallback', oid)
                    if session != 'healthy' and answer.get('reasonCode') != session[12:]:
                        _fail('invalid-fallback', oid)
                elif session != 'healthy' or answer.get('reasonCode') != 'answered':
                    _fail('invalid-session', oid)
            prior = record_ref(row.get('sizePrior'))
            low = _vector(prior.get('minScale'), oid + '.minScale', True)
            high = _vector(prior.get('maxScale'), oid + '.maxScale', True)
            if prior.get('objectId') != oid or any(not lo <= value <= hi for lo, value, hi in zip(low, scale, high)):
                _fail('scale-outside-prior', oid)
            evidence = row.get('evidence')
            if type(evidence) is not dict or set(evidence) != {'metricAnchor', 'depth', 'support'}:
                _fail('missing-evidence', oid + '.evidence')
            for name, ref in evidence.items():
                measurement = record_ref(ref)
                if (any(measurement.get(key) != value for key, value in dict(bindings, objectId=oid, artifactSha256=digest).items())
                        or type(measurement.get('producer')) is not str or not measurement['producer']):
                    _fail('stale-placement-evidence', oid + '.' + name)
                if name == 'metricAnchor':
                    if container_id is not None:
                        room, room_digest, _ = artifacts[container_id]
                        if (measurement.get('mode') != 'room-dimensions'
                                or measurement.get('containerArtifactSha256') != room_digest
                                or measurement.get('roomDimensions') != room['roomDimensions']):
                            _fail('invalid-metric-anchor', oid)
                    elif (measurement.get('mode') != 'size-prior'
                            or measurement.get('sizePriorSha256') != row['sizePrior']['sha256']):
                        _fail('invalid-metric-anchor', oid)
            effective = [_number(x * artifact['scaleEstimate'], oid + '.effectiveScale', True) for x in scale]
            instances.append({'objectId': oid, 'artifactObjectId': artifact['objectId'], 'modelPath': model_path,
                              'artifactSha256': digest, 'position': position, 'scale': effective, 'yawBin': yaw})
        if seen != expected:
            _fail('inventory-mismatch', 'placement.objects')
        camera = record_ref(placements.get('camera'))
        if camera.get('type') != 'perspective':
            _fail('unsupported-camera', 'camera')
        camera = {key: camera.get(key) for key in ('position', 'target', 'fov', 'aspect', 'near', 'far')}
        _vector(camera['position'], 'camera.position')
        _vector(camera['target'], 'camera.target')
        for key in ('fov', 'aspect', 'near', 'far'):
            _number(camera[key], 'camera.' + key, True)
        if camera['position'] == camera['target'] or not 0 < camera['fov'] < 180 or camera['far'] <= camera['near']:
            _fail('invalid-camera', 'camera')
        environment = record_ref(placements.get('environment'))
        if environment.get('type') != 'hemisphere':
            _fail('unsupported-environment', 'environment')
        environment = {'skyColor': _color(environment.get('skyColor'), 'skyColor'),
                       'groundColor': _color(environment.get('groundColor'), 'groundColor'),
                       'intensity': _number(environment.get('intensity'), 'intensity', True)}
        base = None
        if container_id is None:
            base = record_ref(placements.get('baseContainer'))
            for name in ('ground', 'backdrop'):
                plane = base.get(name)
                if type(plane) is not dict:
                    _fail('missing-evidence', 'baseContainer.' + name)
                for key in ('width', 'height'):
                    _number(plane.get(key), name + '.' + key, True)
                _vector(plane.get('position'), name + '.position')
                _vector(plane.get('rotation'), name + '.rotation')
                _color(plane.get('color'), name + '.color')
            base = {name: {key: base[name][key] for key in ('width', 'height', 'position', 'rotation', 'color')} for name in ('ground', 'backdrop')}
        elif placements.get('baseContainer') is not None:
            _fail('duplicate-container', 'baseContainer')
        for path in files:
            if PurePosixPath(path).is_relative_to(output):
                _fail('output-overlaps-input', path)
        data = {'files': [dict(ref, url=quote(posixpath.relpath(path, str(output)), safe='/')) for path, ref in sorted(files.items())],
                'instances': instances, 'camera': camera, 'environment': environment, 'baseContainer': base}
        template = Path(__file__).with_name('runtime.mjs').read_text(encoding='utf-8')
        module = template.replace('__COMPOSITION_JSON__', _canonical(data).decode('utf-8')).encode('utf-8')
        manifest = {'kind': 'img2.composed-scene', 'version': 1, 'sceneSha256': hashlib.sha256(module).hexdigest(),
                    'referenceSha256': bindings['referenceSha256'], 'inventorySha256': bindings['inventorySha256'],
                    'objects': [{'id': oid, 'artifactObjectId': by_id[oid]['artifactObjectId']} for oid in by_id],
                    'instances': instances, 'inputs': list(files.values()), 'frame': {'units': 'm', 'up': '+Y', 'forward': '+Z'},
                    'module': {'path': str(output / 'scene.mjs'), 'sha256': hashlib.sha256(module).hexdigest()},
                    'validation': {'runtime': 'unevaluated', 'visualQuality': 'unevaluated', 'sceneQuality': 'unevaluated', 'rigParity': 'unevaluated'},
                    'deliverable': False}
        _write_output(fd, output, {'scene.mjs': module, 'scene.json': _canonical(manifest) + b'\n'})
        return manifest
    finally:
        os.close(fd)


def _write_output(root_fd, output, contents):
    """Exclusive directory and temporary-file rename; never follow output symlinks."""
    fd = os.dup(root_fd)
    try:
        for part in output.parts[:-1]:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        try:
            os.mkdir(output.name, dir_fd=fd)
        except FileExistsError as exc:
            raise CompositionError('output-exists', str(output)) from exc
        next_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = next_fd
        for name, data in contents.items():
            temporary = name + '.tmp'
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            with os.fdopen(out, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
    except OSError as exc:
        raise CompositionError('unsafe-output-path', str(output)) from exc
    finally:
        os.close(fd)
