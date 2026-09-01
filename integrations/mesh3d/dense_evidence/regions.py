"""Candidate-only region inventory for explicit multipart mesh boundaries."""

from __future__ import annotations

from typing import Any

import numpy as np
import trimesh


def inventory_boundaries(
    scene: trimesh.Scene, transform: np.ndarray
) -> list[dict[str, Any]]:
    nodes = sorted(str(node) for node in scene.graph.nodes_geometry)
    if len(nodes) <= 1:
        return []
    records: list[dict[str, Any]] = []
    for node in nodes:
        node_transform, geometry_name = scene.graph[node]
        geometry = scene.geometry[geometry_name]
        points = trimesh.transform_points(
            np.asarray(geometry.vertices, dtype=float), transform @ node_transform
        )
        minimum = points.min(axis=0)
        maximum = points.max(axis=0)
        records.append(
            {
                "regionId": f"node:{node}/geometry:{geometry_name}",
                "node": node,
                "geometry": str(geometry_name),
                "candidateOnly": True,
                "semanticLabel": None,
                "bounds": {
                    "min": minimum.tolist(),
                    "max": maximum.tolist(),
                    "size": (maximum - minimum).tolist(),
                },
            }
        )
    return records
