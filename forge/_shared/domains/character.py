"""Character domain profile.

Converted to a provider in the same change as CS2, deliberately: an extension point whose only
consumer is one hardcoded in-repo domain is a rename, not an abstraction. Character stays in-repo --
it is not being extracted -- but it reaches the pipeline through the same registry a plugin uses, so
the seam has two consumers from the day it exists.
"""

from __future__ import annotations

from typing import Any, Final

DOMAIN: Final[dict[str, Any]] = {
    "id": "character",
    "setupSteps": (
        (
            "character-contract-read",
            "Read grimoire/character/reconstruction.md and grimoire/character/likeness_maximization.md completely",
        ),
        (
            "character-landmarks",
            "python3 forge/stage1_intake/extract_landmarks.py {reference} --out anatomy.json --overlay landmarks.png",
        ),
    ),
    "setupAnchorBefore": "local-spec-search",
}
