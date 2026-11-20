"""Offline composer contracts; GLBs contain real triangles, skin and animation.

These are format/runtime-input fixtures, not evidence of visual reconstruction
quality. Node/GLTFLoader and browser screenshot gates are intentionally separate.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from _shared.route_artifact import ArtifactError
from stage6_compose.compose import CompositionError, compose_scene


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def glb_fixture(tag='subject'):
    """A self-contained skinned triangle with one material and animated joint."""
    positions = [0, 0, 0, 1, 0, 0, 0, 1, 0]
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    times = [0, 1]
    translations = [0, 0, 0, 0, 0.25, 0]
    chunks = [struct.pack('<9f', *positions), struct.pack('<12H', *([0] * 12)),
              struct.pack('<12f', *([1, 0, 0, 0] * 3)), struct.pack('<16f', *identity),
              struct.pack('<2f', *times), struct.pack('<6f', *translations)]
    views = []
    binary = b''
    for chunk in chunks:
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(chunk)})
        binary += chunk
    document = {
        'asset': {'version': '2.0', 'extras': {'fixture': tag}}, 'scene': 0,
        'scenes': [{'nodes': [0, 1]}],
        'nodes': [{'name': 'triangle', 'mesh': 0, 'skin': 0}, {'name': 'root-joint'}],
        'skins': [{'joints': [1], 'skeleton': 1, 'inverseBindMatrices': 3}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'JOINTS_0': 1, 'WEIGHTS_0': 2}, 'material': 0}]}],
        'materials': [{'pbrMetallicRoughness': {'baseColorFactor': [0.2, 0.4, 0.8, 1], 'metallicFactor': 0, 'roughnessFactor': 0.6}}],
        'animations': [{'name': 'rise', 'samplers': [{'input': 4, 'output': 5, 'interpolation': 'LINEAR'}],
                        'channels': [{'sampler': 0, 'target': {'node': 1, 'path': 'translation'}}]}],
        'buffers': [{'byteLength': len(binary)}], 'bufferViews': views,
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3', 'min': [0, 0, 0], 'max': [1, 1, 0]},
            {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'VEC4'},
            {'bufferView': 2, 'componentType': 5126, 'count': 3, 'type': 'VEC4'},
            {'bufferView': 3, 'componentType': 5126, 'count': 1, 'type': 'MAT4'},
            {'bufferView': 4, 'componentType': 5126, 'count': 2, 'type': 'SCALAR', 'min': [0], 'max': [1]},
            {'bufferView': 5, 'componentType': 5126, 'count': 2, 'type': 'VEC3'},
        ],
    }
    skeleton = {'skins': document['skins'], 'jointNodes': [document['nodes'][1]], 'inverseBindMatrices': identity}
    animation = {'animation': document['animations'][0], 'times': times, 'translations': translations}
    return document, binary, skeleton, animation


def pack_glb(document, binary):
    payload = canonical(document)
    payload += b' ' * (-len(payload) % 4)
    binary += b'\0' * (-len(binary) % 4)
    chunks = struct.pack('<II', len(payload), 0x4e4f534a) + payload
    chunks += struct.pack('<II', len(binary), 0x004e4942) + binary
    return struct.pack('<4sII', b'glTF', 2, 12 + len(chunks)) + chunks


class SceneComposerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifacts = {}
        self.inventory = {'kind': 'img2.object-inventory', 'version': 1, 'objects': []}
        self.placement = {'kind': 'img2.scene-placement', 'version': 1, 'jevSession': 'healthy', 'objects': [],
                          'camera': self.put('setup/camera.json', {'type': 'perspective', 'position': [2, 2, 4],
                              'target': [0, 0.5, 0], 'fov': 40, 'aspect': 1.5, 'near': 0.1, 'far': 100}),
                          'environment': self.put('setup/environment.json', {'type': 'hemisphere',
                              'skyColor': 0xffffff, 'groundColor': 0x555555, 'intensity': 1}),
                          'baseContainer': self.put('setup/base.json', {
                              'ground': {'width': 10, 'height': 10, 'position': [0, 0, 0], 'rotation': [-math.pi / 2, 0, 0], 'color': 0x888888},
                              'backdrop': {'width': 10, 'height': 5, 'position': [0, 2.5, -4], 'rotation': [0, 0, 0], 'color': 0x777777}})}
        self.request = {'kind': 'img2.scene-compose-request', 'version': 1, 'expectedOutput': '3d',
                        'reference': self.put('reference/input.bin', b'reference binding, no visual claim')}
        self.add_object('subject', rigged=True)
        self.bind()

    def put(self, name, value):
        data = value if type(value) is bytes else canonical(value)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    def get(self, ref):
        return json.loads((self.root / ref['path']).read_bytes())

    def change(self, ref, **changes):
        value = self.get(ref)
        value.update(changes)
        return self.put(ref['path'], value)

    def add_object(self, oid, *, rigged=False, container=False):
        document, binary, skeleton, animation = glb_fixture(oid)
        model = self.put(f'objects/{oid}/model.glb', pack_glb(document, binary))
        artifact = {'objectId': oid, 'outputKind': '3d-scene' if container else 'rigged-3d' if rigged else '3d-object',
                    'ownerRoute': 'plugin:interior@fixture' if container else 'base:generic',
                    'ownedObjectIds': [oid], 'files': [model], 'rig': None,
                    'frame': {'units': 'm', 'up': '+Y', 'forward': '+Z'}, 'pivot': 'bottom-centre', 'scaleEstimate': 0.5}
        if rigged:
            sk = self.put(f'objects/{oid}/skeleton.json', skeleton)
            clip = self.put(f'objects/{oid}/rise.json', animation)
            artifact['files'] += [sk, clip]
            artifact['rig'] = {'skeletonSha': sk['sha256'], 'clips': [{'name': 'rise', 'sha256': clip['sha256']}]}
        if container:
            artifact['excludeComponents'] = []
            artifact['roomDimensions'] = {'width': 5, 'height': 3, 'depth': 4}
            self.placement['baseContainer'] = None
        self.artifacts[oid] = artifact
        self.inventory['objects'].append({'objectId': oid, 'role': 'scene-container' if container else 'subject',
                                         'ownerRoute': artifact['ownerRoute'], 'artifactObjectId': oid})
        self.add_placement(oid)

    def add_placement(self, oid):
        self.placement['objects'].append({'objectId': oid, 'position': [0, 0, 0], 'scale': [1, 1, 1], 'yawBin': 2,
            'sizePrior': self.put(f'placement/{oid}/prior.json', {'objectId': oid, 'minScale': [0.5] * 3, 'maxScale': [2] * 3})})

    def bind(self):
        self.request['inventory'] = self.put('inventory.json', self.inventory)
        self.request['artifacts'] = [self.put(f'objects/{oid}/artifact.json', artifact) for oid, artifact in self.artifacts.items()]
        hashes = {oid: ref['sha256'] for oid, ref in zip(self.artifacts, self.request['artifacts'])}
        common = {'inventorySha256': self.request['inventory']['sha256'], 'referenceSha256': self.request['reference']['sha256']}
        self.placement.update(common)
        container = next((oid for oid, a in self.artifacts.items() if a['outputKind'] == '3d-scene'), None)
        by_id = {row['objectId']: row for row in self.inventory['objects']}
        for row in self.placement['objects']:
            oid = row['objectId']
            aid = by_id[oid]['artifactObjectId']
            binding = dict(common, objectId=oid, artifactSha256=hashes[aid])
            row['artifactSha256'] = hashes[aid]
            for question, value in (('size', row['scale']), ('yaw', row['yawBin'])):
                row[question + 'Answer'] = self.put(f'placement/{oid}/{question}.json', dict(binding,
                    kind='img2.placement-answer', version=1, question=question, value=value,
                    decider='jev:recorded-fixture', reasonCode='answered'))
            row['evidence'] = {}
            for name in ('metricAnchor', 'depth', 'support'):
                evidence = dict(binding, producer='offline-fixture-not-production-proof')
                if name == 'metricAnchor':
                    if container:
                        evidence.update(mode='room-dimensions', containerArtifactSha256=hashes[container],
                                        roomDimensions=self.artifacts[container]['roomDimensions'])
                    else:
                        evidence.update(mode='size-prior', sizePriorSha256=row['sizePrior']['sha256'])
                row['evidence'][name] = self.put(f'placement/{oid}/{name}.json', evidence)
        self.save_placement()

    def save_placement(self):
        self.request['placement'] = self.put('placement.json', self.placement)

    def compose(self, output='composed/rev-1'):
        return compose_scene(self.root, self.request, output_dir=output)

    def reject(self, reason, error=CompositionError):
        with self.assertRaises(error) as raised:
            self.compose()
        self.assertEqual(raised.exception.reason_code, reason)
        self.assertFalse((self.root / 'composed/rev-1/scene.json').exists())

    def test_real_skinned_glb_is_retained_and_module_loads_parent_only(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        original = deepcopy(self.request)
        manifest = self.compose()
        self.assertEqual(self.request, original)
        for name, data in before.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        module = (self.root / manifest['module']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(module).hexdigest(), manifest['sceneSha256'])
        self.assertEqual(json.loads((self.root / 'composed/rev-1/scene.json').read_bytes()), manifest)
        source = module.decode()
        self.assertIn("import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'", source)
        self.assertIn('loader.parseAsync(bytes.get(item.modelPath)', source)
        self.assertIn('parent.add(gltf.scene)', source)
        self.assertIn('animations.set(item.objectId, gltf.animations)', source)
        self.assertIn("crypto.subtle.digest('SHA-256', bytes)", source)
        self.assertNotIn('.traverse(', source)
        self.assertNotIn('.clone(', source)
        self.assertNotIn('__COMPOSITION_JSON__', source)
        self.assertEqual(manifest['instances'][0]['scale'], [0.5] * 3)
        for ref in self.artifacts['subject']['files']:
            self.assertIn(ref, manifest['inputs'])
        self.assertEqual(set(manifest['validation'].values()), {'unevaluated'})
        self.assertFalse(manifest['deliverable'])
        self.assertNotIn('measurements', manifest)
        self.assertNotIn('passed', source)

    def test_container_owns_embedded_props_and_excludes_routed_subject(self):
        self.add_object('room', container=True)
        self.inventory['objects'].append({'objectId': 'lamp', 'role': 'prop', 'ownerRoute': 'plugin:interior@fixture', 'artifactObjectId': 'room'})
        self.artifacts['room']['ownedObjectIds'].append('lamp')
        self.artifacts['room']['excludeComponents'] = ['subject']
        self.bind()
        manifest = self.compose()
        self.assertEqual({x['id'] for x in manifest['objects']}, {'room', 'lamp', 'subject'})
        self.assertEqual([x['objectId'] for x in manifest['instances']].count('subject'), 1)
        self.assertNotIn('lamp', [x['objectId'] for x in manifest['instances']])

    def test_repeated_instances_route_once_and_load_each_skeleton_independently(self):
        self.inventory['objects'][0]['instanceOf'] = 'pair'
        self.inventory['objects'].append(dict(self.inventory['objects'][0], objectId='second'))
        self.artifacts['subject']['ownedObjectIds'].append('second')
        self.add_placement('second')
        self.placement['objects'][1]['position'] = [2, 0, 0]
        self.bind()
        manifest = self.compose()
        self.assertEqual(len(self.request['artifacts']), 1)
        self.assertEqual(len(manifest['instances']), 2)
        self.assertEqual(len({x['modelPath'] for x in manifest['instances']}), 1)

    def test_explicit_2d_bypasses_all_3d_inputs(self):
        result = compose_scene(self.root, {'kind': 'img2.scene-compose-request', 'version': 1, 'expectedOutput': '2d'}, output_dir='unused')
        self.assertEqual(result['status'], 'bypassed')
        self.assertFalse((self.root / 'unused').exists())

    def test_duplicate_inventory_is_rejected(self):
        self.inventory['objects'].append(deepcopy(self.inventory['objects'][0]))
        self.bind()
        self.reject('duplicate-object')

    def test_duplicate_manifest_is_rejected(self):
        self.request['artifacts'].append(self.request['artifacts'][0])
        self.reject('duplicate-artifact')

    def test_duplicate_placement_is_rejected(self):
        self.placement['objects'].append(deepcopy(self.placement['objects'][0]))
        self.save_placement()
        self.reject('duplicate-object')

    def test_duplicate_routed_glb_is_rejected(self):
        self.add_object('second')
        self.artifacts['second']['files'] = deepcopy(self.artifacts['subject']['files'][:1])
        self.bind()
        self.reject('duplicate-routed-artifact')

    def test_missing_manifest_and_model_bytes_are_rejected(self):
        (self.root / self.request['artifacts'][0]['path']).unlink()
        self.reject('missing-evidence')

    def test_missing_model_is_rejected_by_shared_validator(self):
        (self.root / self.artifacts['subject']['files'][0]['path']).unlink()
        self.reject('missing-evidence', ArtifactError)

    def test_stale_model_is_rejected_by_shared_validator(self):
        (self.root / self.artifacts['subject']['files'][0]['path']).write_bytes(b'changed')
        self.reject('hash-mismatch', ArtifactError)

    def test_stale_manifest_reference_is_rejected(self):
        (self.root / self.request['artifacts'][0]['path']).write_bytes(b'{}')
        self.reject('hash-mismatch')

    def test_stale_inventory_binding_is_rejected(self):
        self.placement['inventorySha256'] = '0' * 64
        self.save_placement()
        self.reject('stale-placement')

    def test_stale_object_binding_is_rejected(self):
        self.placement['objects'][0]['artifactSha256'] = '0' * 64
        self.save_placement()
        self.reject('stale-placement')

    def test_stale_answer_is_rejected(self):
        row = self.placement['objects'][0]
        row['yawAnswer'] = self.change(row['yawAnswer'], artifactSha256='0' * 64)
        self.save_placement()
        self.reject('stale-placement-answer')

    def test_stale_depth_evidence_is_rejected(self):
        row = self.placement['objects'][0]
        row['evidence']['depth'] = self.change(row['evidence']['depth'], referenceSha256='0' * 64)
        self.save_placement()
        self.reject('stale-placement-evidence')

    def test_missing_placement_is_not_invented(self):
        self.placement['objects'] = []
        self.save_placement()
        self.reject('inventory-mismatch')

    def test_missing_camera_is_not_invented(self):
        del self.placement['camera']
        self.save_placement()
        self.reject('invalid-reference')

    def test_missing_ground_is_not_invented(self):
        self.placement['baseContainer'] = None
        self.save_placement()
        self.reject('invalid-reference')

    def test_missing_lighting_is_not_invented(self):
        del self.placement['environment']
        self.save_placement()
        self.reject('invalid-reference')

    def test_ownership_must_match_exactly(self):
        self.artifacts['subject']['ownerRoute'] = 'other-route'
        self.bind()
        self.reject('ownership-mismatch')

    def test_owned_inventory_must_match_exactly(self):
        self.artifacts['subject']['ownedObjectIds'].append('ghost')
        self.bind()
        self.reject('inventory-mismatch')

    def test_container_exclusions_must_match_exactly(self):
        self.add_object('room', container=True)
        self.bind()
        self.reject('inventory-mismatch')

    def test_invalid_frame_is_rejected_by_shared_validator(self):
        self.artifacts['subject']['frame']['units'] = 'cm'
        self.bind()
        self.reject('invalid-frame', ArtifactError)

    def test_nonpositive_scale_is_rejected(self):
        self.placement['objects'][0]['scale'] = [-1, 1, 1]
        self.bind()
        self.reject('invalid-transform')

    def test_boolean_scale_is_rejected(self):
        self.placement['objects'][0]['scale'] = [True, 1, 1]
        self.bind()
        self.reject('invalid-transform')

    def test_scale_prior_is_enforced(self):
        self.placement['objects'][0]['scale'] = [3, 1, 1]
        self.bind()
        self.reject('scale-outside-prior')

    def test_yaw_must_be_recorded_eight_bin_integer(self):
        self.placement['objects'][0]['yawBin'] = 8
        self.bind()
        self.reject('invalid-transform')

    def test_nonfinite_request_is_rejected(self):
        self.request['extra'] = float('nan')
        self.reject('invalid-json')

    def test_nonfinite_placement_json_is_rejected(self):
        self.request['placement'] = self.put('placement.json', b'{"kind":"img2.scene-placement","version":1,"x":Infinity}')
        self.reject('invalid-json')

    def test_unsafe_manifest_paths_are_rejected(self):
        for path in ('../outside.json', '/tmp/outside.json', 'objects//subject/artifact.json', 'objects\\subject.json', '.', 'file:thing'):
            with self.subTest(path=path):
                self.request['artifacts'][0]['path'] = path
                self.reject('unsafe-path')

    def test_manifest_symlink_is_rejected(self):
        manifest = self.root / self.request['artifacts'][0]['path']
        original = manifest.read_bytes()
        manifest.unlink()
        (self.root / 'real.json').write_bytes(original)
        manifest.symlink_to(self.root / 'real.json')
        self.reject('unsafe-path')

    def test_input_file_symlink_is_rejected(self):
        model = self.root / self.artifacts['subject']['files'][0]['path']
        model.rename(self.root / 'real.glb')
        model.symlink_to(self.root / 'real.glb')
        self.reject('unsafe-path', ArtifactError)

    def test_output_symlink_is_rejected_without_outside_write(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'composed').symlink_to(outside)
        self.reject('unsafe-output-path')
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_output_cannot_overwrite_inputs_or_incumbent(self):
        with self.assertRaises(CompositionError) as raised:
            self.compose('objects/subject')
        self.assertEqual(raised.exception.reason_code, 'output-overlaps-input')
        manifest = self.compose()
        before = (self.root / manifest['module']['path']).read_bytes()
        with self.assertRaises(CompositionError) as raised:
            self.compose()
        self.assertEqual(raised.exception.reason_code, 'output-exists')
        self.assertEqual((self.root / manifest['module']['path']).read_bytes(), before)

    def test_source_only_factory_fails_closed(self):
        artifact = self.artifacts['subject']
        artifact['outputKind'] = '3d-object'
        artifact['rig'] = None
        artifact['files'] = [self.put('objects/subject/factory.mjs', b'export function createObjectModel() { return null; }')]
        self.bind()
        self.reject('source-consumer-unavailable')

    def test_external_glb_resources_fail_closed(self):
        doc, binary, _, _ = glb_fixture()
        doc['images'] = [{'uri': 'https://example.invalid/texture.png'}]
        self.artifacts['subject']['files'][0] = self.put('objects/subject/model.glb', pack_glb(doc, binary))
        self.bind()
        self.reject('external-glb-resource')

    def test_child_lights_are_not_silently_removed_or_duplicated(self):
        doc, binary, _, _ = glb_fixture()
        doc['extensions'] = {'KHR_lights_punctual': {'lights': [{'type': 'point'}]}}
        self.artifacts['subject']['files'][0] = self.put('objects/subject/model.glb', pack_glb(doc, binary))
        self.bind()
        self.reject('child-light-rig')

    def test_unsupported_decoder_fails_closed(self):
        doc, binary, _, _ = glb_fixture()
        doc['extensionsRequired'] = ['KHR_draco_mesh_compression']
        self.artifacts['subject']['files'][0] = self.put('objects/subject/model.glb', pack_glb(doc, binary))
        self.bind()
        self.reject('decoder-unavailable')

    def test_uncertain_jev_does_not_authorize_model_override(self):
        row = self.placement['objects'][0]
        row['yawAnswer'] = self.change(row['yawAnswer'], decider='model:fixture', reasonCode='uncertain',
                                      unavailableReceipt=self.put('receipt.json', {'outcome': 'uncertain'}))
        self.save_placement()
        self.reject('invalid-fallback')

    def test_credential_missing_cannot_be_treated_as_runwide_unavailable(self):
        self.placement['jevSession'] = 'unavailable:credential-missing'
        self.save_placement()
        self.reject('invalid-session')

    def test_model_timeout_answer_retains_receipt_without_new_provider_call(self):
        row = self.placement['objects'][0]
        row['yawAnswer'] = self.change(row['yawAnswer'], decider='model:fixture', reasonCode='timeout',
                                      unavailableReceipt=self.put('receipt.json', {'outcome': 'timeout'}))
        self.save_placement()
        manifest = self.compose()
        self.assertIn('receipt.json', [ref['path'] for ref in manifest['inputs']])
        self.assertFalse(manifest['deliverable'])

    def test_runtime_urls_encode_special_characters_without_code_interpolation(self):
        ref = self.put('reference/a #?.bin', b'reference')
        self.request['reference'] = ref
        self.bind()
        manifest = self.compose()
        source = (self.root / manifest['module']['path']).read_text()
        self.assertIn('a%20%23%3F.bin', source)
        self.assertNotIn('subprocess', source)

    def test_delivery_and_baseline_glbs_require_explicit_selection(self):
        document, binary, _, _ = glb_fixture('blend-off-baseline')
        baseline = self.put('objects/subject/baseline.glb', pack_glb(document, binary))
        self.artifacts['subject']['files'].insert(0, baseline)
        self.bind()
        self.reject('ambiguous-entrypoint')

    def test_explicit_delivery_entrypoint_does_not_choose_first_glb(self):
        delivery = self.artifacts['subject']['files'][0]
        document, binary, _, _ = glb_fixture('blend-off-baseline')
        baseline = self.put('objects/subject/baseline.glb', pack_glb(document, binary))
        self.artifacts['subject']['files'].insert(0, baseline)
        self.artifacts['subject']['entrypoint'] = delivery['path']
        self.bind()
        manifest = self.compose()
        self.assertEqual(manifest['instances'][0]['modelPath'], delivery['path'])
        self.assertIn(delivery, manifest['inputs'])
        self.assertIn(baseline, manifest['inputs'])

    def test_explicit_selection_cannot_reference_unlisted_model(self):
        self.artifacts['subject']['entrypoint'] = 'objects/subject/not-listed.glb'
        self.bind()
        self.reject('ambiguous-entrypoint')


if __name__ == '__main__':
    unittest.main()
