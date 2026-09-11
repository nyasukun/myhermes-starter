"""Cross-language RFC vector and Python 3.11 creation semantics."""

from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid
from unittest.mock import patch

from myhermes.relay_ids import new_request_id


class RelayRequestIdTests(unittest.TestCase):
    def test_rfc_vector_and_version_variant_timestamp(self):
        expected = uuid.UUID("017f22e2-79b0-7cc3-98c4-dc0c0c07398f")
        random = (((expected.int >> 64) & 0xFFF) << 62) | (expected.int & ((1 << 62) - 1))
        with patch("myhermes.relay_ids.secrets.randbits", return_value=random) as source:
            self.assertEqual(new_request_id(1645557742000), str(expected))
        source.assert_called_once_with(74)
        with patch("myhermes.relay_ids.time.time_ns", return_value=1645557742000123456):
            generated = uuid.UUID(new_request_id())
        self.assertEqual(generated.int >> 80, 1645557742000)
        self.assertEqual((generated.int >> 76) & 15, 7)
        self.assertEqual((generated.int >> 62) & 3, 2)

    def test_parallel_same_millisecond_uses_randomness_without_host_or_global_counter(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: new_request_id(1645557742000), range(4000)))
        self.assertEqual(len(set(values)), len(values))
        self.assertTrue(all(uuid.UUID(value).int >> 80 == 1645557742000 for value in values))
        for value in (-1, 1 << 48, True, 1.5, "123", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                new_request_id(value)
        self.assertEqual(uuid.UUID(new_request_id(0)).int >> 80, 0)
        self.assertEqual(uuid.UUID(new_request_id((1 << 48) - 1)).int >> 80, (1 << 48) - 1)


if __name__ == "__main__":
    unittest.main()
