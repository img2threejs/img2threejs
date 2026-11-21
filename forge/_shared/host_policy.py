"""Load explicitly host-approved Python policy code, never candidate authority.

Only this module's bytes are pinned. Its interpreter, imports and dependencies
are trusted host code, not a Python sandbox or inferred source authorization.
"""
from contextlib import contextmanager
from pathlib import Path
import re
import sys
import types

from decision_control import DecisionError
from jev_cli_bridge import _file


@contextmanager
def pinned_policy_module(module_path, sha256, *, workspace, excluded_roots=()):
    path = Path(module_path)
    try:
        if (not path.is_absolute() or path.resolve(strict=True) != path or path.is_symlink()
                or path.suffix != ".py" or not path.is_file()
                or any(path == Path(root) or Path(root) in path.parents
                       for root in (workspace, *excluded_roots))):
            raise DecisionError("policy-module-unsafe")
    except (OSError, RuntimeError):
        raise DecisionError("policy-module-unsafe") from None
    if type(sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        raise DecisionError("policy-pin-required")

    def pin():
        return _file(path.parent, path.name, maximum=1024 * 1024, expected=sha256, capture=True)

    raw = pin()
    name = "img2_host_policy_" + sha256
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
        pin()
        yield module, pin
        pin()
    finally:
        sys.modules.pop(name, None)
