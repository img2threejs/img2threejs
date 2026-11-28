"""Fixed child environment for reviewed local producers, never ambient credentials."""
import os

from forge._shared.runtime_selectors import ENV_KEYS

_ALLOWED = ("HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
            "SYSTEMROOT", "WINDIR", "PATH", "IMG2_HOME", "IMG2_HARNESS_DIR", *ENV_KEYS)


def child_environment(source=None):
    source = os.environ if source is None else source
    env = {key: source[key] for key in _ALLOWED if key in source}
    env.setdefault("PATH", os.defpath)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return env
