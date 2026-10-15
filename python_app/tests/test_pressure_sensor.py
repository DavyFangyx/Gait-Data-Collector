import unittest

from python_app.pressure_sensor import (
    FOOT_ID_LEFT,
    FRAME_HEADER,
    FRAME_LEN,
    PACKED_DATA_LEN,
    _consume_frames,
    sum_checksum,
    verify_checksum,
)


def _left_frame(payload: bytes | None = None) -> bytes:
    data = payload if payload is not None else bytes(PACKED_DATA_LEN)
    body = FRAME_HEADER + bytes([FOOT_ID_LEFT]) + data
    return body + bytes([sum_checksum(body)])


class PressureFrameBufferTests(unittest.TestCase):
    def test_consume_frames_preserves_next_frame_after_duplicate_prefix(self):
        prefix = FRAME_HEADER + bytes([FOOT_ID_LEFT])
        valid_frame = _left_frame()
        buffer = bytearray(prefix + valid_frame)

        frames = _consume_frames(buffer, FRAME_LEN, prefix)

        self.assertEqual(len(frames), 2)
        self.assertFalse(verify_checksum(frames[0]))
        self.assertEqual(frames[1], valid_frame)
        self.assertTrue(verify_checksum(frames[1]))
        self.assertEqual(buffer, bytearray())

    def test_consume_frames_reads_valid_frame_after_noise(self):
        prefix = FRAME_HEADER + bytes([FOOT_ID_LEFT])
        valid_frame = _left_frame(bytes(range(PACKED_DATA_LEN)))
        buffer = bytearray(b"noise" + valid_frame)

        frames = _consume_frames(buffer, FRAME_LEN, prefix)

        self.assertEqual(frames, [valid_frame])
        self.assertEqual(buffer, bytearray())

    def test_valid_payload_prefix_does_not_split_the_frame(self):
        prefix = FRAME_HEADER + bytes([FOOT_ID_LEFT])
        payload = prefix * (PACKED_DATA_LEN // len(prefix))
        first, second = _left_frame(payload), _left_frame()
        buffer = bytearray(first + second)
        self.assertEqual(_consume_frames(buffer, FRAME_LEN, prefix), [first, second])
        self.assertFalse(buffer)

    def test_resynchronization_preserves_partial_next_frame(self):
        prefix = FRAME_HEADER + bytes([FOOT_ID_LEFT])
        valid = _left_frame()
        buffer = bytearray(prefix + valid[:-1])
        candidates = _consume_frames(buffer, FRAME_LEN, prefix)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(verify_checksum(candidates[0]))
        self.assertEqual(buffer, valid[:-1])
        buffer.extend(valid[-1:])
        self.assertEqual(_consume_frames(buffer, FRAME_LEN, prefix), [valid])
        self.assertFalse(buffer)

    def test_split_prefix_survives_noise(self):
        prefix = FRAME_HEADER + bytes([FOOT_ID_LEFT])
        valid = _left_frame()
        buffer = bytearray(b'noise' + prefix[:1])
        self.assertEqual(_consume_frames(buffer, FRAME_LEN, prefix), [])
        buffer.extend(valid[1:])
        self.assertEqual(_consume_frames(buffer, FRAME_LEN, prefix), [valid])


if __name__ == "__main__":
    unittest.main()
