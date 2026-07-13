import os
import unittest
from unittest.mock import patch

from kirby_auto_book import BookingConfig, complete_booking, is_enabled, validate_config


class AutoBookConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_enabled())

    def test_valid_config(self):
        config = BookingConfig(
            name_last="星野",
            name_first="太郎",
            kana_last="ホシノ",
            kana_first="タロウ",
            mobile="09012345678",
            email="test@example.com",
            privacy_consent="YES",
        )
        self.assertEqual(validate_config(config), [])

    def test_rejects_missing_or_invalid_values(self):
        config = BookingConfig("", "", "", "", "abc", "bad", "")
        errors = validate_config(config)
        self.assertIn("KIRBY_NAME_LAST", errors)
        self.assertIn("KIRBY_MOBILE_FORMAT", errors)
        self.assertIn("KIRBY_EMAIL_FORMAT", errors)
        self.assertIn("KIRBY_PRIVACY_CONSENT", errors)

    def test_never_books_outside_july_2026(self):
        config = BookingConfig(
            "星野", "太郎", "ホシノ", "タロウ",
            "09012345678", "test@example.com", "YES",
        )
        result = complete_booking(None, None, "2026-08-01 10:00", config)
        self.assertEqual((result.status, result.code), ("error", "outside_july_2026"))


if __name__ == "__main__":
    unittest.main()
