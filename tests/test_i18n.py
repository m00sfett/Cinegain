#!/usr/bin/env python3
"""
Tests for the UI language mapping in ui/i18n.py.
"""
import importlib.util
import os
import unittest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
I18N_PATH = os.path.join(BASE_DIR, "ui", "i18n.py")


def load_i18n():
    """Load ui/i18n.py as a fresh module so that tests do not share state."""
    spec = importlib.util.spec_from_file_location("i18n_under_test", I18N_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLanguageMapping(unittest.TestCase):
    LOCALE_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")

    def setUp(self):
        self.i18n = load_i18n()
        self._saved_env = {name: os.environ.get(name) for name in self.LOCALE_VARS}
        for name in self.LOCALE_VARS:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _system_lang(self, locale):
        os.environ["LANG"] = locale
        return self.i18n.get_system_lang()

    def test_01_indonesian_locale_maps_to_id(self):
        self.assertEqual(self._system_lang("id_ID.UTF-8"), "id")

    def test_02_legacy_in_locale_maps_to_id(self):
        self.assertEqual(self._system_lang("in_ID.UTF-8"), "id")

    def test_03_set_language_accepts_legacy_code(self):
        self.assertTrue(self.i18n.set_language("in"))
        self.assertEqual(self.i18n.get_current_language(), "id")

    def test_04_unknown_locale_falls_back_to_english(self):
        self.assertEqual(self._system_lang("xx_XX.UTF-8"), "en")

    def test_05_every_language_has_a_display_name(self):
        self.assertEqual(set(self.i18n.LANGUAGE_NAMES), set(self.i18n.TRANSLATIONS))

    def test_06_all_languages_share_the_same_keys(self):
        reference = set(self.i18n.TRANSLATIONS["en"])
        for lang, table in self.i18n.TRANSLATIONS.items():
            self.assertEqual(set(table), reference, f"key set differs for {lang}")

    def test_07_default_slot_name_and_keyboard_help_exist_in_every_language(self):
        """#43: the default slot name takes {n}; the subtitle carries no keyboard help any more."""
        for lang, table in self.i18n.TRANSLATIONS.items():
            self.assertIn("{n}", table["slot_default_name"], lang)
            self.assertIn("↑↓", table["kbd_help_tip"], lang)
            self.assertNotIn("↑", table["app_subtitle"], lang)
            if lang != "en":
                self.assertNotEqual(table["slot_default_name"], "User {n}", lang)
        self.assertEqual(self.i18n.TRANSLATIONS["de"]["slot_default_name"].format(n=2), "Platz 2")


if __name__ == "__main__":
    unittest.main()
