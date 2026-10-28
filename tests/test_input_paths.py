from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PhysicalInputContractTests(unittest.TestCase):
    """The default movement path must be real keyboard input, not flags."""

    def test_mod_defaults_are_physical(self) -> None:
        header = (ROOT / "mod/probe/src/mod/ProbeMod.h").read_text(encoding="utf-8")
        self.assertIn("mButtonMode{0}", header)
        self.assertIn("mJumpImpulseMode{0}", header)
        self.assertIn("mSprintTapPhase", header)
        self.assertIn("applyRealInput", header)

    def test_real_input_uses_hardware_hid_keys(self) -> None:
        source = (ROOT / "mod/probe/src/mod/Input.cpp").read_text(encoding="utf-8")
        self.assertIn("applyRealInput", source)
        self.assertIn("hidKeyEvent(codes[i], want[i], true, error)", source)
        self.assertIn("getKeymappingByAction", source)
        self.assertIn('"key.sprint"', source)
        self.assertIn("refreshBindings", source)
        self.assertIn("mBindings.sprint", source)

    def test_legacy_modes_require_force(self) -> None:
        source = (ROOT / "mod/probe/src/mod/CommandInput.cpp").read_text(encoding="utf-8")
        self.assertIn("legacy_input_mode_requires_force", source)
        self.assertIn("legacy_jump_impulse_requires_force", source)
        client = (ROOT / "driver/harness/commands/input.py").read_text(encoding="utf-8")
        self.assertIn("默认 mode=0 走真实 HID 按键", client)
        self.assertIn("默认 0 走真实 Space", client)

    def test_jump_command_is_not_a_direct_impulse_by_default(self) -> None:
        source = (ROOT / "mod/probe/src/mod/Input.cpp").read_text(encoding="utf-8")
        # The direct impulse may only live behind the legacy early-return branch.
        real = source.split("void ProbeMod::applyRealInput", 1)[1].split(
            "void ProbeMod::applyControls", 1
        )[0]
        self.assertNotIn("jumpFromGround", real)
        self.assertNotIn("_updateClientMoveVector", real)
        self.assertNotIn("MoveInputComponentSprinting", real)


if __name__ == "__main__":
    unittest.main()
