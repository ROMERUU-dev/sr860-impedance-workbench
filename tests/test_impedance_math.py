import math
import unittest

from barrido import impedance_from_series_divider, parse_ohms_label, source_phasor_from_lockin_reference


class ImpedanceMathTest(unittest.TestCase):
    def test_series_divider_returns_real_resistance_at_zero_phase(self) -> None:
        source_v = source_phasor_from_lockin_reference(1.0, 0.0)
        dut_v = 0.5 + 0.0j

        impedance = impedance_from_series_divider(1000.0, source_v, dut_v)

        self.assertAlmostEqual(impedance.real, 1000.0, places=9)
        self.assertAlmostEqual(impedance.imag, 0.0, places=9)

    def test_series_divider_keeps_real_resistance_when_lockin_phase_is_rotated(self) -> None:
        source_v = source_phasor_from_lockin_reference(1.0, 37.0)
        dut_v = source_v * (1500.0 / (1000.0 + 1500.0))

        impedance = impedance_from_series_divider(1000.0, source_v, dut_v)

        self.assertAlmostEqual(impedance.real, 1500.0, places=9)
        self.assertAlmostEqual(impedance.imag, 0.0, places=9)

    def test_source_phase_uses_negative_lockin_reference_phase(self) -> None:
        source_v = source_phasor_from_lockin_reference(1.0, 90.0)

        self.assertAlmostEqual(source_v.real, 0.0, places=12)
        self.assertAlmostEqual(source_v.imag, -1.0, places=12)
        self.assertAlmostEqual(abs(source_v), 1.0, places=12)
        self.assertAlmostEqual(math.degrees(math.atan2(source_v.imag, source_v.real)), -90.0, places=12)

    def test_parse_ohms_label_handles_gui_options(self) -> None:
        self.assertEqual(parse_ohms_label("50 Ω"), 50.0)
        self.assertEqual(parse_ohms_label("0 Ω"), 0.0)
        self.assertEqual(parse_ohms_label("1 MΩ"), 1_000_000.0)


if __name__ == "__main__":
    unittest.main()
