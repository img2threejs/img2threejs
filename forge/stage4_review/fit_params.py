#!/usr/bin/env python3
"""Deterministic bounded analysis-by-synthesis fitting."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

if __package__:
    from ._fit_divine_eye import is_approved_divine_eye_result, normalize_history
else:
    from _fit_divine_eye import is_approved_divine_eye_result, normalize_history

MAX_DIMENSIONS: Final = 15
FitStatus = Literal["max-iterations", "max-evaluations", "plateau", "oscillation"]
ParameterVector = tuple[float, ...]
Bounds = tuple[tuple[float, float], ...]
Objective = Callable[[ParameterVector], float]
RenderForParameters = Callable[[ParameterVector], str | Path]
DivineEyeEvaluator = Callable[[Path, Path], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class FitInputError(ValueError):
    field: str
    detail: str

    def __str__(self) -> str:
        return f"invalid fit input {self.field}: {self.detail}"


@dataclass(frozen=True, slots=True)
class NonFiniteScoreError(ValueError):
    value_repr: str

    def __str__(self) -> str:
        return f"objective returned non-finite score {self.value_repr}"


@dataclass(frozen=True, slots=True)
class FitConfig:
    max_iterations: int = 40
    max_evaluations: int = 500
    min_improvement: float = 1e-9
    plateau_iterations: int = 3
    oscillation_flips: int = 2
    seed: int | None = None


@dataclass(frozen=True, slots=True)
class FitTelemetry:
    iteration: int
    evaluations: int
    best_score: float
    improved: bool
    step_sizes: ParameterVector


@dataclass(frozen=True, slots=True)
class FitResult:
    parameters: ParameterVector
    best_score: float
    status: FitStatus
    iterations: int
    evaluations: int
    seed: int | None
    history: tuple[FitTelemetry, ...]

    def to_correction_history(self, defect_tags: Sequence[str] = ()) -> list[dict[str, float | bool | list[str]]]:
        tags = list(defect_tags)
        for index, record in enumerate(self.history):
            if not 0.0 <= record.best_score <= 1.0:
                raise FitInputError(f"history[{index}].best_score", "must be within [0, 1] for fidelity")
        return [
            {"fidelity": record.best_score, "defectTags": tags.copy(), "reverted": False}
            for record in self.history
        ]

    def to_json(self) -> dict[str, object]:
        return {
            "parameters": list(self.parameters),
            "bestScore": self.best_score,
            "bestObjectiveScore": self.best_score,
            "status": self.status,
            "iterations": self.iterations,
            "evaluations": self.evaluations,
            "seed": self.seed,
            "history": [
                {
                    "iteration": record.iteration,
                    "evaluations": record.evaluations,
                    "bestScore": record.best_score,
                    "bestObjectiveScore": record.best_score,
                    "improved": record.improved,
                    "stepSizes": list(record.step_sizes),
                }
                for record in self.history
            ],
        }


@dataclass(frozen=True, slots=True)
class DivineEyeFitResult:
    fit_result: FitResult
    best_raw_fidelity: float | None
    divine_eye_results: tuple[dict[str, object], ...]
    correction_history: tuple[dict[str, object], ...]

    def to_json(self) -> dict[str, object]:
        return {
            "fitResult": self.fit_result.to_json(),
            "bestObjectiveScore": self.fit_result.best_score,
            "bestRawFidelity": self.best_raw_fidelity,
            "divineEyeResults": deepcopy(self.divine_eye_results),
            "correctionHistory": deepcopy(self.correction_history),
        }


def divine_eye_fidelity(result: Mapping[str, object]) -> float:
    if not isinstance(result, Mapping):
        raise FitInputError("Divine Eye result", "must be a mapping")
    score = result.get("fidelity")
    if score is None:
        raise FitInputError("Divine Eye result", "missing fidelity")
    if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise FitInputError("Divine Eye result.fidelity", "must be a finite number in [0, 1]")
    return float(score)


def divine_eye_correction_history(
    results: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Normalize unmodified Divine Eye results into correction-loop history."""
    return normalize_history(results, divine_eye_fidelity, FitInputError)


def _divine_eye_objective_score(result: Mapping[str, object]) -> float:
    fidelity = divine_eye_fidelity(result)
    return fidelity if is_approved_divine_eye_result(result, FitInputError, "Divine Eye result") else -1.0


def fit_against_divine_eye(
    initial: Sequence[float],
    bounds: Sequence[Sequence[float]],
    render_for_parameters: RenderForParameters,
    reference_png: str | Path,
    evaluator: DivineEyeEvaluator | None = None,
    config: FitConfig = FitConfig(),
) -> DivineEyeFitResult:
    if not callable(render_for_parameters):
        raise FitInputError("render_for_parameters", "must be callable")
    evaluator = _default_divine_eye_evaluator if evaluator is None else evaluator
    if not callable(evaluator):
        raise FitInputError("evaluator", "must be callable")
    reference_path = Path(reference_png)
    results: list[Mapping[str, object]] = []

    def objective(parameters: ParameterVector) -> float:
        render_path = render_for_parameters(parameters)
        evaluated = evaluator(reference_path, Path(render_path))
        if not isinstance(evaluated, Mapping):
            raise FitInputError("Divine Eye result", "must be a mapping")
        result = deepcopy(dict(evaluated))
        result.setdefault("hardGateFailures", [])
        result["fitCandidateParameters"] = list(parameters)
        result["fitReferencePng"] = str(reference_path)
        result["fitRenderPath"] = str(render_path)
        results.append(result)
        return _divine_eye_objective_score(result)

    fit_result = fit(initial, bounds, objective, config)
    history = divine_eye_correction_history(results)
    best_raw_fidelity = _selected_raw_fidelity(fit_result.parameters, results)
    return DivineEyeFitResult(fit_result, best_raw_fidelity, tuple(deepcopy(dict(result)) for result in results), tuple(history))


def _selected_raw_fidelity(parameters: ParameterVector, results: Sequence[Mapping[str, object]]) -> float | None:
    candidate = list(parameters)
    scores = [divine_eye_fidelity(result) for result in results if result["fitCandidateParameters"] == candidate and is_approved_divine_eye_result(result, FitInputError, "Divine Eye result")]
    return max(scores, default=None)


def _default_divine_eye_evaluator(reference_png: Path, render_png: Path) -> Mapping[str, object]:
    if __package__:
        from .divine_eye import evaluate
    else:
        from divine_eye import evaluate
    return evaluate(reference_png, render_png)


def fit(initial: Sequence[float], bounds: Sequence[Sequence[float]], objective: Objective, config: FitConfig = FitConfig()) -> FitResult:
    if not callable(objective):
        raise FitInputError("objective", "must be callable")
    parameters, normalized_bounds = _normalize_inputs(initial, bounds)
    _validate_config(config)
    current_score = _finite_score(objective(parameters))
    evaluations = 1
    step_sizes = tuple((upper - lower) / 4.0 for lower, upper in normalized_bounds)
    history: list[FitTelemetry] = [FitTelemetry(0, evaluations, current_score, False, step_sizes)]
    plateau_count = 0
    direction_by_coordinate: dict[int, int] = {}
    direction_flips = 0
    for iteration in range(1, config.max_iterations + 1):
        iteration_start = current_score
        improved = False
        iteration_directions: dict[int, int] = {}
        for index, (lower, upper) in enumerate(normalized_bounds):
            coordinate_start = parameters[index]
            candidate_parameters = parameters
            candidate_score = current_score
            candidate_direction = 0
            for direction in (-1, 1):
                value = min(upper, max(lower, coordinate_start + direction * step_sizes[index]))
                if value == coordinate_start:
                    continue
                if evaluations >= config.max_evaluations:
                    if candidate_direction:
                        parameters, current_score = candidate_parameters, candidate_score
                        history.append(FitTelemetry(iteration, evaluations, current_score, True, step_sizes))
                        return _result(parameters, current_score, "max-evaluations", iteration, evaluations, config, history)
                    return _result(parameters, current_score, "max-evaluations", iteration - 1, evaluations, config, history)
                proposal = _replace_coordinate(parameters, index, value)
                proposal_score = _finite_score(objective(proposal))
                evaluations += 1
                if proposal_score > candidate_score:
                    candidate_parameters = proposal
                    candidate_score = proposal_score
                    candidate_direction = direction
            if candidate_direction:
                iteration_directions[index] = candidate_direction
                parameters = candidate_parameters
                current_score = candidate_score
                improved = True
        net_gain = current_score - iteration_start
        if net_gain < config.min_improvement:
            plateau_count += 1
            flips = sum(direction_by_coordinate.get(index) not in (None, direction) for index, direction in iteration_directions.items())
            direction_flips = direction_flips + 1 if flips else 0
            direction_by_coordinate = iteration_directions
        else:
            plateau_count = direction_flips = 0
            direction_by_coordinate = {}
        history.append(FitTelemetry(iteration, evaluations, current_score, improved, step_sizes))
        if direction_flips >= config.oscillation_flips:
            return _result(parameters, current_score, "oscillation", iteration, evaluations, config, history)
        if plateau_count >= config.plateau_iterations:
            return _result(parameters, current_score, "plateau", iteration, evaluations, config, history)
        if not improved:
            step_sizes = tuple(step / 2.0 for step in step_sizes)
    return _result(parameters, current_score, "max-iterations", config.max_iterations, evaluations, config, history)


def _result(
    parameters: ParameterVector,
    best_score: float,
    status: FitStatus,
    iterations: int,
    evaluations: int,
    config: FitConfig,
    history: list[FitTelemetry],
) -> FitResult:
    return FitResult(parameters, best_score, status, iterations, evaluations, config.seed, tuple(history))


def _normalize_inputs(initial: Sequence[float], bounds: Sequence[Sequence[float]]) -> tuple[ParameterVector, Bounds]:
    if isinstance(initial, str | bytes) or not isinstance(initial, Sequence):
        raise FitInputError("initial", "must be a sequence of parameters")
    if isinstance(bounds, str | bytes) or not isinstance(bounds, Sequence):
        raise FitInputError("bounds", "must be a sequence of bound pairs")
    parameters = tuple(_finite_parameter(value, f"initial[{index}]") for index, value in enumerate(initial))
    if not 1 <= len(parameters) <= MAX_DIMENSIONS:
        raise FitInputError("initial", f"must contain 1..{MAX_DIMENSIONS} parameters")
    if len(bounds) != len(parameters):
        raise FitInputError("bounds", "must have one pair per parameter")
    normalized_bounds: list[tuple[float, float]] = []
    for index, pair in enumerate(bounds):
        if isinstance(pair, str | bytes) or not isinstance(pair, Sequence):
            raise FitInputError(f"bounds[{index}]", "must be a two-value sequence")
        if len(pair) != 2:
            raise FitInputError(f"bounds[{index}]", "must contain [lower, upper]")
        lower = _finite_parameter(pair[0], f"bounds[{index}][0]")
        upper = _finite_parameter(pair[1], f"bounds[{index}][1]")
        if lower >= upper:
            raise FitInputError(f"bounds[{index}]", "lower must be smaller than upper")
        if not lower <= parameters[index] <= upper:
            raise FitInputError(f"initial[{index}]", "must be within its bounds")
        normalized_bounds.append((lower, upper))
    return parameters, tuple(normalized_bounds)


def _finite_parameter(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise FitInputError(field, "must be a finite non-boolean number")
    return float(value)


def _finite_score(score: float) -> float:
    if isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score):
        raise NonFiniteScoreError(repr(score))
    return float(score)


def _replace_coordinate(values: ParameterVector, index: int, value: float) -> ParameterVector:
    return tuple(value if position == index else current for position, current in enumerate(values))


def _validate_config(config: FitConfig) -> None:
    if not isinstance(config, FitConfig):
        raise FitInputError("config", "must be a FitConfig")
    if not all(_is_positive_integer(value) for value in (config.max_iterations, config.max_evaluations, config.plateau_iterations, config.oscillation_flips)):
        raise FitInputError("config", "iteration, evaluation, plateau, and oscillation limits must be positive")
    if (
        isinstance(config.min_improvement, bool)
        or not isinstance(config.min_improvement, int | float)
        or config.min_improvement < 0.0
        or not math.isfinite(config.min_improvement)
    ):
        raise FitInputError("config.min_improvement", "must be a finite non-negative number")
    if config.seed is not None and (isinstance(config.seed, bool) or not isinstance(config.seed, int)):
        raise FitInputError("config.seed", "must be an integer or null")


def _is_positive_integer(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _quadratic_objective(target: ParameterVector) -> Objective:
    return lambda values: 1.0 - sum((value - target[index]) ** 2 for index, value in enumerate(values))


def _cli_config(raw: Mapping[str, object]) -> FitConfig:
    names = {"maxIterations": "max_iterations", "maxEvaluations": "max_evaluations", "minImprovement": "min_improvement", "plateauIterations": "plateau_iterations", "oscillationFlips": "oscillation_flips", "seed": "seed"}
    unknown = sorted(set(raw) - set(names))
    if unknown:
        raise FitInputError("config", f"unknown key(s): {', '.join(unknown)}")
    values = {names[key]: value for key, value in raw.items() if key in names}
    return FitConfig(**values)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="quadratic objective JSON input")
    parser.add_argument("--json", action="store_true", help="emit JSON result")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise FitInputError("input", "must be a JSON object")
        expected = {"initial", "bounds", "target", "config"}
        if set(raw) != expected:
            raise FitInputError("input", f"must contain exactly {sorted(expected)}; missing={sorted(expected - set(raw))} unknown={sorted(set(raw) - expected)}")
        initial = raw.get("initial")
        bounds = raw.get("bounds")
        target = raw.get("target")
        config_raw = raw.get("config")
        if not isinstance(initial, list) or not isinstance(bounds, list) or not isinstance(target, list):
            raise FitInputError("input", "initial, bounds, and target must be arrays")
        if not isinstance(config_raw, dict):
            raise FitInputError("config", "must be an object")
        target_parameters, _ = _normalize_inputs(target, bounds)
        result = fit(initial, bounds, _quadratic_objective(target_parameters), _cli_config(config_raw))
    except (FitInputError, NonFiniteScoreError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    payload = result.to_json()
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{payload['status']} score={payload['bestScore']} evaluations={payload['evaluations']}")
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
