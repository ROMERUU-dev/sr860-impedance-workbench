import math
import unittest

import numpy as np

from barrido import (
    MeasurementPoint,
    build_characterization_summary,
    impedance_from_series_divider,
    parse_ohms_label,
    scale_series_to_si,
    source_phasor_from_lockin_reference,
)


def make_point(frequency_hz: float, z_complex: complex) -> MeasurementPoint:
    return MeasurementPoint(
        frequency_hz=frequency_hz,
        x_v=0.0,
        y_v=0.0,
        source_v=1.0,
        source_phase_deg=0.0,
        external_series_ohm=220.0,
        source_series_ohm=50.0,
        total_series_ohm=270.0,
        z_complex=z_complex,
    )


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

    def test_resistor_summary_reports_real_impedance(self) -> None:
        points = [make_point(freq, complex(1500.0 + freq * 0.01, 2.0)) for freq in (100.0, 300.0, 1000.0)]

        summary = build_characterization_summary(points, "Resistencia")

        self.assertIn("Resumen Resistencia", summary["lines"][0])
        self.assertIn("1.503 kΩ", summary["lines"][0])
        self.assertEqual(summary["used_points"], 3)

    def test_capacitor_summary_prefers_capacitive_phase_points(self) -> None:
        capacitance_f = 100e-9
        points = [
            make_point(freq, complex(1.0, -1.0 / (2.0 * math.pi * freq * capacitance_f)))
            for freq in (5_000.0, 10_000.0, 30_000.0)
        ]

        summary = build_characterization_summary(points, "Capacitor")

        self.assertIn("Resumen Capacitor", summary["lines"][0])
        self.assertIn("100 nF", summary["lines"][0])
        self.assertEqual(summary["used_points"], 3)

    def test_inductor_summary_reports_inductance(self) -> None:
        inductance_h = 44.4e-3
        points = [
            make_point(freq, complex(90.0, 2.0 * math.pi * freq * inductance_h))
            for freq in (300.0, 1000.0, 3000.0)
        ]

        summary = build_characterization_summary(points, "Inductor")

        self.assertIn("Resumen Inductor", summary["lines"][0])
        self.assertIn("44.4 mH", summary["lines"][0])
        self.assertEqual(summary["used_points"], 3)

    def test_scale_series_to_si_uses_readable_capacitance_unit(self) -> None:
        scaled, unit = scale_series_to_si(np.array([47e-9, 100e-9, math.nan]), "F")

        self.assertEqual(unit, "nF")
        self.assertAlmostEqual(float(scaled[1]), 100.0)

    def test_scale_series_to_si_uses_readable_impedance_unit(self) -> None:
        scaled, unit = scale_series_to_si(np.array([1_500.0, 2_200.0]), "Î©")

        self.assertEqual(unit, "kÎ©")
        self.assertAlmostEqual(float(scaled[0]), 1.5)


if __name__ == "__main__":
    unittest.main()
