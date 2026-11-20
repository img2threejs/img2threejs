"""Fail-closed scene checks over externally produced measurements.

Measurements are assertions, not an image renderer or proof of producer identity. The
caller must independently produce and verify them against the current scene and
reference. Each measurement is {value, producer, sceneSha256, referenceSha256};
its hashes must match the requested comparison. Missing or stale data is unevaluated,
never a pass. No result here grants canonical workflow acceptance by itself.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

_POLICY = Path(__file__).with_name('scene_quality_policy_v1.json')
_HASH = re.compile(r'^[0-9a-f]{64}$')


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def assess_scene(scene, *, expected_object_ids, policy=None):
    """Assess D12 scene gates; return pass/fail/unevaluated with named checks.

    Input requires sceneSha256, referenceSha256, objects (nonempty list of unique
    {id, supported, boxIoU, height, scale, scalePrior:{min,max}, contactGap?}),
    intersections (exactly one {objects:[id,id], count:measurement} per pair),
    silhouetteIoU, interiorDifference and lightRigCount. Numeric fields other than
    prior bounds are measurements as defined above. An explicit zero is necessary
    for a nonintersection claim. Missing pair coverage is unevaluated.
    expected_object_ids is the independently verified routed inventory, not an
    inventory derived from the scene under review.
    """
    frozen = json.loads(_POLICY.read_text(encoding='utf-8'))
    if policy is not None and policy != frozen:
        return {'status': 'unevaluated', 'deliverable': False, 'checks': [
            {'id': 'policy', 'status': 'unevaluated', 'reason': 'policy-mismatch'}],
            'policyVersion': None}
    policy = frozen
    limits = policy['scene']
    checks = []

    def add(name, status, reason):
        checks.append({'id': name, 'status': status, 'reason': reason})

    if type(scene) is not dict or not _hash(scene.get('sceneSha256')) or not _hash(scene.get('referenceSha256')):
        return {'status': 'unevaluated', 'deliverable': False, 'checks': [
            {'id': 'bindings', 'status': 'unevaluated', 'reason': 'missing-scene-or-reference-hash'}],
            'policyVersion': policy['version']}
    scene_hash, reference_hash = scene['sceneSha256'], scene['referenceSha256']

    def measurement(value):
        if (type(value) is not dict or set(value) != {'value', 'producer', 'sceneSha256', 'referenceSha256'}
                or type(value['producer']) is not str or not value['producer'].strip()
                or value['sceneSha256'] != scene_hash or value['referenceSha256'] != reference_hash
                or not _number(value['value'])):
            return None
        return value['value']

    def bound(name, value, low, high):
        value = measurement(value)
        if value is None:
            add(name, 'unevaluated', 'missing-or-stale-measurement')
        elif value < low or value > high:
            add(name, 'fail', 'outside-required-range')
        else:
            add(name, 'pass', 'within-required-range')

    objects = scene.get('objects')
    valid_objects = (type(objects) is list and bool(objects)
                     and all(type(o) is dict and type(o.get('id')) is str and o['id'] for o in objects))
    if not valid_objects or len({o['id'] for o in objects}) != len(objects):
        add('objects', 'unevaluated', 'missing-or-duplicate-object-inventory')
        objects = []
    if (type(expected_object_ids) not in (list, tuple) or not expected_object_ids
            or any(type(item) is not str or not item for item in expected_object_ids)
            or len(set(expected_object_ids)) != len(expected_object_ids)
            or set(expected_object_ids) != {obj['id'] for obj in objects}):
        add('inventory', 'unevaluated', 'scene-does-not-match-routed-inventory')
    for obj in objects:
        prefix = 'object:' + obj['id'] + ':'
        bound(prefix + 'box-iou', obj.get('boxIoU'), limits['minimumObjectBoxIoU'], 1)
        prior = obj.get('scalePrior')
        if (type(prior) is not dict or set(prior) != {'min', 'max'} or not _number(prior['min'])
                or not _number(prior['max']) or not 0 < prior['min'] <= prior['max']):
            add(prefix + 'scale', 'unevaluated', 'missing-scale-prior')
        else:
            bound(prefix + 'scale', obj.get('scale'), prior['min'], prior['max'])
        supported = obj.get('supported')
        if type(supported) is not bool:
            add(prefix + 'contact', 'unevaluated', 'missing-support-classification')
        elif not supported:
            add(prefix + 'contact', 'pass', 'not-supported')
        else:
            height, gap = measurement(obj.get('height')), measurement(obj.get('contactGap'))
            if height is None or gap is None or height <= 0 or gap < 0:
                add(prefix + 'contact', 'unevaluated', 'missing-or-invalid-contact-measurement')
            elif gap / height > limits['maximumContactGapHeightRatio']:
                add(prefix + 'contact', 'fail', 'contact-gap-too-large')
            else:
                add(prefix + 'contact', 'pass', 'contact-gap-within-limit')

    pairs = {tuple(sorted((a['id'], b['id']))) for index, a in enumerate(objects) for b in objects[index + 1:]}
    reported = {}
    intersections = scene.get('intersections')
    if type(intersections) is list:
        for row in intersections:
            if (type(row) is not dict or type(row.get('objects')) is not list
                    or len(row['objects']) != 2 or not all(type(x) is str for x in row['objects'])):
                add('intersections', 'unevaluated', 'invalid-intersection-pair')
                continue
            pair = tuple(sorted(row['objects']))
            if pair not in pairs or pair in reported:
                add('intersections', 'unevaluated', 'unexpected-or-duplicate-pair')
                continue
            reported[pair] = row.get('count')
    elif pairs:
        add('intersections', 'unevaluated', 'missing-pair-coverage')
    for pair in sorted(pairs):
        name = 'intersection:' + ':'.join(pair)
        count = measurement(reported.get(pair))
        if count is None or type(count) is not int or count < 0:
            add(name, 'unevaluated', 'missing-or-invalid-intersection-count')
        elif count:
            add(name, 'fail', 'objects-intersect')
        else:
            add(name, 'pass', 'no-intersections')

    bound('scene:silhouette-iou', scene.get('silhouetteIoU'), limits['minimumSilhouetteIoU'], 1)
    bound('scene:interior-difference', scene.get('interiorDifference'), 0, limits['maximumInteriorDifference'])
    light_count = measurement(scene.get('lightRigCount'))
    if light_count is None or type(light_count) is not int or light_count < 0:
        add('scene:light-rig', 'unevaluated', 'missing-light-rig-count')
    else:
        add('scene:light-rig', 'pass' if light_count == 1 else 'fail', 'one-light-rig-required')
    status = ('fail' if any(c['status'] == 'fail' for c in checks) else
              'unevaluated' if any(c['status'] == 'unevaluated' for c in checks) else 'pass')
    return {'status': status, 'deliverable': status == 'pass', 'checks': checks,
            'policyVersion': policy['version']}
