#!/usr/bin/env python3
"""Provision a FRESH isolated img2 harness home for end-to-end acceptance runs.

Nothing here touches the real HOME or the real ~/.img2: every command runs
with a sanitized environment whose HOME, XDG_* and TMPDIR live under --home.
Commands are argv arrays executed without a shell, each under a timeout. The
script never grants Jev consent, never calls a model and never spends tokens.

Layout under --home (the isolated root; it must not exist or be empty):

  home/            fake HOME for every command
  home/.img2       IMG2_HOME created by ``img2 install``
  runtime/         TMPDIR, XDG_*, Blender user directories
  harness-source/  detached clone of the harness at --harness-ref
  provision.json   every command, exit code, doctor result and the pins

Exit status: 0 provisioned and doctor green; 1 a step failed or doctor is
red; 2 refused before running anything (bad arguments, unsafe home).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

REQUIRED_PLUGINS = ("character", "cs2", "interior", "jev", "pixi2d")
COMMAND_TIMEOUT = 600
OUTPUT_LIMIT = 64 * 1024
_PLUGIN_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z|[0-9a-f]{64}\Z")


class Refusal(Exception):
    """Argument or environment problem detected before any command ran."""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_plugin_spec(spec):
    """``ID=PATH@SHA`` -> (id, path, sha). The SHA follows the LAST ``@``."""
    identifier, equals, rest = spec.partition("=")
    path, at, sha = rest.rpartition("@")
    if not equals or not at or not path:
        raise Refusal("malformed --plugin %r: expected ID=PATH@SHA" % spec)
    if _PLUGIN_ID.fullmatch(identifier) is None:
        raise Refusal("malformed --plugin %r: invalid plugin id" % spec)
    if _SHA.fullmatch(sha) is None:
        raise Refusal("malformed --plugin %r: SHA must be 40 or 64 lowercase hex characters" % spec)
    return identifier, path, sha


def parse_plugins(specs):
    plugins = {}
    for spec in specs:
        identifier, path, sha = parse_plugin_spec(spec)
        if identifier in plugins:
            raise Refusal("duplicate --plugin id %r" % identifier)
        plugins[identifier] = (path, sha)
    missing = [name for name in REQUIRED_PLUGINS if name not in plugins]
    if missing:
        raise Refusal("missing required --plugin: " + ", ".join(missing))
    return plugins


def check_home(home):
    """Refuse an existing non-empty home, a symlink, or anything overlapping the real HOME."""
    if home.is_symlink():
        raise Refusal("--home must not be a symlink: %s" % home)
    if home.exists() and (not home.is_dir() or any(home.iterdir())):
        raise Refusal("--home must not exist or be an empty directory: %s" % home)
    resolved = home.resolve()
    real_home = Path.home().resolve()
    for forbidden in (real_home, real_home / ".img2"):
        if resolved == forbidden or forbidden in resolved.parents or resolved in forbidden.parents:
            raise Refusal("--home overlaps the real %s; refusing" % forbidden)
    return resolved


def sanitized_environment(root, node):
    """Environment from the acceptance report, section A. No inherited variables."""
    home, runtime = root / "home", root / "runtime"
    return {
        "HOME": str(home),
        "PATH": ":".join((str(home / ".local/bin"), str(node.parent),
                          "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(runtime / "tmp") + "/",
        "TMP": str(runtime / "tmp"),
        "TEMP": str(runtime / "tmp"),
        "XDG_CONFIG_HOME": str(runtime / "config"),
        "XDG_CACHE_HOME": str(runtime / "cache"),
        "XDG_DATA_HOME": str(runtime / "data"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NODE_COMPILE_CACHE": str(runtime / "cache/node"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "BLENDER_USER_CONFIG": str(runtime / "blender/config"),
        "BLENDER_USER_SCRIPTS": str(runtime / "blender/scripts"),
        "BLENDER_USER_DATAFILES": str(runtime / "blender/datafiles"),
    }


def subprocess_runner(argv, *, env, cwd, timeout):
    """Run one argv array; returns (exit code, stdout, stderr). 124 = timeout, 127 = not found."""
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, shell=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        return 124, _text(error.stdout), _text(error.stderr) + "\ntimed out after %ss" % timeout
    except FileNotFoundError as error:
        return 127, "", str(error)
    return result.returncode, result.stdout, result.stderr


def _text(value):
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _clip(text):
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + "\n[truncated]"


def parse_doctor(code, stdout):
    """Doctor is red on a non-zero exit, unparsable output, or any FAIL finding."""
    try:
        value = json.loads(stdout)
        fails, warns, findings = value["fails"], value["warns"], value["findings"]
        if type(fails) is not int or type(warns) is not int or type(findings) is not list:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return {"exitCode": code, "parsed": False, "fails": None, "warns": None,
                "findings": [], "green": False}
    return {"exitCode": code, "parsed": True, "fails": fails, "warns": warns,
            "findings": findings, "green": code == 0 and fails == 0}


def provision(*, home, harness_src, harness_ref, plugins, node, git=None,
              runner=subprocess_runner, timeout=COMMAND_TIMEOUT, clock=time.time):
    """Run every step; write provision.json; return (exit code, record)."""
    if _SHA.fullmatch(harness_ref) is None:
        raise Refusal("--harness-ref must be a 40 or 64 lowercase hex commit SHA")
    harness_src = Path(harness_src).resolve()
    if not (harness_src / ".git").exists():
        raise Refusal("--harness-src is not a git checkout: %s" % harness_src)
    for identifier, (path, _) in plugins.items():
        if not Path(path).is_dir():
            raise Refusal("--plugin %s: not a directory: %s" % (identifier, path))
    node = Path(node)
    if not node.is_absolute() or not node.is_file() or not os.access(node, os.X_OK):
        raise Refusal("--node must be an absolute path to an executable: %s" % node)
    root = check_home(Path(home))
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()  # the harness refuses non-canonical paths (macOS /tmp is a symlink)
    for directory in ("home/.local/bin", "runtime/tmp", "runtime/cache", "runtime/data",
                      "runtime/config", "runtime/blender/config", "runtime/blender/scripts",
                      "runtime/blender/datafiles"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    env = sanitized_environment(root, node)
    git = git or shutil.which("git", path=env["PATH"])
    if git is None:
        raise Refusal("git not found on the sanitized PATH")
    img2_home = root / "home/.img2"
    source = root / "harness-source"
    installed = img2_home / "harness/bin/img2.mjs"
    steps = [
        ("clone-harness", [git, "clone", "--no-hardlinks", "--no-checkout", str(harness_src), str(source)]),
        ("pin-harness-source", [git, "-C", str(source), "checkout", "--detach", harness_ref]),
        ("install", [str(node), str(source / "bin/img2.mjs"), "install", "--from", str(source),
                     "--yes", "--home", str(img2_home)]),
    ]
    for identifier, (path, sha) in plugins.items():
        steps.append(("add-" + identifier, [
            str(node), str(installed), "add", str(Path(path).resolve()), "--ref", sha,
            "--plugin", identifier, "--allow-any-source", "--yes", "--home", str(img2_home)]))
    steps += [
        ("sync", [str(node), str(installed), "sync", "--home", str(img2_home)]),
        ("sync-check", [str(node), str(installed), "sync", "--check", "--home", str(img2_home)]),
        ("installed-harness-head", [git, "-C", str(img2_home / "harness"), "rev-parse", "HEAD"]),
        ("doctor", [str(node), str(installed), "doctor", "--json", "--home", str(img2_home)]),
    ]
    record = {
        "kind": "img2.e2e-provision", "version": 1, "status": "failed",
        "startedAt": clock(), "root": str(root), "img2Home": str(img2_home),
        "environment": env, "commands": [], "doctor": None, "pins": None,
        "grants": 0, "modelCalls": 0,
    }
    failed = None
    outputs = {}
    for identifier, argv in steps:
        started = clock()
        code, stdout, stderr = runner(argv, env=env, cwd=str(root), timeout=timeout)
        outputs[identifier] = (code, stdout)
        record["commands"].append({"id": identifier, "argv": argv, "cwd": str(root),
                                   "exitCode": code, "elapsedSeconds": clock() - started,
                                   "stdout": _clip(stdout), "stderr": _clip(stderr)})
        if identifier == "doctor":
            record["doctor"] = parse_doctor(code, stdout)
        elif code != 0:
            failed = identifier
            break
    problems = [] if failed is None else ["step failed: " + failed]
    if failed is None:
        problems += _verify_install(record, outputs, img2_home, installed, node, harness_ref, plugins)
        doctor = record["doctor"]
        if not doctor["green"]:
            problems.append("doctor is red (exit %s, fails %s)" % (doctor["exitCode"], doctor["fails"]))
    record["problems"] = problems
    record["status"] = "ok" if not problems else "failed"
    record["finishedAt"] = clock()
    with (root / "provision.json").open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return (0 if record["status"] == "ok" else 1), record


def _verify_install(record, outputs, img2_home, installed, node, harness_ref, plugins):
    """Compute the pins and check the installed state matches what was requested."""
    problems = []
    head = outputs["installed-harness-head"][1].strip()
    if head != harness_ref:
        problems.append("installed harness HEAD %s != --harness-ref %s" % (head, harness_ref))
    registry = {}
    try:
        rows = json.loads((img2_home / "plugins.json").read_text(encoding="utf-8"))["plugins"]
        registry = {row["id"]: row for row in rows}
    except (OSError, ValueError, KeyError, TypeError):
        problems.append("plugins.json missing or invalid")
    pins = {}
    for identifier, (path, sha) in plugins.items():
        row = registry.get(identifier)
        resolved = row.get("resolvedSha") if isinstance(row, dict) else None
        if resolved != sha:
            problems.append("plugin %s registered at %s, expected %s" % (identifier, resolved, sha))
        pins[identifier] = {"path": str(Path(path).resolve()), "sha": sha, "resolvedSha": resolved,
                            "installedDir": str(img2_home / "plugins" / identifier)}
    try:
        record["pins"] = {
            "harnessSha": head, "harnessRef": harness_ref,
            "harnessRoot": str(img2_home / "harness"),
            "harnessExecutable": str(installed), "harnessExecutableSha256": sha256_file(installed),
            "node": str(node), "nodeSha256": sha256_file(node),
            "registrySha256": sha256_file(img2_home / "plugins.json") if registry else None,
            "plugins": pins,
        }
    except OSError as error:
        problems.append("cannot hash pinned executable: %s" % error)
    return problems


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", required=True, type=Path, help="isolated root; must not exist or be empty")
    parser.add_argument("--harness-src", required=True, type=Path, help="harness git checkout to clone")
    parser.add_argument("--harness-ref", required=True, help="harness commit SHA to check out detached")
    parser.add_argument("--plugin", action="append", default=[], metavar="ID=PATH@SHA",
                        help="plugin checkout and pinned commit; give character, cs2, interior, jev, pixi2d")
    parser.add_argument("--node", required=True, type=Path, help="absolute path to the node executable")
    parser.add_argument("--git", help="git executable (default: first git on the sanitized PATH)")
    parser.add_argument("--timeout", type=int, default=COMMAND_TIMEOUT, help="per-command timeout, seconds")
    return parser


def main(argv=None, *, runner=subprocess_runner, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        plugins = parse_plugins(args.plugin)
        code, record = provision(home=args.home, harness_src=args.harness_src,
                                 harness_ref=args.harness_ref, plugins=plugins, node=args.node,
                                 git=args.git, runner=runner, timeout=args.timeout)
    except Refusal as error:
        print("refused: %s" % error, file=out)
        return 2
    print("status: %s" % record["status"], file=out)
    print("record: %s" % (Path(record["root"]) / "provision.json"), file=out)
    for command in record["commands"]:
        print("  %-24s exit %s" % (command["id"], command["exitCode"]), file=out)
    doctor = record["doctor"]
    if doctor is not None:
        print("doctor: exit %s, fails %s, warns %s" % (doctor["exitCode"], doctor["fails"], doctor["warns"]), file=out)
        for finding in doctor["findings"]:
            print("  %s %s: %s" % (finding.get("level"), finding.get("plugin"), finding.get("message")), file=out)
    for problem in record["problems"]:
        print("problem: %s" % problem, file=out)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
