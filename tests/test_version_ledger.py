#!/usr/bin/env python3
"""
Version ledger: every place that carries the Cinegain version must agree.

Runs offline. The release workflow runs it with RELEASE_TAG set, so a tag that
does not match the sources stops the release before anything is built.
Dependency minimums (e.g. PipeWire >= 0.3.50) are not app versions; they are
checked separately so a broad search-and-replace cannot bend them again (#21).
"""
import os
import re
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PIPEWIRE_MIN = "0.3.50"


def read(rel):
    with open(os.path.join(BASE_DIR, rel), encoding="utf-8") as fh:
        return fh.read()


def one(pattern, text, rel):
    found = re.findall(pattern, text, re.MULTILINE)
    if len(found) != 1:
        raise AssertionError(f"{rel}: expected exactly one match for {pattern!r}, got {found!r}")
    return found[0]


def source_version():
    return one(r'^__version__ = "([^"]+)"$', read("bin/cinegain"), "bin/cinegain")


class TestVersionLedger(unittest.TestCase):
    def setUp(self):
        self.version = source_version()

    def test_semver(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")

    def test_single_value_places(self):
        places = {
            "ui/cinegain-gui.py": r'^APP_VERSION = "([^"]+)"$',
            "install.sh": r'^VERSION="([^"]+)"$',
            "packaging/debian/build-deb.sh": r'^VERSION="([^"]+)"$',
            "packaging/arch/PKGBUILD": r"^pkgver=(\S+)$",
            "packaging/rpm/cinegain.spec": r"^Version:\s+(\S+)$",
        }
        for rel, pattern in places.items():
            with self.subTest(file=rel):
                self.assertEqual(one(pattern, read(rel), rel), self.version)

    def test_rpm_changelog_top_entry(self):
        spec = read("packaging/rpm/cinegain.spec")
        tail = spec.split("%changelog", 1)[1]
        top = re.search(r"^\* \w{3} \w{3} \d{1,2} \d{4} .+ - (\S+)-\d+$", tail, re.MULTILINE)
        self.assertIsNotNone(top, "no %changelog entry")
        self.assertEqual(top.group(1), self.version)

    def test_changelog_top_section(self):
        top = re.search(r"^## \[([^\]]+)\]", read("CHANGELOG.md"), re.MULTILINE)
        self.assertIsNotNone(top, "no version section in CHANGELOG.md")
        self.assertEqual(top.group(1), self.version)

    def test_footer_strings(self):
        footers = re.findall(r'"footer_version": "Cinegain v([^ ]+) ', read("ui/i18n.py"))
        self.assertTrue(footers)
        self.assertEqual(set(footers), {self.version})

    def test_readme(self):
        readme = read("README.md")
        self.assertEqual(one(r"badge/Release-v([^-]+)-", readme, "README.md"), self.version)
        names = re.findall(r"cinegain[-_](\d+\.\d+\.\d+)[-_]", readme)
        self.assertTrue(names)
        self.assertEqual(set(names), {self.version})

    def test_pipewire_minimum_untouched(self):
        self.assertIn(f"pipewire (>= {PIPEWIRE_MIN})", read("packaging/debian/build-deb.sh"))
        self.assertIn(f"pipewire >= {PIPEWIRE_MIN}", read("packaging/rpm/cinegain.spec"))
        self.assertIn(f"(>= {PIPEWIRE_MIN})", read("README.md"))

    def test_release_tag(self):
        tag = os.environ.get("RELEASE_TAG")
        if not tag:
            self.skipTest("RELEASE_TAG not set (only set by the release workflow)")
        self.assertRegex(tag, r"^v\d+\.\d+\.\d+$")
        self.assertEqual(tag, f"v{self.version}")


if __name__ == "__main__":
    unittest.main()
