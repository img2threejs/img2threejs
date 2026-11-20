"""Offline capture boundaries; mocked transport is never browser/quality evidence.

Opt-in genuine Chrome smoke: IMG2_GLBCAPTURE_HOST_PINS=/absolute/host-pins.json,
containing {chrome:HOST_REF,modules:{fixed URL:HOST_REF}}. Pins must be approved
by the host, not copied from plugin input. Without it the genuine test skips.
"""
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)
from stage4_review import capture_delivered_glb as capture


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def triangle_glb(extra=None):
    document = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}], 'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'material': 0}]}],
        'materials': [{'pbrMetallicRoughness': {'baseColorFactor': [0.8, 0.2, 0.1, 1], 'metallicFactor': 0, 'roughnessFactor': 1}, 'doubleSided': True}],
        'buffers': [{'byteLength': 36}], 'bufferViews': [{'buffer': 0, 'byteLength': 36}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
                       'min': [-0.5, 0, 0], 'max': [0.5, 1, 0]}]}
    document.update(extra or {})
    payload = canonical(document)
    payload += b' ' * (-len(payload) % 4)
    binary = struct.pack('<9f', -0.5, 0, 0, 0.5, 0, 0, 0, 1, 0)
    chunks = struct.pack('<II', len(payload), 0x4e4f534a) + payload + struct.pack('<II', len(binary), 0x004e4942) + binary
    return struct.pack('<4sII', b'glTF', 2, len(chunks) + 12) + chunks


def config_fixture():
    return {'viewId': 'reference', 'frame': {'units': 'm', 'up': '+Y', 'forward': '+Z'},
            'viewport': {'width': 128, 'height': 128, 'devicePixelRatio': 1}, 'background': 0xffffff,
            'camera': {'projection': 'perspective', 'position': [0, 0.5, 3], 'target': [0, 0.5, 0],
                       'up': [0, 1, 0], 'fov': 40, 'near': 0.1, 'far': 20},
            'transform': {'position': [0, 0, 0], 'rotation': [0, 0, 0], 'scale': [1, 1, 1]},
            'lighting': {'ambient': {'color': 0xffffff, 'intensity': 1}, 'directional': []}}


def png_fixture(width=128, height=128):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    # An encoding fixture, not a rendered triangle or visual evidence.
    pixels = (b'\0' + b'\xff\xff\xff' * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


class DeliveredGLBCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.delivery = self.put('assets/delivery.glb', triangle_glb())
        self.artifact = {'objectId': 'triangle', 'outputKind': '3d-object', 'files': [self.delivery],
                         'entrypoint': self.delivery['path'], 'frame': {'units': 'm', 'up': '+Y', 'forward': '+Z'},
                         'pivot': 'bottom-centre', 'scaleEstimate': 1, 'rig': None}
        self.artifact_ref = self.put('artifact.json', self.artifact)
        self.config = config_fixture()
        # Invalid native bytes allow preflight hashing tests without a real binary.
        self.chrome = self.host('host/Google Chrome', b'\xcf\xfa\xed\xfe' + b'not an executable', executable=True)
        self.modules = {url: self.host('host/' + url.removeprefix('/deps/'), b'export const testOnly = true;')
                        for url in capture.REQUIRED_MODULES}

    def put(self, path, value):
        data = value if type(value) is bytes else canonical(value)
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {'path': path, 'sha256': hashlib.sha256(data).hexdigest()}

    def host(self, path, data, executable=False):
        ref = self.put(path, data)
        target = self.root / ref['path']
        if executable:
            target.chmod(0o755)
        return {'path': str(target), 'sha256': ref['sha256']}

    def bind(self):
        self.artifact_ref = self.put('artifact.json', self.artifact)

    def run_capture(self, **changes):
        args = dict(artifact=self.artifact_ref, delivery=self.delivery, config=self.config,
                    chrome=self.chrome, modules=self.modules, output_dir='captures/rev-1')
        args.update(changes)
        return capture.capture_delivered_glb(self.root, **args)

    def mock_browser(self, *args):
        config = args[2]
        viewport = config['viewport']
        camera = dict(config['camera'], aspect=viewport['width'] / viewport['height'])
        report = {'deliverySha256': self.delivery['sha256'], 'threeRevision': 'test-transport-only',
                  'viewport': viewport, 'canvas': {'width': viewport['width'], 'height': viewport['height']},
                  'frame': config['frame'], 'camera': camera, 'renderer': {'name': 'WebGLRenderer'}}
        return png_fixture(viewport['width'], viewport['height']), report, {'product': 'test-transport-only'}, [], ['/', '/runtime.mjs', '/config.json', '/delivery.glb']

    def reject(self, reason, **changes):
        with patch.object(capture, '_browser') as browser:
            with self.assertRaises(capture.CaptureError) as raised:
                self.run_capture(**changes)
            self.assertEqual(raised.exception.reason_code, reason)
            browser.assert_not_called()
        self.assertFalse((self.root / 'captures/rev-1/capture.json').exists())

    def test_mocked_transport_only_persists_real_hashes_without_quality_claim(self):
        before = (self.root / self.delivery['path']).read_bytes()
        with patch.object(capture, '_browser', side_effect=self.mock_browser):
            result = self.run_capture()
        self.assertFalse(result['deliverable'])
        self.assertEqual(result['quality'], 'unevaluated')
        self.assertEqual(result['sourceToRenderFidelity'], 'unevaluated')
        self.assertEqual(result['frame'], self.config['frame'])
        self.assertEqual(result['camera']['aspect'], 1)
        for key in ('config', 'renderer', 'screenshot'):
            ref = result[key]
            self.assertEqual(hashlib.sha256((self.root / ref['path']).read_bytes()).hexdigest(), ref['sha256'])
        self.assertEqual((self.root / self.delivery['path']).read_bytes(), before)
        self.assertEqual(json.loads((self.root / 'captures/rev-1/capture.json').read_bytes()), result)

    def test_unpinned_or_hash_changed_browser_is_rejected_before_execution(self):
        wrong = dict(self.chrome, sha256='0' * 64)
        self.reject('hash-mismatch', chrome=wrong)

    def test_script_executable_is_never_run_even_with_correct_hash(self):
        marker = self.root / 'executed'
        script = ('#!/bin/sh\ntouch ' + str(marker) + '\n').encode()
        fake = self.host('scripts/Google Chrome', script, executable=True)
        self.reject('unsupported-executable', chrome=fake)
        self.assertFalse(marker.exists())

    def test_unapproved_executable_name_fails_closed(self):
        fake = self.host('host/arbitrary-command', b'\xcf\xfa\xed\xfeXXXX', executable=True)
        self.reject('unsupported-executable', chrome=fake)

    def test_fake_native_executable_cannot_create_capture_evidence(self):
        # The invalid executable really fails exec; no renderer or screenshot mock.
        with self.assertRaises(capture.CaptureError) as raised:
            self.run_capture(timeout_seconds=2)
        self.assertIn(raised.exception.reason_code, ('browser-transport-failed', 'browser-start-failed'))
        self.assertFalse((self.root / 'captures/rev-1/capture.json').exists())

    def test_relative_host_executable_is_not_resolved_from_path(self):
        self.reject('unsafe-host-path', chrome=dict(self.chrome, path='Google Chrome'))

    def test_missing_or_unknown_module_paths_fail_closed(self):
        self.reject('unsupported-module-map', modules={})
        self.reject('unsupported-module-map', modules=dict(self.modules, **{'/plugin.js': self.chrome}))

    def test_pinned_modules_are_rechecked(self):
        modules = deepcopy(self.modules)
        modules[next(iter(modules))]['sha256'] = '0' * 64
        self.reject('hash-mismatch', modules=modules)

    def test_external_glb_resource_never_starts_browser(self):
        self.delivery = self.put('assets/delivery.glb', triangle_glb({'images': [{'uri': 'https://example.invalid/image.png'}]}))
        self.artifact['files'] = [self.delivery]
        self.bind()
        self.reject('external-or-unsupported-glb-resource')

    def test_external_font_or_environment_config_is_not_accepted(self):
        self.config['environment'] = 'https://example.invalid/environment.hdr'
        self.reject('invalid-config')

    def test_any_glb_extension_fails_closed_without_silent_material_drift(self):
        self.delivery = self.put('assets/delivery.glb', triangle_glb({'extensionsUsed': ['KHR_materials_unlit']}))
        self.artifact['files'] = [self.delivery]
        self.bind()
        self.reject('unsupported-glb-extension')

    def test_hash_mismatch_in_delivered_bytes_fails_before_browser(self):
        (self.root / self.delivery['path']).write_bytes(b'changed')
        self.reject('hash-mismatch')

    def test_explicit_selection_cannot_disagree_with_artifact_entrypoint(self):
        baseline = self.put('assets/baseline.glb', triangle_glb({'extras': {'baseline': True}}))
        self.artifact['files'].append(baseline)
        self.bind()
        self.reject('delivery-selection-mismatch', delivery=baseline)

    def test_explicit_delivery_can_be_second_without_filename_inference(self):
        baseline = self.put('assets/baseline.glb', triangle_glb({'extras': {'baseline': True}}))
        self.artifact['files'].insert(0, baseline)
        self.bind()
        with patch.object(capture, '_browser', side_effect=self.mock_browser):
            result = self.run_capture()
        self.assertEqual(result['delivery'], self.delivery)
        served_delivery = next(row for row in result['served'] if row['urlPath'] == '/delivery.glb')
        self.assertEqual(served_delivery['sha256'], self.delivery['sha256'])

    def test_unlisted_delivery_is_rejected(self):
        unlisted = self.put('assets/unlisted.glb', triangle_glb({'extras': {'unlisted': True}}))
        self.reject('delivery-not-in-artifact', delivery=unlisted)

    def test_unsafe_workspace_paths_are_rejected(self):
        self.reject('unsafe-path', artifact=dict(self.artifact_ref, path='../artifact.json'))
        self.reject('unsafe-path', output_dir='../capture')

    def test_symlink_host_pin_cannot_read_or_execute_other_files(self):
        target = self.root / 'host/link'
        target.symlink_to(self.chrome['path'])
        self.reject('unsafe-or-missing-path', chrome=dict(self.chrome, path=str(target)))

    def test_transform_and_camera_data_are_bounded_not_source_interpolated(self):
        self.config['transform']['position'] = ['alert(1)', 0, 0]
        self.reject('invalid-config')

    def test_unknown_code_or_flag_fields_are_rejected(self):
        self.config['script'] = 'throw new Error()'
        self.reject('invalid-config')

    def test_camera_is_explicit_and_nondegenerate(self):
        self.config['camera']['target'] = self.config['camera']['position']
        self.reject('degenerate-camera')

    def test_invalid_viewport_and_nonfinite_values_fail_closed(self):
        self.config['viewport']['width'] = 16384
        self.reject('invalid-config')
        self.config = config_fixture()
        self.config['camera']['fov'] = float('inf')
        self.reject('invalid-json')

    def test_boolean_scale_is_not_numeric(self):
        self.config['transform']['scale'][0] = True
        self.reject('invalid-config')

    def test_postcapture_glb_change_invalidates_entire_candidate(self):
        def mutate(*args):
            output = self.mock_browser(*args)
            (self.root / self.delivery['path']).write_bytes(b'changed during capture')
            return output
        with patch.object(capture, '_browser', side_effect=mutate):
            with self.assertRaises(capture.CaptureError) as raised:
                self.run_capture()
        self.assertEqual(raised.exception.reason_code, 'hash-mismatch')
        self.assertFalse((self.root / 'captures/rev-1/capture.json').exists())

    def test_postcapture_module_change_invalidates_entire_candidate(self):
        def mutate(*args):
            output = self.mock_browser(*args)
            Path(next(iter(self.modules.values()))['path']).write_bytes(b'changed module')
            return output
        with patch.object(capture, '_browser', side_effect=mutate):
            with self.assertRaises(capture.CaptureError) as raised:
                self.run_capture()
        self.assertEqual(raised.exception.reason_code, 'hash-mismatch')
        self.assertFalse((self.root / 'captures/rev-1/capture.json').exists())

    def test_wrong_runtime_viewport_is_not_relabelled(self):
        def wrong(*args):
            png, report, *rest = self.mock_browser(*args)
            report['viewport'] = dict(report['viewport'], width=64)
            return png, report, *rest
        with patch.object(capture, '_browser', side_effect=wrong):
            with self.assertRaises(capture.CaptureError) as raised:
                self.run_capture()
        self.assertEqual(raised.exception.reason_code, 'runtime-binding-mismatch')

    def test_wrong_screenshot_dimensions_are_not_relabelled(self):
        def wrong(*args):
            _, *rest = self.mock_browser(*args)
            return png_fixture(64, 64), *rest
        with patch.object(capture, '_browser', side_effect=wrong):
            with self.assertRaises(capture.CaptureError) as raised:
                self.run_capture()
        self.assertEqual(raised.exception.reason_code, 'screenshot-size-mismatch')

    def test_png_checks_actual_compressed_pixels_and_crc(self):
        self.assertEqual(capture._png_dimensions(png_fixture(), [128, 128]), (128, 128))
        for data in (b'', png_fixture()[:-12], png_fixture()[:40] + b'bad'):
            with self.subTest(size=len(data)), self.assertRaises(capture.CaptureError):
                capture._png_dimensions(data, [128, 128])

    def test_server_has_no_filesystem_root_or_path_traversal(self):
        with capture._serve({'/': (b'fixed program page', 'text/html')}) as (port, _):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                for route in ('/../artifact.json', '/artifact.json', '/?path=/etc/passwd'):
                    connection.request('GET', route)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 404)
                    response.read()
                connection.request('GET', '/', headers={'Host': 'attacker.invalid'})
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                connection.request('GET', '/')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'fixed program page')
            finally:
                connection.close()

    def test_existing_output_is_rejected_before_browser_work(self):
        (self.root / 'captures/rev-1').mkdir(parents=True)
        self.reject('output-exists')

    def test_output_symlink_parent_is_rejected_before_browser_work(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'captures').symlink_to(outside)
        self.reject('unsafe-output-path')
        self.assertEqual(list(outside.iterdir()), [])

    def test_output_cannot_contain_an_input(self):
        self.reject('output-overlaps-input', output_dir='assets')

    @unittest.skipUnless(os.environ.get('IMG2_GLBCAPTURE_HOST_PINS'), 'requires explicitly approved local Chrome/Three.js pins')
    def test_genuine_local_pinned_chrome_smoke(self):
        pins = json.loads(Path(os.environ['IMG2_GLBCAPTURE_HOST_PINS']).read_text())
        result = self.run_capture(chrome=pins['chrome'], modules=pins['modules'], timeout_seconds=45)
        png = (self.root / result['screenshot']['path']).read_bytes()
        self.assertEqual(capture._png_dimensions(png, [128, 128]), (128, 128))
        self.assertEqual(result['delivery']['sha256'], self.delivery['sha256'])
        self.assertEqual(result['quality'], 'unevaluated')
        self.assertFalse(result['deliverable'])


if __name__ == '__main__':
    unittest.main()
