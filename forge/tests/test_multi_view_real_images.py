#!/usr/bin/env python3
"""Test multi-view synthesis with real M9 Bayonet images.

This test verifies the pipeline works with actual reference images,
not just mock data. Run with: python -m unittest forge/tests/test_multi_view_real_images.py -v
"""

import sys
import unittest
import tempfile
import shutil
from pathlib import Path

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from forge.stage1b_multi_view.synthesize import synthesize_geometry_brief
from forge.stage1b_multi_view.view_counter import detect_named_views


# Fixture images
FIXTURES_DIR = Path(__file__).parent / "fixtures"
FRONT_IMAGE = FIXTURES_DIR / "m9-front.png"
BACK_IMAGE = FIXTURES_DIR / "m9-back.png"


class TestRealImageSynthesis(unittest.TestCase):
    """Test synthesis with real M9 Bayonet images (2 views)."""

    @classmethod
    def setUpClass(cls):
        """Run synthesis once for all tests."""
        if not FRONT_IMAGE.exists() or not BACK_IMAGE.exists():
            raise unittest.SkipTest("Real fixture images not found")
        cls.synthesis_result = synthesize_geometry_brief(
            image_paths=[FRONT_IMAGE, BACK_IMAGE]
        )

    def test_view_detection(self):
        """Test that front/back views are detected from real filenames."""
        views = detect_named_views([FRONT_IMAGE, BACK_IMAGE])
        self.assertIn("front", views, "Front view not detected")
        self.assertIn("back", views, "Back view not detected")

    def test_opposing_views_detected(self):
        """Test that real images are identified as opposing views."""
        self.assertEqual(self.synthesis_result["synthesisMode"], "opposing-views")

    def test_confidence_is_silhouette_based(self):
        """Test that confidence uses silhouette-based scoring (not feature matching)."""
        confidence = self.synthesis_result["confidence"]
        # Silhouette-based confidence should be > 0.8
        # Feature matching would fail (confidence ~0.02)
        self.assertGreater(confidence, 0.8, f"Confidence {confidence} too low for silhouette-based")

    def test_status_is_complete(self):
        """Test that status is 'complete' (not 'evidence-only')."""
        self.assertEqual(self.synthesis_result["status"], "complete")

    def test_two_views_recognized(self):
        """Test that exactly 2 views are recognized."""
        self.assertEqual(self.synthesis_result["viewCount"], 2)
        self.assertEqual(len(self.synthesis_result["namedViews"]), 2)

    def test_named_views_correct(self):
        """Test that named views are front and back."""
        named_views = set(self.synthesis_result["namedViews"].keys())
        self.assertEqual(named_views, {"front", "back"})


class TestMultiViewSynthesis3Images(unittest.TestCase):
    """Test synthesis with 3 images (front/back/left)."""

    @classmethod
    def setUpClass(cls):
        """Create temporary directory with 3 images."""
        if not FRONT_IMAGE.exists():
            raise unittest.SkipTest("Real fixture images not found")
        
        cls.tmpdir = tempfile.mkdtemp()
        cls.tmpdir_path = Path(cls.tmpdir)
        
        # Create 3 copies with different view names
        for name in ['front', 'back', 'left']:
            shutil.copy(FRONT_IMAGE, cls.tmpdir_path / f'm9-{name}.png')
        
        cls.image_paths = list(cls.tmpdir_path.glob('m9-*.png'))
        cls.synthesis_result = synthesize_geometry_brief(
            image_paths=cls.image_paths
        )

    @classmethod
    def tearDownClass(cls):
        """Clean up temporary directory."""
        shutil.rmtree(cls.tmpdir)

    def test_three_views_recognized(self):
        """Test that 3 views are recognized."""
        self.assertEqual(self.synthesis_result["viewCount"], 3)

    def test_standard_synthesis_mode(self):
        """Test that 3+ images use standard synthesis mode (not opposing-views)."""
        self.assertEqual(self.synthesis_result["synthesisMode"], "standard")

    def test_evidence_only_status(self):
        """Test that 3+ images get evidence-only status."""
        self.assertEqual(self.synthesis_result["status"], "evidence-only")

    def test_named_views_correct(self):
        """Test that named views are front, back, left."""
        named_views = set(self.synthesis_result["namedViews"].keys())
        self.assertEqual(named_views, {"front", "back", "left"})


class TestMultiViewSynthesis4Images(unittest.TestCase):
    """Test synthesis with 4 images (front/back/left/right)."""

    @classmethod
    def setUpClass(cls):
        """Create temporary directory with 4 images."""
        if not FRONT_IMAGE.exists():
            raise unittest.SkipTest("Real fixture images not found")
        
        cls.tmpdir = tempfile.mkdtemp()
        cls.tmpdir_path = Path(cls.tmpdir)
        
        # Create 4 copies with different view names
        for name in ['front', 'back', 'left', 'right']:
            shutil.copy(FRONT_IMAGE, cls.tmpdir_path / f'm9-{name}.png')
        
        cls.image_paths = list(cls.tmpdir_path.glob('m9-*.png'))
        cls.synthesis_result = synthesize_geometry_brief(
            image_paths=cls.image_paths
        )

    @classmethod
    def tearDownClass(cls):
        """Clean up temporary directory."""
        shutil.rmtree(cls.tmpdir)

    def test_four_views_recognized(self):
        """Test that 4 views are recognized."""
        self.assertEqual(self.synthesis_result["viewCount"], 4)

    def test_standard_synthesis_mode(self):
        """Test that 4 images use standard synthesis mode."""
        self.assertEqual(self.synthesis_result["synthesisMode"], "standard")

    def test_named_views_correct(self):
        """Test that named views are front, back, left, right."""
        named_views = set(self.synthesis_result["namedViews"].keys())
        self.assertEqual(named_views, {"front", "back", "left", "right"})


class TestMultiViewSynthesis5Images(unittest.TestCase):
    """Test synthesis with 5 images (front/back/left/right/top)."""

    @classmethod
    def setUpClass(cls):
        """Create temporary directory with 5 images."""
        if not FRONT_IMAGE.exists():
            raise unittest.SkipTest("Real fixture images not found")
        
        cls.tmpdir = tempfile.mkdtemp()
        cls.tmpdir_path = Path(cls.tmpdir)
        
        # Create 5 copies with different view names
        for name in ['front', 'back', 'left', 'right', 'top']:
            shutil.copy(FRONT_IMAGE, cls.tmpdir_path / f'm9-{name}.png')
        
        cls.image_paths = list(cls.tmpdir_path.glob('m9-*.png'))
        cls.synthesis_result = synthesize_geometry_brief(
            image_paths=cls.image_paths
        )

    @classmethod
    def tearDownClass(cls):
        """Clean up temporary directory."""
        shutil.rmtree(cls.tmpdir)

    def test_five_views_recognized(self):
        """Test that 5 views are recognized."""
        self.assertEqual(self.synthesis_result["viewCount"], 5)

    def test_synthesis_mode(self):
        """Test that 5 images use full synthesis mode."""
        self.assertEqual(self.synthesis_result["synthesisMode"], "full")

    def test_named_views_correct(self):
        """Test that named views include all 5 views."""
        named_views = set(self.synthesis_result["namedViews"].keys())
        self.assertEqual(named_views, {"front", "back", "left", "right", "top"})


class TestMultiViewSynthesis6Images(unittest.TestCase):
    """Test synthesis with 6 images (front/back/left/right/top/bottom)."""

    @classmethod
    def setUpClass(cls):
        """Create temporary directory with 6 images."""
        if not FRONT_IMAGE.exists():
            raise unittest.SkipTest("Real fixture images not found")
        
        cls.tmpdir = tempfile.mkdtemp()
        cls.tmpdir_path = Path(cls.tmpdir)
        
        # Create 6 copies with different view names
        for name in ['front', 'back', 'left', 'right', 'top', 'bottom']:
            shutil.copy(FRONT_IMAGE, cls.tmpdir_path / f'm9-{name}.png')
        
        cls.image_paths = list(cls.tmpdir_path.glob('m9-*.png'))
        cls.synthesis_result = synthesize_geometry_brief(
            image_paths=cls.image_paths
        )

    @classmethod
    def tearDownClass(cls):
        """Clean up temporary directory."""
        shutil.rmtree(cls.tmpdir)

    def test_six_views_recognized(self):
        """Test that 6 views are recognized."""
        self.assertEqual(self.synthesis_result["viewCount"], 6)

    def test_synthesis_mode(self):
        """Test that 6 images use full synthesis mode."""
        self.assertEqual(self.synthesis_result["synthesisMode"], "full")

    def test_named_views_correct(self):
        """Test that named views include all 6 views."""
        named_views = set(self.synthesis_result["namedViews"].keys())
        self.assertEqual(named_views, {"front", "back", "left", "right", "top", "bottom"})


if __name__ == "__main__":
    unittest.main()
