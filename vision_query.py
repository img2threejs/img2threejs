#!/usr/bin/env python3
"""
vision_query.py — Send an image + text prompt to a dedicated vision backend.

Intended for use in the img2threejs pipeline when the host AI agent lacks
native vision capability.  Sends the image to a remote OpenAI-compatible
vision model (mimo-v2.5-free primary, agnes-2.0-flash fallback) and returns
the model's analysis as text.

Usage:
    python3 vision_query.py <image_path> "<prompt>"
    python3 vision_query.py <image_path> "<prompt>" --out analysis.json

Exit codes:
    0   Success
    1   Configuration error (missing file, invalid JSON)
    2   Image error (unreadable, unsupported format)
    3   API error (all backends failed)
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

# ── Paths ──────────────────────────────────────────────────────────────────

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SKILL_DIR, "backend-vision.json")


# ── Config loading ─────────────────────────────────────────────────────────

def load_config() -> dict:
    """Read backend-vision.json from the skill directory."""
    if not os.path.isfile(CONFIG_PATH):
        print(f"ERROR: Config not found at {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    # Validate required keys
    for key in ("primary", "request"):
        if key not in cfg:
            print(f"ERROR: Missing required key '{key}' in config", file=sys.stderr)
            sys.exit(1)
    for key in ("baseURL", "apiKey", "models"):
        if key not in cfg["primary"]:
            print(f"ERROR: Missing required key 'primary.{key}' in config", file=sys.stderr)
            sys.exit(1)
    return cfg


# ── Image encoding ─────────────────────────────────────────────────────────

def encode_image(image_path: str) -> str:
    """Read a PNG/JPEG and return a base64 data URI string."""
    ext = os.path.splitext(image_path)[1].lower()
    if ext in (".png",):
        mime = "image/png"
    elif ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext in (".webp",):
        mime = "image/webp"
    else:
        print(f"ERROR: Unsupported image format '{ext}' (use .png, .jpg, or .webp)", file=sys.stderr)
        sys.exit(2)

    try:
        with open(image_path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        print(f"ERROR: Image not found: {image_path}", file=sys.stderr)
        sys.exit(2)
    except PermissionError:
        print(f"ERROR: Permission denied: {image_path}", file=sys.stderr)
        sys.exit(2)

    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ── API call ───────────────────────────────────────────────────────────────

def call_vision_api(
    base_url: str,
    api_key: str,
    model: str,
    data_uri: str,
    prompt: str,
    timeout: int = 120,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    image_detail: str = "high",
) -> str:
    """OpenAI-compatible chat completion with an image payload."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_uri,
                            "detail": image_detail,
                        },
                    },
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        status = e.code
        detail = e.read().decode("utf-8", errors="replace")[:500]
        return f"__ERROR__HTTP_{status}: {detail}"
    except urllib.error.URLError as e:
        return f"__ERROR__NETWORK: {e.reason}"
    except TimeoutError:
        return f"__ERROR__TIMEOUT: request exceeded {timeout}s"

    result = json.loads(resp.read().decode("utf-8"))
    choices = result.get("choices", [])
    if not choices:
        return "__ERROR__NO_CHOICES: API returned empty choices array"

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        return "__ERROR__EMPTY_RESPONSE: model returned no content"

    return content.strip()


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: python3 vision_query.py <image_path> \"<prompt>\" [--out <file>]",
            file=sys.stderr,
        )
        sys.exit(1)

    image_path = sys.argv[1]
    prompt = sys.argv[2]
    out_path = None
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            out_path = sys.argv[idx + 1]

    # 1. Load config
    config = load_config()
    primary = config["primary"]
    fallback = config.get("fallback")
    req_cfg = config.get("request", {})

    # 2. Encode image
    data_uri = encode_image(image_path)

    # 3. Try primary backend
    model_name = primary["models"][0]
    result = call_vision_api(
        base_url=primary["baseURL"],
        api_key=primary["apiKey"],
        model=model_name,
        data_uri=data_uri,
        prompt=prompt,
        timeout=primary.get("timeout", req_cfg.get("timeout", 120)),
        max_tokens=req_cfg.get("maxTokens", 4096),
        temperature=req_cfg.get("temperature", 0.3),
        image_detail=req_cfg.get("imageDetail", "high"),
    )

    # 4. Detect rate-limit or error → try fallback
    used_fallback = False
    if result.startswith("__ERROR__"):
        error_reason = result
        print(f"WARN: Primary backend ({primary['provider']}/{model_name}) failed:",
              error_reason[9:], file=sys.stderr)

        if fallback:
            fallback_model = fallback["models"][0]
            print(f"INFO: Falling back to {fallback['provider']}/{fallback_model} ...",
                  file=sys.stderr)
            result = call_vision_api(
                base_url=fallback["baseURL"],
                api_key=fallback["apiKey"],
                model=fallback_model,
                data_uri=data_uri,
                prompt=prompt,
                timeout=fallback.get("timeout", req_cfg.get("timeout", 120)),
                max_tokens=req_cfg.get("maxTokens", 4096),
                temperature=req_cfg.get("temperature", 0.3),
                image_detail=req_cfg.get("imageDetail", "high"),
            )
            used_fallback = True
            if result.startswith("__ERROR__"):
                print(f"ERROR: Fallback also failed: {result[9:]}", file=sys.stderr)
                print(f"ERROR: All backends exhausted for image: {image_path}", file=sys.stderr)
                sys.exit(3)
        else:
            print("ERROR: No fallback configured and primary failed.", file=sys.stderr)
            sys.exit(3)

    # 5. Output
    output = {
        "source": image_path,
        "prompt": prompt,
        "backend": f"{fallback['provider'] if used_fallback else primary['provider']}",
        "model": f"{fallback['models'][0] if used_fallback else model_name}",
        "usedFallback": used_fallback,
        "analysis": result,
    }

    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        print(f"Analysis written to {out_path}")
    else:
        print(result)


if __name__ == "__main__":
    main()
