from __future__ import annotations

import struct
import unittest

from driver.harness.packets import PacketDecodeError, Reader, decode_packet, decode_row


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def vstring(text: str) -> bytes:
    raw = text.encode()
    return varint(len(raw)) + raw


class CommandRequestTests(unittest.TestCase):
    def payload(self) -> bytes:
        return (
            vstring("/give @s minecraft:stone 5")
            + bytes([6])
            + vstring("player")
            + bytes(range(16))
            + vstring("")
            + struct.pack("<q", 0)
            + vstring("latest")
        )

    def test_decodes_real_layout(self) -> None:
        decoded = decode_packet(77, self.payload())
        self.assertEqual(decoded["name"], "CommandRequest")
        fields = decoded["fields"]
        self.assertEqual(fields["command"], "/give @s minecraft:stone 5")
        self.assertEqual(fields["origin_type"], 6)
        self.assertEqual(fields["origin_name"], "player")
        self.assertEqual(fields["origin_uuid"], "00010203-0405-0607-0809-0a0b0c0d0e0f")
        self.assertEqual(fields["version"], "latest")

    def test_truncated_payload_reports_error(self) -> None:
        decoded = decode_packet(77, self.payload()[:5])
        self.assertIn("decode_error", decoded)


class PlayerActionTests(unittest.TestCase):
    def test_decodes(self) -> None:
        payload = varint(123) + varint(2) + struct.pack("<fff", 1.5, 64.0, -3.25) + varint(1)
        fields = decode_packet(36, payload)["fields"]
        self.assertEqual(fields["runtime_id"], 123)
        self.assertEqual(fields["action"], 2)
        self.assertAlmostEqual(fields["x"], 1.5)
        self.assertAlmostEqual(fields["z"], -3.25)
        self.assertEqual(fields["face"], 1)


class MovePlayerTests(unittest.TestCase):
    def test_decodes(self) -> None:
        payload = (
            varint(9)
            + struct.pack("<ffffff", 1.0, 2.0, 3.0, 10.0, 20.0, 30.0)
            + bytes([0, 1])
            + varint(0)
            + varint(42)
        )
        fields = decode_packet(19, payload)["fields"]
        self.assertEqual(fields["runtime_id"], 9)
        self.assertAlmostEqual(fields["yaw"], 20.0)
        self.assertTrue(fields["on_ground"])
        self.assertEqual(fields["tick"], 42)


class UnknownTests(unittest.TestCase):
    def test_unknown_id_keeps_hex(self) -> None:
        decoded = decode_packet(999, b"\x01\x02")
        self.assertFalse(decoded["known"])
        self.assertEqual(decoded["name"], "id_999")
        self.assertEqual(decoded["hex"], "0102")
        self.assertNotIn("fields", decoded)

    def test_decode_row(self) -> None:
        row = {"id": 77, "hex": CommandRequestTests().payload().hex()}
        decoded = decode_row(row)
        self.assertEqual(decoded["fields"]["origin_name"], "player")

    def test_reader_bounds(self) -> None:
        with self.assertRaises(PacketDecodeError):
            Reader(b"").u8()


class PacketNameTableTests(unittest.TestCase):
    def test_table_matches_the_1_26_51_enum(self) -> None:
        from driver.harness.packets import PACKET_NAMES

        self.assertEqual(PACKET_NAMES[13], "AddActor")
        self.assertEqual(PACKET_NAMES[19], "MovePlayer")
        self.assertEqual(PACKET_NAMES[36], "PlayerAction")
        self.assertEqual(PACKET_NAMES[77], "CommandRequest")
        self.assertEqual(PACKET_NAMES[111], "MoveDeltaActor")
        self.assertEqual(PACKET_NAMES[144], "PlayerAuthInputPacket")
        self.assertGreaterEqual(len(PACKET_NAMES), 250)
        self.assertNotIn("EndId", PACKET_NAMES.values())

    def test_recv_row_decodes_with_direction_kept(self) -> None:
        payload = (
            varint(9)
            + struct.pack("<ffffff", 1.0, 2.0, 3.0, 10.0, 20.0, 30.0)
            + bytes([0, 1])
            + varint(0)
            + varint(42)
        )
        row = {"id": 19, "hex": payload.hex(), "dir": "recv"}
        decoded = decode_row(row)
        self.assertEqual(decoded["name"], "MovePlayer")
        self.assertEqual(row["dir"], "recv")


if __name__ == "__main__":
    unittest.main()
