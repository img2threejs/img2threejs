"""Owned finite placement facts for offline protocol tests, never native measurement proof."""


def placement_records_fixture():
    return {
        "camera": {"type": "perspective", "position": [0, 0, 3], "target": [0, 0, 0],
                   "up": [0, 1, 0], "fov": 45, "aspect": 1, "near": .1, "far": 100},
        "depth": {"cameraDepth": 3, "relativeDepth": 1, "metricLandmark": [0, 0, 0]},
        "support": {"mode": "free", "supportObjectId": None},
        "size-prior": {"minScale": [.5]*3, "maxScale": [2]*3,
                       "authoredMetricHeight": 1, "heightBand": {"min": .5, "max": 2}},
    }
