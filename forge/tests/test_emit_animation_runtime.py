#!/usr/bin/env python3
"""Tests for the Stage R5 runtime emitter (forge/stage5_rig/emit_animation_runtime.py).

This is a code generator: the correct level to assert on is the EMITTED TEXT,
not a re-implementation of TypeScript semantics in Python. Each test traces to
a specific rule in docs/pipelines/character-rigging-animation-1.5.2.md Stage
R1 / Stage R5, or to one of the Appendix failures — see the docstring on each
test for which one.

Pure Python 3.10+ stdlib.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "stage5_rig"))

from emit_animation_runtime import (  # noqa: E402
    BINDING_EPSILON,
    MINIMUM_GATE_R1_SAMPLES,
    emit_animation_runtime,
)


def _clip(id_: str, label: str = "", source_name: str = "", duration: float = 1.0, loop: bool | None = False) -> dict:
    return {
        "id": id_,
        "label": label or id_,
        "sourceName": source_name or id_,
        "duration": duration,
        "loop": loop,
    }


SAMPLE_CLIPS = [
    _clip("walk-forward", duration=1.133, loop=True),
    _clip("idle-still", duration=2.0, loop=False),
    _clip("mystery-strip", duration=0.75, loop=None),
]


class EmitAnimationRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = emit_animation_runtime(SAMPLE_CLIPS)

    # -- Stage R1: bind space (Appendix failure 1 / failure 3) ---------------

    def test_bind_call_is_identity_always(self) -> None:
        self.assertIn("bind(skeleton, new THREE.Matrix4())", self.source)

    def test_display_offset_uses_bounds_only(self) -> None:
        start = self.source.index("function computeDisplayOffset")
        end = self.source.index("\n}\n", start)
        body = self.source[start:end]
        self.assertIn("centre.x", body)
        self.assertIn("bounds.min.y", body)
        self.assertIn("centre.z", body)
        # Appendix failure 3: armature translation double-counted into the
        # display offset. The offset must come from mesh bounds alone.
        self.assertNotIn("armature.matrixWorld", body)
        self.assertNotIn("armature.position", body)

    def test_bind_at_identity_does_not_read_armature_transform(self) -> None:
        start = self.source.index("export function bindAtIdentity")
        end = self.source.index("\n}\n", start)
        body = self.source[start:end]
        self.assertNotIn("armature.matrixWorld", body)
        self.assertNotIn("armature.position", body)

    def test_appendix_failures_are_documented(self) -> None:
        # The three concrete things that were tried and measured, per the
        # spec's Stage R1 table -- kept as code comments so the mistake isn't
        # repeated silently.
        self.assertIn("blank frame", self.source)
        self.assertIn("0.5", self.source)
        self.assertIn("floating", self.source)
        self.assertIn("armature translation", self.source)

    # -- Stage R5.1: ticker collection vs lazy geometry (Appendix failure 2) -

    def test_refresh_tickers_is_exported_and_not_inlined_into_start(self) -> None:
        self.assertIn("export function refreshTickers", self.source)
        self.assertNotIn("function start(", self.source)

    def test_lazy_character_wiring_calls_refresh(self) -> None:
        self.assertIn("export function onCharacterReady", self.source)
        start = self.source.index("export function onCharacterReady")
        end = self.source.index("\n}\n", start)
        self.assertIn("refreshTickers(", self.source[start:end])

    def test_animation_ui_is_a_mount_function(self) -> None:
        self.assertIn("export function mountAnimationUI(", self.source)
        # Must not run at module scope -- it has to be callable after the
        # controller exists, per Stage R5.1.
        self.assertNotIn("mountAnimationUI();", self.source)

    # -- Stage R5.2: presentation offsets vs animated pose --------------------

    def test_tick_loop_orders_explode_offset_around_ticks(self) -> None:
        start = self.source.index("export function tickAll")
        end = self.source.index("\n}\n", start)
        body = self.source[start:end]
        remove_index = body.index("removeExplodeOffsetHook()")
        loop_index = body.index("for (const tick of tickers)")
        apply_index = body.index("applyExplodeOffsetHook()")
        self.assertLess(remove_index, loop_index)
        self.assertLess(loop_index, apply_index)

    def test_no_cached_rest_pose_restore_in_tick_path(self) -> None:
        start = self.source.index("export function tickAll")
        end = self.source.index("\n}\n", start)
        body = self.source[start:end]
        self.assertNotIn("restoreRestPose", body)
        self.assertNotIn("restoreBindPose", body)
        self.assertNotIn("rest pose", body.lower())

    # -- Stage R5.3: controller contract --------------------------------------

    def test_play_orders_stop_restore_reset_loopmode_play(self) -> None:
        start = self.source.index("  play(name: string): void {")
        end = self.source.index("\n  }\n", start)
        body = self.source[start:end]
        stop_index = body.index("this.mixer.stopAllAction()")
        restore_index = body.index("this.restoreBindPose()")
        reset_index = body.index("action.reset()")
        loop_index = body.index("resolveLoopMode(")
        play_index = body.index("action.play()")
        self.assertLess(stop_index, restore_index)
        self.assertLess(restore_index, reset_index)
        self.assertLess(reset_index, loop_index)
        self.assertLess(loop_index, play_index)

    def test_seek_plays_then_pauses_sets_time_and_updates(self) -> None:
        start = self.source.index("  seek(name: string, t: number): void {")
        end = self.source.index("\n  }\n", start)
        body = self.source[start:end]
        play_call_index = body.index("this.play(name)")
        paused_index = body.index("action.paused = true")
        time_index = body.index("action.time = t")
        update_index = body.index("this.mixer.update(0)")
        matrix_index = body.index("this.root.updateMatrixWorld(true)")
        self.assertLess(play_call_index, paused_index)
        self.assertLess(paused_index, time_index)
        self.assertLess(time_index, update_index)
        self.assertLess(update_index, matrix_index)

    def test_stop_orders_stop_restore_then_idle(self) -> None:
        start = self.source.index("  stop(): void {")
        end = self.source.index("\n  }\n", start)
        body = self.source[start:end]
        stop_index = body.index("this.mixer.stopAllAction()")
        restore_index = body.index("this.restoreBindPose()")
        idle_index = body.index("this.active = 'idle'")
        self.assertLess(stop_index, restore_index)
        self.assertLess(restore_index, idle_index)

    def test_advance_updates_mixer_then_matrix_world(self) -> None:
        start = self.source.index("  advance(dt: number): void {")
        end = self.source.index("\n  }\n", start)
        body = self.source[start:end]
        mixer_index = body.index("this.mixer.update(dt)")
        matrix_index = body.index("this.root.updateMatrixWorld(true)")
        self.assertLess(mixer_index, matrix_index)

    # -- loop mode ------------------------------------------------------------

    def test_loop_true_emits_looprepeat_infinity(self) -> None:
        self.assertIn("clip.loop === true", self.source)
        self.assertIn("THREE.LoopRepeat, repetitions: Infinity", self.source)

    def test_loop_false_emits_looponce_clamped(self) -> None:
        self.assertIn("clip.loop === false", self.source)
        # LoopOnce appears at least twice: once for the false branch, once
        # for the null (unmeasured) branch.
        self.assertGreaterEqual(self.source.count("THREE.LoopOnce"), 2)
        self.assertIn("clampWhenFinished: true", self.source)

    def test_loop_null_emits_looponce_with_not_measured_comment(self) -> None:
        null_branch_start = self.source.index("clip.loop === null")
        # The explanatory comment sits immediately above the null-branch
        # return in resolveLoopMode(); look at a window around it.
        window = self.source[max(0, null_branch_start - 400) : null_branch_start + 200]
        self.assertIn("not measured", window)
        self.assertIn("LoopOnce", window)

    def test_clips_data_round_trips_loop_null_as_json_null(self) -> None:
        # json.dumps(None) -> "null"; must not become the string "None".
        self.assertIn('"loop": null', self.source)
        self.assertNotIn("None", self.source)

    # -- Gate R1 harness --------------------------------------------------------

    def test_binding_epsilon_is_two_to_the_negative_23(self) -> None:
        self.assertEqual(BINDING_EPSILON, 2**-23)
        self.assertTrue(
            "Math.pow(2, -23)" in self.source or "2 ** -23" in self.source,
            "expected BINDING_EPSILON to be emitted as Math.pow(2, -23) or 2 ** -23",
        )
        self.assertIn("export const BINDING_EPSILON", self.source)

    def test_gate_r1_seeks_at_least_five_times_per_clip(self) -> None:
        self.assertIn("export function runGateR1", self.source)
        start = self.source.index("export function runGateR1")
        # Grab the full function through its default-parameter sample count.
        header_end = self.source.index("{", start)
        header = self.source[start:header_end]
        # Default sample count baked into the signature must be >= 5.
        self.assertIn("sampleCount: number = 5", header)
        body_end = self.source.index("\n}\n", header_end)
        body = self.source[header_end:body_end]
        self.assertIn("Math.max(", body)
        self.assertIn("controller.seek(", body)

    def test_gate_sample_count_below_minimum_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            emit_animation_runtime(SAMPLE_CLIPS, {"gate_sample_count": MINIMUM_GATE_R1_SAMPLES - 1})

    def test_gate_r1_docstring_states_its_purpose(self) -> None:
        gate_function_index = self.source.index("export function runGateR1")
        only_check_index = self.source.index("ONLY check")
        # The "this is the ONLY check that ... drives nothing" explanation
        # must sit ahead of (documenting) the harness, and name what it
        # distinguishes: a clip that plays vs. one that merely exists.
        self.assertLess(only_check_index, gate_function_index)
        nearby = self.source[only_check_index : only_check_index + 300]
        self.assertIn("drives", nearby)

    # -- escaping ---------------------------------------------------------------

    def test_special_characters_in_label_are_json_escaped_and_contained(self) -> None:
        tricky_label = 'say "hi"\\like `this`'
        tricky_source_name = 'Nla"Track\\`.003'
        clips = [_clip("weird-clip", label=tricky_label, source_name=tricky_source_name, loop=None)]
        source = emit_animation_runtime(clips)

        # The label/sourceName must appear only inside a properly escaped
        # JSON string -- i.e. the raw backslash-quote sequence from Python's
        # literal must not appear verbatim (it must be doubled/escaped), and
        # the module must still be syntactically balanced around it.
        self.assertIn('\\"hi\\"', source)   # escaped quote survives
        self.assertIn("\\\\like", source)   # escaped backslash survives
        self.assertIn("`this`", source)     # backtick is inert in a "..." string

        # Sanity: the CLIPS array is still well-formed JSON-ish and the
        # following export statement is intact, i.e. nothing broke out of
        # the string literal into the surrounding module.
        clips_start = source.index("export const CLIPS")
        next_export = source.index("export const BINDING_EPSILON")
        clips_block = source[clips_start:next_export]
        self.assertIn('"id": "weird-clip"', clips_block)

    # -- module map / no stray dependency ---------------------------------------

    def test_clips_export_matches_input_ids(self) -> None:
        for clip in SAMPLE_CLIPS:
            self.assertIn(f'"id": "{clip["id"]}"', self.source)

    def test_rejects_clip_missing_required_key(self) -> None:
        with self.assertRaises(ValueError):
            emit_animation_runtime([{"id": "x", "label": "x", "sourceName": "x", "duration": 1.0}])

    def test_rejects_non_boolean_loop(self) -> None:
        with self.assertRaises(ValueError):
            emit_animation_runtime([_clip("x", loop="yes")])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main(verbosity=2)
