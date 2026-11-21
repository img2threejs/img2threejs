"""World-triangle scene measurements; no metadata scores or bounding-box collisions.

Strict crossings and closed-component containment count as interpenetration. Mere
surface contact does not. Unsupported/degenerate geometry raises, never returns 0.
"""
from __future__ import annotations

from collections import Counter
import math

from stage6_compose.compose import CompositionError

_EPS = 1e-8


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def triangles(value):
    """Validate expanded world triangles from the owned Three runtime."""
    if type(value) is not list or not value or len(value) > 1_000_000:
        raise CompositionError('invalid-geometry', 'triangles')
    result = []
    for triangle in value:
        if type(triangle) is not list or len(triangle) != 3:
            raise CompositionError('invalid-geometry', 'triangle')
        for point in triangle:
            if (type(point) is not list or len(point) != 3
                    or any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 1e9 for v in point)):
                raise CompositionError('invalid-geometry', 'vertex')
        normal = _cross(_sub(triangle[1], triangle[0]), _sub(triangle[2], triangle[0]))
        if math.hypot(*normal) <= 1e-16:
            raise CompositionError('degenerate-geometry', 'triangle')
        result.append(tuple(tuple(point) for point in triangle))
    return result


def bounds(mesh):
    return [[op(p[axis] for tri in mesh for p in tri) for axis in range(3)] for op in (min, max)]


def box_iou(a, b):
    """Normalized half-open corner boxes; no empty or off-frame boxes."""
    for box in (a, b):
        if (type(box) not in (list, tuple) or len(box) != 4
                or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise CompositionError('invalid-projected-box', 'box')
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union


def ray_distance(origin, direction, triangle):
    """Double-sided Moller-Trumbore ray distance, including edge contact."""
    a, b, c = triangle
    edge1, edge2 = _sub(b, a), _sub(c, a)
    h = _cross(direction, edge2)
    determinant = _dot(edge1, h)
    if abs(determinant) < 1e-14:
        return None
    s = _sub(origin, a)
    u = _dot(s, h) / determinant
    q = _cross(s, edge1)
    v = _dot(direction, q) / determinant
    distance = _dot(edge2, q) / determinant
    if u < -_EPS or v < -_EPS or u + v > 1 + _EPS or distance < -_EPS:
        return None
    return max(0.0, distance)


def contact_gap(mesh, support):
    """Minimum vertical gap from actual lowest vertices to explicit support triangles.

    A support hit is mandatory; an absent floor/depth record must not become zero.
    All lowest vertices are sampled (not an AABB centre that may lie in a hole).
    """
    bottom = bounds(mesh)[0][1]
    samples = {p for tri in mesh for p in tri if abs(p[1] - bottom) <= _EPS}
    gaps = []
    for point in samples:
        origin = (point[0], point[1] + _EPS * 2, point[2])
        hits = [distance for tri in support if (distance := ray_distance(origin, (0, -1, 0), tri)) is not None]
        if hits:
            gaps.append(max(0, min(hits) - _EPS * 2))
    if not gaps:
        raise CompositionError('missing-support-ray-hit', 'support')
    return min(gaps)


def _crosses(a, b):
    """Positive interior crossing; a shared support edge is not penetration."""
    normal_a = _cross(_sub(a[1], a[0]), _sub(a[2], a[0]))
    normal_b = _cross(_sub(b[1], b[0]), _sub(b[2], b[0]))
    length_a, length_b = math.hypot(*normal_a), math.hypot(*normal_b)
    a_distances = [_dot(_sub(point, b[0]), normal_b) / length_b for point in a]
    b_distances = [_dot(_sub(point, a[0]), normal_a) / length_a for point in b]
    if any(not (min(distances) < -_EPS and max(distances) > _EPS)
           for distances in (a_distances, b_distances)):
        return False
    direction = _cross(normal_a, normal_b)
    length = math.hypot(*direction)
    if length == 0:
        return False
    direction = tuple(value / length for value in direction)

    def section_interval(triangle, distances):
        projected = [_dot(point, direction) for point in triangle]
        section = []
        for index, distance in enumerate(distances):
            if abs(distance) <= _EPS:
                section.append(projected[index])
            following = (index + 1) % 3
            other = distances[following]
            if distance * other < 0:
                fraction = distance / (distance - other)
                section.append(projected[index] + fraction * (projected[following] - projected[index]))
        return min(section), max(section)

    first = section_interval(a, a_distances)
    second = section_interval(b, b_distances)
    return min(first[1], second[1]) - max(first[0], second[0]) > _EPS


def _closed_components(mesh):
    # Components retain orientation: nested reversed shells bound real cavities.
    # Intersecting components are rejected before winding can cancel their volume.
    edges, owners = Counter(), {}
    adjacency = [set() for _ in mesh]
    for index, tri in enumerate(mesh):
        for a, b in zip(tri, tri[1:] + tri[:1]):
            edge = tuple(sorted((a, b)))
            edges[edge] += 1
            for previous in owners.get(edge, ()):
                adjacency[index].add(previous)
                adjacency[previous].add(index)
            owners.setdefault(edge, []).append(index)
    if any(count > 2 for count in edges.values()):
        raise CompositionError('nonmanifold-containment-geometry', 'containment')
    unseen = set(range(len(mesh)))
    result = []
    while unseen:
        pending = [unseen.pop()]
        component = []
        while pending:
            index = pending.pop()
            component.append(mesh[index])
            neighbours = adjacency[index] & unseen
            unseen.difference_update(neighbours)
            pending.extend(neighbours)
        if all(edges[tuple(sorted((a, b)))] == 2 for tri in component for a, b in zip(tri, tri[1:] + tri[:1])):
            directions = Counter((a, b) for tri in component for a, b in zip(tri, tri[1:] + tri[:1]))
            if any(directions[(a, b)] != directions[(b, a)] for a, b in directions):
                raise CompositionError('inconsistent-closed-mesh-winding', 'containment')
            result.append(component)
    return result


def _inside(point, shell):
    # Solid angle handles rays through mesh vertices and parallel/coplanar edges.
    angle = 0.0
    for tri in shell:
        a, b, c = (_sub(v, point) for v in tri)
        la, lb, lc = math.hypot(*a), math.hypot(*b), math.hypot(*c)
        if min(la, lb, lc) < _EPS:
            return False  # surface contact, not strict containment
        numerator = _dot(a, _cross(b, c))
        denominator = la*lb*lc + _dot(a, b)*lc + _dot(b, c)*la + _dot(c, a)*lb
        angle += 2 * math.atan2(numerator, denominator)
    return abs(angle) > 2 * math.pi + _EPS


def _tree(mesh):
    box = bounds(mesh)
    if len(mesh) <= 8:
        return box, mesh, None
    axis = max(range(3), key=lambda k: box[1][k]-box[0][k])
    ordered = sorted(mesh, key=lambda tri: sum(p[axis] for p in tri))
    middle = len(ordered)//2
    return box, None, (_tree(ordered[:middle]), _tree(ordered[middle:]))


def _overlap(a, b):
    return all(a[1][k] >= b[0][k]-_EPS and b[1][k] >= a[0][k]-_EPS for k in range(3))


def _crossing_trees(left, right):
    if not _overlap(left[0], right[0]):
        return False
    if left[1] is not None and right[1] is not None:
        return any(_crosses(a, b) for a in left[1] for b in right[1])
    if left[2] is not None:
        return any(_crossing_trees(child, right) for child in left[2])
    return any(_crossing_trees(left, child) for child in right[2])


def intersection_count(left, right):
    """Return 0 or one witnessed interpenetration (not an exhaustive event tally)."""
    left_bounds, right_bounds = bounds(left), bounds(right)
    if any(left_bounds[1][axis] <= right_bounds[0][axis] + _EPS
           or right_bounds[1][axis] <= left_bounds[0][axis] + _EPS for axis in range(3)):
        return 0
    if _crossing_trees(_tree(left), _tree(right)):
        return 1
    closed = [_closed_components(mesh) for mesh in (left, right)]
    for components in closed:
        for index, first in enumerate(components):
            for second in components[index+1:]:
                if _crossing_trees(_tree(first), _tree(second)):
                    raise CompositionError('self-intersecting-closed-components', 'containment')
    solids = [[tri for component in components for tri in component] for components in closed]
    for components, solid, points, other_solid in ((closed[0], solids[0], right, solids[1]),
                                                    (closed[1], solids[1], left, solids[0])):
        for component in components:
            box = bounds(component)
            for tri in points:
                centre = tuple(sum(p[axis] for p in tri) / 3 for axis in range(3))
                for point in (*tri, centre):
                    if all(box[0][k]-_EPS <= point[k] <= box[1][k]+_EPS for k in range(3)) and _inside(point, solid):
                        return 1
            # Identical/coincident closed shells have no strictly interior vertex.
            # Probe both sides of actual faces and require volume in BOTH shells;
            # this does not turn legitimate adjacent support contact into overlap.
            step = max(1e-7, max(box[1][k]-box[0][k] for k in range(3))*1e-7)
            for tri in component:
                centre = tuple(sum(p[axis] for p in tri)/3 for axis in range(3))
                normal = _cross(_sub(tri[1], tri[0]), _sub(tri[2], tri[0]))
                length = math.hypot(*normal)
                for sign in (-1, 1):
                    point = tuple(centre[k]+sign*step*normal[k]/length for k in range(3))
                    if _inside(point, solid) and other_solid and _inside(point, other_solid):
                        return 1
    return 0
