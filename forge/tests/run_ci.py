"""Disjoint CI profiles; the required CS2 contract is never made an optional skip."""
import argparse
from pathlib import Path
import sys
import unittest

CONTRACT_MODULE = "test_cs2_terminal_authority"


def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        else:
            yield item


def _suite(profile):
    tests = Path(__file__).resolve().parent
    root = str(tests.parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    loader = unittest.TestLoader()
    if profile == "cs2-contract":
        return loader.discover(str(tests), pattern=CONTRACT_MODULE + ".py")
    discovered = loader.discover(str(tests), pattern="test_*.py")
    return unittest.TestSuite(case for case in _cases(discovered)
                              if type(case).__module__ != CONTRACT_MODULE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("unit", "cs2-contract"))
    args = parser.parse_args(argv)
    suite = _suite(args.profile)
    if suite.countTestCases() == 0:
        parser.error("the selected required CI profile contains no tests")
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
