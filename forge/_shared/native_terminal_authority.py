"""Host-approved native capture/replay plus journal-authenticated semantic review.

There is no policy loader, callback gate or default approval. The host constructs a
NativeTerminalPolicy only after inspecting the exact sources/dependency closure.
Hashes keep that approval current; they do not establish its authenticity. Plugin
code is executed only through the two fixed subprocess entrypoints below, never
imported. No controller consult/consume/lock operation runs in verify_terminal.

prepare returns a shared quality-review context. The caller consults the shared
controller OUTSIDE the parent transaction, saves that exact context at AREA +
quality-review-context-<canonical_digest(context)>.json and writes quality-review-link.json containing exactly
{context:{path,sha256},record:{path,sha256}} (both parent-relative). Completion uses
the actual held DecisionController.workspace_transaction verifier, reruns native
capture and compares decoded pixels/config/geometry, not temporary paths/ports.
The fixed base static producer supplies rest-geometry, approved-camera MATERIAL
captures and required-part isolation. Semantic recognition is resolved only by
the authenticated journal review over those exact images. Missing/unsupported
measurements fail per input; no saved gate flag can waive a producer. Whole
composed-scene/browser/D12 acceptance is a separate parent responsibility.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from decision_control import canonical_digest
from route_parent import ParentError, _decode, _encode, _read, _write
from stage0_route.images import decode, png

AREA = '.img2/artifacts/native-terminal/'
AXES = ('silhouette-proportions', 'part-relationships', 'geometry',
        'material-appearance', 'identity-features')
SHA = re.compile(r'[0-9a-f]{64}\Z')
COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')
SOURCE_SUFFIXES = frozenset(('.py', '.pyc', '.pyo', '.mjs', '.js', '.ts', '.json', '.pth', '.so', '.dylib', '.pyd'))
SKIP = frozenset(('.git', 'node_modules', '.venv', '.venv-blender', '.venv-images'))


def _require(condition, reason):
    if not condition:
        raise ParentError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(value):
    _require(type(value) is str and bool(value) and not value.startswith('/')
             and not any(char in value for char in ('\\', ':', '\x00'))
             and all(part not in ('', '.', '..') for part in value.split('/')),
             'unsafe-native-authority-path')
    return value


def _pair(value, *, absolute=False):
    _require(type(value) is tuple and len(value) == 2 and type(value[1]) is str
             and SHA.fullmatch(value[1]) is not None, 'invalid-native-policy-binding')
    if absolute:
        _require(type(value[0]) is str and Path(value[0]).is_absolute(), 'absolute-native-host-path-required')
    else:
        _relative(value[0])
    return {'path': value[0], 'sha256': value[1]}


def _pairs(values, *, absolute=False):
    _require(type(values) is tuple and len({row[0] for row in values if type(row) is tuple and row}) == len(values),
             'immutable-unique-native-policy-bindings-required')
    for value in values:
        _pair(value, absolute=absolute)


def _canonical_root(value):
    _require(type(value) is str and Path(value).is_absolute(), 'absolute-native-root-required')
    path = Path(value)
    _require(str(path.resolve(strict=True)) == value and path.is_dir(), 'canonical-native-root-required')
    return path


def _file_sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _tree(root, *, sources=False):
    """Enumerate the complete approved source/runtime closure, including caches.

    Source scans include ALL Python under the plugin, not just its tools folder.
    Runtime scans include startup .pth/native/data/bytecode files too. Caches in
    nested fixed plugin subprocesses therefore cannot be unpinned executable code.
    """
    rows = []
    def visit(directory):
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if sources and path.name in SKIP:
                continue
            _require(not path.is_symlink(), 'native-code-tree-alias')
            if path.is_dir():
                visit(path)
            else:
                _require(path.is_file(), 'native-code-tree-special-file')
                if not sources or path.suffix in SOURCE_SUFFIXES:
                    rows.append((path.relative_to(root).as_posix(), _file_sha(path)))
    visit(root)
    return tuple(rows)


@dataclass(frozen=True)
class NativeTerminalPolicy:
    """Immutable program-owned approval, never deserialize candidate/workspace JSON.

    Relative pairs are (POSIX path, sha256); request/parent bind parent workspace,
    reference/spec/artifact/sources bind child. plugin_files/harness_files are the
    complete _tree(..., sources=True) closure, including authoring/resources_lib.
    dependency_trees contains (absolute root, complete relative file-pair tuple).
    Include Python startup/site-packages, bpy/native libs, browser bundle and Three
    dependencies in the inspected runtime closure; no discovery grants approval.
    Executable pairs preserve venv invocation paths, hashing their actual target.
    review_id/notes identify the host's actual source/dependency review.
    """
    object_id: str
    revision: int
    run_id: str
    parent_root: str
    parent_binding: tuple[str, str]
    provider: str
    owner_route: str
    plugin_root: str
    plugin_commit: str
    plugin_files: tuple[tuple[str, str], ...]
    python: tuple[str, str]
    bpy_python: tuple[str, str]
    git: tuple[str, str]
    harness_root: str
    harness_commit: str
    harness_files: tuple[tuple[str, str], ...]
    base_root: str
    base_commit: str
    base_files: tuple[tuple[str, str], ...]
    request_binding: tuple[str, str]
    reference_binding: tuple[str, str]
    spec_binding: tuple[str, str]
    artifact_binding: tuple[str, str]
    sources: tuple[tuple[str, str], ...]
    required_parts: tuple[str, ...]
    critical_features: tuple[str, ...]
    static_config_json: bytes
    dependency_files: tuple[tuple[str, str], ...]
    dependency_trees: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    review_id: str
    review_notes: str
    node: tuple[str, str] | None = None
    browser: tuple[str, str] | None = None
    three_root: str | None = None
    three_package_sha: str | None = None
    requested_model: str = 'jev-1.14.0'

    def __post_init__(self):
        _require(type(self.object_id) is str and ID.fullmatch(self.object_id) is not None
                 and type(self.run_id) is str and ID.fullmatch(self.run_id) is not None
                 and type(self.revision) is int and self.revision > 0,
                 'invalid-native-policy-subject')
        _require(self.provider in ('base', 'interior', 'character') and type(self.owner_route) is str and bool(self.owner_route)
                 and all(type(value) is str and bool(value.strip()) for value in
                         (self.review_id, self.review_notes, self.requested_model)), 'native-source-review-required')
        for root in (self.parent_root, self.plugin_root, self.harness_root, self.base_root):
            _canonical_root(root)
        _require(Path(self.harness_root).name == 'harness', 'native-harness-layout-mismatch')
        for commit in (self.plugin_commit, self.harness_commit, self.base_commit):
            _require(type(commit) is str and COMMIT.fullmatch(commit) is not None, 'exact-native-commit-required')
        for value in (self.parent_binding, self.request_binding, self.reference_binding,
                      self.spec_binding, self.artifact_binding):
            _pair(value)
        _require(self.parent_binding[0] == '.img2threejs/state.json'
                 and self.artifact_binding[0] == 'artifact.json', 'native-policy-layout-mismatch')
        for value in (self.python, self.bpy_python, self.git):
            _pair(value, absolute=True)
        for values in (self.sources, self.plugin_files, self.harness_files, self.base_files):
            _pairs(values)
            _require(bool(values), 'complete-native-source-approval-required')
        _pairs(self.dependency_files, absolute=True)
        _require(type(self.dependency_trees) is tuple and bool(self.dependency_trees),
                 'complete-native-dependency-approval-required')
        roots = []
        for root, rows in self.dependency_trees:
            roots.append(str(_canonical_root(root)))
            _pairs(rows)
            _require(bool(rows), 'complete-native-dependency-approval-required')
        _require(len(set(roots)) == len(roots), 'duplicate-native-dependency-root')
        for values in (self.required_parts, self.critical_features):
            _require(type(values) is tuple and bool(values) and len(set(values)) == len(values)
                     and all(type(value) is str and ID.fullmatch(value) is not None for value in values),
                     'exact-native-critical-inventory-required')
        _require(type(self.static_config_json) is bytes, 'approved-static-config-required')
        config = _decode(self.static_config_json)
        _require(_encode(config) == self.static_config_json
                 and type(config) is dict and config.get('kind') == 'img2.native-static-request'
                 and config.get('version') == 1, 'canonical-approved-static-config-required')
        _require(config.get('bindings', {}).get('artifact') == _pair(self.artifact_binding)
                 and config['bindings'].get('reference') == _pair(self.reference_binding)
                 and config['bindings'].get('spec') == _pair(self.spec_binding)
                 and [part.get('id') for part in config.get('parts', [])] == list(self.required_parts),
                 'static-config-policy-binding-mismatch')
        from stage4_review.measure_native_static import validate_request
        try:
            validate_request(config)
        except ValueError as error:
            raise ParentError('invalid-static-policy-config:' + str(error)) from error
        if self.provider == 'base':
            _require(self.plugin_root == self.base_root and self.plugin_commit == self.base_commit
                     and self.plugin_files == self.base_files, 'base-provider-source-approval-mismatch')
        if self.provider == 'character':
            _pair(self.node, absolute=True)
            _pair(self.browser, absolute=True)
            _canonical_root(self.three_root)
            _require(type(self.three_package_sha) is str and SHA.fullmatch(self.three_package_sha) is not None,
                     'reviewed-native-three-required')
        else:
            _require(all(value is None for value in (self.node, self.browser, self.three_root, self.three_package_sha)),
                     'unexpected-interior-runtime-selection')


class NativeTerminalAuthority:
    def __init__(self, parent_workspace, policies):
        self.parent = _canonical_root(str(parent_workspace))
        _require(type(policies) is dict and all(type(policy) is NativeTerminalPolicy
                 and key == policy.object_id and policy.parent_root == str(self.parent)
                 for key, policy in policies.items()), 'host-native-policies-required')
        self._live = policies
        self._policies = deepcopy(policies)

    def __reduce_ex__(self, protocol):
        raise TypeError('native terminal authority cannot be serialized')

    def _policy(self, child_workspace, provider, state, artifact_relative):
        _require(self._live == self._policies, 'native-policy-revoked-or-changed')
        _require(type(state) is dict and type(state.get('routeChild')) is dict, 'native-child-subject-required')
        subject = state['routeChild']
        policy = self._policies.get(subject.get('objectId'))
        _require(type(policy) is NativeTerminalPolicy and provider == policy.provider, 'native-policy-unavailable')
        child = Path(child_workspace)
        expected = self.parent / '.img2threejs/objects' / policy.object_id / ('rev-' + str(policy.revision))
        _require(child == expected and child.resolve(strict=True) == expected, 'native-child-workspace-mismatch')
        _require(subject.get('runId') == policy.run_id and subject.get('revision') == policy.revision
                 and subject.get('ownerRoute') == policy.owner_route
                 and subject.get('cropSha256') == policy.reference_binding[1]
                 and subject.get('outputKind') == {'interior': '3d-scene', 'character': 'rigged-3d', 'base': '3d-object'}[provider]
                 and state.get('profile') == {'base':'generic','interior':'interior','character':'animated-character'}[provider],
                 'native-child-subject-mismatch')
        _require(artifact_relative == policy.artifact_binding[0], 'native-artifact-path-mismatch')
        _require(_decode(_read(child, 'state.json')[0]) == state, 'native-child-state-changed')
        return policy, child

    @staticmethod
    def _checked(root, binding):
        _require(type(binding) is dict and set(binding) == {'path', 'sha256'}
                 and type(binding['sha256']) is str and SHA.fullmatch(binding['sha256']) is not None,
                 'invalid-native-evidence-binding')
        raw = _read(root, _relative(binding['path']))[0]
        _require(bool(raw) and _sha(raw) == binding['sha256'], 'stale-native-evidence')
        return raw

    def _host(self, policy):
        """Revalidate live host approval, source trees, runtime closure and commits."""
        _require(self._live == self._policies, 'native-policy-revoked-or-changed')
        for pair in (policy.python, policy.bpy_python, policy.git, *policy.dependency_files,
                     *((policy.node, policy.browser) if policy.provider == 'character' else ())):
            path = Path(pair[0])
            _require(path.parent.resolve(strict=True) == path.parent and path.is_file()
                     and _file_sha(path) == pair[1], 'native-host-file-pin-mismatch')
        for pair in (policy.python, policy.bpy_python, policy.git,
                     *((policy.node, policy.browser) if policy.provider == 'character' else ())):
            _require(os.access(pair[0], os.X_OK), 'native-host-executable-unavailable')
        for root, rows in policy.dependency_trees:
            _require(_tree(_canonical_root(root)) == rows, 'native-dependency-tree-changed')
        _require(policy.base_root == str(Path(__file__).resolve().parents[2]), 'native-base-source-root-mismatch')
        for root, commit, rows in ((policy.plugin_root, policy.plugin_commit, policy.plugin_files),
                                   (policy.harness_root, policy.harness_commit, policy.harness_files),
                                   (policy.base_root, policy.base_commit, policy.base_files)):
            _require(not Path(root).is_relative_to(self.parent), 'native-code-inside-candidate-workspace')
            _require(_tree(_canonical_root(root), sources=True) == rows, 'native-producer-source-tree-changed')
            result = subprocess.run([policy.git[0], '-C', root, 'rev-parse', '--verify', 'HEAD'],
                                    capture_output=True, timeout=20, env={'PATH': os.defpath}, shell=False)
            _require(result.returncode == 0 and result.stdout.decode().strip() == commit, 'native-provider-commit-changed')
        _require('forge/stage4_review/measure_native_static.py' in dict(policy.base_files),
                 'native-static-producer-unapproved')
        if policy.provider != 'base':
            _require('tools/' + ('audit_routed_artifact.py' if policy.provider == 'interior'
                                 else 'gate_routed_terminal.py') in dict(policy.plugin_files), 'native-fixed-producer-unapproved')
        if policy.provider == 'interior':
            _require(any(path.startswith('authoring/') for path, _ in policy.plugin_files)
                     and any(path.startswith('resources_lib/') for path, _ in policy.plugin_files),
                     'native-complete-plugin-tree-required')
        self._checked(self.parent, _pair(policy.parent_binding))
        self._checked(self.parent, _pair(policy.request_binding))

    def _inputs(self, policy, child):
        rows = []
        for pair in (policy.reference_binding, policy.spec_binding, policy.artifact_binding, *policy.sources):
            binding = _pair(pair)
            self._checked(child, binding)
            if binding not in rows:
                rows.append(binding)
        decode(self._checked(child, _pair(policy.reference_binding)))
        artifact = _decode(self._checked(child, _pair(policy.artifact_binding)))
        _require(type(artifact) is dict and artifact.get('objectId') == policy.object_id
                 and artifact.get('frame') == {'units': 'm', 'up': '+Y', 'forward': '+Z'}
                 and artifact.get('pivot') == 'bottom-centre', 'native-artifact-contract-mismatch')
        _require(type(artifact.get('files')) is list and bool(artifact['files']), 'native-artifact-files-required')
        for binding in artifact['files']:
            self._checked(child, binding)
            if binding not in rows:
                rows.append(binding)
        state_raw = _read(child, 'state.json')[0]
        rows.append({'path': 'state.json', 'sha256': _sha(state_raw)})
        return rows

    @staticmethod
    def _environment(policy):
        env = {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LC_ALL',
                                               'LC_CTYPE', 'SYSTEMROOT', 'WINDIR') if key in os.environ}
        env.update(PATH=os.defpath, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                   IMG2_HOME=str(Path(policy.harness_root).parent))
        if policy.provider == 'character':
            env.update(IMG2_CHARACTER_BPY_PYTHON=policy.bpy_python[0], IMG2_CHARACTER_NODE=policy.node[0],
                       IMG2_CHARACTER_BROWSER=policy.browser[0], IMG2_CHARACTER_THREE_ROOT=policy.three_root,
                       IMG2_CHARACTER_THREE_SHA256=policy.three_package_sha)
        return env

    def _run(self, policy, child):
        self._host(policy)
        initial = self._inputs(policy, child)
        area = child / AREA / 'runs'
        # _read's confinement rules protect files; creation additionally checks
        # every directory ancestor before mkdir to refuse output symlink aliases.
        current = child
        for part in (AREA + 'runs').split('/'):
            if part:
                current /= part
                _require(not current.is_symlink(), 'native-output-directory-alias')
        area.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix='capture-', dir=area))
        relative_output = output.relative_to(child).as_posix()
        if policy.provider != 'base':
            argv = [policy.python[0], '-E', '-s', '-S', '-B', '-X',
                    'pycache_prefix=' + str(output / 'python-cache')]
            if policy.provider == 'interior':
                argv += [str(Path(policy.plugin_root) / 'tools/audit_routed_artifact.py'), '--workspace', str(child),
                         '--artifact', policy.artifact_binding[0], '--spec', policy.spec_binding[0],
                         '--blender-python', policy.bpy_python[0], '--quality-output', relative_output + '/native', '--gate']
                gate_id = 'interior-routed-structure'
            else:
                argv += [str(Path(policy.plugin_root) / 'tools/gate_routed_terminal.py'), '--workspace', str(child),
                         '--artifact', policy.artifact_binding[0]]
                gate_id = 'character-routed-terminal'
            result = subprocess.run(argv, cwd=child, env=self._environment(policy), capture_output=True,
                                    timeout=600, shell=False)
            self._host(policy)
            _require(self._inputs(policy, child) == initial, 'native-input-changed-during-capture')
            try:
                verdict = _decode(result.stdout)
            except (ValueError, UnicodeError) as exc:
                raise ParentError('native-fixed-gate-invalid-output') from exc
            _require(type(verdict) is dict and verdict.get('kind') == 'img2.gate-verdict'
                     and verdict.get('version') == 1 and verdict.get('gate') == gate_id
                     and verdict.get('plugin') == policy.provider and verdict.get('status') == 'pass'
                     and verdict.get('reasons') == [] and result.returncode == 0,
                     'native-fixed-gate-failed:' + str(verdict.get('reasons', [])))
            _write(child, relative_output + '/gate.json', _encode(verdict))
            evidence = verdict.get('evidence')
            _require(type(evidence) is dict, 'native-fixed-gate-missing-evidence')
            snapshot = (self._interior(policy, child, evidence, initial, relative_output) if policy.provider == 'interior'
                        else self._character(policy, child, evidence, initial, relative_output))
            if policy.provider == 'interior':
                workflow_inputs, workflow_summary = self._interior_workflow(policy, child, relative_output)
                for binding in workflow_inputs:
                    if binding not in snapshot['inputs']:
                        snapshot['inputs'].append(binding)
                snapshot['deterministic']['workflow'] = workflow_summary
        else:
            # Base has no native-domain plugin gate. Its actual mandatory gate is
            # the static producer below, not a fabricated passing domain report.
            snapshot = {'provider': 'base', 'inputs': initial, 'captures': [], 'deterministic': {}}
        static = self._static(policy, child, relative_output)
        snapshot['captures'].extend(static['captures'])
        snapshot['deterministic']['static'] = {key: static[key] for key in
            ('bindings', 'requestSha256', 'policySha256', 'measurements', 'checks', 'meshes', 'parts', 'restPose', 'renderSetup')}
        snapshot['policySha256'] = _sha(_encode(self._policy_description(policy)))
        self._host(policy)
        _require(self._inputs(policy, child) == initial, 'native-input-changed-during-capture')
        for binding in snapshot['inputs']:
            self._checked(child, binding)
        _write(child, relative_output + '/snapshot.json', _encode(snapshot))
        return snapshot

    def _static(self, policy, child, output):
        request = _decode(policy.static_config_json)
        delivery = request['bindings']['delivery']
        artifact = _decode(self._checked(child, _pair(policy.artifact_binding)))
        # Match stage6_compose.compose's selection exactly: an explicit entrypoint
        # wins; only a sole GLB may be selected implicitly. Never audit an auxiliary.
        glbs = [entry for entry in artifact['files'] if entry['path'].lower().endswith('.glb')]
        _require(bool(glbs), 'native-source-consumer-unavailable')
        selected = artifact.get('entrypoint')
        if selected is None and len(glbs) == 1:
            selected = glbs[0]['path']
        matches = [entry for entry in glbs if entry['path'] == selected]
        _require(len(matches) == 1, 'native-ambiguous-entrypoint')
        _require(delivery == matches[0], 'native-static-entrypoint-mismatch')
        self._checked(child, delivery)
        _write(child, output + '/static-request.json', _encode(request))
        command = [policy.bpy_python[0], '-I', '-B', '-X', 'pycache_prefix=' + str(child / output / 'static-python-cache'),
                   str(Path(policy.base_root) / 'forge/stage4_review/measure_native_static.py'),
                   '--workspace', str(child), '--input', output + '/static-request.json',
                   '--output', output + '/static']
        result = subprocess.run(command, cwd=child, env=self._environment(policy), capture_output=True,
                                timeout=600, shell=False)
        self._host(policy)
        records = [line.removeprefix(b'IMG2_NATIVE_STATIC ') for line in result.stdout.splitlines()
                   if line.startswith(b'IMG2_NATIVE_STATIC ')]
        _require(len(records) == 1, 'native-static-producer-invalid-output')
        report = _decode(records[0])
        _require(type(report) is dict and report.get('kind') == 'img2.native-static-measurement'
                 and report.get('version') == 1 and report.get('status') == 'pass' and result.returncode == 0,
                 'native-static-producer-failed:' + str(report.get('reasons', report.get('checks', {}))))
        _require(report.get('bindings') == request['bindings']
                 and report.get('requestSha256') == _sha(policy.static_config_json)
                 and [part.get('id') for part in report.get('parts', [])] == list(policy.required_parts),
                 'native-static-current-inputs-mismatch')
        for capture in report['captures']:
            raw = self._checked(child, {key: capture[key] for key in ('path', 'sha256')})
            width, height, channels, pixels = decode(raw)
            _require((width, height, channels, _sha(bytes(pixels))) ==
                     (capture['width'], capture['height'], capture['channels'], capture['pixelSha256']),
                     'native-static-pixels-changed')
        return report


    @staticmethod
    def _policy_description(policy):
        # Frozen dataclass contents contain no callbacks and are JSON-compatible
        # after conversion; this binding detects host requirement changes too.
        from dataclasses import asdict
        value = asdict(policy)
        value['static_config_json'] = _decode(policy.static_config_json)
        return value

    def _interior_workflow(self, policy, child, output):
        """Rerun the remaining legacy blocking gate, not a saved checklist flag."""
        state_raw = _read(child, '.img2/state.json')[0]
        command = [policy.python[0], '-E', '-s', '-S', '-B', '-X',
                   'pycache_prefix=' + str(child / output / 'workflow-python-cache'),
                   str(Path(policy.plugin_root) / 'tools/gate_workflow.py'), '--workspace', str(child)]
        result = subprocess.run(command, cwd=child, env=self._environment(policy),
                                capture_output=True, timeout=180, shell=False)
        verdict = _decode(result.stdout)
        _require(result.returncode == 0 and verdict.get('kind') == 'img2.gate-verdict'
                 and verdict.get('version') == 1 and verdict.get('plugin') == 'interior'
                 and verdict.get('gate') == 'interior-workflow' and verdict.get('status') == 'pass'
                 and verdict.get('reasons') == [], 'native-interior-workflow-gate-failed')
        _require(_read(child, '.img2/state.json')[0] == state_raw, 'native-interior-workflow-changed')
        pins = [{'path': '.img2/state.json', 'sha256': _sha(state_raw)}]
        def collect(value):
            if type(value) is list:
                for item in value:
                    collect(item)
            elif type(value) is dict:
                if 'path' in value and 'sha256' in value:
                    path = Path(value.get('resolvedPath', value['path']))
                    if path.is_absolute():
                        _require(path.is_relative_to(child), 'native-workflow-evidence-outside-child')
                        path = path.relative_to(child)
                    binding = {'path': path.as_posix(), 'sha256': value['sha256']}
                    self._checked(child, binding)
                    if binding not in pins:
                        pins.append(binding)
                for item in value.values():
                    collect(item)
        evidence = verdict.get('evidence', {})
        collect(evidence)
        _require(evidence.get('source', {}).get('sha256') == policy.reference_binding[1],
                 'native-workflow-reference-mismatch')
        _write(child, output + '/workflow-gate.json', _encode(verdict))
        return pins, {'historyLength': evidence['historyLength'],
                      'stages': {key: value['status'] for key, value in evidence['stages'].items()}}


    def _interior(self, policy, child, audit, initial, output):
        _require(audit.get('kind') == 'img2.interior-routed-audit' and audit.get('status') == 'pass',
                 'native-interior-audit-missing')
        evidence = audit.get('evidence', {})
        _require(evidence.get('artifact') == _pair(policy.artifact_binding), 'native-gate-artifact-mismatch')
        structure = evidence.get('measurements', {}).get('structure', {})
        _require(structure.get('kind') == 'img2.interior-routed-structure' and structure.get('version') == 1
                 and structure.get('artifact') == _pair(policy.artifact_binding), 'native-interior-structure-missing')
        inputs = deepcopy(initial)
        for binding in evidence.get('inputs', []) + evidence.get('auditInputs', []):
            self._checked(child, binding)
            if binding not in inputs:
                inputs.append(binding)
        measured = structure.get('measurements', {})
        for name in ('width', 'height', 'depth', 'frontArea', 'sideArea'):
            value = measured.get(name)
            _require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                     'native-static-geometry-measurement-missing')
        _require(measured.get('selfIntersectionCount') == 0
                 and type(measured.get('selfIntersectionCount')) is int
                 and measured['depth'] / max(measured['width'], measured['height']) >= 0.15
                 and measured['sideArea'] / measured['frontArea'] >= 0.25,
                 'native-static-geometry-failed')
        meshes = structure.get('meshes')
        _require(type(meshes) is list and bool(meshes)
                 and all(row.get('degenerateTriangles') == 0 and row.get('selfIntersectionCount') == 0 for row in meshes),
                 'native-static-mesh-integrity-failed')
        coverage = structure.get('componentCoverage', {})
        _require(coverage.get('missing') == [] and coverage.get('unexpected') == [],
                 'native-admitted-component-inventory-mismatch')
        captures = []
        for index, capture in enumerate(structure.get('captures', [])):
            binding = {key: capture[key] for key in ('path', 'sha256')}
            raw = self._checked(child, binding)
            from stage4_review.measure_native_static import normalize_render_png
            raw = normalize_render_png(raw)
            image_path = output + '/domain-orbit-' + str(capture['angle']) + '.png'
            self._save_bytes(child, image_path, raw)
            binding = {'path': image_path, 'sha256': _sha(raw)}
            width, height, channels, pixels = decode(raw)
            captures.append({'role': 'domain-orbit-' + str(capture['angle']),
                             **binding, 'pixelSha256': _sha(bytes(pixels)), 'width': width, 'height': height,
                             'channels': channels,
                             'view': {key: capture[key] for key in ('angle', 'positionNativeZUp', 'rotationNativeXYZ')}})
        _require([row['view']['angle'] for row in captures] == [0, 90, 180, 270], 'native-four-view-capture-missing')
        declared = structure.get('evidence', {})
        _require(declared.get('referenceSha256') == policy.reference_binding[1]
                 and declared.get('specSha256') == policy.spec_binding[1]
                 and declared.get('artifactSha256') == policy.artifact_binding[1], 'native-structure-reference-binding-mismatch')
        return {'provider': policy.provider, 'inputs': inputs, 'captures': captures,
                'deterministic': {'measurements': measured, 'meshes': meshes, 'componentCoverage': coverage,
                                  'captureSetup': structure['captureSetup'], 'cameraSha256': structure['cameraSha256']}}

    def _character(self, policy, child, evidence, initial, output):
        _require(evidence.get('artifact') == {'path': str(child / policy.artifact_binding[0]),
                                            'sha256': policy.artifact_binding[1]}
                 and evidence.get('measurementOrigin') == 'live-native-and-browser', 'native-character-live-measurement-missing')
        measurement = _decode(self._checked(child, evidence['measurement']))
        _require(measurement.get('kind') == 'img2.character-terminal-measurement' and measurement.get('version') == 1
                 and measurement.get('reference') == _pair(policy.reference_binding)
                 and measurement.get('subject') == _decode(_read(child, 'state.json')[0])['routeChild'],
                 'native-character-measurement-subject-mismatch')
        source = measurement.get('native', {}).get('source')
        _require(source in [_pair(value) for value in policy.sources], 'native-character-source-not-approved')
        native = measurement['native']
        _require(native.get('executable', {}).get('executable') == policy.bpy_python[0]
                 and native['executable'].get('sha256') == policy.bpy_python[1], 'native-bpy-selection-mismatch')
        executables = measurement.get('executables', {})
        _require(executables.get('node', {}).get('executable') == policy.node[0]
                 and executables['node'].get('sha256') == policy.node[1]
                 and executables.get('browser', {}).get('path') == policy.browser[0]
                 and executables['browser'].get('sha256') == policy.browser[1], 'native-browser-selection-mismatch')
        _require(measurement.get('three', {}).get('root') == policy.three_root
                 and measurement['three'].get('sha256') == policy.three_package_sha, 'native-three-selection-mismatch')
        inputs = deepcopy(initial)
        for binding in measurement.get('boundInputs', []):
            self._checked(child, binding)
            if binding not in inputs:
                inputs.append(binding)
        parity = native.get('parity', {})
        _require(type(parity.get('vertexComparisons')) is int and parity['vertexComparisons'] > 0
                 and parity.get('toleranceOverH') == 1e-4
                 and type(parity.get('maxWorldVertexDelta')) in (int, float)
                 and type(parity.get('tolerance')) in (int, float)
                 and 0 <= parity['maxWorldVertexDelta'] <= parity['tolerance']
                 and parity.get('fractions') == [0, .25, .5, .75, 1], 'native-pose-parity-unmeasured-or-failed')
        gate_rows = measurement.get('gateReport', {}).get('gates')
        _require(type(gate_rows) is list and len(gate_rows) >= 12
                 and all(row.get('status') == 'pass' for row in gate_rows), 'native-character-blocking-gates-failed')
        sweep = _decode(self._checked(child, measurement['sweep']))
        runtime = _decode(self._checked(child, measurement['runtime']))
        captures = []
        for index, frame in enumerate(sweep.get('frames', [])):
            width, height = frame.get('width'), frame.get('height')
            _require(type(width) is int and type(height) is int and width > 0 and height > 0
                     and width * height <= 16_000_000, 'native-character-frame-size-invalid')
            raw = self._checked(child, frame['candidate'])
            _require(len(raw) == width * height * 4, 'native-character-rgba-missing')
            stride = width * 4
            # Owned WebGL producer explicitly uses bottom-up RGBA rows.
            upright = b''.join(raw[y * stride:(y + 1) * stride] for y in range(height - 1, -1, -1))
            image = png(width, height, 4, upright)
            image_path = output + '/frame-' + str(index) + '.png'
            self._save_bytes(child, image_path, image)
            camera = _decode(self._checked(child, frame['camera']))
            self._checked(child, frame['roi'])
            captures.append({'role': 'domain-pose-' + str(index),
                             'path': image_path, 'sha256': _sha(image), 'pixelSha256': _sha(upright),
                             'width': width, 'height': height, 'channels': 4,
                             'view': {**{key: frame[key] for key in ('clip', 'time', 'side', 'azimuth')},
                                      'camera': camera, 'roiSha256': frame['roi']['sha256']}})
        _require(bool(captures) and {0, 90, 180, 270} <= {row['view']['azimuth'] for row in captures},
                 'native-character-four-view-capture-missing')
        return {'provider': policy.provider, 'inputs': inputs, 'captures': captures,
                'deterministic': {'parity': parity, 'gates': [{key: row[key] for key in
                                  ('id', 'status', 'threshold', 'measured')} for row in gate_rows],
                                  'runtimeMeasurements': runtime.get('measurements'), 'axes': sweep.get('axes')}}

    @staticmethod
    def _save_bytes(child, relative, raw):
        # parent helpers' atomic JSON writer is not an image writer.
        from stage0_route.storage import atomic_write
        atomic_write(child, relative, raw, immutable=True)

    @staticmethod
    def _semantic(snapshot):
        return {'provider': snapshot['provider'], 'inputs': snapshot['inputs'],
                'policySha256': snapshot['policySha256'], 'deterministic': snapshot['deterministic'],
                'captures': [{key: row[key] for key in ('role', 'pixelSha256', 'width', 'height', 'channels', 'view')}
                             for row in snapshot['captures']]}

    def _context(self, policy, child, snapshot):
        prefix = child.relative_to(self.parent).as_posix() + '/'
        bindings, images = [], []
        def bound(role, binding, *, parent=False, image=False):
            raw = self._checked(self.parent if parent else child, binding)
            bindings.append({'role': role, 'path': binding['path'] if parent else prefix + binding['path'],
                             'sha256': binding['sha256']})
            if image:
                decode(raw)
                images.append({'role': role, 'asset': role})
        bound('user-requirements', _pair(policy.request_binding), parent=True)
        bound('parent-state', _pair(policy.parent_binding), parent=True)
        bound('reference', _pair(policy.reference_binding), image=True)
        bound('artifact', _pair(policy.artifact_binding))
        bound('spec', _pair(policy.spec_binding))
        for index, source in enumerate(policy.sources):
            bound('source-' + str(index), _pair(source))
        for index, value in enumerate(snapshot['inputs']):
            bound('native-input-' + str(index), value)
        for capture in snapshot['captures']:
            bound(capture['role'], {key: capture[key] for key in ('path', 'sha256')}, image=True)
        image_roles = [item['role'] for item in images]
        critical = ['part:' + value for value in policy.required_parts] + ['feature:' + value for value in policy.critical_features]
        views = [item['role'] for item in snapshot['captures']]
        return {'kind': 'img2.quality-review-context', 'version': 1, 'group': 'quality-review',
                'subject': {'runId': policy.run_id, 'objectId': policy.object_id, 'revision': policy.revision,
                            'passId': policy.provider + '-terminal-review'},
                'bindings': bindings, 'candidates': [],
                'observations': {'images': images, 'availableViews': views,
                                 'nativeEvidence': self._semantic(snapshot),
                                 'requiredParts': list(policy.required_parts),
                                 'criticalInventory': list(policy.critical_features),
                                 'reviewScope': 'Actual approved-camera material/orbit/part capture and measured rest geometry, plus route rig checks where applicable. Required parts need semantic recognition against the bound reference. Not composed-scene/D12 acceptance.'},
                'requirements': {'rubricVersion': 'quality-review-rubric-v1',
                                 'dimensions': [{'id': axis, 'threshold': 3, 'evidenceRoles': image_roles} for axis in AXES],
                                 'criticalFeatures': [{'id': value, 'threshold': 3, 'evidenceRoles': image_roles} for value in critical],
                                 'requiredViews': views, 'evidenceRoles': [value['role'] for value in bindings]},
                'policyVersion': 'native-terminal-policy-v1', 'questionSetVersion': 'quality-review-questions-v1',
                'requestedModel': policy.requested_model}

    def prepare(self, child_workspace, *, provider, state, artifact_relative='artifact.json'):
        """Run actual fixed producers and return context; no consultation/acceptance."""
        policy, child = self._policy(child_workspace, provider, state, artifact_relative)
        snapshot = self._run(policy, child)
        return self._context(policy, child, snapshot)

    def verify_terminal(self, verify, *, child_workspace, provider, state, artifact_relative='artifact.json'):
        """Replay and consume the exact journal review under the caller's held lease."""
        policy, child = self._policy(child_workspace, provider, state, artifact_relative)
        # RouteParent supplies its program-owned held verifier. Invoking it below
        # enforces journal identity, consent and lifetime without taking a lock.
        _require(callable(verify) and callable(getattr(verify, 'route_rounds', None))
                 and callable(getattr(verify, 'route_round_links', None)), 'held-native-journal-verifier-required')
        link = _decode(_read(child, AREA + 'quality-review-link.json')[0])
        _require(type(link) is dict and set(link) == {'context', 'record'}, 'native-quality-link-invalid')
        context = _decode(self._checked(self.parent, link['context']))
        record = _decode(self._checked(self.parent, link['record']))
        expected_context_path = (child.relative_to(self.parent).as_posix() + '/' + AREA
                                 + 'quality-review-context-' + canonical_digest(context) + '.json')
        _require(link['context']['path'] == expected_context_path
                 and type(record) is dict
                 and link['record']['path'] == '.img2threejs/decisions/quality-review/' + str(record.get('recordId')) + '.json',
                 'native-quality-link-invalid')
        fresh = self._run(policy, child)
        _require(context.get('observations', {}).get('nativeEvidence') == self._semantic(fresh), 'native-quality-replay-mismatch')
        # Keep the original reviewed paths, after validating every original byte
        # and comparing fresh decoded pixels/config/geometry to that exact context.
        original = deepcopy(fresh)
        by_role = {binding['role']: binding for binding in context.get('bindings', [])}
        for capture in original['captures']:
            prior = by_role.get(capture['role'], {})
            prefix = child.relative_to(self.parent).as_posix() + '/'
            _require(type(prior.get('path')) is str and prior['path'].startswith(prefix), 'native-original-capture-path-invalid')
            relative = prior['path'][len(prefix):]
            raw = self._checked(child, {'path': relative, 'sha256': prior.get('sha256')})
            width, height, channels, pixels = decode(raw)
            _require((width, height, channels, _sha(bytes(pixels))) ==
                     (capture['width'], capture['height'], capture['channels'], capture['pixelSha256']),
                     'native-original-capture-changed')
            capture.update(path=relative, sha256=prior['sha256'])
        _require(context == self._context(policy, child, original), 'native-quality-context-mismatch')
        authenticated = verify(record, context)
        _require(authenticated.get('status') == 'answered' and authenticated.get('group') == 'quality-review'
                 and authenticated.get('subject') == context['subject']
                 and authenticated.get('decider', '').startswith(('jev:', 'model:')), 'native-review-not-authorized')
        answers = authenticated.get('answers', {})
        for axis in AXES:
            answer = answers.get('dimension:' + axis, {})
            score = answer.get('score')
            _require(answer.get('type') == 'score' and type(score) in (int, float)
                     and math.isfinite(score) and 3 <= score <= 4, 'native-semantic-quality-failed')
        for feature in context['requirements']['criticalFeatures']:
            answer = answers.get('critical:' + feature['id'], {})
            _require(answer.get('type') == 'choice' and answer.get('choice') == 'pass', 'native-critical-feature-failed')
        self._host(policy)
        for binding in fresh['inputs']:
            self._checked(child, binding)
        return authenticated
