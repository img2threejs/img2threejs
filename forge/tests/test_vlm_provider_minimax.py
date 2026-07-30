#!/usr/bin/env python3
"""Tests for the MiniMax VLM sampler. The network boundary is stubbed — no real endpoint,
no API key, no token. Request construction and response parsing are exercised in isolation."""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "stage4_review"))

from vlm_provider_minimax import (  # noqa: E402
    CRITERIA,
    DEFAULT_MODEL,
    MiniMaxConfig,
    MiniMaxSamplerError,
    build_request,
    config_from_env,
    encode_image_data_url,
    make_sampler,
    parse_sample,
)


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _good_scores(claimed: str = "knife") -> dict:
    return {"objectness": 0.9, "semantic": 0.88, "structural": 0.86,
            "specular": 0.85, "claimedClass": claimed}


class MiniMaxConfigTest(unittest.TestCase):
    def test_region_endpoints(self):
        glob = MiniMaxConfig(api_key="k", region="global_en")
        self.assertEqual(glob.endpoint(), "https://api.minimax.io/v1/chat/completions")
        cn = MiniMaxConfig(api_key="k", region="cn_zh")
        self.assertEqual(cn.endpoint(), "https://api.minimaxi.com/v1/chat/completions")

    def test_base_url_override_wins(self):
        cfg = MiniMaxConfig(api_key="k", region="global_en", base_url="https://example.test/v1/")
        self.assertEqual(cfg.endpoint(), "https://example.test/v1/chat/completions")

    def test_unknown_region_raises(self):
        with self.assertRaises(MiniMaxSamplerError):
            MiniMaxConfig(api_key="k", region="mars").endpoint()

    def test_default_model_is_vision_capable(self):
        self.assertEqual(MiniMaxConfig(api_key="k").model, DEFAULT_MODEL)


class ConfigFromEnvTest(unittest.TestCase):
    def test_reads_key_from_env(self):
        import os
        prev = os.environ.get("MINIMAX_API_KEY")
        os.environ["MINIMAX_API_KEY"] = "unit-test-key"
        try:
            cfg = config_from_env(region="cn_zh")
        finally:
            if prev is None:
                del os.environ["MINIMAX_API_KEY"]
            else:
                os.environ["MINIMAX_API_KEY"] = prev
        self.assertEqual(cfg.api_key, "unit-test-key")
        self.assertEqual(cfg.region, "cn_zh")

    def test_missing_key_raises(self):
        with self.assertRaises(MiniMaxSamplerError):
            config_from_env(api_key_env="OCTO_DEFINITELY_UNSET_KEY_ENV")


class BuildRequestTest(unittest.TestCase):
    def test_request_shape(self):
        cfg = MiniMaxConfig(api_key="secret-key", region="global_en", model="MiniMax-M3")
        url, headers, body = build_request(cfg, "data:image/png;base64,AAAA", geometry_class="knife")
        self.assertEqual(url, "https://api.minimax.io/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer secret-key")
        self.assertEqual(headers["Content-Type"], "application/json")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["model"], "MiniMax-M3")
        user = payload["messages"][-1]
        self.assertEqual(user["role"], "user")
        kinds = [part["type"] for part in user["content"]]
        self.assertIn("text", kinds)
        self.assertIn("image_url", kinds)
        image_part = next(p for p in user["content"] if p["type"] == "image_url")
        self.assertEqual(image_part["image_url"]["url"], "data:image/png;base64,AAAA")


class ParseSampleTest(unittest.TestCase):
    def test_flat_keys(self):
        sample = parse_sample(_response(json.dumps(_good_scores())))
        for crit in CRITERIA:
            self.assertIn(crit, sample)
        self.assertEqual(sample["claimedClass"], "knife")

    def test_nested_criteria_and_snake_case_class(self):
        payload = {"criteria": {c: 0.7 for c in CRITERIA}, "claimed_class": "spoon"}
        sample = parse_sample(_response(json.dumps(payload)))
        self.assertEqual(sample["claimedClass"], "spoon")
        self.assertAlmostEqual(sample["objectness"], 0.7)

    def test_scores_are_clamped(self):
        payload = {c: 5.0 for c in CRITERIA}
        payload["structural"] = -3.0
        sample = parse_sample(_response(json.dumps(payload)))
        self.assertEqual(sample["objectness"], 1.0)
        self.assertEqual(sample["structural"], 0.0)

    def test_tolerates_json_fence(self):
        fenced = "```json\n" + json.dumps(_good_scores()) + "\n```"
        sample = parse_sample(_response(fenced))
        self.assertEqual(sample["claimedClass"], "knife")

    def test_missing_criterion_raises(self):
        payload = _good_scores()
        del payload["specular"]
        with self.assertRaises(MiniMaxSamplerError):
            parse_sample(_response(json.dumps(payload)))

    def test_non_json_reply_raises(self):
        with self.assertRaises(MiniMaxSamplerError):
            parse_sample(_response("looks good to me"))

    def test_empty_content_raises(self):
        with self.assertRaises(MiniMaxSamplerError):
            parse_sample({"choices": [{"message": {"content": ""}}]})

    def test_no_choices_raises(self):
        with self.assertRaises(MiniMaxSamplerError):
            parse_sample({"choices": []})


class EncodeImageTest(unittest.TestCase):
    def test_data_url_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "render.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\n_fake_")
            data_url = encode_image_data_url(img)
        self.assertTrue(data_url.startswith("data:image/png;base64,"))
        encoded = data_url.split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), b"\x89PNG\r\n\x1a\n_fake_")


class MakeSamplerTest(unittest.TestCase):
    def test_sampler_uses_injected_transport(self):
        calls = {"n": 0, "url": None, "headers": None}

        def fake_transport(url, headers, body, timeout):
            calls["n"] += 1
            calls["url"] = url
            calls["headers"] = headers
            return _response(json.dumps(_good_scores()))

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "render.png"
            img.write_bytes(b"fake-image-bytes")
            cfg = MiniMaxConfig(api_key="secret-key", region="global_en")
            sampler = make_sampler(cfg, img, geometry_class="knife", transport=fake_transport)
            first = sampler(0)
            second = sampler(1)

        self.assertEqual(calls["n"], 2, "each draw performs one call for self-consistency")
        self.assertEqual(calls["url"], "https://api.minimax.io/v1/chat/completions")
        self.assertEqual(calls["headers"]["Authorization"], "Bearer secret-key")
        self.assertEqual(first["claimedClass"], "knife")
        self.assertEqual(second["objectness"], 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
