"""Read-only typed-edge discovery. Never executes a card's commands or installs tools."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

from .storage import RouteError, snapshot

EDGES = {
    "pixi2d": ("image", "pixi2d-scene"),
    "interior": ("image", "interior-scene-candidate"),
    "character": ("character-mesh", "rigged-character"),
    "cs2": ("image", "cs2-skin-spec"),
}
JEV_EDGE = ("img2.route-context", "img2.route-advisory")
_DEPENDENCIES = {"blender": ("blender", "--version"), "node": ("node", "--version"),
                 "python3": ("python3", "--version")}

# Values are immutable and local to the calling thread/async context. A scope
# applies only to the exact Discovery shared by the controller's callbacks.
_READ_ONLY_PROBES = ContextVar("stage0_read_only_probes", default=())


@contextmanager
def read_only_probes(discovery):
    """Run real probes without recursively acquiring the controller journal lock.

    Wrap controller consult/consume or a host workspace_transaction whenever its
    live callbacks use this Discovery. The outer controller records authoritative
    availability and results; detailed argv probe journaling resumes on exit.
    """
    token = _READ_ONLY_PROBES.set((*_READ_ONLY_PROBES.get(), discovery))
    try:
        yield
    finally:
        _READ_ONLY_PROBES.reset(token)


class Discovery:
    def __init__(self, controller, *, harness=("img2",), auth=("img2-jev", "auth-status"),
                 runner=subprocess.run, clock=time.time):
        self.controller = controller
        self.workspace = controller.workspace
        self.harness = tuple(harness)
        self.auth = tuple(auth)
        self.runner = runner
        self.clock = clock
        self.probes = []
        self.version = None
        self.degraded = False
        self.jev = None
        self.jev_status = None

    def run(self, argv):
        """Only callers in this module construct argv; stdout is never journalled verbatim."""
        argv = list(argv)
        try:
            result = self.runner(argv, capture_output=True, timeout=15, check=False,
                                 shell=False, cwd=str(self.workspace))
            output = result.stdout
            if type(output) is str:
                output = output.encode()
            code = result.returncode
        except FileNotFoundError:
            output, code = b"", 127
        except subprocess.TimeoutExpired:
            output, code = b"", 124
        except OSError:
            output, code = b"", 126
        if not any(active is self for active in _READ_ONLY_PROBES.get()):
            fact = {"argv": argv, "exitCode": code,
                    "stdoutSha256": hashlib.sha256(output).hexdigest(), "at": self.clock()}
            self.probes.append(self.controller.record_probe(fact))
        return code, output

    def query(self, edge):
        code, raw = self.run((*self.harness, "capabilities", "--from-kind", edge[0],
                              "--to-kind", edge[1], "--json"))
        try:
            value = json.loads(raw)
            if (type(value) is not dict or value.get("version") != 1
                    or value.get("query") != {"from": edge[0], "to": edge[1]}
                    or value.get("status") not in {"answered", "ambiguous", "data-fault"}
                    or type(value.get("providers")) is not list
                    or code != {"answered": 0, "ambiguous": 3, "data-fault": 1}[value["status"]]):
                raise ValueError
            if value["status"] == "answered" and len(value["providers"]) > 1:
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError):
            return {"status": "data-fault", "providers": []}

    def _pinned_file(self, row, relative):
        """Git SHA and bytes must agree. Local links and unsupported integrity pins fail closed."""
        if type(row) is not dict or type(row.get("dir")) is not str:
            raise RouteError("invalid-provider-data")
        sha = row.get("resolvedSha")
        if type(sha) is not str or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            raise RouteError("provider-unpinned")
        directory = Path(row["dir"])
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise RouteError("unsafe-provider-path")
        path = directory / relative
        if any(part in {"", ".", ".."} for part in relative.split("/")):
            raise RouteError("unsafe-provider-path")
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise RouteError("provider-file-missing")
        code, committed = self.run(("git", "-C", str(directory), "show", sha + ":" + relative))
        if code != 0 or path.read_bytes() != committed:
            raise RouteError("provider-pin-mismatch")
        return committed

    def dependency(self, name):
        match = re.fullmatch(r"(blender|node|python3)>=(\d+(?:\.\d+)*)", name)
        if not match:
            return False
        code, raw = self.run(_DEPENDENCIES[match[1]])
        version = re.search(rb"\b(\d+)\.(\d+)(?:\.(\d+))?", raw)
        if code != 0 or version is None:
            return False
        actual = tuple(int(part or b"0") for part in version.groups())
        required = tuple(int(part) for part in match[2].split("."))
        return actual >= required + (0,) * (3 - len(required))

    def _gate_producer(self, row):
        """Admission checks real pinned executable producers, not a model's gate assertion.

        This is NOT evidence that any gate passed; child execution remains authoritative.
        """
        runner = row.get("gateRunner")
        if (type(runner) is not dict or type(runner.get("argv")) is not list
                or not runner["argv"]):
            return False
        try:
            gates = json.loads(self._pinned_file(row, "gates.json"))
        except RouteError as exc:
            if exc.reason_code == "provider-file-missing":
                return False
            raise
        if type(gates) is not list:
            return False
        found = False
        for gate in gates:
            if type(gate) is not dict or gate.get("blocking") is not True:
                continue
            if gate.get("actor", "program") != "program":
                continue
            command = gate.get("command")
            if type(command) is not str:
                continue
            argv = shlex.split(command)
            if not argv:
                continue
            # Do not run an arbitrary gate. Check its interpreter and pinned script exist.
            if argv[0] not in {"python3", "node"} or shutil.which(argv[0]) is None or len(argv) < 2:
                continue
            prefix = "{plugin_dir}/"
            if not argv[1].startswith(prefix):
                continue
            try:
                self._pinned_file(row, argv[1][len(prefix):])
            except RouteError as exc:
                if exc.reason_code == "provider-file-missing":
                    continue
                raise
            found = True
        return found

    def _candidate(self, row, expected_name, status, edge):
        if type(row) is not dict or row.get("plugin") != expected_name:
            raise RouteError("unapproved-provider")
        manifest = json.loads(self._pinned_file(row, "plugin.json"))
        if (type(manifest) is not dict or manifest.get("name") != expected_name or manifest.get("version") != row.get("version")
                or {"from": edge[0], "to": edge[1]} not in manifest.get("capabilities", [])):
            raise RouteError("provider-identity-mismatch")
        requirements = manifest.get("requires", {})
        if type(requirements) is not dict:
            raise RouteError("invalid-provider-data")
        requirement = requirements.get("harness")
        if requirement is not None:
            minimum = re.fullmatch(r">=(\d+)\.(\d+)\.(\d+)", requirement)
            current = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", self.version or "")
            if (minimum is None or current is None
                    or tuple(map(int, current.groups())) < tuple(map(int, minimum.groups()))):
                raise RouteError("harness-version-incompatible")
        try:
            card = json.loads(self._pinned_file(row, "route.json"))
        except RouteError as exc:
            if exc.reason_code == "provider-file-missing":
                raise RouteError("route-card-missing") from exc
            raise
        card_binding = snapshot(self.workspace, "route-card", {
            "provider": expected_name, "resolvedSha": row["resolvedSha"], "card": card})
        deps = card.get("deps", []) if type(card) is dict else []
        dependency_results = {d["probe"]: self.dependency(d["probe"]) for d in deps
                              if type(d) is dict and type(d.get("probe")) is str}
        gate = self._gate_producer(row)
        return {"id": expected_name + ":" + hashlib.sha256(
                    (row["resolvedSha"] + ":" + card_binding["sha256"]).encode()).hexdigest(),
                "provider": expected_name, "resolvedSha": row["resolvedSha"], "card": card,
                "binding": card_binding, "providerStatus": status,
                "dependencies": dependency_results, "blockingGateProduced": gate}

    def enumerate(self):
        self.probes = []
        code, raw = self.run((*self.harness, "--version", "--json"))
        try:
            version = json.loads(raw)
            available = (code == 0 and type(version) is dict
                         and type(version.get("harness")) is str
                         and "capabilities" in version.get("commands", []))
        except (ValueError, TypeError):
            available = False
            version = None
        self.version = version.get("harness") if available else None
        self.degraded = not available
        candidates, rejected = [], []
        if not available:
            self.jev_status = "availability-unproven"
            return candidates, rejected
        for name, edge in EDGES.items():
            response = self.query(edge)
            if response["status"] == "data-fault":
                self.degraded = True
            claimants = [row.get("plugin") for row in response["providers"] if type(row) is dict]
            if response["status"] == "ambiguous":
                rejected.append({"id": name, "reasonCode": "provider-ambiguous", "claimants": claimants})
                continue
            if not response["providers"]:
                rejected.append({"id": name, "reasonCode": "provider-unavailable"})
            for row in response["providers"]:
                try:
                    candidate = self._candidate(row, name, response["status"], edge)
                    candidate["claimants"] = claimants
                    candidates.append(candidate)
                except (RouteError, ValueError, KeyError, TypeError) as exc:
                    rejected.append({"id": name, "reasonCode": getattr(exc, "reason_code", "invalid-provider-data"),
                                     "claimants": claimants})
        if self.degraded:
            rejected.extend({"id": c["id"], "reasonCode": "enumeration-degraded"} for c in candidates)
            candidates = []
        response = self.query(JEV_EDGE)
        self.jev_status = "availability-unproven"
        self.jev = None
        if response["status"] == "answered" and not response["providers"]:
            self.jev_status = "capability-unavailable"
        elif response["status"] == "answered" and len(response["providers"]) == 1:
            row = response["providers"][0]
            try:
                manifest = json.loads(self._pinned_file(row, "plugin.json"))
                if (type(manifest) is not dict or row.get("plugin") != "jev" or manifest.get("name") != "jev"
                        or {"from": JEV_EDGE[0], "to": JEV_EDGE[1]} not in manifest.get("capabilities", [])):
                    raise RouteError("provider-identity-mismatch")
                # An exact step id and subcommand, never the first returned step.
                steps = [s for s in row.get("steps", []) if type(s) is dict and s.get("id") == "jev-decision-routing"]
                if (len(steps) != 1 or steps[0].get("actor") != "program"
                        or type(steps[0].get("argv")) is not list
                        or "decision-routing" not in steps[0]["argv"]):
                    raise RouteError("provider-adapter-missing")
                pinned_steps = json.loads(self._pinned_file(row, "steps.json"))
                declared = [s for s in pinned_steps if type(s) is dict and s.get("id") == "jev-decision-routing"]
                expected = ["python3", str(Path(row["dir"]) / "tools" / "jev.py"), "decision-routing",
                            "--workspace", "{workspace}", "--input", ".img2/artifacts/jev/contexts/routing.json",
                            "--assets", ".img2/artifacts/jev/contexts/routing-assets.json"]
                if (len(declared) != 1 or declared[0].get("actor") != "program"
                        or shlex.split(declared[0].get("command", "").replace("{plugin_dir}", row["dir"])) != expected
                        or steps[0]["argv"] != expected):
                    raise RouteError("provider-adapter-missing")
                self._pinned_file(row, "tools/jev.py")
                self.jev = row
                self.jev_status = "present"
            except (RouteError, ValueError, TypeError, KeyError):
                self.jev_status = "availability-unproven"
        return candidates, rejected

    def availability(self):
        # Requery on every consultation, so a prior outage is not a model-provided fact.
        if self.jev_status != "present":
            return {"status": self.jev_status}
        response = self.query(JEV_EDGE)
        if (response["status"] != "answered" or response["providers"] != [self.jev]):
            # Controller interprets exceptions as probe failure, not proven outage.
            return {"status": "availability-unproven"}
        code, _ = self.run(self.auth)
        status = {0: "present", 3: "credential-missing", 1: "credential-storage-error"}.get(code)
        if status is None:
            return {"status": "availability-unproven"}
        return {"status": status}
