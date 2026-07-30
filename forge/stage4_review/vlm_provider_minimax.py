#!/usr/bin/env python3
"""Concrete MiniMax vision-language sampler for the VLM gate (Plan 1.3 §3.4).

`vlm_gate.gate()` consumes an INJECTED `vlm_sampler: Callable[[int], dict]` — a callable
that, given a sample index, returns one per-criterion opinion dict:

    {"objectness": float, "semantic": float, "structural": float, "specular": float,
     "claimedClass": str}

Until now the only sampler shipped was offline sample replay (`vlm_gate --samples`). This
module supplies the real thing: it looks at the render with a MiniMax vision-language model,
builds the request, calls the endpoint, and parses the reply back into the same criteria dict
the deterministic layer already understands. Nothing about the gate's rules changes — this
only fills in where a live model opinion comes from.

Design mirrors the rest of the repo: pure standard library (`urllib`, `base64`, `json`), and
the network boundary is itself INJECTED as `transport`, so request construction and response
parsing are unit-testable with a stub — no real endpoint, no key, no token in tests. The API
key is never a constant; it is read from the environment (default ``MINIMAX_API_KEY``).

The API is OpenAI-compatible: a chat-completions POST whose user turn carries the image inline
as a data URL. Two regional endpoints are supported (global and mainland base URLs); the
caller picks one by region name.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROVIDER_NAME = "MiniMax"

# The vision-language model: it is the only model here that accepts image input.
DEFAULT_MODEL = "MiniMax-M3"

# Regional OpenAI-compatible base URLs. Chat completions live under ``/chat/completions``.
REGION_BASE_URLS: dict[str, str] = {
    "global_en": "https://api.minimax.io/v1",
    "cn_zh": "https://api.minimaxi.com/v1",
}
DEFAULT_REGION = "global_en"
CHAT_COMPLETIONS_PATH = "/chat/completions"

DEFAULT_API_KEY_ENV = "MINIMAX_API_KEY"
DEFAULT_TIMEOUT = 60
# Slightly warm sampling so repeated draws vary — self-consistency voting (§3.4 rule 2) needs
# independent opinions, not the same deterministic answer N times.
DEFAULT_TEMPERATURE = 0.4
DEFAULT_MAX_TOKENS = 512

# Transient HTTP statuses worth a retry (matches the repo's other network clients).
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})

# The four criteria the gate scores, in the order the deterministic layer expects.
CRITERIA = ("objectness", "semantic", "structural", "specular")

SYSTEM_PROMPT = (
    "You are a strict, calibrated 3D reconstruction reviewer. You compare a rendered candidate "
    "object against how a real instance of that object should look. Judge only what is visible; "
    "never invent detail. You are deliberately conservative: reserve high scores for renders that "
    "are genuinely faithful."
)

# What each criterion means, so the model scores the same axes the deterministic ensemble uses.
CRITERIA_RUBRIC = (
    "Score each criterion in [0.0, 1.0]:\n"
    "- objectness: is this clearly a coherent, complete object rather than noise or fragments?\n"
    "- semantic: does it read as the intended object class (correct identity and defining features)?\n"
    "- structural: are proportions, topology, and part layout correct and undistorted?\n"
    "- specular: do materials/highlights react to light plausibly (not flat, not blown out)?\n"
    'Also report "claimedClass": the single object class you actually see (a short lowercase noun).'
)


class MiniMaxSamplerError(RuntimeError):
    """Raised when a MiniMax sampler cannot be built or a response cannot be parsed."""


@dataclass(frozen=True, slots=True)
class MiniMaxConfig:
    """Everything needed to turn one render into one MiniMax criteria opinion."""

    api_key: str
    region: str = DEFAULT_REGION
    model: str = DEFAULT_MODEL
    base_url: str | None = None  # explicit override wins over the region lookup
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: int = DEFAULT_TIMEOUT

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        try:
            return REGION_BASE_URLS[self.region].rstrip("/")
        except KeyError as exc:
            known = ", ".join(sorted(REGION_BASE_URLS))
            raise MiniMaxSamplerError(
                f"unknown region {self.region!r}; known regions: {known}"
            ) from exc

    def endpoint(self) -> str:
        return f"{self.resolved_base_url()}{CHAT_COMPLETIONS_PATH}"


def config_from_env(
    *,
    region: str = DEFAULT_REGION,
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT,
) -> MiniMaxConfig:
    """Build a config, reading the API key from the environment (never hard-coded)."""
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise MiniMaxSamplerError(
            f"missing API key: set the {api_key_env} environment variable"
        )
    return MiniMaxConfig(
        api_key=api_key,
        region=region,
        model=model,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def encode_image_data_url(image_path: str | Path) -> str:
    """Read an image file and return an inline ``data:`` URL (base64) for the request."""
    path = Path(image_path).expanduser()
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_user_prompt(geometry_class: str | None = None) -> str:
    prompt = (
        "Evaluate the rendered object in the image.\n"
        f"{CRITERIA_RUBRIC}\n"
        'Respond with ONLY a JSON object with keys "objectness", "semantic", "structural", '
        '"specular", and "claimedClass". No prose, no markdown fences.'
    )
    if geometry_class:
        prompt += (
            f"\nThe deterministic geometry descriptor believes this is a {geometry_class!r}; "
            'report what you actually see in "claimedClass" regardless.'
        )
    return prompt


def build_request(
    config: MiniMaxConfig,
    image_data_url: str,
    geometry_class: str | None = None,
) -> tuple[str, dict[str, str], bytes]:
    """Construct the (url, headers, body) triple for one chat-completions call."""
    body = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_user_prompt(geometry_class)},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    return config.endpoint(), headers, json.dumps(body).encode("utf-8")


def _clamp_unit(value: Any) -> float:
    """Coerce a model-supplied score into a float clamped to [0.0, 1.0]."""
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise MiniMaxSamplerError(f"criterion score is not numeric: {value!r}") from exc
    return max(0.0, min(1.0, score))


def _extract_content(response: dict[str, Any]) -> str:
    """Pull the assistant text out of an OpenAI-compatible chat-completions response."""
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MiniMaxSamplerError("response has no choices[0].message") from exc
    content = message.get("content")
    if isinstance(content, list):  # some responses split text into parts
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise MiniMaxSamplerError("response message content is empty")
    return content


def _strip_json_fence(text: str) -> str:
    """Tolerate a triple-backtick json fence the model may wrap the object in."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def parse_sample(response: dict[str, Any]) -> dict[str, Any]:
    """Turn one raw chat-completions response into the gate's criteria opinion dict."""
    content = _strip_json_fence(_extract_content(response))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MiniMaxSamplerError(f"model reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise MiniMaxSamplerError("model reply JSON must be an object")
    # Accept either flat keys or a nested "criteria" object.
    scores = parsed.get("criteria") if isinstance(parsed.get("criteria"), dict) else parsed
    sample: dict[str, Any] = {}
    for crit in CRITERIA:
        if crit not in scores:
            raise MiniMaxSamplerError(f"model reply is missing criterion {crit!r}")
        sample[crit] = _clamp_unit(scores[crit])
    claimed = parsed.get("claimedClass") or parsed.get("claimed_class") or parsed.get("class")
    if claimed is not None:
        sample["claimedClass"] = str(claimed).strip()
    return sample


def _http_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: int
) -> dict[str, Any]:
    """Default network boundary: POST JSON with Bearer auth and transient-status retry."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_STATUSES or attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable retry loop")


# A transport takes (url, headers, body, timeout) and returns the parsed JSON response.
Transport = Callable[[str, dict, bytes, int], dict]


def make_sampler(
    config: MiniMaxConfig,
    image_path: str | Path,
    *,
    geometry_class: str | None = None,
    transport: Transport | None = None,
) -> Callable[[int], dict]:
    """Build a `vlm_sampler(i)` callable suitable for `vlm_gate.gate`.

    The image is encoded once; each call performs one independent draw so the gate's
    self-consistency voting sees genuinely separate opinions. `transport` is injectable so
    tests exercise request/response handling without any network access.
    """
    send: Transport = transport or _http_transport
    data_url = encode_image_data_url(image_path)
    url, headers, body = build_request(config, data_url, geometry_class)

    def sampler(_index: int) -> dict:
        response = send(url, headers, body, config.timeout)
        return parse_sample(response)

    return sampler
