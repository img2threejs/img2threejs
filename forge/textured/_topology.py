"""bmesh topology helpers shared by inspect and normalize (run under bpy)."""


def position_islands(bm, tolerance):
    """Connected components computed on welded POSITIONS, as lists of bm verts.

    glTF import splits vertices at UV seams, so raw connectivity over-counts islands. Welding is
    done only in the union-find; the mesh itself is never welded, which keeps UVs and normals.
    """
    inverse = 1.0 / tolerance
    parent = {}

    def key(vert):
        return (round(vert.co.x * inverse), round(vert.co.y * inverse), round(vert.co.z * inverse))

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for vert in bm.verts:
        parent.setdefault(key(vert), key(vert))
    for face in bm.faces:
        keys = [key(v) for v in face.verts]
        root = find(keys[0])
        for other in keys[1:]:
            other_root = find(other)
            if other_root != root:
                parent[other_root] = root
    for edge in bm.edges:
        if not edge.link_faces:  # wire edges still connect their endpoints
            a, b = (find(key(v)) for v in edge.verts)
            if a != b:
                parent[b] = a
    groups = {}
    for vert in bm.verts:
        groups.setdefault(find(key(vert)), []).append(vert)
    return sorted(groups.values(), key=len, reverse=True)


def speck_islands(islands, max_vertices, max_ratio):
    """Islands strictly smaller than both the absolute and the relative-to-largest thresholds."""
    if not islands:
        return []
    largest = len(islands[0])
    return [island for island in islands[1:] if len(island) < max_vertices and len(island) < largest * max_ratio]


def nonmanifold_counts(mesh, tolerance):
    """(raw, welded) non-manifold edge counts; welded merges verts within ``tolerance``."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    raw = sum(1 for edge in bm.edges if not edge.is_manifold)
    loose = sum(1 for vert in bm.verts if not vert.link_edges)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=tolerance)
    welded = sum(1 for edge in bm.edges if not edge.is_manifold)
    bm.free()
    return raw, welded, loose
