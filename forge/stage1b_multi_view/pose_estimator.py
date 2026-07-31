"""
Pose Estimator Module

Estimates relative camera poses between views.
Uses matched features to compute relative transformations.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import math

from .feature_matcher import MatchResult, FeatureMatch


@dataclass
class CameraPose:
    """Represents a camera pose relative to a reference."""
    rotation: Tuple[float, float, float]  # Euler angles (roll, pitch, yaw)
    translation: Tuple[float, float, float]
    confidence: float
    view_name: str
    calibrated: bool = False
    method: str = "heuristic"


@dataclass
class PoseEstimationResult:
    """Result of pose estimation."""
    poses: Dict[str, CameraPose]
    relative_poses: Dict[Tuple[str, str], CameraPose]
    confidence: float


def estimate_relative_poses(
    match_results: Dict[Tuple[str, str], MatchResult],
    camera_matrix: Optional[List[List[float]]] = None,
) -> PoseEstimationResult:
    """
    Estimate relative poses between all view pairs.

    Args:
        match_results: Dictionary mapping view pairs to MatchResult

    Returns:
        PoseEstimationResult with estimated poses
    """
    poses = {}
    relative_poses = {}

    # Estimate pose for each matched pair
    for (view1, view2), match_result in match_results.items():
        if match_result.match_count < 4:
            # Need at least 4 matches for pose estimation
            continue

        relative_pose = estimate_pair_pose(match_result, camera_matrix)
        if relative_pose:
            relative_poses[(view1, view2)] = relative_pose

    # Calculate absolute poses (relative to first view)
    if relative_poses:
        poses = _calculate_absolute_poses(relative_poses)

    # Calculate overall confidence
    confidence = _calculate_pose_confidence(relative_poses)

    return PoseEstimationResult(
        poses=poses,
        relative_poses=relative_poses,
        confidence=confidence,
    )


def estimate_pair_pose(
    match_result: MatchResult,
    camera_matrix: Optional[List[List[float]]] = None,
) -> Optional[CameraPose]:
    """
    Estimate relative pose between two views.

    Args:
        match_result: MatchResult between two views

    Returns:
        CameraPose or None if estimation fails
    """
    if match_result.match_count < 4:
        return None

    if camera_matrix is not None:
        try:
            calibrated_pose = _estimate_pose_opencv(match_result, camera_matrix)
        except ImportError:
            calibrated_pose = None
        if calibrated_pose is not None:
            return calibrated_pose

    return _estimate_pose_simple(match_result)


def _estimate_pose_opencv(
    match_result: MatchResult,
    camera_matrix,
) -> Optional[CameraPose]:
    """
    Estimate pose using OpenCV.

    Args:
        match_result: MatchResult between two views

    Returns:
        CameraPose or None if estimation fails
    """
    import cv2
    import numpy as np

    # Extract matched points
    points1 = []
    points2 = []
    for match in match_result.matches:
        points1.append([match.feature1.x, match.feature1.y])
        points2.append([match.feature2.x, match.feature2.y])

    points1 = np.array(points1, dtype=np.float32)
    points2 = np.array(points2, dtype=np.float32)

    # Find fundamental matrix
    F, mask = cv2.findFundamentalMat(points1, points2, cv2.FM_RANSAC)
    if F is None:
        return None

    # Convert the pixel-coordinate fundamental matrix into an essential
    # matrix. This path is intentionally unavailable without intrinsics.
    K = np.asarray(camera_matrix, dtype=np.float64)
    if K.shape != (3, 3):
        raise ValueError("camera_matrix must be a 3x3 intrinsic matrix")
    E = K.T @ F @ K

    # Decompose essential matrix
    inliers = mask.ravel().astype(bool) if mask is not None else None
    if inliers is not None:
        points1 = points1[inliers]
        points2 = points2[inliers]
    if len(points1) < 5:
        return None
    _, R, t, _ = cv2.recoverPose(E, points1, points2, K)

    # Convert rotation matrix to euler angles
    roll, pitch, yaw = _rotation_matrix_to_euler(R)

    return CameraPose(
        rotation=(roll, pitch, yaw),
        translation=(float(t[0]), float(t[1]), float(t[2])),
        confidence=match_result.confidence,
        view_name=match_result.view2,
        calibrated=True,
        method="essential-matrix",
    )


def _estimate_pose_simple(match_result: MatchResult) -> Optional[CameraPose]:
    """
    Simple pose estimation based on feature displacement.

    Args:
        match_result: MatchResult between two views

    Returns:
        CameraPose or None if estimation fails
    """
    if not match_result.matches:
        return None

    # Calculate average displacement
    dx = 0.0
    dy = 0.0
    for match in match_result.matches:
        dx += match.feature2.x - match.feature1.x
        dy += match.feature2.y - match.feature1.y

    dx /= len(match_result.matches)
    dy /= len(match_result.matches)

    # Estimate rotation from displacement pattern
    # This is a very rough approximation
    yaw = math.degrees(math.atan2(dx, 1000))  # Assume 1000px focal length
    pitch = math.degrees(math.atan2(dy, 1000))

    return CameraPose(
        rotation=(0.0, pitch, yaw),
        translation=(dx / 1000.0, dy / 1000.0, 0.0),
        confidence=min(0.2, match_result.confidence * 0.2),
        view_name=match_result.view2,
        calibrated=False,
        method="image-displacement-heuristic",
    )


def _calculate_absolute_poses(
    relative_poses: Dict[Tuple[str, str], CameraPose],
) -> Dict[str, CameraPose]:
    """
    Calculate absolute poses from relative poses.

    Args:
        relative_poses: Dictionary of relative poses

    Returns:
        Dictionary of absolute poses
    """
    poses = {}

    # Start with first view as identity
    if relative_poses:
        first_pair = next(iter(relative_poses.keys()))
        poses[first_pair[0]] = CameraPose(
            rotation=(0.0, 0.0, 0.0),
            translation=(0.0, 0.0, 0.0),
            confidence=1.0,
            view_name=first_pair[0],
            calibrated=True,
            method="identity-reference",
        )

    # Add relative poses
    for (view1, view2), pose in relative_poses.items():
        if view2 not in poses:
            poses[view2] = CameraPose(
                rotation=pose.rotation,
                translation=pose.translation,
                confidence=pose.confidence,
                view_name=view2,
                calibrated=pose.calibrated,
                method=pose.method,
            )

    return poses


def _calculate_pose_confidence(
    relative_poses: Dict[Tuple[str, str], CameraPose],
) -> float:
    """
    Calculate overall confidence for pose estimation.

    Args:
        relative_poses: Dictionary of relative poses

    Returns:
        Confidence score between 0 and 1
    """
    if not relative_poses:
        return 0.0

    # Average confidence across all poses
    confidences = [pose.confidence for pose in relative_poses.values()]
    return sum(confidences) / len(confidences)


def _rotation_matrix_to_euler(R) -> Tuple[float, float, float]:
    """
    Convert rotation matrix to Euler angles.

    Args:
        R: 3x3 rotation matrix

    Returns:
        Tuple of (roll, pitch, yaw) in degrees
    """
    import math

    # Extract Euler angles from rotation matrix
    # Using ZYX convention
    sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])

    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0

    return math.degrees(x), math.degrees(y), math.degrees(z)
