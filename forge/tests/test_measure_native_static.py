"""Static request/triangle negatives and opt-in real bpy primitive/skin fixtures.

Synthetic geometry exercises producer mechanics, not expert/reference accuracy.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

FORGE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(FORGE), str(FORGE / '_shared')]
from route_parent import _encode
from stage4_review.measure_native_static import (StaticMeasurementError, camera_parameters,
                                                 normalize_render_png,
                                                 triangle_crosses, validate_request)
from stage0_route.images import _chunk, png


def camera(position):
    """Explicit fixture-host cameras around its authored Y-up (0,1,0) center."""
    normal = lambda v: [x / math.sqrt(sum(y*y for y in v)) for x in v]
    cross = lambda a,b: [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    z=normal([position[0],position[1]-1,position[2]]);x=normal(cross([0,1,0],z));y=cross(z,x)
    near,far=.1,100
    return {'matrixWorld':[*x,0,*y,0,*z,0,*position,1],
            'projectionMatrix':[2,0,0,0,0,2,0,0,0,0,-(far+near)/(far-near),-1,0,0,-2*far*near/(far-near),0]}


def request_fixture(bindings=None, nodes=None, flatness='volumetric'):
    return {'kind':'img2.native-static-request','version':1,'flatness':flatness,
            'bindings':bindings or {key:{'path':key+'.json','sha256':'a'*64}
                                   for key in ('artifact','reference','spec','delivery')},
            'parts':[{'id':'body','nodes':[0] if nodes is None else nodes}],
            'camera':camera([4,4,5]),
            'orbits':[{'angle':angle,**camera(position)} for angle,position in
                      [(0,[0,1,5]),(90,[5,1,0]),(180,[0,1,-5]),(270,[-5,1,0])]],
            'render':{'width':32,'height':32,'samples':1,'exposure':0,
                      'worldColor':[.3,.3,.3],'worldStrength':1,
                      'lights':[{'position':[3,5,4],'target':[0,0,0],'energy':200,'size':4,'color':[1,1,1]}]}}


EXPORT_FIXTURE = r'''
import bpy, json, sys, hashlib
from pathlib import Path
root=Path(sys.argv[1]);kind=sys.argv[2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2,location=(0,0,1))
body=bpy.context.object
if kind=='thin': body.scale.y=.01
if kind=='wrong-pivot': body.location.z=0
material=bpy.data.materials.new('ActualFixtureMaterial');material.use_nodes=True
shader=material.node_tree.nodes.get('Principled BSDF')
shader.inputs['Base Color'].default_value=(.7,.1,.05,1)
shader.inputs['Roughness'].default_value=.8
if kind=='emissive':
    shader.inputs['Emission Color'].default_value=(.2,.05,.01,1)
    shader.inputs['Emission Strength'].default_value=2
body.data.materials.append(material)
if kind in ('rigged','rigged-child'):
    bpy.ops.object.armature_add()
    rig=bpy.context.object
    modifier=body.modifiers.new('ActualSkin','ARMATURE');modifier.object=rig
    group=body.vertex_groups.new(name=rig.data.bones[0].name)
    group.add(list(range(len(body.data.vertices))),1,'REPLACE')
    body.parent=rig
    if kind=='rigged-child':
        child=bpy.data.objects.new('ActualMeshChild',None)
        bpy.context.scene.collection.objects.link(child)
        child.parent=body
if kind=='authored-light':
    light=bpy.data.lights.new('ActualDeliveryLight','POINT')
    light_object=bpy.data.objects.new(light.name,light)
    bpy.context.scene.collection.objects.link(light_object)
    light_object.location=(0,0,3)
bpy.ops.export_scene.gltf(filepath=str(root/'delivery.glb'),export_format='GLB',export_yup=True,
                          export_skins=True,export_lights=kind=='authored-light')
raw=(root/'delivery.glb').read_bytes();length=int.from_bytes(raw[12:16],'little');doc=json.loads(raw[20:20+length])
if kind=='authored-light':
    # Blender marks this extension required. Exercise the optional-extension path
    # without changing any actual light node, mesh, material or binary buffer.
    required=[value for value in doc.get('extensionsRequired',[]) if value!='KHR_lights_punctual']
    if required: doc['extensionsRequired']=required
    else: doc.pop('extensionsRequired',None)
    payload=json.dumps(doc,separators=(',',':')).encode()
    payload+=b' '*(-len(payload)%4)
    chunks=len(payload).to_bytes(4,'little')+raw[16:20]+payload+raw[20+length:]
    (root/'delivery.glb').write_bytes(raw[:8]+(12+len(chunks)).to_bytes(4,'little')+chunks)
image=bpy.data.images.new('ExplicitSyntheticFixtureReference',width=8,height=8)
image.filepath_raw=str(root/'reference.png');image.file_format='PNG';image.save()
(root/'spec.json').write_text(json.dumps({'fixture':'authored cube; not reference reconstruction'}))
(root/'source.py').write_text('# Synthetic mechanism source, not independent expert labels.\n')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
artifact={'objectId':'object-1','outputKind':'3d-object','frame':{'units':'m','up':'+Y','forward':'+Z'},
          'pivot':'bottom-centre','scaleEstimate':1,'rig':None,
          'files':[{'path':'delivery.glb','sha256':digest(root/'delivery.glb')}]}
(root/'artifact.json').write_text(json.dumps(artifact))
if kind=='rigged-child':
    assert any('mesh' in node and node.get('children') for node in doc['nodes']), 'fixture omitted actual mesh child'
if kind=='authored-light':
    assert 'KHR_lights_punctual' in doc.get('extensionsUsed',[]), 'fixture omitted actual light'
    assert 'KHR_lights_punctual' not in doc.get('extensionsRequired',[]), 'fixture light was not optional'
print('FIXTURE_NODES '+json.dumps([i for i,node in enumerate(doc['nodes']) if 'mesh' in node]))
'''


def export_fixture(root, kind='primitive'):
    process=subprocess.run([os.environ['IMG2_BLENDER_PYTHON'],'-I','-B','-c',EXPORT_FIXTURE,str(root),kind],
                           capture_output=True,text=True,timeout=90)
    if process.returncode:
        raise AssertionError(process.stdout+process.stderr)
    nodes=json.loads(next(line.removeprefix('FIXTURE_NODES ') for line in process.stdout.splitlines()
                          if line.startswith('FIXTURE_NODES ')))
    bindings={key:{'path':name,'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest()}
              for key,name in [('artifact','artifact.json'),('reference','reference.png'),
                               ('spec','spec.json'),('delivery','delivery.glb')]}
    return request_fixture(bindings,nodes,'flat' if kind=='thin' else 'volumetric')


class NativeStaticRequestTests(unittest.TestCase):
    def test_explicit_projection_and_complete_part_map_are_required(self):
        validate_request(request_fixture())
        for mutation in ({'parts':[]},{'parts':[{'id':'body','nodes':[0,0]}]},
                         {'orbits':[]},{'flatness':'unknown'}):
            with self.subTest(mutation=mutation),self.assertRaises(StaticMeasurementError):
                validate_request({**request_fixture(),**mutation})
        p=request_fixture()['camera']['projectionMatrix']
        self.assertEqual(camera_parameters(p,32,32)['type'],'PERSP')
        p[8]=.2
        with self.assertRaisesRegex(StaticMeasurementError,'skew'):
            camera_parameters(p,32,32)

    def test_owned_render_metadata_changes_do_not_change_pixels(self):
        image=png(2,2,4,bytes([255,80,20,255])*4)
        exif=(b'MM'+struct.pack('>HI',42,8)+struct.pack('>H',1)
              +struct.pack('>HHI',0x112,3,1)+struct.pack('>H',1)+b'\x00\x00'+struct.pack('>I',0))
        rendered=image[:33]+_chunk(b'eXIf',exif)+_chunk(b'tEXt',b'Date\x002026/11/21')+image[33:]
        self.assertEqual(normalize_render_png(rendered),image)
        flipped=exif[:18]+struct.pack('>H',6)+exif[20:]
        with self.assertRaisesRegex(StaticMeasurementError,'orientation'):
            normalize_render_png(image[:33]+_chunk(b'eXIf',flipped)+image[33:])

    def test_actual_triangle_interiors_not_shared_boundaries(self):
        first=[(-1,-1,0),(1,-1,0),(0,1,0)]
        self.assertTrue(triangle_crosses(first,[(0,-.5,-1),(0,-.5,1),(0,.5,0)],1e-8))
        self.assertTrue(triangle_crosses(first,[(-.5,-.5,0),(.5,-.5,0),(0,.5,0)],1e-8))
        self.assertFalse(triangle_crosses([(0,0,0),(1,0,0),(1,1,0)],[(0,0,0),(1,1,0),(0,1,0)],1e-8))
        self.assertFalse(triangle_crosses(first,[(x,y,z+2) for x,y,z in first],1e-8))

    def test_endpoint_witness_requires_positive_interior_interval_overlap(self):
        first=[(0,0,0),(-1,2,0),(1,2,0)]
        crossing=[(0,1,0),(0,-1,-1),(0,-1,1)]
        for a,b in ((first,crossing),(crossing,first),(first[::-1],crossing[::-1])):
            with self.subTest(first=a,second=b):
                self.assertTrue(triangle_crosses(a,b,1e-8))
        # Both planes cut both triangles, but the intervals meet at one vertex
        # only (y=0), not along a positive common interior segment.
        self.assertFalse(triangle_crosses(first,[(0,0,0),(0,-1,-1),(0,-1,1)],1e-8))
        self.assertFalse(triangle_crosses(first,[(0,-1,0),(0,-2,-1),(0,-2,1)],1e-8))
        self.assertFalse(triangle_crosses([(0,0,0),(1,0,0),(0,1,0)],
                                         [(0,0,0),(1,0,0),(0,0,1)],1e-8))


@unittest.skipUnless(os.environ.get('IMG2_BLENDER_PYTHON'),'Set IMG2_BLENDER_PYTHON for actual native static capture')
class NativeStaticCaptureTests(unittest.TestCase):
    def test_real_primitive_thin_and_skin_rest_geometry_and_material_parts(self):
        for kind in ('primitive','thin','rigged','rigged-child','emissive'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve()
                request=export_fixture(root,kind)
                (root/'request.json').write_bytes(_encode(request))
                process=subprocess.run([os.environ['IMG2_BLENDER_PYTHON'],'-I','-B',
                                        str(FORGE/'stage4_review/measure_native_static.py'),
                                        '--workspace',str(root),'--input','request.json','--output','capture-r1'],
                                       capture_output=True,text=True,timeout=240)
                self.assertEqual(process.returncode,0,process.stdout+process.stderr)
                report=json.loads((root/'capture-r1/measurement.json').read_text())
                self.assertEqual(report['status'],'pass')
                self.assertEqual(report['measurements']['selfIntersectionCount'],0)
                self.assertTrue(report['measurements']['closed'])
                self.assertTrue(report['measurements']['twoSided'])
                self.assertEqual(report['semanticPartCoverage'],'requires-authenticated-reference-review')
                self.assertNotIn('partCoveragePassed',report['measurements'])
                self.assertEqual([part['id'] for part in report['parts']],['body'])
                self.assertEqual({int(mesh['name']) for mesh in report['meshes']},
                                 set(request['parts'][0]['nodes']))
                roles={capture['role'] for capture in report['captures']}
                self.assertTrue({'render','orbit-0','orbit-90','orbit-180','orbit-270','part-0-crop'}<=roles)
                for capture in report['captures']:
                    self.assertEqual(hashlib.sha256((root/capture['path']).read_bytes()).hexdigest(),capture['sha256'])
                wrong=deepcopy(request);wrong['parts'][0]['nodes']=[999]
                (root/'bad.json').write_bytes(_encode(wrong))
                process=subprocess.run([os.environ['IMG2_BLENDER_PYTHON'],'-I','-B',
                                        str(FORGE/'stage4_review/measure_native_static.py'),
                                        '--workspace',str(root),'--input','bad.json','--output','bad-capture'],
                                       capture_output=True,text=True,timeout=90)
                self.assertNotEqual(process.returncode,0)
                self.assertIn('required-part-node-missing',process.stdout)

    def test_declared_bottom_centre_flag_cannot_override_actual_vertices(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            request=export_fixture(root,'wrong-pivot')
            (root/'request.json').write_bytes(_encode(request))
            process=subprocess.run([os.environ['IMG2_BLENDER_PYTHON'],'-I','-B',
                                    str(FORGE/'stage4_review/measure_native_static.py'),
                                    '--workspace',str(root),'--input','request.json','--output','bad-pivot'],
                                   capture_output=True,text=True,timeout=90)
            self.assertNotEqual(process.returncode,0)
            self.assertIn('pivot-not-bottom-centre',process.stdout)

    def test_optional_authored_light_cannot_augment_approved_render_rig(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            request=export_fixture(root,'authored-light')
            (root/'request.json').write_bytes(_encode(request))
            process=subprocess.run([os.environ['IMG2_BLENDER_PYTHON'],'-I','-B',
                                    str(FORGE/'stage4_review/measure_native_static.py'),
                                    '--workspace',str(root),'--input','request.json','--output','bad-light'],
                                   capture_output=True,text=True,timeout=90)
            self.assertNotEqual(process.returncode,0)
            self.assertIn('static-delivery-authored-lights-forbidden',process.stdout)
            self.assertFalse((root/'bad-light/render.png').exists())


if __name__=='__main__':
    unittest.main()
