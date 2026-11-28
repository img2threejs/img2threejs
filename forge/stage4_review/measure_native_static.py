"""Actual isolated-bpy static/rest geometry, material and part capture producer.

The entrypoint is invoked only by NativeTerminalAuthority with a host-approved
request. It imports no plugin implementation and executes no workspace builder.
Node mappings and source/orbit cameras are exact caller-approved data, not labels
inferred from a mesh. All static geometry uses evaluated delivered world triangles.
Semantic part recognition belongs to the authenticated image review, not this tool.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import zlib

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / '_shared')]
from glb_container import parse_glb
from route_parent import _decode, _encode, _read, _write
from stage0_route.images import _exif_orientation, decode, png


class StaticMeasurementError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise StaticMeasurementError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def vector(value, size):
    return type(value) is list and len(value) == size and all(finite(item) for item in value)


def normalize_render_png(raw):
    """Canonicalize owned renderer metadata without changing decoded pixels.

    Blender writes resolution EXIF and Date/RenderTime text, not camera authority.
    Use only on program-rendered captures, never arbitrary reference images.
    """
    require(raw.startswith(b'\x89PNG\r\n\x1a\n'), 'static-render-png-required')
    chunks=[raw[:8]];offset=8
    while offset+12<=len(raw):
        length=struct.unpack_from('>I',raw,offset)[0]
        end=offset+12+length
        require(end<=len(raw),'static-render-png-truncated')
        kind=raw[offset+4:offset+8];payload=raw[offset+8:offset+8+length]
        require(zlib.crc32(kind+payload)&0xffffffff==struct.unpack_from('>I',raw,end-4)[0],
                'static-render-png-crc-invalid')
        if kind==b'eXIf':
            require(_exif_orientation(b'Exif\x00\x00'+payload)==1,'static-render-orientation-unsupported')
        if kind in (b'IHDR',b'IDAT',b'IEND'):
            chunks.append(raw[offset:end])
        else:
            require(kind[0]&32,'static-render-png-critical-chunk-unsupported')
        offset=end
    require(offset==len(raw),'static-render-png-trailing-bytes')
    return png(*decode(b''.join(chunks)))


def validate_request(request):
    require(type(request) is dict and set(request) == {'kind', 'version', 'flatness', 'bindings', 'parts',
                                                     'camera', 'orbits', 'render'}, 'invalid-static-request')
    require(request['kind'] == 'img2.native-static-request' and request['version'] == 1
            and request['flatness'] in ('flat', 'volumetric'), 'invalid-static-request')
    require(type(request['bindings']) is dict and set(request['bindings']) == {'artifact', 'reference', 'spec', 'delivery'},
            'static-exact-input-bindings-required')
    for binding in request['bindings'].values():
        require(type(binding) is dict and set(binding) == {'path', 'sha256'} and type(binding['path']) is str
                and type(binding['sha256']) is str and len(binding['sha256']) == 64
                and all(char in '0123456789abcdef' for char in binding['sha256']), 'invalid-static-binding')
    require(type(request['parts']) is list and bool(request['parts']), 'static-required-part-map-missing')
    identities, nodes = set(), set()
    for part in request['parts']:
        require(type(part) is dict and set(part) == {'id', 'nodes'} and type(part['id']) is str and bool(part['id'])
                and part['id'] not in identities and type(part['nodes']) is list and bool(part['nodes'])
                and all(type(index) is int and index >= 0 and index not in nodes for index in part['nodes'])
                and len(set(part['nodes'])) == len(part['nodes']), 'invalid-static-required-part-map')
        identities.add(part['id']); nodes.update(part['nodes'])
    require(type(request['orbits']) is list and [view.get('angle') for view in request['orbits']] == [0, 90, 180, 270],
            'static-approved-four-orbit-cameras-required')
    for view in [request['camera'], *request['orbits']]:
        require(type(view) is dict and set(view) in ({'matrixWorld', 'projectionMatrix'},
                                                   {'angle', 'matrixWorld', 'projectionMatrix'})
                and vector(view['matrixWorld'], 16) and vector(view['projectionMatrix'], 16),
                'static-approved-camera-required')
        m = view['matrixWorld']
        require(all(abs(m[index] - expected) <= 1e-8 for index, expected in ((3, 0), (7, 0), (11, 0), (15, 1))),
                'static-camera-must-be-affine')
        axes = [[m[column * 4 + row] for row in range(3)] for column in range(3)]
        require(all(abs(sum(a*b for a,b in zip(axes[i], axes[j])) - int(i == j)) <= 1e-5
                    for i in range(3) for j in range(3)), 'static-camera-must-be-rigid')
    render = request['render']
    require(type(render) is dict and set(render) == {'width', 'height', 'samples', 'exposure', 'worldColor',
                                                  'worldStrength', 'lights'}, 'static-render-config-required')
    require(all(type(render[key]) is int and render[key] > 0 for key in ('width', 'height', 'samples'))
            and render['width'] * render['height'] <= 16_000_000 and render['samples'] <= 256
            and finite(render['exposure']) and vector(render['worldColor'], 3)
            and finite(render['worldStrength']) and render['worldStrength'] >= 0
            and type(render['lights']) is list, 'invalid-static-render-config')
    for light in render['lights']:
        require(type(light) is dict and set(light) == {'position', 'target', 'energy', 'size', 'color'}
                and all(vector(light[key], 3) for key in ('position', 'target', 'color'))
                and light['position'] != light['target']
                and all(finite(light[key]) and light[key] > 0 for key in ('energy', 'size')), 'invalid-static-light')
    return request


def subtract(a, b):
    return tuple(x-y for x,y in zip(a,b))


def dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def triangle_crosses(first, second, epsilon):
    """Positive interior intersection, excluding boundary-only edge/vertex contact.

    BVH supplies candidates; this narrow phase distinguishes true crossings and
    coplanar overlapping area from adjacent triangles. No vertex sampling occurs.
    """
    normal = cross(subtract(first[1], first[0]), subtract(first[2], first[0]))
    norm = math.sqrt(dot(normal, normal))
    other = cross(subtract(second[1], second[0]), subtract(second[2], second[0]))
    other_norm = math.sqrt(dot(other, other))
    if norm == 0 or other_norm == 0:
        return False  # separately counted as degenerate, never accepted
    if all(abs(dot(subtract(point, first[0]), normal)) <= epsilon * norm for point in second):
        axis = max(range(3), key=lambda index: abs(normal[index]))
        project = lambda p: tuple(value for index,value in enumerate(p) if index != axis)
        polygon = [project(p) for p in first]
        clip = [project(p) for p in second]
        area2 = lambda a,b,c: (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        sign = 1 if area2(*clip) > 0 else -1
        for a,b in zip(clip, clip[1:] + clip[:1]):
            previous, polygon = polygon, []
            if not previous:
                return False
            for p,q in zip(previous, previous[1:] + previous[:1]):
                dp, dq = sign*area2(a,b,p), sign*area2(a,b,q)
                if dp >= 0:
                    polygon.append(p)
                if (dp < 0) != (dq < 0):
                    fraction = dp/(dp-dq)
                    polygon.append((p[0]+fraction*(q[0]-p[0]), p[1]+fraction*(q[1]-p[1])))
        area = abs(sum(p[0]*q[1]-p[1]*q[0] for p,q in zip(polygon, polygon[1:] + polygon[:1]))) / 2
        return area > epsilon * epsilon
    # Each triangle's interior must straddle the other's plane. Their plane
    # sections are intervals on the common line; a positive overlap is a true
    # interior crossing even when all edge intersections occur at endpoints.
    first_distances=[dot(subtract(point,second[0]),other)/other_norm for point in first]
    second_distances=[dot(subtract(point,first[0]),normal)/norm for point in second]
    if not all(min(distances)<-epsilon and max(distances)>epsilon
               for distances in (first_distances,second_distances)):
        return False
    direction=cross(normal,other)
    length=math.sqrt(dot(direction,direction))
    if length==0:
        return False
    direction=tuple(value/length for value in direction)
    def section(points,distances):
        values=[dot(point,direction) for point,distance in zip(points,distances) if abs(distance)<=epsilon]
        for index in range(3):
            following=(index+1)%3
            a,b=distances[index],distances[following]
            if (a<0<b) or (b<0<a):
                fraction=a/(a-b)
                point=tuple(p+fraction*(q-p) for p,q in zip(points[index],points[following]))
                values.append(dot(point,direction))
        return min(values),max(values)
    first_interval=section(first,first_distances)
    second_interval=section(second,second_distances)
    return min(first_interval[1],second_interval[1])-max(first_interval[0],second_interval[0])>epsilon


def geometry_report(name, vertices, triangles):
    from mathutils.bvhtree import BVHTree
    require(vertices and triangles and all(finite(v) for point in vertices for v in point), 'static-empty-or-nonfinite-mesh')
    scale = max(max(p[a] for p in vertices)-min(p[a] for p in vertices) for a in range(3))
    require(scale > 0, 'static-zero-extent-mesh')
    epsilon = scale * 1e-8
    keys = [tuple(p) for p in vertices]
    edges, directed = Counter(), Counter()
    degenerate = 0
    for tri in triangles:
        a,b,c = [vertices[index] for index in tri]
        normal = cross(subtract(b,a), subtract(c,a))
        degenerate += dot(normal,normal) <= epsilon**4
        for x,y in zip(tri, tri[1:] + tri[:1]):
            ka,kb = keys[x],keys[y]
            edges[tuple(sorted((ka,kb)))] += 1
            directed[(ka,kb)] += 1
    tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True, epsilon=0)
    pairs = sorted({tuple(sorted(pair)) for pair in tree.overlap(tree) if pair[0] != pair[1]})
    intersections = sum(triangle_crosses([vertices[i] for i in triangles[a]],
                                        [vertices[i] for i in triangles[b]], epsilon) for a,b in pairs)
    closed = all(count == 2 for count in edges.values())
    oriented = all(directed[(a,b)] == directed[(b,a)] for a,b in edges)
    volume = sum(dot(vertices[a],cross(vertices[b],vertices[c]))/6 for a,b,c in triangles)
    return {'name':name, 'vertices':len(vertices), 'triangles':len(triangles),
            'selfIntersectionCount':intersections, 'degenerateTriangles':int(degenerate),
            'boundaryEdges':sum(count == 1 for count in edges.values()),
            'nonManifoldEdges':sum(count > 2 for count in edges.values()),
            'closed':closed, 'twoSided':closed and oriented and abs(volume) > epsilon**3,
            'signedVolume':volume if closed and oriented else None,
            'intersectionTolerance':epsilon, 'intersectionScope':'all-intra-mesh-triangle-pairs'}


def camera_parameters(projection, width, height):
    p = projection
    require(vector(p,16) and p[0] > 0 and p[5] > 0
            and all(abs(p[index]) <= 1e-8 for index in (1,2,3,4,6,7,8,9,12,13)),
            'static-off-axis-or-skew-projection-unsupported')
    require(math.isclose(p[5]/p[0], width/height, rel_tol=1e-5), 'static-camera-render-aspect-mismatch')
    if abs(p[11]+1) <= 1e-8 and abs(p[15]) <= 1e-8:
        near, far = p[14]/(p[10]-1), p[14]/(p[10]+1)
        result = {'type':'PERSP', 'lens':18*p[0]}
    elif abs(p[11]) <= 1e-8 and abs(p[15]-1) <= 1e-8:
        near, far = (p[14]+1)/p[10], (p[14]-1)/p[10]
        result = {'type':'ORTHO', 'ortho_scale':2/p[0]}
    else:
        raise StaticMeasurementError('static-projection-unsupported')
    require(0 < near < far and math.isfinite(far), 'static-camera-clipping-invalid')
    return {**result, 'clip_start':near, 'clip_end':far}


def import_delivery(path):
    """Retain the importer's actual source-node -> created-mesh association.

    Skinned instances may live on synthetic virtual nodes, which do not receive
    original node extras. mesh_node_idx remains their exact glTF node identity.
    Armature display shapes are consumer UI, not delivered triangle geometry.
    """
    import bpy
    from io_scene_gltf2.io.imp.gltf2_io_gltf import glTFImporter
    from io_scene_gltf2.blender.imp.blender_gltf import BlenderGlTF
    properties=bpy.ops.import_scene.gltf.get_rna_type().properties
    settings={}
    for prop in properties:
        if prop.type in ('BOOLEAN','INT','FLOAT','STRING','ENUM'):
            settings[prop.identifier]=(tuple(prop.default_array) if getattr(prop,'is_array',False)
                                       else prop.default)
    settings.update(disable_bone_shape=True,import_user_extensions=[],loglevel=40)
    importer=glTFImporter(str(path),settings)
    importer.read()
    importer.checks()
    BlenderGlTF.create(importer)
    scene=bpy.data.scenes[importer.blender_scene]
    node_by_object={}
    for vnode in importer.vnodes.values():
        index=vnode.mesh_node_idx
        if index is None:
            continue
        obj=vnode.blender_object
        require(type(index) is int and 0<=index<len(importer.data.nodes)
                and importer.data.nodes[index].mesh is not None
                and obj is not None and obj.type=='MESH' and obj not in node_by_object,
                'static-import-node-provenance-missing')
        node_by_object[obj]=index
    expected={index for index,node in enumerate(importer.data.nodes) if node.mesh is not None}
    require(set(node_by_object.values())==expected
            and set(node_by_object)=={obj for obj in scene.objects if obj.type=='MESH'},
            'static-import-mesh-instance-coverage-mismatch')
    bpy.context.window.scene=scene
    return scene,node_by_object


def measure(workspace, request, output_relative):
    import bpy
    from mathutils import Matrix, Vector
    require(bpy.app.version >= (4,3,0), 'static-bpy>=4.3-required')
    validate_request(request)
    root = Path(workspace).resolve(strict=True)
    # _read refuses symlinks/traversal. Reserve a fresh confined output directory.
    require(type(output_relative) is str and not output_relative.startswith('/')
            and all(part not in ('','.','..') for part in output_relative.split('/')), 'static-unsafe-output')
    output = root
    for part in output_relative.split('/'):
        output /= part
        require(not output.is_symlink(), 'static-output-alias')
    require(not output.exists(), 'static-output-already-exists')
    output.mkdir(parents=True)
    for binding in request['bindings'].values():
        require(sha(_read(root,binding['path'])[0]) == binding['sha256'], 'static-input-changed')
    delivery = request['bindings']['delivery']
    data = _read(root,delivery['path'])[0]
    require(sha(data)==delivery['sha256'],'static-input-changed')
    consumer_path=output/'consumer.glb'
    consumer_path.write_bytes(data)
    document,_,metadata = parse_glb(consumer_path)
    require([item['type'] for item in metadata['chunks']] == ['JSON','BIN\x00'], 'static-glb-chunks-unsupported')
    require(not any('uri' in item for key in ('images','buffers') for item in document.get(key,[])), 'static-external-resource-unsupported')
    require(not document.get('extensionsRequired'), 'static-required-extension-unsupported')
    require(len(document.get('scenes',[])) == 1 and document.get('scene') == 0, 'static-single-active-scene-required')
    nodes = document.get('nodes',[]); seen=set(); pending=list(document['scenes'][0].get('nodes',[]))
    while pending:
        index=pending.pop()
        require(type(index) is int and 0 <= index < len(nodes) and index not in seen, 'static-node-graph-invalid')
        seen.add(index); pending.extend(nodes[index].get('children',[]))
    require(seen == set(range(len(nodes))), 'static-unreachable-node-unsupported')
    for part in request['parts']:
        require(all(index in seen and 'mesh' in nodes[index] for index in part['nodes']), 'static-required-part-node-missing')
    # The consumer opens an exact byte snapshot; source extras/names are never
    # manufactured or used to establish required-part node ownership.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene,node_by_object=import_delivery(consumer_path)
    require(not any(obj.type=='LIGHT' for obj in scene.objects),'static-delivery-authored-lights-forbidden')
    for obj in scene.objects:
        obj.animation_data_clear()
        if obj.type=='ARMATURE':
            obj.data.pose_position='REST'
        if obj.type=='MESH' and obj.data.shape_keys:
            obj.data.shape_keys.animation_data_clear()
    bpy.context.view_layer.update()
    objects=list(node_by_object)
    require(objects, 'static-delivery-has-no-meshes')
    actual_nodes=set(node_by_object.values())
    required_nodes={index for part in request['parts'] for index in part['nodes']}
    require(required_nodes <= actual_nodes, 'static-required-part-geometry-missing')
    reports, all_points=[],[]
    deps=bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        evaluated=obj.evaluated_get(deps); mesh=evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            points=[tuple(evaluated.matrix_world@vertex.co) for vertex in mesh.vertices]
            triangles=[tuple(face.vertices) for face in mesh.loop_triangles]
            report=geometry_report(str(node_by_object[obj]),points,triangles)
            reports.append(report)
            referenced={index for triangle in triangles for index in triangle}
            all_points.extend((points[index][0],points[index][2],-points[index][1]) for index in referenced)
        finally:
            evaluated.to_mesh_clear()
        obj.hide_render=False
    extents=[max(p[a] for p in all_points)-min(p[a] for p in all_points) for a in range(3)]
    center=[(max(p[a] for p in all_points)+min(p[a] for p in all_points))/2 for a in range(3)]
    pivot=[center[0],min(p[1] for p in all_points),center[2]]
    require(all(abs(value)<=1e-5 for value in pivot),'static-delivery-pivot-not-bottom-centre')
    orbit_projection=request['orbits'][0]['projectionMatrix']
    radii=[]
    for view in request['orbits']:
        require(view['projectionMatrix']==orbit_projection,'static-orbit-projections-not-comparable')
        matrix=view['matrixWorld'];position=[matrix[12],matrix[13],matrix[14]]
        offset=subtract(position,center);radius=math.sqrt(dot(offset,offset));radii.append(radius)
        require(radius>0,'static-orbit-camera-at-center')
        actual=math.degrees(math.atan2(offset[0],offset[2]))%360
        error=abs((actual-view['angle']+180)%360-180)
        require(error<=1,'static-orbit-angle-does-not-match-camera')
        forward=[-matrix[8],-matrix[9],-matrix[10]]
        require(dot(forward,tuple(-value/radius for value in offset))>=0.99,'static-orbit-camera-not-facing-subject')
    require(all(math.isclose(value,radii[0],rel_tol=1e-5) for value in radii),'static-orbit-radii-not-comparable')
    scene.render.engine='CYCLES'; scene.cycles.device='CPU'; scene.cycles.samples=request['render']['samples']
    scene.cycles.use_denoising=False; scene.cycles.seed=17
    settings=request['render']
    scene.render.resolution_x=settings['width'];scene.render.resolution_y=settings['height'];scene.render.resolution_percentage=100
    scene.render.film_transparent=True
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='8'
    scene.view_settings.view_transform='Standard';scene.view_settings.look='None';scene.view_settings.exposure=settings['exposure'];scene.view_settings.gamma=1
    world=bpy.data.worlds.new('ApprovedStaticWorld');world.use_nodes=True
    world.node_tree.nodes['Background'].inputs['Color'].default_value=(*settings['worldColor'],1)
    world.node_tree.nodes['Background'].inputs['Strength'].default_value=settings['worldStrength'];scene.world=world
    convert=Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))
    for index,light in enumerate(settings['lights']):
        lamp=bpy.data.lights.new('ApprovedStaticLight-'+str(index),'AREA');lamp.energy=light['energy'];lamp.size=light['size'];lamp.color=light['color']
        obj=bpy.data.objects.new(lamp.name,lamp);scene.collection.objects.link(obj)
        obj.location=(convert@Vector((*light['position'],1))).to_3d()
        target=(convert@Vector((*light['target'],1))).to_3d()
        obj.rotation_euler=(target-obj.location).to_track_quat('-Z','Y').to_euler()
    camera_data=bpy.data.cameras.new('ApprovedStaticCamera');camera=bpy.data.objects.new(camera_data.name,camera_data)
    scene.collection.objects.link(camera);scene.camera=camera
    camera_data.sensor_width=36;camera_data.sensor_fit='HORIZONTAL'
    mask=bpy.data.materials.new('MeasuredOpaqueGeometry');mask.use_nodes=True
    shader=mask.node_tree.nodes;shader.clear();emission=shader.new('ShaderNodeEmission');target=shader.new('ShaderNodeOutputMaterial')
    mask.node_tree.links.new(emission.outputs[0],target.inputs['Surface'])
    captures=[]
    def render(name,view,*,silhouette=False,selected=None,allow_empty=False):
        matrix=view['matrixWorld']
        camera.matrix_world=convert@Matrix([matrix[i:i+4] for i in range(0,16,4)]).transposed()
        for key,value in camera_parameters(view['projectionMatrix'],settings['width'],settings['height']).items():
            setattr(camera_data,key,value)
        for obj in objects:
            obj.hide_render=selected is not None and node_by_object[obj] not in selected
        scene.view_layers[0].material_override=mask if silhouette else None
        bpy.context.view_layer.update()
        path=output/(name+'.png');scene.render.filepath=str(path)
        bpy.ops.render.render(write_still=True)
        raw=normalize_render_png(path.read_bytes());path.write_bytes(raw)
        width,height,channels,pixels=decode(raw)
        require(channels==4,'static-rgba-capture-required')
        foreground=[index for index in range(width*height) if pixels[index*4+3]>=(128 if silhouette else 1)]
        require(bool(foreground) or allow_empty,'static-capture-empty:'+name)
        record={'role':name,'path':path.relative_to(root).as_posix(),'sha256':sha(raw),
                'pixelSha256':sha(bytes(pixels)),'width':width,'height':height,'channels':4,
                'view':view,'materialMode':'opaque-emission' if silhouette else 'delivered-materials',
                'foregroundPixels':len(foreground)}
        if foreground:
            box=[min(i%width for i in foreground),min(i//width for i in foreground),
                 max(i%width for i in foreground)+1,max(i//width for i in foreground)+1]
            record['bounds']=box
        captures.append(record)
        return record,(width,height,channels,pixels)
    render('render',request['camera'])
    areas={}
    for view in request['orbits']:
        thin_side=request['flatness']=='flat' and view['angle'] in (90,270)
        render('orbit-'+str(view['angle']),view,allow_empty=thin_side)
        capture,_=render('mask-'+str(view['angle']),view,silhouette=True,allow_empty=thin_side)
        areas[view['angle']]=capture['foregroundPixels']
    part_records=[]
    for index,part in enumerate(request['parts']):
        selected=set(part['nodes'])
        chosen=None
        for view_index,view in enumerate([request['camera'],*request['orbits']]):
            isolated,_=render('part-'+str(index)+'-mask-'+str(view_index),view,silhouette=True,selected=selected,allow_empty=True)
            if isolated['foregroundPixels']:
                chosen=(view,isolated);break
        require(chosen is not None,'static-required-part-off-camera:'+part['id'])
        view,isolated=chosen
        material,decoded=render('part-'+str(index)+'-material',view,selected=selected)
        width,height,channels,pixels=decoded;x0,y0,x1,y1=isolated['bounds']
        cropped=b''.join(pixels[(y*width+x0)*4:(y*width+x1)*4] for y in range(y0,y1))
        crop_path=output/('part-'+str(index)+'-crop.png');crop_bytes=png(x1-x0,y1-y0,4,cropped);crop_path.write_bytes(crop_bytes)
        captures.append({'role':'part-'+str(index)+'-crop','path':crop_path.relative_to(root).as_posix(),
                         'sha256':sha(crop_bytes),'pixelSha256':sha(cropped),'width':x1-x0,'height':y1-y0,'channels':4,
                         'view':view,'materialMode':'delivered-materials-crop','foregroundPixels':isolated['foregroundPixels']})
        part_records.append({'id':part['id'],'nodes':part['nodes'],'isolation':isolated['role'],
                             'material':material['role'],'crop':'part-'+str(index)+'-crop','bounds':isolated['bounds']})
    for binding in request['bindings'].values():
        require(sha(_read(root,binding['path'])[0])==binding['sha256'],'static-input-changed')
    measurements={'width':extents[0],'height':extents[1],'depth':extents[2],
                  'frontArea':areas[0],'sideArea':min(areas[90],areas[270]),
                  'closed':all(row['closed'] for row in reports),'twoSided':all(row['twoSided'] for row in reports),
                  'selfIntersectionCount':sum(row['selfIntersectionCount'] for row in reports)}
    measurements['pivotBottomCentre']=pivot
    frozen=_decode((FORGE/'_shared/route_quality_policy_v1.json').read_bytes())
    checks={'nondegenerate':all(row['degenerateTriangles']==0 for row in reports),
            'selfIntersection':measurements['selfIntersectionCount']<=frozen['maximumSelfIntersections']}
    ratio=measurements['depth']/max(measurements['width'],measurements['height'])
    if request['flatness']=='flat':
        checks.update(depth=ratio>=frozen['flat']['minimumThicknessRatio'],closed=measurements['closed'],twoSided=measurements['twoSided'])
    else:
        checks.update(depth=ratio>=frozen['volumetric']['minimumDepthRatio'],
                      sideArea=measurements['sideArea']/measurements['frontArea']>=frozen['volumetric']['minimumSideFrontRatio'])
    report={'kind':'img2.native-static-measurement','version':1,'status':'pass' if all(checks.values()) else 'fail',
            'bindings':request['bindings'],'requestSha256':sha(_encode(request)),
            'policySha256':sha(_encode(frozen)),'blenderVersion':bpy.app.version_string,
            'measurements':measurements,'checks':checks,'meshes':reports,'captures':captures,'parts':part_records,
            'semanticPartCoverage':'requires-authenticated-reference-review','restPose':'evaluated-skin-rest-no-animation',
            'renderSetup':settings,'terminalAcceptance':False}
    _write(root,output_relative+'/measurement.json',_encode(report))
    return report


def main():
    from argparse import ArgumentParser
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True);parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        request=_decode(_read(args.workspace,args.input)[0]);report=measure(args.workspace,request,args.output)
        print('IMG2_NATIVE_STATIC '+json.dumps(report,sort_keys=True,allow_nan=False),flush=True)
        return 0 if report['status']=='pass' else 1
    except (Exception,SystemExit) as error:
        print('IMG2_NATIVE_STATIC '+json.dumps({'kind':'img2.native-static-measurement','version':1,'status':'error','reasons':[str(error)]}),flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
