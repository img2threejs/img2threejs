"""
Multi-View Synthesis Module

Synthesizes 3D geometry information from multiple reference images.
"""

from .synthesize import synthesize_geometry_brief
from .view_counter import count_views, detect_named_views, group_duplicate_views
from .feature_detector import detect_features
from .feature_matcher import match_features
from .pose_estimator import estimate_relative_poses
from .depth_estimator import estimate_depth
from .integrate import (
    run_synthesis_for_intake,
    enhance_intake_record_with_synthesis,
    get_synthesis_summary,
)
from .brief_enhancer import (
    enhance_spec_with_brief,
    enhance_components_with_brief,
    load_brief_from_file,
    get_brief_summary,
)
from .build_enhancer import (
    enhance_build_with_brief,
    enhance_build_components,
    generate_geometry_from_brief,
    get_brief_for_build,
)
from .review_enhancer import (
    enhance_review_with_multi_view,
    compare_multi_view,
    aggregate_multi_view_scores,
    generate_multi_view_report,
)
from .hybrid_synthesis import (
    hybrid_synthesis,
    combine_results,
    create_agent_analysis_template,
)

__all__ = [
    "synthesize_geometry_brief",
    "count_views",
    "detect_named_views",
    "group_duplicate_views",
    "detect_features",
    "match_features",
    "estimate_relative_poses",
    "estimate_depth",
    "run_synthesis_for_intake",
    "enhance_intake_record_with_synthesis",
    "get_synthesis_summary",
    "enhance_spec_with_brief",
    "enhance_components_with_brief",
    "load_brief_from_file",
    "get_brief_summary",
    "enhance_build_with_brief",
    "enhance_build_components",
    "generate_geometry_from_brief",
    "get_brief_for_build",
    "enhance_review_with_multi_view",
    "compare_multi_view",
    "aggregate_multi_view_scores",
    "generate_multi_view_report",
    "hybrid_synthesis",
    "combine_results",
    "create_agent_analysis_template",
]
