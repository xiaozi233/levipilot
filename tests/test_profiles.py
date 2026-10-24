from __future__ import annotations

import unittest

from driver.gen_profiles import HEADER, ProfileError, load_profiles, render


class GeneratedProfilesTests(unittest.TestCase):
    def test_header_is_up_to_date(self) -> None:
        # A profile edit without regeneration must fail here, not ship silently.
        self.assertTrue(HEADER.exists(), f"missing {HEADER}")
        expected = render(load_profiles())
        actual = HEADER.read_text(encoding="utf-8")
        self.assertEqual(actual, expected, "run: py -3.11 driver/gen_profiles.py")

    def test_runtime_profiles_are_unique(self) -> None:
        profiles = load_profiles()
        self.assertGreaterEqual(len(profiles), 1)
        build_ids = [profile["build_id"] for profile in profiles]
        self.assertEqual(len(build_ids), len(set(build_ids)))
        for profile in profiles:
            self.assertNotEqual(profile["version"], "")
            self.assertNotEqual(profile["build_id"], "")

    def test_legacy_profile_is_not_runtime(self) -> None:
        # docs/offsets/1.26.45.01.json is reference data with a different schema.
        build_ids = {profile["build_id"] for profile in load_profiles()}
        self.assertNotIn("1.26.45.1", build_ids)

    def test_current_version_profile_matches_the_game_build(self) -> None:
        profiles = {profile["build_id"]: profile for profile in load_profiles()}
        profile = profiles["1.26.51.1"]
        self.assertEqual(profile["version"], "1.26.51.01")
        anchors = profile["anchors"]
        self.assertIn("LocalPlayer::normalTick", anchors)
        self.assertEqual(len(anchors["LocalPlayer::normalTick"]["expected_hex"]), 48)
        fields = profile["structs"]["StateVector"]
        self.assertEqual(fields["VelY"], "0x1C")
        self.assertEqual(profile["components"]["StateVector"]["id"], 459100728)

    def test_malformed_component_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            # A component without size must not silently become id 0.
            render(
                [
                    {
                        "file": "x",
                        "version": "1.0",
                        "build_id": "1.0.0.1",
                        "version_prefix": "",
                        "anchors": {},
                        "structs": {},
                        "components": {"Bad": {"id": 1}},
                    }
                ]
            )

    def test_malformed_anchor_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            # A bare byte string must not render as an anchor with an empty
            # signature; that would silently disable the version check.
            render(
                [
                    {
                        "file": "x",
                        "version": "1.0",
                        "build_id": "1.0.0.1",
                        "version_prefix": "",
                        "anchors": {"LocalPlayer::normalTick": "AABBCC"},
                        "structs": {},
                        "components": {},
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
