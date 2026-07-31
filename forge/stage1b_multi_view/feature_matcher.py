"""
Feature Matcher Module

Matches features across multiple views.
Supports brute-force matching with ratio test.
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import math

from .feature_detector import Feature, FeatureSet


@dataclass
class FeatureMatch:
    """Represents a match between two features."""
    feature1: Feature
    feature2: Feature
    distance: float
    confidence: float


@dataclass
class MatchResult:
    """Result of matching features between two views."""
    view1: str
    view2: str
    matches: List[FeatureMatch]
    match_count: int
    confidence: float


def match_features(
    all_features: Dict[str, FeatureSet],
    ratio_threshold: float = 0.75,
) -> Dict[Tuple[str, str], MatchResult]:
    """
    Match features across all view pairs.

    Args:
        all_features: Dictionary mapping view names to FeatureSet
        ratio_threshold: Ratio test threshold for good matches

    Returns:
        Dictionary mapping view pairs to MatchResult
    """
    results = {}
    view_names = list(all_features.keys())

    # Match each pair of views
    for i in range(len(view_names)):
        for j in range(i + 1, len(view_names)):
            view1 = view_names[i]
            view2 = view_names[j]

            features1 = all_features[view1]
            features2 = all_features[view2]

            match_result = match_pair(features1, features2, ratio_threshold)
            results[(view1, view2)] = match_result

    return results


def match_pair(
    features1: FeatureSet,
    features2: FeatureSet,
    ratio_threshold: float = 0.75,
) -> MatchResult:
    """
    Match features between two views.

    Args:
        features1: Features from first view
        features2: Features from second view
        ratio_threshold: Ratio test threshold

    Returns:
        MatchResult with matches
    """
    matches = []

    has_descriptors = all(feature.descriptor is not None for feature in features1.features) and all(
        feature.descriptor is not None for feature in features2.features
    )
    if has_descriptors:
        try:
            matches = _match_opencv(features1, features2, ratio_threshold)
        except ImportError:
            matches = _match_simple(features1, features2, ratio_threshold)
    else:
        matches = _match_simple(features1, features2, ratio_threshold)

    # Calculate confidence based on match count and quality
    confidence = _calculate_match_confidence(matches, features1, features2)

    return MatchResult(
        view1=features1.image_path.stem,
        view2=features2.image_path.stem,
        matches=matches,
        match_count=len(matches),
        confidence=confidence,
    )


def _match_opencv(
    features1: FeatureSet,
    features2: FeatureSet,
    ratio_threshold: float,
) -> List[FeatureMatch]:
    """
    Match features using OpenCV.

    Args:
        features1: Features from first view
        features2: Features from second view
        ratio_threshold: Ratio test threshold

    Returns:
        List of FeatureMatch objects
    """
    import cv2
    import numpy as np

    # Convert features to keypoints and descriptors
    kp1 = []
    desc1 = []
    for f in features1.features:
        kp1.append(cv2.KeyPoint(f.x, f.y, 1, f.angle))
        if f.descriptor:
            desc1.append(f.descriptor)

    kp2 = []
    desc2 = []
    for f in features2.features:
        kp2.append(cv2.KeyPoint(f.x, f.y, 1, f.angle))
        if f.descriptor:
            desc2.append(f.descriptor)

    if not desc1 or not desc2:
        return []

    descriptor_dtype = (
        np.float32
        if features1.detection_method == "sift" and features2.detection_method == "sift"
        else np.uint8
    )
    matcher_norm = (
        cv2.NORM_L2 if descriptor_dtype is np.float32 else cv2.NORM_HAMMING
    )
    desc1 = np.array(desc1, dtype=descriptor_dtype)
    desc2 = np.array(desc2, dtype=descriptor_dtype)

    # Use BFMatcher with ratio test
    bf = cv2.BFMatcher(matcher_norm)
    raw_matches = bf.knnMatch(desc1, desc2, k=2)

    # Apply ratio test
    good_matches = []
    for m, n in raw_matches:
        if m.distance < ratio_threshold * n.distance:
            good_matches.append(m)

    # Convert to FeatureMatch objects
    matches = []
    for m in good_matches:
        match = FeatureMatch(
            feature1=features1.features[m.queryIdx],
            feature2=features2.features[m.trainIdx],
            distance=m.distance,
            confidence=max(0.0, min(1.0, 1.0 - (m.distance / 512.0))),
        )
        matches.append(match)

    return matches


def _match_simple(
    features1: FeatureSet,
    features2: FeatureSet,
    ratio_threshold: float,
) -> List[FeatureMatch]:
    """
    Simple distance-based matching (no OpenCV dependency).

    Args:
        features1: Features from first view
        features2: Features from second view
        ratio_threshold: Ratio test threshold

    Returns:
        List of FeatureMatch objects
    """
    matches = []

    for f1 in features1.features:
        # Find closest match in features2
        best_match = None
        best_distance = float("inf")
        second_best_distance = float("inf")

        for f2 in features2.features:
            distance = _feature_distance(f1, f2)
            if distance < best_distance:
                second_best_distance = best_distance
                best_distance = distance
                best_match = f2
            elif distance < second_best_distance:
                second_best_distance = distance

        # Apply ratio test
        if best_match and second_best_distance > 0:
            ratio = best_distance / second_best_distance
            if ratio < ratio_threshold:
                match = FeatureMatch(
                    feature1=f1,
                    feature2=best_match,
                    distance=best_distance,
                    confidence=1.0 - ratio,
                )
                matches.append(match)

    return matches


def _feature_distance(f1: Feature, f2: Feature) -> float:
    """
    Calculate distance between two features.

    Args:
        f1: First feature
        f2: Second feature

    Returns:
        Distance value (lower is better)
    """
    # Simple Euclidean distance based on position
    dx = f1.x - f2.x
    dy = f1.y - f2.y
    return math.sqrt(dx * dx + dy * dy)


def _calculate_match_confidence(
    matches: List[FeatureMatch],
    features1: FeatureSet,
    features2: FeatureSet,
) -> float:
    """
    Calculate confidence score for matches.

    Args:
        matches: List of matches
        features1: Features from first view
        features2: Features from second view

    Returns:
        Confidence score between 0 and 1
    """
    if not matches:
        return 0.0

    # Base confidence on match count relative to feature count
    min_features = min(features1.feature_count, features2.feature_count)
    if min_features == 0:
        return 0.0

    match_ratio = len(matches) / min_features

    # Average match confidence
    avg_confidence = sum(m.confidence for m in matches) / len(matches)

    # Combine factors
    confidence = match_ratio * avg_confidence

    return max(0.0, min(1.0, confidence))
