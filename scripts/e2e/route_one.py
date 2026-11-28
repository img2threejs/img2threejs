#!/usr/bin/env python3
"""Route ONE reference image through forge/route.py inside a provisioned isolated home.

This script never authorizes Jev consent, never revokes it and never spends
anything itself. It first runs ``jev.py consent decision-status`` for the
workspace and run id. If the four decision purposes are not authorized it
prints the exact consent command for the HUMAN to run and exits 20 without
starting forge/route.py. Only when a live grant already exists does it run the
route, then prints a plain summary of the result.

The route and the consent command inherit the real user environment (Jev
credentials live in the user's keychain) plus IMG2_HOME and IMG2_HARNESS_DIR
pointing at the provisioned home, so consent is bound to this home, workspace
and run id only. Routing is decision-only: it authorizes no build and
delivers nothing.

Exit status: 0 route decided and every object resolved; 1 route crashed or
printed unparsable output; 2 refused before running anything; 20 consent not
authorized (nothing was run); 21 route finished without a resolved decision
(request-input, suspended, refusal or unresolved objects).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provision_home import subprocess_runner  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_CONSENT, EXIT_UNRESOLVED = 0, 1, 2, 20, 21
ROUTE_SCRIPT = Path(__file__).resolve().parents[2] / "forge" / "route.py"
DEFAULT_TOKEN_BUDGET = 256_000
MIN_TOKEN_BUDGET = 64_000  # one Jev dispatch reserves 64,000 (provider factory refuses less)
ROUTE_TIMEOUT = 3600
PROBE_TIMEOUT = 60
MARKER = "e2e-route-input.json"
_LABEL = re.compile(r"[a-z][a-z0-9-]{0,95}\Z")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PNG = b"\x89PNG\r\n\x1a\n"


class Refusal(Exception):
    """Bad arguments or unusable inputs, detected before anything ran."""


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_box(text):
    try:
        box = [float(part) for part in text.split(",")]
    except ValueError:
        raise Refusal("--box must be four comma-separated numbers x0,y0,x1,y1") from None
    if (len(box) != 4 or any(not 0 <= value <= 1 for value in box)
            or box[0] >= box[2] or box[1] >= box[3]):
        raise Refusal("--box must be normalized 0..1 with x0<x1 and y0<y1")
    return box


def parse_observations(values):
    observations = {}
    for text in values:
        key, separator, value = text.partition("=")
        if not separator or not key.strip():
            raise Refusal("--observation must be key=value with a non-empty key")
        try:
            observations[key] = json.loads(value)
        except ValueError:
            observations[key] = value
    return observations


def load_provision(home):
    path = Path(home) / "provision.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        pins = record["pins"]
        img2_home = Path(record["img2Home"])
        values = {
            "img2Home": img2_home,
            "harnessRoot": Path(pins["harnessRoot"]),
            "harnessSha": pins["harnessSha"],
            "harnessExecutable": Path(pins["harnessExecutable"]),
            "harnessExecutableSha256": pins["harnessExecutableSha256"],
            "node": Path(pins["node"]),
            "jevDir": Path(pins["plugins"]["jev"]["installedDir"]),
        }
    except FileNotFoundError:
        raise Refusal(
            "%s is missing: required E2E provision record; harness setup does not create it. "
            "Run scripts/e2e/provision_home.py with --home %s in a fresh/empty directory, "
            "or see docs/E2E.md for the record schema." % (path, home)) from None
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal("%s is not a provision record with pins; see docs/E2E.md for the "
                      "required schema" % path) from None
    if record.get("status") != "ok":
        raise Refusal("%s says status=%r; provisioning did not succeed" % (path, record.get("status")))
    if _HEX64.fullmatch(str(values["harnessExecutableSha256"])) is None:
        raise Refusal("%s: pins.harnessExecutableSha256 is not a SHA-256; "
                      "see docs/E2E.md for the required schema" % path)
    return values


def reference_suffix(raw):
    if raw.startswith(_PNG):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    raise Refusal("--image must be a PNG or baseline JPEG")


def prepare_workspace(workspace, image, label, kind, box, run_id, intent, *,
                      role, observations, create):
    """Create a fresh workspace, or reuse one this script created for identical input."""
    raw = Path(image).read_bytes()
    suffix = reference_suffix(raw)
    reference = "ref" + suffix
    proposals = {"kind": "img2.object-proposals", "version": 1, "objects": [{
        "objectId": label, "label": label, "role": role,
        "approxBox": box, "observations": observations}]}
    marker = {"kind": "img2.e2e-route-input", "version": 1, "runId": run_id, "label": label,
              "expectedOutput": kind, "intent": intent, "box": box, "imageSha256": sha256_bytes(raw),
              "role": role, "observations": observations}
    workspace = Path(workspace)
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir():
            raise Refusal("--workspace must be a real directory: %s" % workspace)
        if any(workspace.iterdir()):
            try:
                previous = json.loads((workspace / MARKER).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = None
            if json.dumps(previous, sort_keys=True) != json.dumps(marker, sort_keys=True):
                raise Refusal("--workspace is not empty and was not prepared by this script for "
                              "the same run id, image, label, kind, intent, box, role and observations: %s" % workspace)
            return reference
    if create:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / reference).write_bytes(raw)
        if kind == "3d":
            (workspace / "objects.json").write_text(
                json.dumps(proposals, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (workspace / MARKER).write_text(json.dumps(marker, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return reference


def route_environment(pins):
    env = dict(os.environ)
    env.update(IMG2_HOME=str(pins["img2Home"]), IMG2_HARNESS_DIR=str(pins["harnessRoot"]),
               PATH=str(pins["node"].parent) + os.pathsep + env.get("PATH", ""),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def jev_script(pins):
    return str(pins["jevDir"] / "tools" / "jev.py")


def status_argv(pins, workspace, run_id):
    return [sys.executable, jev_script(pins), "consent", "decision-status",
            "--workspace", str(workspace), "--run-id", run_id]


def consent_argv(pins, workspace, run_id, budget):
    return [sys.executable, jev_script(pins), "consent", "authorize-decisions",
            "--workspace", str(workspace), "--run-id", run_id, "--token-budget", str(budget),
            "--accept-image-disclosure", "--accept-paid-calls"]


def revoke_argv(pins, workspace, run_id):
    return [sys.executable, jev_script(pins), "consent", "revoke-decisions",
            "--workspace", str(workspace), "--run-id", run_id]


def route_argv(pins, workspace, run_id, reference, kind, intent):
    argv = [sys.executable, str(ROUTE_SCRIPT), "--workspace", str(workspace), "--run-id", run_id,
            "--image", reference, "--decider", "jev"]
    if kind == "3d":
        argv += ["--objects", "objects.json"]
    argv += ["--expected-output", kind]
    for item in intent:
        argv += ["--intent", item]
    argv += ["--harness-root", str(pins["harnessRoot"]), "--harness-sha", pins["harnessSha"],
             "--harness-executable", str(pins["harnessExecutable"]),
             "--harness-sha256", pins["harnessExecutableSha256"]]
    return argv


def shell_line(env_values, argv):
    prefix = ["%s=%s" % (key, shlex.quote(value)) for key, value in env_values.items()]
    return " ".join(prefix + [shlex.quote(part) for part in argv])


def last_json(text):
    for line in reversed(text.strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def consent_state(code, stdout, minimum_budget=MIN_TOKEN_BUDGET):
    """(authorized, detail) from ``consent decision-status`` output."""
    value = last_json(stdout)
    if value is None:
        return False, "decision-status printed no JSON (exit %s)" % code
    if code == 0 and value.get("granted") is True:
        budget = value.get("tokenBudget")
        if type(budget) is int and budget >= minimum_budget:
            return True, "granted, token budget %d" % budget
        return False, "granted but token budget %r is below the %d needed for one dispatch" % (
            budget, minimum_budget)
    if code == 0:
        return False, "declined (granted=false)"
    return False, str(value.get("reasonCode") or value.get("reason") or "not authorized")


def read_json_file(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def ledger_tokens(img2_home, workspace, run_id):
    """Tokens charged for this run, from Jev's private budget ledger; None if unreadable.

    Mirrors jev_consent._decision_ledger_path: the file name hashes uid, the
    resolved HOME, the resolved workspace and the run id.
    """
    user = sha256_bytes((str(os.getuid()) + "\0" + str(Path.home().resolve())).encode())
    identity = sha256_bytes((user + sha256_bytes(str(Path(workspace).resolve()).encode("utf-8")) + run_id).encode())
    ledger = read_json_file(Path(img2_home) / "config" / ("jev-decision-run-" + identity + ".json"))
    attempts = ledger.get("attempts") if ledger else None
    if not isinstance(attempts, dict):
        return None
    return sum(row.get("chargedTokens", 0) for row in attempts.values() if isinstance(row, dict))


def summarize(workspace, output, img2_home, run_id):
    """Plain-data summary of a route command output; reads only files the output names."""
    workspace = Path(workspace)
    summary = {"status": output.get("status"), "reasonCode": output.get("reasonCode"),
               "requestedCommand": output.get("requestedCommand"),
               "journal": str(workspace / ".img2threejs/route/journal.jsonl"),
               "decisionRef": output.get("decisionRef"), "owners": [], "deciders": [],
               "records": [], "unresolved": [], "fallbackDecisions": output.get("fallbackDecisions") or [],
               "tokensCharged": ledger_tokens(img2_home, workspace, run_id)}
    reference = output.get("decisionRef")
    match = re.fullmatch(r"\.img2threejs/route/decision-([0-9a-f]{64})\.json", reference or "")
    if match is None:
        return summary
    raw = (workspace / reference).read_bytes() if (workspace / reference).is_file() else b""
    if sha256_bytes(raw) != match.group(1):
        summary["unresolved"].append("decision file missing or does not match its content hash")
        return summary
    decision = json.loads(raw)
    summary["phase"] = decision.get("phase")
    suspended = decision.get("suspendedAt")
    if isinstance(suspended, dict):
        summary["unresolved"].append("suspended at %s: %s" % (suspended.get("step"), suspended.get("reasonCode")))
    inventory = read_json_file(workspace / decision["inventoryRef"]) if decision.get("inventoryRef") else None
    for row in (inventory or {}).get("objects", []):
        summary["owners"].append({"objectId": row.get("objectId"), "ownerRoute": row.get("ownerRoute")})
    for row in decision.get("objects", []):
        if row.get("status") not in {"resolved", "dropped", "merged"}:
            summary["unresolved"].append("object %s: %s %s" % (row.get("objectId"), row.get("status"), row.get("reasonCode") or ""))
        elif len(row.get("selected") or []) != 1 and row.get("status") == "resolved":
            summary["unresolved"].append("object %s resolved with %d selected owners (best-of-N unresolved)" % (
                row.get("objectId"), len(row.get("selected") or [])))
        if row.get("decider") and row["decider"] not in summary["deciders"]:
            summary["deciders"].append(row["decider"])
        for consultation in row.get("consultations") or []:
            record = read_json_file(workspace / consultation["decisionRef"]) if consultation.get("decisionRef") else None
            usage = (record or {}).get("usage") or {}
            summary["records"].append({
                "ref": consultation.get("decisionRef"), "group": (record or {}).get("group"),
                "decider": consultation.get("decider"), "outcome": consultation.get("outcome"),
                "reasonCode": consultation.get("reasonCode"),
                "inputTokens": usage.get("input_tokens"), "outputTokens": usage.get("output_tokens")})
    return summary


def describe_decider(decider):
    if decider is None:
        return "none (no answer was accepted)"
    if decider.startswith("jev:"):
        return "Jev (%s)" % decider[4:]
    if decider.startswith("model:"):
        return "FALLBACK MODEL %s (not Jev; not Jev acceptance)" % decider[6:]
    return decider


def print_summary(summary, out):
    def line(text=""):
        print(text, file=out)
    line("status: %s" % summary["status"])
    if summary.get("reasonCode"):
        line("reason: %s" % summary["reasonCode"])
    if summary.get("requestedCommand"):
        line("requested command: %s" % summary["requestedCommand"])
    for owner in summary["owners"]:
        line("owner route [%s]: %s" % (owner["objectId"], owner["ownerRoute"]))
    if not summary["owners"]:
        line("owner route: none")
    for decider in summary["deciders"] or [None]:
        line("decided by: %s" % describe_decider(decider))
    for fallback in summary["fallbackDecisions"]:
        line("fallback decision: %s" % json.dumps(fallback, sort_keys=True))
    line("journal: %s" % summary["journal"])
    line("decision: %s" % (summary["decisionRef"] or "none"))
    for record in summary["records"]:
        line("decision record: %s group=%s decider=%s outcome=%s tokens in/out=%s/%s" % (
            record["ref"], record["group"], record["decider"], record["outcome"],
            record["inputTokens"], record["outputTokens"]))
    charged = summary["tokensCharged"]
    line("tokens charged: %s" % ("unknown (ledger unreadable)" if charged is None else charged))
    for problem in summary["unresolved"]:
        line("unresolved: %s" % problem)
    line("routing is decision-only: nothing was built or delivered.")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", required=True, type=Path, help="provisioned root (contains provision.json)")
    parser.add_argument("--workspace", required=True, type=Path, help="fresh directory for this run")
    parser.add_argument("--image", required=True, type=Path, help="reference PNG or baseline JPEG")
    parser.add_argument("--label", required=True, help="what the object is: lowercase letters, digits, hyphens")
    parser.add_argument("--kind", required=True, choices=("2d", "3d"), help="expected output kind")
    parser.add_argument("--role", choices=("subject", "prop", "scene-container", "set-dressing"),
                        default="subject", help="object role (default %(default)s)")
    parser.add_argument("--observation", action="append", default=[], metavar="KEY=VALUE",
                        help="object observation; JSON value when possible, otherwise string; repeatable")
    parser.add_argument("--intent", action="append", default=[], help="route intent, e.g. rig; repeatable")
    parser.add_argument("--run-id", required=True, help="run id; consent is bound to it")
    parser.add_argument("--box", help="normalized x0,y0,x1,y1 of the subject (default whole image)")
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET,
                        help="budget printed in the consent command (default %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="print argv arrays; execute nothing")
    parser.add_argument("--route-timeout", type=int, default=ROUTE_TIMEOUT)
    return parser


def main(argv=None, *, runner=subprocess_runner, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        if _LABEL.fullmatch(args.label) is None:
            raise Refusal("--label must match [a-z][a-z0-9-]{0,95}")
        if _RUN_ID.fullmatch(args.run_id) is None:
            raise Refusal("--run-id must match [A-Za-z0-9][A-Za-z0-9_-]{0,95}")
        if args.token_budget < MIN_TOKEN_BUDGET:
            raise Refusal("--token-budget must be at least %d (one dispatch reserves that much)" % MIN_TOKEN_BUDGET)
        if not args.image.is_file():
            raise Refusal("--image is not a file: %s" % args.image)
        box = parse_box(args.box) if args.box else [0, 0, 1, 1]
        observations = parse_observations(args.observation)
        pins = load_provision(args.home)
        workspace = args.workspace.resolve()
        if workspace == pins["img2Home"] or pins["img2Home"] in workspace.parents or workspace in pins["img2Home"].parents:
            raise Refusal("--workspace must be outside the isolated IMG2_HOME")
        reference = prepare_workspace(workspace, args.image, args.label, args.kind, box,
                                      args.run_id, args.intent, role=args.role,
                                      observations=observations, create=not args.dry_run)
    except Refusal as error:
        print("refused: %s" % error, file=out)
        return EXIT_REFUSED
    env = route_environment(pins)
    env_values = {"IMG2_HOME": env["IMG2_HOME"], "IMG2_HARNESS_DIR": env["IMG2_HARNESS_DIR"]}
    commands = [("decision-status", status_argv(pins, workspace, args.run_id)),
                ("consent (HUMAN ONLY, not run here)", consent_argv(pins, workspace, args.run_id, args.token_budget)),
                ("route", route_argv(pins, workspace, args.run_id, reference, args.kind, args.intent))]
    if args.dry_run:
        print("dry run: nothing executed, workspace not created", file=out)
        for name, command in commands:
            print("%s:\n  %s" % (name, json.dumps(command)), file=out)
        return EXIT_OK

    code, stdout, stderr = runner(commands[0][1], env=env, cwd=str(workspace), timeout=PROBE_TIMEOUT)
    authorized, detail = consent_state(code, stdout)
    if not authorized:
        print("consent not authorized for run %s: %s" % (args.run_id, detail), file=out)
        print("No route was run and nothing was spent. Jev receives the image and charges tokens "
              "only after YOU authorize. Review the workspace, then run:", file=out)
        print("\n  %s\n" % shell_line(env_values, commands[1][1]), file=out)
        print("Budget %d tokens covers routing, build-strategy, quality-review and repair together; "
              "each dispatch reserves %d." % (args.token_budget, MIN_TOKEN_BUDGET), file=out)
        print("If Jev reports credential-missing or credential-rejected, run `img2-jev login` yourself.", file=out)
        print("Then re-run this same command to route.", file=out)
        return EXIT_CONSENT
    print("consent authorized: %s" % detail, file=out)

    code, stdout, stderr = runner(commands[2][1], env=env, cwd=str(workspace), timeout=args.route_timeout)
    output = last_json(stdout)
    if output is None or output.get("kind") != "img2.route-command":
        print("route crashed or printed no route result (exit %s)" % code, file=out)
        print("stderr: %s" % stderr.strip()[-2000:], file=out)
        return EXIT_ERROR
    summary = summarize(workspace, output, pins["img2Home"], args.run_id)
    print_summary(summary, out)
    print("revoke when done: %s" % shell_line(env_values, revoke_argv(pins, workspace, args.run_id)), file=out)
    resolved = summary["status"] == "decided" and not summary["unresolved"]
    return EXIT_OK if resolved else EXIT_UNRESOLVED


if __name__ == "__main__":
    raise SystemExit(main())
