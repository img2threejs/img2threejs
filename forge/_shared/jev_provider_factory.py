"""Host-owned Jev bridge factory; preparation is local and never authorizes payment.

The mandatory ``approved_release`` record must come ONLY from the base host's
reviewed immutable release lock, not a model, context, CLI response or plugin:
{resolvedSha, files:{relative:sha256}, contractVersion:'jev-v4-four-decisions'}.
Git consistency alone does not establish the consent/reservation contract. This
module has no default approved release. Its copied lock binds the actual package,
including every REQUIRED_PROVIDER_FILES entry, to both Git and reviewed bytes.

Usage: factory = JevProviderFactory(discovery, approved_release=host_lock,
    run_id=run_id, consent=live_run_consent)
provider = factory.provider(original_context); controller.consult(original_context,
    provider=provider, consent=live_run_consent, availability=discovery.availability)
Each different context gets its own immutable context/assets bindings. Image roles
must each identify exactly one binding; repeated roles/assets are rejected, never
chosen by ordering. Crops must already be bound images; no inferred crop is added.

The Python executable is pinned by canonical path and SHA256. Python's installed
stdlib/Pillow and Jev's external harness/core remain trusted host dependencies,
not authenticated transitively by the Jev release lock. No plugin Python is
imported into this process. Content-addressed preparation evidence is private
workspace data, not a log, receipt, approval, or unified base/CLI attempt ledger.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shlex
import signal
import struct
import subprocess
import sys
import time
import zlib

from decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from jev_cli_bridge import EDGES, JevCliBridge, REQUIRED_PROVIDER_FILES, _file


_MAX_CONTEXT = 256 * 1024
_MAX_IMAGE = 16 * 1024 * 1024
_MAX_ASSETS = 48 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_PREPARE_PROGRAM = r'''
import importlib.util, json, sys

def deny_network(event, args):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"):
        raise RuntimeError("local preparation only")
sys.addaudithook(deny_network)
try:
    spec = importlib.util.spec_from_file_location("_jev_image_preparation", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    image = module.prepare_image(sys.argv[2])
    print(json.dumps({"status": "prepared", "image": image}, separators=(",", ":"), allow_nan=False))
except Exception as error:
    reason = "image-dependency-unavailable" if isinstance(error, ImportError) or str(error) == "image dependency unavailable" else "image-preparation-failed"
    print(json.dumps({"status": "request-input", "reasonCode": reason}))
    sys.exit(2)
'''


def _fail(reason):
    raise DecisionError(reason) from None


def _encode(value, maximum):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("context-fault")
    if len(raw) > maximum:
        _fail("context-too-large")
    return raw


def _parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        _fail("image-preparation-failed")


def _publish(workspace, category, raw, suffix="json"):
    """Publish a private immutable blob, using no-follow descriptor traversal."""
    sha = hashlib.sha256(raw).hexdigest()
    directories = (".img2threejs", "route-inputs", "jev-" + category)
    relative = "/".join((*directories, sha + "." + suffix))
    directory = descriptor = temporary = None
    temporary_created = False
    try:
        directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in directories:
            try:
                os.mkdir(part, 0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        temporary = ".preparing-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        temporary_created = True
        with os.fdopen(descriptor, "wb") as target:
            descriptor = None
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), 0o400)
        try:
            os.link(temporary, sha + "." + suffix, src_dir_fd=directory,
                    dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            _file(workspace, relative, maximum=len(raw), expected=sha)
        os.fsync(directory)
    except OSError:
        _fail("unsafe-path")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_created and directory is not None:
            os.unlink(temporary, dir_fd=directory)
        if directory is not None:
            os.close(directory)
    return relative, sha


def _prepare_image(executable, helper, source, workspace, timeout):
    """Bounded isolated child: only the pinned local image helper, no host secrets."""
    process = None
    try:
        process = subprocess.Popen(
            [str(executable), "-I", "-B", "-c", _PREPARE_PROGRAM, str(helper), str(source)],
            cwd=workspace, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, start_new_session=True)
        deadline = time.monotonic() + timeout
        output, size = bytearray(), 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _fail("timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > 24 * 1024 * 1024:
                        _fail("image-preparation-failed")
                    if key.fileobj is process.stdout:
                        output.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("timeout")
            process.wait(timeout=remaining)
        result = _parse(bytes(output))
        if process.returncode != 0:
            code = result.get("reasonCode") if type(result) is dict else None
            _fail(code if code == "image-dependency-unavailable" else "image-preparation-failed")
        if type(result) is not dict or set(result) != {"status", "image"} or result["status"] != "prepared":
            _fail("image-preparation-failed")
        return result["image"]
    except subprocess.TimeoutExpired:
        _fail("timeout")
    except OSError:
        _fail("image-dependency-unavailable")
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            process.stdout.close()
            process.stderr.close()


def _image_bytes(image):
    """Validate the metadata-free RGB PNG envelope without importing Pillow."""
    if (type(image) is not dict
            or set(image) != {"format", "data_base64", "sha256", "width", "height", "transform"}
            or image["format"] != "png" or type(image["data_base64"]) is not str
            or len(image["data_base64"]) > 4 * ((_MAX_IMAGE + 2) // 3)
            or any(type(image[side]) is not int or not 280 <= image[side] <= 1568
                   for side in ("width", "height"))):
        _fail("invalid-assets")
    try:
        data = base64.b64decode(image["data_base64"], validate=True)
    except (ValueError, binascii.Error):
        _fail("invalid-assets")
    if (not data or len(data) > _MAX_IMAGE or hashlib.sha256(data).hexdigest() != image["sha256"]
            or data[:8] != b"\x89PNG\r\n\x1a\n"):
        _fail("invalid-assets")
    offset, kinds, compressed = 8, [], bytearray()
    while offset < len(data):
        if offset + 12 > len(data):
            _fail("invalid-assets")
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + 12 + length
        if end > len(data):
            _fail("invalid-assets")
        kind, payload = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        if kind not in (b"IHDR", b"IDAT", b"IEND") or zlib.crc32(kind + payload) != int.from_bytes(data[end - 4:end], "big"):
            _fail("invalid-assets")
        if kind == b"IHDR":
            expected = struct.pack(">IIBBBBB", image["width"], image["height"], 8, 2, 0, 0, 0)
            if kinds or payload != expected:
                _fail("invalid-assets")
        elif kind == b"IDAT":
            if not kinds or kinds[-1] not in (b"IHDR", b"IDAT"):
                _fail("invalid-assets")
            compressed.extend(payload)
        elif length or not kinds or kinds[-1] != b"IDAT" or end != len(data):
            _fail("invalid-assets")
        kinds.append(kind)
        offset = end
    if not kinds or kinds[-1] != b"IEND":
        _fail("invalid-assets")
    expected_size = (image["width"] * 3 + 1) * image["height"]
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected_size + 1)
    except zlib.error:
        _fail("invalid-assets")
    if (len(pixels) != expected_size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or any(pixels[row * (image["width"] * 3 + 1)] > 4 for row in range(image["height"]))):
        _fail("invalid-assets")
    transform = image["transform"]
    if (type(transform) is not dict or set(transform) != {
            "sourceWidth", "sourceHeight", "crop", "scaleX", "scaleY", "padX", "padY"}
            or any(type(transform[side]) is not int or transform[side] <= 0
                   for side in ("sourceWidth", "sourceHeight"))
            or type(transform["crop"]) is not list or len(transform["crop"]) != 4):
        _fail("invalid-assets")
    numbers = transform["crop"] + [transform[k] for k in ("scaleX", "scaleY", "padX", "padY")]
    try:
        finite = all(type(n) in (int, float) and math.isfinite(n) for n in numbers)
    except OverflowError:
        finite = False
    if not finite:
        _fail("invalid-assets")
    left, top, right, bottom = transform["crop"]
    if not (0 <= left < right <= transform["sourceWidth"] and 0 <= top < bottom <= transform["sourceHeight"]):
        _fail("invalid-assets")
    for extent, scale, padding, output in ((right - left, transform["scaleX"], transform["padX"], image["width"]),
                                           (bottom - top, transform["scaleY"], transform["padY"], image["height"])):
        if not 0 < scale <= 1:
            _fail("invalid-assets")
        scaled = max(1, round(extent * scale))
        if max(280, scaled) != output or padding != (output - scaled) // 2:
            _fail("invalid-assets")
    return len(data)


class JevProviderFactory:
    def __init__(self, discovery, *, approved_release, run_id, consent,
                 python_executable=None, timeout=30.0):
        if (type(approved_release) is not dict
                or set(approved_release) != {"resolvedSha", "files", "contractVersion"}
                or approved_release["contractVersion"] != "jev-v4-four-decisions"
                or type(approved_release["resolvedSha"]) is not str
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", approved_release["resolvedSha"]) is None
                or type(approved_release["files"]) is not dict
                or not REQUIRED_PROVIDER_FILES <= approved_release["files"].keys()):
            _fail("provider-contract-unproven")
        self.release = copy.deepcopy(approved_release)
        for relative, sha in self.release["files"].items():
            if (type(relative) is not str or "\\" in relative
                    or any(part in ("", ".", "..") for part in relative.split("/"))
                    or type(sha) is not str or not _HEX.fullmatch(sha)):
                _fail("provider-contract-unproven")
        if type(run_id) is not str or not _ID.fullmatch(run_id):
            _fail("subject-mismatch")
        if not callable(consent):
            _fail("consent-required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            _fail("context-fault")
        self.discovery, self.run_id, self.consent, self.timeout = discovery, run_id, consent, timeout
        try:
            self.workspace = Path(discovery.workspace).resolve(strict=True)
            self.executable = Path(python_executable or sys.executable).resolve(strict=True)
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                _fail("image-dependency-unavailable")
            self.executable_sha256 = self._interpreter_hash()
        except OSError:
            _fail("image-dependency-unavailable")
        if discovery.controller.run_id != run_id or discovery.controller.workspace != self.workspace:
            _fail("subject-mismatch")

    def _interpreter_hash(self):
        return hashlib.sha256(_file(self.executable.parent, self.executable.name,
                                    maximum=256 * 1024 * 1024, capture=True)).hexdigest()

    def _grant(self):
        try:
            grant = self.consent()
        except Exception:
            _fail("consent-required")
        if (type(grant) is not dict or set(grant) != {"granted", "purposes", "sha256", "tokenBudget"}
                or grant["granted"] is not True or type(grant["purposes"]) is not list
                or any(type(p) is not str for p in grant["purposes"])
                or len(grant["purposes"]) != len(GROUPS) or set(grant["purposes"]) != set(GROUPS)
                or type(grant["sha256"]) is not str or not _HEX.fullmatch(grant["sha256"])):
            _fail("consent-required")
        if type(grant["tokenBudget"]) is not int or grant["tokenBudget"] < 64000:
            _fail("budget-exhausted")
        return copy.deepcopy(grant)

    def _row(self, group):
        try:
            status = self.discovery.availability()
        except Exception:
            _fail("availability-unproven")
        reason = status.get("status") if type(status) is dict else None
        if reason != "present":
            allowed = {"credential-missing", "credential-rejected", "credential-storage-error",
                       "capability-unavailable", "not-installed"}
            _fail(reason if reason in allowed else "availability-unproven")
        row = copy.deepcopy(self.discovery.jev)
        if (type(row) is not dict or row.get("plugin") != "jev"
                or row.get("resolvedSha") != self.release["resolvedSha"] or type(row.get("dir")) is not str):
            _fail("provider-unpinned")
        root = Path(row["dir"])
        try:
            if (not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir()
                    or root == self.workspace or root in self.workspace.parents
                    or self.workspace in root.parents):
                _fail("unsafe-provider-path")
        except OSError:
            _fail("unsafe-provider-path")
        try:
            response = self.discovery.query(EDGES[group][:2])
        except Exception:
            _fail("capability-unavailable")
        if (type(response) is not dict or response.get("status") != "answered"
                or response.get("providers") != [row]):
            _fail("capability-unavailable")
        return row, root

    def _verify_release(self, group, expected_row=None):
        row, root = self._row(group)
        if expected_row is not None and row != expected_row:
            _fail("provider-pin-mismatch")
        if self._interpreter_hash() != self.executable_sha256:
            _fail("provider-pin-mismatch")
        raw_files = {}
        for relative, sha in self.release["files"].items():
            _file(root, relative, maximum=64 * 1024 * 1024, expected=sha)
            try:
                raw = self.discovery._pinned_file(row, relative)
            except Exception:
                _fail("provider-pin-mismatch")
            if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != sha:
                _fail("provider-pin-mismatch")
            if relative in ("plugin.json", "steps.json"):
                raw_files[relative] = raw
        manifest, declared = _parse(raw_files["plugin.json"]), _parse(raw_files["steps.json"])
        edge = EDGES[group]
        expected_argv = ["python3", str(root / "tools/jev.py"), "decision-" + group,
                         "--workspace", "{workspace}", "--input", ".img2/artifacts/jev/contexts/" + group + ".json",
                         "--assets", ".img2/artifacts/jev/contexts/" + group + "-assets.json"]
        if (type(manifest) is not dict or manifest.get("name") != "jev"
                or type(manifest.get("capabilities")) is not list
                or {"from": edge[0], "to": edge[1]} not in manifest["capabilities"]
                or type(declared) is not list or type(row.get("steps")) is not list):
            _fail("capability-unavailable")
        selected = [s for s in declared if type(s) is dict and s.get("id") == edge[2]]
        actual = [s for s in row["steps"] if type(s) is dict and s.get("id") == edge[2]]
        try:
            matches = (len(selected) == len(actual) == 1 and selected[0].get("actor") == "program"
                       and actual[0].get("actor") == "program"
                       and type(selected[0].get("command")) is str
                       and shlex.split(selected[0].get("command", "").replace("{plugin_dir}", str(root))) == expected_argv
                       and actual[0].get("argv") == expected_argv)
        except (ValueError, TypeError):
            matches = False
        if not matches:
            _fail("capability-unavailable")
        return row, root

    def __call__(self, context):
        frozen = copy.deepcopy(context)
        if (type(frozen) is not dict or frozen.get("group") not in EDGES
                or type(frozen.get("subject")) is not dict or frozen["subject"].get("runId") != self.run_id):
            _fail("subject-mismatch")
        grant = self._grant()
        projected = DecisionController._context(self.discovery.controller, frozen)
        group = frozen["group"]
        row, root = self._verify_release(group)
        images = frozen["observations"].get("images", [])
        if type(images) is not list or len(images) > 32:
            _fail("invalid-assets")
        assets, roles, admitted, total = {}, set(), [], 0
        asset_ids = set()
        for image in images:
            if (type(image) is not dict or set(image) != {"role", "asset"}
                    or any(type(image[k]) is not str or not _ID.fullmatch(image[k]) for k in image)
                    or image["asset"] in asset_ids or image["role"] in roles):
                _fail("ambiguous-image-binding")
            bindings = [binding for binding in frozen["bindings"] if binding["role"] == image["role"]]
            if len(bindings) != 1:
                _fail("ambiguous-image-binding" if bindings else "missing-evidence")
            admitted.append((image, bindings[0]))
            roles.add(image["role"])
            asset_ids.add(image["asset"])
        started = time.monotonic()
        for image, binding in admitted:
            source = _file(self.workspace, binding["path"], maximum=64 * 1024 * 1024,
                           expected=binding["sha256"], capture=True)
            source_path, _ = _publish(self.workspace, "sources", source, "bin")
            if self._grant() != grant:
                _fail("consent-changed")
            remaining = self.timeout - (time.monotonic() - started)
            if remaining <= 0:
                _fail("timeout")
            try:
                prepared = _prepare_image(self.executable, root / "tools/jev_decision_images.py",
                                          self.workspace / source_path, self.workspace, remaining)
            finally:
                self._verify_release(group, row)
            total += _image_bytes(prepared)
            if total > 32 * 1024 * 1024:
                _fail("context-too-large")
            assets[image["asset"]] = prepared
        if self._grant() != grant:
            _fail("consent-changed")
        DecisionController._context(self.discovery.controller, frozen)
        context_path, context_sha = _publish(self.workspace, "contexts", _encode(frozen, _MAX_CONTEXT))
        assets_path, assets_sha = _publish(self.workspace, "assets", _encode(assets, _MAX_ASSETS))
        inventory_digest = canonical_digest({"root": str(root), "resolvedSha": self.release["resolvedSha"],
                                              "files": self.release["files"]})
        def attest_contract():
            self._verify_release(group, row)
            return {"inventoryDigest": inventory_digest, "consentPolicyVersion": "jev-disclosure-v4",
                    "durableSharedReservationTokens": 64000}
        bridge = JevCliBridge(
            self.workspace, provider_root=root, resolved_sha=self.release["resolvedSha"],
            provider_files=self.release["files"], entrypoint="tools/jev.py", executable=self.executable,
            edge=EDGES[group][:2], step_id=EDGES[group][2], context=frozen, context_path=context_path,
            context_sha256=context_sha, assets_path=assets_path, assets_sha256=assets_sha,
            consent=self._grant, attest_contract=attest_contract, timeout=self.timeout)
        bridge._snapshot(projected)
        return bridge

    def provider(self, original_context):
        """Return the actual projected-context -> advisory controller callback."""
        frozen = copy.deepcopy(original_context)
        expected = DecisionController._context(self.discovery.controller, frozen)
        expected_digest = canonical_digest(expected)
        def consult(projected):
            if canonical_digest(projected) != expected_digest:
                _fail("context-fault")
            return self(frozen)(projected)
        return consult
