"""Host-policy and journal negatives; projection fixtures are NOT producer proof.

Actual native static/skin capture is exercised separately by test_measure_native_static.
The mocked projection below is used only to demonstrate rejection, never admission.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

FORGE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(FORGE),str(FORGE/'_shared'),str(FORGE/'tests')]
from decision_control import DecisionController, DecisionError, canonical_digest
from native_terminal_authority import (AREA, AXES, NativeTerminalAuthority, NativeTerminalPolicy,
                                       _sha, _tree)
from route_parent import ParentError, _decode, _encode, _write
from stage0_route.images import png
from test_measure_native_static import request_fixture


class NativeTerminalAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.parent=self.root/'parent';self.parent.mkdir()
        self.child=self.parent/'.img2threejs/objects/object-1/rev-1';self.child.mkdir(parents=True)
        self.harness=self.root/'harness';self.harness.mkdir()
        (self.harness/'fixture.py').write_text('# Metadata fixture, not a real harness runtime.\n')
        self.reference=png(2,2,4,bytes([255,255,255,255])*4)
        files={'reference.png':self.reference,'spec.json':_encode({'fixture':'projection only'}),
               'source.py':b'# Projection fixture only.\n','delivery.glb':b'not actual geometry; never run as producer'}
        for name,raw in files.items():(self.child/name).write_bytes(raw)
        self.state={'profile':'generic','routeChild':{'runId':'run-1','objectId':'object-1','revision':1,
                    'ownerRoute':'base:fixture','outputKind':'3d-object','cropSha256':_sha(self.reference)}}
        _write(self.child,'state.json',_encode(self.state))
        artifact={'objectId':'object-1','outputKind':'3d-object','frame':{'units':'m','up':'+Y','forward':'+Z'},
                  'pivot':'bottom-centre','files':[self.binding('delivery.glb')]}
        _write(self.child,'artifact.json',_encode(artifact))
        _write(self.parent,'.img2threejs/state.json',_encode({'fixture':'parent metadata'}))
        _write(self.parent,'request.json',_encode({'fixture':'original host requirement'}))
        config=request_fixture({name:self.binding(relative) for name,relative in
                                [('artifact','artifact.json'),('reference','reference.png'),
                                 ('spec','spec.json'),('delivery','delivery.glb')]})
        pair=lambda binding:(binding['path'],binding['sha256'])
        base=FORGE.parent.resolve()
        # This policy is used only for metadata/rejection tests. No _host/run
        # acceptance is claimed for these intentionally synthetic dependency pins.
        self.policy=NativeTerminalPolicy(object_id='object-1',revision=1,run_id='run-1',parent_root=str(self.parent),
            parent_binding=pair(self.binding('.img2threejs/state.json',parent=True)),provider='base',owner_route='base:fixture',
            plugin_root=str(base),plugin_commit='a'*40,plugin_files=(('fixture.py','b'*64),),
            python=(str(Path(sys.executable).parent.resolve()/Path(sys.executable).name),'c'*64),
            bpy_python=(str(Path(sys.executable).parent.resolve()/Path(sys.executable).name),'c'*64),git=('/usr/bin/git','d'*64),
            harness_root=str(self.harness),harness_commit='e'*40,harness_files=(('fixture.py','b'*64),),
            base_root=str(base),base_commit='a'*40,base_files=(('fixture.py','b'*64),),
            request_binding=pair(self.binding('request.json',parent=True)),reference_binding=pair(self.binding('reference.png')),
            spec_binding=pair(self.binding('spec.json')),artifact_binding=pair(self.binding('artifact.json')),
            sources=(pair(self.binding('source.py')),),required_parts=('body',),critical_features=('outline',),
            static_config_json=_encode(config),dependency_files=(),
            dependency_trees=((str(self.harness),(('fixture.py',_sha((self.harness/'fixture.py').read_bytes())),)),),
            review_id='test:projection-only',review_notes='Negative protocol fixture; not production source/runtime approval.')
        self.policies={'object-1':self.policy}
        self.authority=NativeTerminalAuthority(self.parent,self.policies)
        self.snapshot={'provider':'base','inputs':[self.binding(name) for name in ('reference.png','spec.json','artifact.json','source.py')],
                       'captures':[],'deterministic':{'fixture':'projection only'},'policySha256':'f'*64}
        for index,role in enumerate(('render','orbit-0','orbit-90','orbit-180','orbit-270','part-0-crop')):
            path='projection/'+str(index)+'.png';target=self.child/path;target.parent.mkdir(exist_ok=True);target.write_bytes(self.reference)
            self.snapshot['captures'].append({'role':role,'path':path,'sha256':_sha(self.reference),
                'pixelSha256':_sha(bytes([255,255,255,255])*4),'width':2,'height':2,'channels':4,
                'view':{'fixtureCamera':index}})

    def binding(self,path,parent=False):
        return {'path':path,'sha256':_sha(((self.parent if parent else self.child)/path).read_bytes())}

    def test_no_dictionary_policy_or_mutable_nested_approval(self):
        with self.assertRaises(ParentError):NativeTerminalAuthority(self.parent,{'object-1':{'approved':True}})
        for mutation in ({'required_parts':[]},{'critical_features':[]},{'sources':[]},
                         {'static_config_json':b'{}'},{'review_notes':''},{'provider':'unknown'}):
            with self.subTest(mutation=mutation),self.assertRaises(ParentError):replace(self.policy,**mutation)

    def test_live_revocation_and_exact_child_state_precede_producers(self):
        self.policies.clear()
        with patch.object(self.authority,'_run') as run,self.assertRaisesRegex(ParentError,'revoked'):
            self.authority.prepare(self.child,provider='base',state=self.state)
        run.assert_not_called()
        self.policies['object-1']=self.policy
        altered=deepcopy(self.state);altered['routeChild']['revision']=2
        with self.assertRaisesRegex(ParentError,'subject-mismatch'):
            self.authority.prepare(self.child,provider='base',state=altered)

    def test_complete_source_scan_includes_authoring_and_resources(self):
        root=self.root/'source-tree';(root/'authoring').mkdir(parents=True);(root/'resources_lib').mkdir()
        (root/'authoring/scene.py').write_text('VALUE=1\n');(root/'resources_lib/storage.py').write_text('VALUE=2\n')
        before=_tree(root,sources=True)
        self.assertEqual({path for path,_ in before},{'authoring/scene.py','resources_lib/storage.py'})
        (root/'resources_lib/storage.py').write_text('VALUE=3\n')
        self.assertNotEqual(_tree(root,sources=True),before)

    def test_static_delivery_cannot_substitute_auxiliary_for_composed_entrypoint(self):
        (self.child/'composed.glb').write_bytes(b'Different composed model; rejection fixture only.')
        for entrypoint,files,reason in (
                ('composed.glb',['delivery.glb','composed.glb'],'static-entrypoint-mismatch'),
                (None,['delivery.glb','composed.glb'],'ambiguous-entrypoint'),
                ('missing.glb',['delivery.glb'],'ambiguous-entrypoint')):
            with self.subTest(entrypoint=entrypoint,files=files):
                artifact={'files':[self.binding(name) for name in files]}
                if entrypoint is not None:
                    artifact['entrypoint']=entrypoint
                _write(self.child,'artifact.json',_encode(artifact),replace=True)
                binding=self.binding('artifact.json')
                request=_decode(self.policy.static_config_json)
                request['bindings']['artifact']=binding
                policy=replace(self.policy,artifact_binding=(binding['path'],binding['sha256']),
                               static_config_json=_encode(request))
                with patch('native_terminal_authority.subprocess.run') as run:
                    with self.assertRaisesRegex(ParentError,reason):
                        self.authority._static(policy,self.child,'unused-output')
                run.assert_not_called()

    def test_projection_has_five_axes_exact_parts_and_real_png_bindings(self):
        context=self.authority._context(self.policy,self.child,self.snapshot)
        self.assertEqual([axis['id'] for axis in context['requirements']['dimensions']],list(AXES))
        self.assertEqual([value['id'] for value in context['requirements']['criticalFeatures']],['part:body','feature:outline'])
        self.assertTrue(all(axis['threshold']==3 for axis in context['requirements']['dimensions']))
        self.assertNotIn('passed',context)
        roles={image['role'] for image in context['observations']['images']}
        self.assertTrue({'reference','render','part-0-crop'}<=roles)

    def test_transient_paths_do_not_replace_pixel_camera_or_geometry_comparison(self):
        fresh=deepcopy(self.snapshot)
        fresh['captures'][0].update(path='different-temp-port/frame.png',sha256='0'*64)
        self.assertEqual(self.authority._semantic(fresh),self.authority._semantic(self.snapshot))
        fresh['captures'][0]['pixelSha256']='0'*64
        self.assertNotEqual(self.authority._semantic(fresh),self.authority._semantic(self.snapshot))
        fresh=deepcopy(self.snapshot);fresh['captures'][0]['view']['fixtureCamera']=999
        self.assertNotEqual(self.authority._semantic(fresh),self.authority._semantic(self.snapshot))

    def test_real_held_journal_rejects_forged_review_and_closed_transaction(self):
        context=self.authority._context(self.policy,self.child,self.snapshot)
        context_name=(self.child.relative_to(self.parent).as_posix()+'/'+AREA
                      +'quality-review-context-'+canonical_digest(context)+'.json')
        _write(self.parent,context_name,_encode(context))
        forged={'recordId':'f'*64,'group':'quality-review','subject':context['subject'],'status':'answered',
                'decider':'jev:fixture','answers':{}}
        record_name='.img2threejs/decisions/quality-review/'+forged['recordId']+'.json'
        _write(self.parent,record_name,_encode(forged))
        _write(self.child,AREA+'quality-review-link.json',_encode({
            'context':self.binding(context_name,parent=True),'record':self.binding(record_name,parent=True)}))
        controller=DecisionController(self.parent,'run-1')
        consent=lambda:{'granted':True,'purposes':['quality-review'],'sha256':'a'*64,'tokenBudget':10000}
        with patch.object(self.authority,'_run',return_value=deepcopy(self.snapshot)):
            with controller.workspace_transaction(consent=consent) as verify:
                with self.assertRaisesRegex(DecisionError,'record-mismatch'):
                    self.authority.verify_terminal(verify,child_workspace=self.child,provider='base',state=self.state)
            with self.assertRaisesRegex(DecisionError,'transaction-closed'):
                self.authority.verify_terminal(verify,child_workspace=self.child,provider='base',state=self.state)


if __name__=='__main__':
    unittest.main()
