"""
Depth Estimator Module

Estimates depth from parallax using matched features and relative poses.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import math

from .feature_matcher import MatchResult
from .pose_estimator import PoseEstimationResult, CameraPose


@dataclass
class DepthPoint:
    """Represents a 3D point with depth information."""
    x: float
    y: float
    z: float
    confidence: float
    view_name: str


@dataclass
class DepthMap:
    """Depth map for a single view."""
    points: List[DepthPoint]
    width: int
    height: int
    view_name: str
    confidence: float


@dataclass
class DepthEstimationResult:
    """Result of depth estimation."""
    depth_maps: Dict[str, DepthMap]
    point_cloud: List[DepthPoint]
    confidence: float


def estimate_depth(
    match_results: Dict[Tuple[str, str], MatchResult],
    pose_estimation: PoseEstimationResult,
    focal_length_px: Optional[float] = None,
    baseline_units: Optional[float] = None,
) -> DepthEstimationResult:
    """
    Estimate depth from matched features and poses.

    Args:
        match_results: Dictionary mapping view pairs to MatchResult
        pose_estimation: PoseEstimationResult with estimated poses

    Returns:
        DepthEstimationResult with depth maps and point cloud
    """
    depth_maps = {}
    point_cloud = []

    # Process each matched pair
    for (view1, view2), match_result in match_results.items():
        if match_result.match_count < 3:
            continue

        # Get relative pose
        relative_pose = pose_estimation.relative_poses.get((view1, view2))
        if not relative_pose or not relative_pose.calibrated:
            continue
        if focal_length_px is None or baseline_units is None:
            continue

        # Estimate depth for this pair
        pair_depths = _estimate_pair_depth(
            match_result,
            relative_pose,
            focal_length_px,
            baseline_units,
        )
        point_cloud.extend(pair_depths)

    # Group depth points by view
    for point in point_cloud:
        if point.view_name not in depth_maps:
            depth_maps[point.view_name] = DepthMap(
                points=[],
                width=0,
                height=0,
                view_name=point.view_name,
                confidence=0.0,
            )
        depth_maps[point.view_name].points.append(point)

    for depth_map in depth_maps.values():
        point_confidences = [point.confidence for point in depth_map.points]
        depth_map.confidence = (
            sum(point_confidences) / len(point_confidences)
            if point_confidences
            else 0.0
        )

    # Calculate confidence
    confidence = _calculate_depth_confidence(depth_maps)

    return DepthEstimationResult(
        depth_maps=depth_maps,
        point_cloud=point_cloud,
        confidence=confidence,
    )


def _estimate_pair_depth(
    match_result: MatchResult,
    relative_pose: CameraPose,
    focal_length_px: float,
    baseline_units: float,
) -> List[DepthPoint]:
    """
    Estimate depth for a pair of matched features.

    Args:
        match_result: MatchResult between two views
        relative_pose: Relative camera pose

    Returns:
        List of DepthPoint objects
    """
    depth_points = []

    # Simple triangulation using disparity
    # In practice, would use proper stereo triangulation
    for match in match_result.matches:
        # Calculate disparity (difference in x coordinates)
        disparity = match.feature2.x - match.feature1.x

        if abs(disparity) < 1e-6:
            # No disparity, can't estimate depth
            continue

        # This rectified-stereo fallback is only allowed when callers provide
        # calibration. It must not invent focal length or baseline.
        depth = (focal_length_px * baseline_units) / abs(disparity)

        # Convert to 3D point
        x = match.feature1.x * depth / focal_length_px
        y = match.feature1.y * depth / focal_length_px
        z = depth

        point = DepthPoint(
            x=x,
            y=y,
            z=z,
            confidence=match.confidence * relative_pose.confidence,
            view_name=match_result.view1,
        )
        depth_points.append(point)

    return depth_points


def _calculate_depth_confidence(
    depth_maps: Dict[str, DepthMap],
) -> float:
    """
    Calculate overall confidence for depth estimation.

    Args:
        depth_maps: Dictionary of depth maps

    Returns:
        Confidence score between 0 and 1
    """
    if not depth_maps:
        return 0.0

    # Average confidence across all depth maps
    confidences = [dm.confidence for dm in depth_maps.values()]
    return sum(confidences) / len(confidences) if confidences else 0.0


def interpolate_depth(
    depth_map: DepthMap,
    x: float,
    y: float,
) -> Optional[float]:
    """
    Interpolate depth at a specific pixel location.

    Args:
        depth_map: Depth map to interpolate from
        x: X coordinate
        y: Y coordinate

    Returns:
        Interpolated depth value or None
    """
    if not depth_map.points:
        return None

    # Find nearest points
    distances = []
    for point in depth_map.points:
        dx = point.x - x
        dy = point.y - y
        dist = math.sqrt(dx * dx + dy * dy)
        distances.append((dist, point.z, point.confidence))

    # Sort by distance
    distances.sort(key=lambda x: x[0])

    # Weighted average of nearest points
    total_weight = 0.0
    weighted_depth = 0.0

    for dist, depth, conf in distances[:5]:  # Use 5 nearest points
        if dist < 1e-6:
            return depth  # Exact match

        weight = conf / (dist + 1e-6)
        weighted_depth += depth * weight
        total_weight += weight

    if total_weight < 1e-6:
        return None

    return weighted_depth / total_weight
