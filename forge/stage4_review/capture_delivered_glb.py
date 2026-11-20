"""Capture exact delivered GLB pixels in a fixed, disposable Three.js/Chrome scene.

This is evidence production, NOT quality approval, a security sandbox, camera
calibration, proof of source-to-render fidelity, or browser-independent pixels.
The host must authenticate Chrome/module pins; a digest supplied by an untrusted
plugin does not authorize execution. No plugin scripts/HTML or user sessions load.

API: capture_delivered_glb(workspace, *, artifact=REF, delivery=REF, config=CONFIG,
 chrome=HOST_REF, modules={URL:HOST_REF}, output_dir=REL, timeout_seconds=30).
REF = {path:canonical workspace-relative POSIX path,sha256:lowercase hex}.
HOST_REF = {path:canonical absolute local file path,sha256:lowercase hex}.
artifact is a D18 artifact.json ref; delivery explicitly names one listed GLB,
never a filename/list-order heuristic. Host pins and artifact bytes are rechecked
before/after capture. Modules are the exact allowlisted Three.js dependency URLs
below (core/SkeletonUtils are optional if the pinned version doesn't import them).
Pinned JavaScript is trusted host code, NOT executable input from a provider.

CONFIG has exactly: viewId (ASCII id), frame:{units:'m',up:'+Y',forward:'+Z'},
 viewport:{width:32..2048,height:32..2048,devicePixelRatio:1}, background:RGB_INT,
 camera:{projection:'perspective',position:VEC3,target:VEC3,up:VEC3,fov:1..175,
 near:positive,far:positive}, transform:{position:VEC3,rotation:VEC3,scale:VEC3},
 lighting:{ambient:{color:RGB_INT,intensity:0..100},directional:[up to four
 {color,intensity,position:VEC3,target:VEC3}]}. All values are finite/bounded;
 scale is positive and multiplies D18 scaleEstimate on a parent only. Nothing is
 inferred. Animation is the authored rest pose, without advancing any clips.
No external URLs, external fonts, environments, extensions/decoders, supplied
HTML/scripts, arbitrary executable arguments, or arbitrary filesystem service.
Only self-contained GLB 2 with optional embedded PNG/JPEG textures is supported.
A fresh profile runs with Chromium sandbox defaults (never --no-sandbox), a
closed external proxy, no DNS, CSP, and exact target request interception.
These controls are defense in depth, not OS-level network/process isolation.

The deadline includes browser setup/capture and every child process is killed
and reaped on exit. scene/config/screenshot/renderer/module hashes are retained
in a new output directory; partial capture never creates an acceptance record.
An actual screenshot can be empty of visible geometry: no automatic framing,
visibility or reconstruction-quality claim is made from a valid PNG alone.
"""
from __future__ import annotations

import base64
from collections import deque
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)
from _shared.route_artifact import validate_artifact

_SHA = re.compile(r'[0-9a-f]{64}\Z')
MODULE_URLS = frozenset({'/deps/three.module.js', '/deps/three.core.js',
    '/deps/loaders/GLTFLoader.js', '/deps/utils/BufferGeometryUtils.js', '/deps/utils/SkeletonUtils.js'})
REQUIRED_MODULES = frozenset({'/deps/three.module.js', '/deps/loaders/GLTFLoader.js', '/deps/utils/BufferGeometryUtils.js'})
_FRAME = {'units': 'm', 'up': '+Y', 'forward': '+Z'}
_MAX_FILE = 128 * 1024 * 1024
_NATIVE_MAGIC = (b'\x7fELF', b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')
_CHROME_NAMES = frozenset({'Google Chrome', 'Google Chrome for Testing', 'Chromium', 'chrome', 'chromium', 'chrome-headless-shell', 'headless_shell'})


class CaptureError(ValueError):
    def __init__(self, reason_code, field):
        self.reason_code = reason_code
        self.field = field
        super().__init__(f'{reason_code}: {field}')


def _fail(reason, field):
    raise CaptureError(reason, field)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CaptureError('invalid-json', 'input') from exc


def _json(data, field):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail('invalid-json', field)
            result[key] = value
        return result
    def invalid(_):
        _fail('invalid-json', field)
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)
        _canonical(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CaptureError):
            raise
        raise CaptureError('invalid-json', field) from exc


def _relative(value):
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _fail('unsafe-path', 'path')
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(p in ('.', '..') for p in path.parts):
        _fail('unsafe-path', value)
    return path


def _read_at(root_fd, relative, limit=_MAX_FILE):
    parts = _relative(relative).parts
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index != len(parts) - 1:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            _fail('invalid-file', relative)
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        if len(data) != before.st_size or (before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
            _fail('input-changed', relative)
        return data, before.st_mode
    except OSError as exc:
        raise CaptureError('unsafe-or-missing-path', relative) from exc
    finally:
        os.close(fd)


def _reference(ref):
    if type(ref) is not dict or set(ref) != {'path', 'sha256'} or type(ref.get('sha256')) is not str or not _SHA.fullmatch(ref['sha256']):
        _fail('invalid-reference', 'reference')
    return ref


def _read_ref(root_fd, ref, limit=_MAX_FILE):
    _reference(ref)
    data, mode = _read_at(root_fd, ref['path'], limit)
    if hashlib.sha256(data).hexdigest() != ref['sha256']:
        _fail('hash-mismatch', ref['path'])
    return data, mode


def _host_ref(ref, limit=_MAX_FILE):
    _reference(ref)
    value = ref['path']
    if type(value) is not str or not value.startswith('/') or str(Path(value)) != value:
        _fail('unsafe-host-path', 'host.path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        return _read_ref(fd, {'path': value[1:], 'sha256': ref['sha256']}, limit)
    finally:
        os.close(fd)


def _keys(value, keys, field):
    if type(value) is not dict or set(value) != set(keys):
        _fail('invalid-config', field)


def _number(value, low, high, field):
    try:
        good = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        good = False
    if not good:
        _fail('invalid-config', field)
    return value


def _vec(value, field, low=-1e6, high=1e6):
    if type(value) is not list or len(value) != 3:
        _fail('invalid-config', field)
    return [_number(x, low, high, field) for x in value]


def _color(value, field):
    if type(value) is not int or not 0 <= value <= 0xffffff:
        _fail('invalid-config', field)


def validate_config(config):
    config = _json(_canonical(config), 'config')
    _keys(config, ('viewId', 'frame', 'viewport', 'background', 'camera', 'transform', 'lighting'), 'config')
    if type(config['viewId']) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', config['viewId']):
        _fail('invalid-config', 'viewId')
    if config['frame'] != _FRAME:
        _fail('invalid-frame', 'frame')
    viewport = config['viewport']
    _keys(viewport, ('width', 'height', 'devicePixelRatio'), 'viewport')
    for key in ('width', 'height'):
        if type(viewport[key]) is not int or not 32 <= viewport[key] <= 2048:
            _fail('invalid-config', 'viewport.' + key)
    if type(viewport['devicePixelRatio']) not in (int, float) or viewport['devicePixelRatio'] != 1:
        _fail('unsupported-device-pixel-ratio', 'viewport')
    _color(config['background'], 'background')
    camera = config['camera']
    _keys(camera, ('projection', 'position', 'target', 'up', 'fov', 'near', 'far'), 'camera')
    if camera['projection'] != 'perspective':
        _fail('unsupported-camera', 'camera')
    position, target, up = (_vec(camera[k], 'camera.' + k) for k in ('position', 'target', 'up'))
    direction = [a - b for a, b in zip(position, target)]
    cross = [direction[1] * up[2] - direction[2] * up[1], direction[2] * up[0] - direction[0] * up[2], direction[0] * up[1] - direction[1] * up[0]]
    if sum(x * x for x in direction) < 1e-12 or sum(x * x for x in cross) < 1e-12 or abs(sum(x * x for x in up) - 1) > 1e-8:
        _fail('degenerate-camera', 'camera')
    _number(camera['fov'], 1, 175, 'camera.fov')
    _number(camera['near'], 1e-6, 1e6, 'camera.near')
    _number(camera['far'], 1e-6, 1e7, 'camera.far')
    if camera['far'] <= camera['near']:
        _fail('invalid-config', 'camera.far')
    transform = config['transform']
    _keys(transform, ('position', 'rotation', 'scale'), 'transform')
    _vec(transform['position'], 'transform.position')
    _vec(transform['rotation'], 'transform.rotation', -2 * math.pi, 2 * math.pi)
    _vec(transform['scale'], 'transform.scale', 1e-6, 1e6)
    lighting = config['lighting']
    _keys(lighting, ('ambient', 'directional'), 'lighting')
    _keys(lighting['ambient'], ('color', 'intensity'), 'lighting.ambient')
    _color(lighting['ambient']['color'], 'ambient.color')
    _number(lighting['ambient']['intensity'], 0, 100, 'ambient.intensity')
    if type(lighting['directional']) is not list or len(lighting['directional']) > 4:
        _fail('invalid-config', 'lighting.directional')
    for light in lighting['directional']:
        _keys(light, ('color', 'intensity', 'position', 'target'), 'directional')
        _color(light['color'], 'directional.color')
        _number(light['intensity'], 0, 100, 'directional.intensity')
        _vec(light['position'], 'directional.position')
        _vec(light['target'], 'directional.target')
        if light['position'] == light['target']:
            _fail('invalid-config', 'directional.target')
    return config


def _check_glb(data):
    # validate_artifact has already checked container bytes and lengths.
    length = struct.unpack_from('<I', data, 12)[0]
    document = _json(data[20:20 + length], 'GLB JSON')
    for key in ('extensionsRequired', 'extensionsUsed'):
        if key in document and document[key] != []:
            _fail('unsupported-glb-extension', key)
    def visit(value):
        if type(value) is dict:
            for key, item in value.items():
                if key == 'extensions' and item:
                    _fail('unsupported-glb-extension', 'extensions')
                if key == 'uri':
                    if type(item) is not str or not re.fullmatch(r'data:image/(png|jpeg);base64,[A-Za-z0-9+/]*={0,2}', item):
                        _fail('external-or-unsupported-glb-resource', 'uri')
                    try:
                        image = base64.b64decode(item.split(',', 1)[1], validate=True)
                    except ValueError:
                        _fail('invalid-embedded-texture', 'uri')
                    if not (image.startswith(b'\x89PNG\r\n\x1a\n') or image.startswith(b'\xff\xd8\xff')):
                        _fail('invalid-embedded-texture', 'uri')
                visit(item)
        elif type(value) is list:
            for item in value:
                visit(item)
    visit(document)
    for image in document.get('images', []):
        if image.get('mimeType') not in (None, 'image/png', 'image/jpeg'):
            _fail('unsupported-texture', 'mimeType')
    index = document.get('scene', 0)
    scenes = document.get('scenes')
    if type(scenes) is not list or type(index) is not int or not 0 <= index < len(scenes) or type(scenes[index]) is not dict or not scenes[index].get('nodes'):
        _fail('missing-gltf-scene', 'scene')


def _png_dimensions(data, expected):
    if len(data) < 64 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        _fail('invalid-screenshot', 'PNG')
    cursor, image_data, header, ended = 8, bytearray(), None, False
    while cursor < len(data):
        if cursor + 12 > len(data):
            _fail('invalid-screenshot', 'PNG chunk')
        size = struct.unpack_from('>I', data, cursor)[0]
        end = cursor + 12 + size
        if end > len(data):
            _fail('invalid-screenshot', 'PNG chunk size')
        tag, payload = data[cursor + 4:cursor + 8], data[cursor + 8:end - 4]
        crc = struct.unpack_from('>I', data, end - 4)[0]
        if zlib.crc32(tag + payload) & 0xffffffff != crc:
            _fail('invalid-screenshot', 'PNG CRC')
        if tag == b'IHDR':
            if header is not None or cursor != 8 or size != 13:
                _fail('invalid-screenshot', 'PNG header')
            header = struct.unpack('>IIBBBBB', payload)
        elif tag == b'IDAT':
            image_data.extend(payload)
        elif tag == b'IEND':
            ended = size == 0 and end == len(data)
        cursor = end
    if not ended or not header or not image_data:
        _fail('invalid-screenshot', 'PNG structure')
    width, height, bits, color, compression, filtering, interlace = header
    if [width, height] != expected or bits != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
        _fail('screenshot-size-mismatch', 'PNG dimensions/format')
    expected_bytes = height * (1 + width * (3 if color == 2 else 4))
    try:
        inflater = zlib.decompressobj()
        pixels = inflater.decompress(image_data, expected_bytes + 1)
    except zlib.error as exc:
        raise CaptureError('invalid-screenshot', 'PNG compressed pixels') from exc
    if len(pixels) != expected_bytes or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        _fail('invalid-screenshot', 'PNG pixels')
    return width, height


@contextmanager
def _serve(routes):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            if self.path not in routes or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                self.send_error(404)
                return
            data, content_type = routes[self.path]
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' 'nonce-fixed-capture-map'; connect-src 'self'; img-src 'self' blob: data:; style-src 'unsafe-inline'; font-src 'none'; object-src 'none'; base-uri 'none'; frame-src 'none'; worker-src 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail('capture-timeout', 'deadline')
    return remaining


class _CDP:
    """Small RFC6455/CDP client for one owned browser, not arbitrary endpoints."""
    def __init__(self, port, path, deadline, allowed_urls):
        if not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', path):
            _fail('invalid-browser-endpoint', 'DevToolsActivePort')
        self.deadline, self.serial = deadline, 0
        self.responses, self.events, self.blocked = {}, deque(), []
        self.allowed_urls = allowed_urls
        self.socket = socket.create_connection(('127.0.0.1', port), timeout=_remaining(deadline))
        key = base64.b64encode(secrets.token_bytes(16)).decode('ascii')
        request = f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
        self.socket.sendall(request.encode('ascii'))
        header = b''
        while not header.endswith(b'\r\n\r\n') and len(header) < 16384:
            header += self._bytes(1)
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest())
        if not header.startswith(b'HTTP/1.1 101 ') or b'sec-websocket-accept: ' + expected.lower() not in header.lower():
            self.socket.close()
            _fail('browser-websocket-failed', 'handshake')
    def close(self):
        self.socket.close()
    def _bytes(self, size):
        parts = bytearray()
        while len(parts) < size:
            self.socket.settimeout(_remaining(self.deadline))
            chunk = self.socket.recv(size - len(parts))
            if not chunk:
                _fail('browser-disconnected', 'websocket')
            parts.extend(chunk)
        return bytes(parts)
    def _frame(self, opcode, data):
        mask = secrets.token_bytes(4)
        size = len(data)
        header = bytes([0x80 | opcode])
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 65536:
            header += bytes([0x80 | 126]) + struct.pack('>H', size)
        else:
            header += bytes([0x80 | 127]) + struct.pack('>Q', size)
        self.socket.settimeout(_remaining(self.deadline))
        self.socket.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
    def send(self, method, params=None, session=None):
        self.serial += 1
        message = {'id': self.serial, 'method': method, 'params': params or {}}
        if session is not None:
            message['sessionId'] = session
        self._frame(1, _canonical(message))
        return self.serial
    def _receive(self):
        payload = bytearray()
        started = False
        while True:
            first, second = self._bytes(2)
            opcode, final = first & 15, bool(first & 128)
            if second & 128 or first & 112:
                _fail('invalid-browser-protocol', 'websocket frame')
            size = second & 127
            if size == 126:
                size = struct.unpack('>H', self._bytes(2))[0]
            elif size == 127:
                size = struct.unpack('>Q', self._bytes(8))[0]
            if size > 40 * 1024 * 1024 or len(payload) + size > 40 * 1024 * 1024:
                _fail('browser-message-too-large', 'websocket')
            data = self._bytes(size)
            if opcode == 8:
                _fail('browser-disconnected', 'websocket close')
            if opcode == 9:
                self._frame(10, data)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1) or (opcode == 0 and not started) or (opcode == 1 and started):
                _fail('invalid-browser-protocol', 'websocket opcode')
            started = True
            payload.extend(data)
            if final:
                break
        message = _json(bytes(payload), 'CDP')
        if 'id' in message:
            self.responses[message['id']] = message
        elif message.get('method') == 'Fetch.requestPaused':
            params = message['params']
            url = params['request']['url']
            if url not in self.allowed_urls or params['request']['method'] != 'GET':
                self.blocked.append(url)
                self.send('Fetch.failRequest', {'requestId': params['requestId'], 'errorReason': 'BlockedByClient'}, message.get('sessionId'))
            else:
                self.send('Fetch.continueRequest', {'requestId': params['requestId']}, message.get('sessionId'))
        else:
            self.events.append(message)
    def call(self, method, params=None, session=None):
        mid = self.send(method, params, session)
        while mid not in self.responses:
            self._receive()
        response = self.responses.pop(mid)
        if 'error' in response:
            _fail('browser-command-failed', method)
        return response.get('result', {})
    def event(self, method, session, predicate):
        while True:
            for item in list(self.events):
                if (item.get('method') == method and item.get('sessionId') == session
                        and predicate(item.get('params', {}))):
                    self.events.remove(item)
                    return item.get('params', {})
            self._receive()


def _browser(chrome_path, routes, config, deadline):
    with tempfile.TemporaryDirectory(prefix='img2-delivered-capture-') as temporary, _serve(routes) as (port, requests):
        root = Path(temporary)
        home, profile = root / 'home', root / 'profile'
        home.mkdir()
        profile.mkdir()
        # No inherited credentials, preload hooks, browser extensions or profile.
        env = {'HOME': str(home), 'TMPDIR': str(root), 'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}
        flags = ['--headless=new', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
                 '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                 '--disable-extensions', '--disable-background-networking', '--disable-component-update',
                 '--disable-sync', '--disable-default-apps', '--disable-domain-reliability',
                 '--disable-features=MediaRouter,OptimizationHints,Translate,AutofillServerCommunication',
                 '--disable-breakpad', '--disable-quic', '--metrics-recording-only',
                 '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                 '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=127.0.0.1',
                 '--force-device-scale-factor=1', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                 '--hide-scrollbars', '--window-size=' + str(config['viewport']['width']) + ',' + str(config['viewport']['height']), 'about:blank']
        cdp, process = None, None
        try:
            with (root / 'chrome.log').open('wb') as log:
                process = subprocess.Popen([chrome_path, *flags], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           cwd=root, env=env, start_new_session=True)
                active = profile / 'DevToolsActivePort'
                while not active.is_file():
                    if process.poll() is not None:
                        _fail('browser-start-failed', 'Chrome')
                    time.sleep(min(0.02, _remaining(deadline)))
                lines = active.read_text(encoding='utf-8').splitlines()
                if len(lines) != 2 or not lines[0].isdigit() or not 1 <= int(lines[0]) <= 65535:
                    _fail('invalid-browser-endpoint', 'DevToolsActivePort')
                origin = f'http://127.0.0.1:{port}'
                cdp = _CDP(int(lines[0]), lines[1], deadline, {origin + path for path in routes})
                version = cdp.call('Browser.getVersion')
                if not re.fullmatch(r'(HeadlessChrome|Chrome|Chromium)/[0-9.]+', version.get('product', '')):
                    _fail('unsupported-browser', 'Browser.getVersion')
                context = cdp.call('Target.createBrowserContext', {'disposeOnDetach': True})['browserContextId']
                target = cdp.call('Target.createTarget', {'url': 'about:blank', 'browserContextId': context})['targetId']
                session = cdp.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
                cdp.call('Browser.setDownloadBehavior', {'behavior': 'deny', 'browserContextId': context})
                cdp.call('Page.enable', session=session)
                cdp.call('Page.setLifecycleEventsEnabled', {'enabled': True}, session)
                cdp.call('Runtime.enable', session=session)
                cdp.call('Network.enable', session=session)
                cdp.call('Network.setCacheDisabled', {'cacheDisabled': True}, session)
                cdp.call('Network.setBypassServiceWorker', {'bypass': True}, session)
                cdp.call('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session)
                viewport = config['viewport']
                cdp.call('Emulation.setDeviceMetricsOverride', {'width': viewport['width'], 'height': viewport['height'],
                         'deviceScaleFactor': 1, 'mobile': False}, session)
                navigation = cdp.call('Page.navigate', {'url': origin + '/'}, session)
                if navigation.get('errorText'):
                    _fail('navigation-failed', 'fixed capture page')
                cdp.event('Page.lifecycleEvent', session, lambda event:
                          event.get('name') == 'load' and event.get('loaderId') == navigation.get('loaderId'))
                result = cdp.call('Runtime.evaluate', {'expression': 'globalThis.__DELIVERED_CAPTURE__', 'awaitPromise': True,
                                  'returnByValue': True, 'timeout': max(1, int(_remaining(deadline) * 1000))}, session)
                report = result.get('result', {}).get('value')
                if result.get('exceptionDetails') or type(report) is not dict:
                    _fail('render-runtime-failed', 'fixed capture runtime')
                screenshot = cdp.call('Page.captureScreenshot', {'format': 'png', 'fromSurface': True, 'captureBeyondViewport': False,
                                      'clip': {'x': 0, 'y': 0, 'width': viewport['width'], 'height': viewport['height'], 'scale': 1}}, session)
                if cdp.blocked:
                    _fail('forbidden-network-request', 'browser')
                if any(path not in routes for path in requests):
                    _fail('forbidden-server-request', 'browser')
                if not {'/', '/runtime.mjs', '/config.json', '/delivery.glb'}.issubset(requests):
                    _fail('incomplete-runtime-load', 'browser requests')
                try:
                    png = base64.b64decode(screenshot.get('data', ''), validate=True)
                except ValueError as exc:
                    raise CaptureError('invalid-screenshot', 'CDP base64') from exc
                return png, report, version, [flag for flag in flags if not flag.startswith('--user-data-dir=')], sorted(set(requests))
        except (OSError, socket.timeout, subprocess.SubprocessError) as exc:
            raise CaptureError('browser-transport-failed', type(exc).__name__) from exc
        finally:
            if cdp is not None:
                cdp.close()
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


def _preflight_output(root_fd, directory):
    fd = os.dup(root_fd)
    try:
        for index, part in enumerate(directory.parts):
            try:
                info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if index == len(directory.parts) - 1:
                _fail('output-exists', str(directory))
            if not stat.S_ISDIR(info.st_mode):
                _fail('unsafe-output-path', str(directory))
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    except OSError as exc:
        raise CaptureError('unsafe-output-path', str(directory)) from exc
    finally:
        os.close(fd)


def _output(root_fd, directory, contents):
    fd = os.dup(root_fd)
    try:
        for part in directory.parts[:-1]:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        try:
            os.mkdir(directory.name, dir_fd=fd)
        except FileExistsError as exc:
            raise CaptureError('output-exists', str(directory)) from exc
        child = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = child
        for name, data in contents.items():
            temporary = name + '.tmp'
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            with os.fdopen(handle, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
    except OSError as exc:
        raise CaptureError('unsafe-output-path', str(directory)) from exc
    finally:
        os.close(fd)


def capture_delivered_glb(workspace, *, artifact, delivery, config, chrome, modules, output_dir, timeout_seconds=30):
    """Return a capture-only manifest, never quality approval; write fresh output."""
    _number(timeout_seconds, 1, 120, 'timeout_seconds')
    deadline = time.monotonic() + timeout_seconds
    config = validate_config(config)
    directory = _relative(output_dir)
    try:
        root_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CaptureError('unsafe-workspace', 'workspace') from exc
    try:
        manifest_data, _ = _read_ref(root_fd, artifact, 8 * 1024 * 1024)
        model_data, _ = _read_ref(root_fd, delivery)
        if PurePosixPath(delivery['path']).suffix.lower() != '.glb':
            _fail('unsupported-delivery-kind', 'delivery')
        raw = _json(manifest_data, 'artifact')
        if type(raw) is not dict or type(raw.get('files')) is not list:
            _fail('invalid-artifact', 'artifact')
        for entry in raw['files']:
            if type(entry) is not dict:
                _fail('invalid-artifact', 'artifact.files')
            _read_ref(root_fd, {'path': entry.get('path'), 'sha256': entry.get('sha256')})
        validated = validate_artifact(workspace, raw)
        if not any(entry['path'] == delivery['path'] and entry['sha256'] == delivery['sha256'] for entry in validated['files']):
            _fail('delivery-not-in-artifact', 'delivery')
        if validated.get('entrypoint') is not None and validated['entrypoint'] != delivery['path']:
            _fail('delivery-selection-mismatch', 'entrypoint')
        _check_glb(model_data)
        runtime_path = Path(__file__).with_name('delivered_glb_runtime.mjs')
        runtime = runtime_path.read_bytes()
        if type(modules) is not dict or not REQUIRED_MODULES.issubset(modules) or not set(modules).issubset(MODULE_URLS):
            _fail('unsupported-module-map', 'modules')
        module_bytes = {url: _host_ref(ref, 8 * 1024 * 1024)[0] for url, ref in modules.items()}
        executable, mode = _host_ref(chrome)
        if Path(chrome['path']).name not in _CHROME_NAMES or not mode & 0o111 or executable[:4] not in _NATIVE_MAGIC:
            _fail('unsupported-executable', 'chrome')
        # The server holds only validated snapshots, never an open filesystem root.
        render_config = dict(config, deliverySha256=delivery['sha256'])
        render_config['effectiveScale'] = [x * validated['scaleEstimate'] for x in config['transform']['scale']]
        for value in render_config['effectiveScale']:
            _number(value, 1e-12, 1e12, 'effectiveScale')
        html = b'<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,"><style>html,body{margin:0;padding:0;overflow:hidden}canvas{display:block}</style><script type="importmap" nonce="fixed-capture-map">{"imports":{"three":"/deps/three.module.js"}}</script><script type="module" src="/runtime.mjs"></script>'
        routes = {'/': (html, 'text/html; charset=utf-8'), '/runtime.mjs': (runtime, 'text/javascript; charset=utf-8'),
                  '/config.json': (_canonical(render_config), 'application/json'), '/delivery.glb': (model_data, 'model/gltf-binary')}
        routes.update({url: (data, 'text/javascript; charset=utf-8') for url, data in module_bytes.items()})
        for ref in (artifact, delivery, *validated['files']):
            if _relative(ref['path']).is_relative_to(directory):
                _fail('output-overlaps-input', ref['path'])
        _preflight_output(root_fd, directory)
        png, report, browser, flags, requests = _browser(chrome['path'], routes, render_config, deadline)
        width, height = _png_dimensions(png, [config['viewport']['width'], config['viewport']['height']])
        expected_viewport = dict(config['viewport'])
        if report.get('deliverySha256') != delivery['sha256'] or report.get('viewport') != expected_viewport or report.get('canvas') != {'width': width, 'height': height} or report.get('frame') != config['frame']:
            _fail('runtime-binding-mismatch', 'runtime report')
        expected_camera = dict(config['camera'], aspect=width / height)
        if report.get('camera') != expected_camera or type(report.get('renderer')) is not dict or report['renderer'].get('name') != 'WebGLRenderer':
            _fail('runtime-binding-mismatch', 'camera/renderer')
        if type(report.get('threeRevision')) is not str or not report['threeRevision']:
            _fail('missing-renderer-evidence', 'threeRevision')
        _read_ref(root_fd, artifact, 8 * 1024 * 1024)
        _read_ref(root_fd, delivery)
        for entry in validated['files']:
            _read_ref(root_fd, {'path': entry['path'], 'sha256': entry['sha256']})
        _host_ref(chrome)
        for ref in modules.values():
            _host_ref(ref, 8 * 1024 * 1024)
        if runtime_path.read_bytes() != runtime:
            _fail('input-changed', 'program runtime')
        _remaining(deadline)
        config_bytes = _canonical(config)
        renderer = {'chrome': chrome, 'modules': modules, 'browser': browser, 'flags': flags,
                    'programRuntimeSha256': hashlib.sha256(runtime).hexdigest(), 'htmlSha256': hashlib.sha256(html).hexdigest(),
                    'runtimeReport': report}
        renderer_bytes = _canonical(renderer)
        result = {'kind': 'img2.delivered-glb-capture', 'version': 1, 'status': 'captured',
                  'artifact': dict(artifact), 'delivery': dict(delivery), 'viewId': config['viewId'], 'frame': config['frame'],
                  'viewport': report['viewport'], 'camera': report.get('camera'), 'effectiveScale': render_config['effectiveScale'],
                  'config': {'path': str(directory / 'config.json'), 'sha256': hashlib.sha256(config_bytes).hexdigest()},
                  'renderer': {'path': str(directory / 'renderer.json'), 'sha256': hashlib.sha256(renderer_bytes).hexdigest()},
                  'screenshot': {'path': str(directory / 'capture.png'), 'sha256': hashlib.sha256(png).hexdigest(),
                                 'width': width, 'height': height, 'byteLength': len(png)},
                  'served': [{'urlPath': path, 'sha256': hashlib.sha256(routes[path][0]).hexdigest()} for path in sorted(routes)],
                  'requestedPaths': requests, 'quality': 'unevaluated', 'sourceToRenderFidelity': 'unevaluated', 'deliverable': False,
                  'scope': 'exact-delivery-reference-view-capture-not-security-sandbox'}
        _output(root_fd, directory, {'capture.png': png, 'config.json': config_bytes,
                                    'renderer.json': renderer_bytes, 'capture.json': _canonical(result) + b'\n'})
        return result
    finally:
        os.close(root_fd)
