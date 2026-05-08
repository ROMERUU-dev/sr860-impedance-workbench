"""
GUI para barridos de impedancia con un SR860.

La idea de esta aplicación es dejar claro qué amplitud usa el cálculo.
El script anterior asumía `Vs = 1.0 V` fijo. Eso podía introducir errores
de escala si la amplitud real del SR860 o la amplitud efectiva en el DUT
no coincidían con ese valor. Aquí la GUI separa:

1. La amplitud que se programa al SR860 (`SLVL`).
2. La amplitud efectiva usada en la ecuación de impedancia.

El resultado es una herramienta más legible, menos propensa a errores de
factor x10 y bastante más cómoda para operar desde laboratorio.
"""

from __future__ import annotations

import csv
import errno
import fcntl
import json
import math
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pyvisa
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


# Mapeo simple y directo entre los nombres visibles en la GUI
# y los códigos de comandos remotos del SR860.
TIME_CONSTANT_OPTIONS = {
    "1 us": 0,
    "3 us": 1,
    "10 us": 2,
    "30 us": 3,
    "100 us": 4,
    "300 us": 5,
    "1 ms": 6,
    "3 ms": 7,
    "10 ms": 8,
    "30 ms": 9,
    "100 ms": 10,
    "300 ms": 11,
    "1 s": 12,
    "3 s": 13,
    "10 s": 14,
    "30 s": 15,
    "100 s": 16,
    "300 s": 17,
    "1 ks": 18,
    "3 ks": 19,
    "10 ks": 20,
    "30 ks": 21,
}

TIME_CONSTANT_SECONDS = {
    "1 us": 1e-6,
    "3 us": 3e-6,
    "10 us": 10e-6,
    "30 us": 30e-6,
    "100 us": 100e-6,
    "300 us": 300e-6,
    "1 ms": 1e-3,
    "3 ms": 3e-3,
    "10 ms": 1e-2,
    "30 ms": 3e-2,
    "100 ms": 1e-1,
    "300 ms": 3e-1,
    "1 s": 1.0,
    "3 s": 3.0,
    "10 s": 10.0,
    "30 s": 30.0,
    "100 s": 100.0,
    "300 s": 300.0,
    "1 ks": 1000.0,
    "3 ks": 3000.0,
    "10 ks": 10000.0,
    "30 ks": 30000.0,
}

FILTER_SLOPE_OPTIONS = {
    "6 dB/oct": 0,
    "12 dB/oct": 1,
    "18 dB/oct": 2,
    "24 dB/oct": 3,
}

REFERENCE_SOURCE_OPTIONS = {
    "Internal": 0,
    "External": 1,
    "Dual": 2,
    "Chop": 3,
}

INPUT_MODE_OPTIONS = {
    "A": 0,
    "A-B": 1,
    "Current 1 MΩ": 2,
    "Current 100 MΩ": 3,
}

INPUT_RANGE_OPTIONS = {
    "1 V": 0,
    "300 mV": 1,
    "100 mV": 2,
    "30 mV": 3,
    "10 mV": 4,
}

INPUT_RANGE_FROM_CODE = {value: key for key, value in INPUT_RANGE_OPTIONS.items()}
REFERENCE_SOURCE_FROM_CODE = {value: key for key, value in REFERENCE_SOURCE_OPTIONS.items()}
TIME_CONSTANT_FROM_CODE = {value: key for key, value in TIME_CONSTANT_OPTIONS.items()}
FILTER_SLOPE_FROM_CODE = {value: key for key, value in FILTER_SLOPE_OPTIONS.items()}

OUTPUT_CONNECTION_OPTIONS = ("Single-ended", "Differential")
OUTPUT_LOAD_OPTIONS = ("High-Z", "50 Ω")
SOURCE_SERIES_OPTIONS = ("50 Ω", "0 Ω", "1 MΩ")
DUT_TYPE_OPTIONS = ("Resistencia", "Capacitor", "Inductor", "Impedancia mixta")
PLOT_MODE_OPTIONS = ("Auto", "R/X/Z/Fase", "R/C/Z/L")
EXPORT_CHART_OPTIONS = {
    "dashboard": "Panel 2x2 actual",
    "r": "Re(Z) vs frecuencia",
    "xz": "Xz vs frecuencia",
    "c": "C vs frecuencia",
    "z": "|Z| vs frecuencia",
    "phase": "Fase vs frecuencia",
    "l": "L vs frecuencia",
}


def resource_path(relative_path: str) -> Path:
    """
    Resuelve recursos tanto en desarrollo como dentro de PyInstaller.
    """
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def source_phasor_from_lockin_reference(magnitude_v: float, reference_phase_deg: float) -> complex:
    """
    Devuelve el fasor RMS de la fuente en el marco X/Y reportado por el SR860.

    `PHAS` rota la referencia interna usada por los detectores X/Y. La salida
    SINE OUT sigue al oscilador, por lo que en el marco del lock-in la fuente
    queda con fase `-PHAS`.
    """
    phase_rad = math.radians(-reference_phase_deg)
    return magnitude_v * complex(math.cos(phase_rad), math.sin(phase_rad))


def impedance_from_series_divider(series_resistor_ohm: float, source_v: complex, dut_v: complex) -> complex:
    """
    Calcula Z_DUT para el divisor serie Rs + DUT usando fasores RMS complejos.
    """
    denominator = source_v - dut_v
    if abs(denominator) < 1e-18:
        raise ZeroDivisionError(
            "La amplitud medida es prácticamente igual a la amplitud de excitación. "
            "No es posible calcular Z con estabilidad numérica."
        )
    return series_resistor_ohm * dut_v / denominator


def parse_ohms_label(value: str) -> float:
    normalized = value.strip().replace("Ω", "").replace("ohm", "").replace("Ohm", "").strip()
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("M", "e6").replace("k", "e3")
    return float(normalized)


@dataclass
class SweepConfig:
    dut_name: str
    dut_type_label: str
    plot_mode_label: str
    start_freq_hz: float
    stop_freq_hz: float
    points: int
    logarithmic: bool
    series_resistor_ohm: float
    source_series_ohm: float
    effective_source_v: float
    time_constant_seconds: float
    settling_factor: float
    output_amplitude_v: float
    output_connection_label: str
    output_load_label: str
    output_phase_deg: float
    dc_offset_v: float
    time_constant_label: str
    filter_slope_label: str
    reference_source_label: str
    input_mode_label: str
    input_range_label: str
    coupling_dc: bool
    shield_grounded: bool
    sync_filter: bool


@dataclass
class MeasurementPoint:
    frequency_hz: float
    x_v: float
    y_v: float
    source_v: float
    source_phase_deg: float
    external_series_ohm: float
    source_series_ohm: float
    total_series_ohm: float
    z_complex: complex

    @property
    def r_ohm(self) -> float:
        return self.z_complex.real

    @property
    def x_ohm(self) -> float:
        return self.z_complex.imag

    @property
    def z_abs_ohm(self) -> float:
        return abs(self.z_complex)

    @property
    def phase_deg(self) -> float:
        return math.degrees(math.atan2(self.z_complex.imag, self.z_complex.real))

    @property
    def capacitance_f(self) -> float:
        # Capacitancia equivalente serie: Zc = -j / (omega*C).
        if self.x_ohm >= 0 or self.frequency_hz <= 0:
            return math.nan
        return -1.0 / (2.0 * math.pi * self.frequency_hz * self.x_ohm)

    @property
    def inductance_h(self) -> float:
        # Inductancia equivalente serie: Zl = j*omega*L.
        if self.x_ohm <= 0 or self.frequency_hz <= 0:
            return math.nan
        return self.x_ohm / (2.0 * math.pi * self.frequency_hz)


def format_si(value: float, unit: str) -> str:
    if not math.isfinite(value):
        return "nan"

    magnitude = abs(value)
    scales = [
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    ]
    for scale, prefix in scales:
        if magnitude >= scale or scale == scales[-1][0]:
            return f"{value / scale:.4g} {prefix}{unit}"
    return f"{value:.4g} {unit}"


def _finite_median(values: list[float]) -> float:
    finite = np.array([value for value in values if math.isfinite(value)], dtype=float)
    if finite.size == 0:
        return math.nan
    return float(np.median(finite))


def _finite_std(values: list[float]) -> float:
    finite = np.array([value for value in values if math.isfinite(value)], dtype=float)
    if finite.size < 2:
        return 0.0
    return float(np.std(finite, ddof=1))


def _frequency_span(points: list[MeasurementPoint]) -> str:
    freqs = [point.frequency_hz for point in points if math.isfinite(point.frequency_hz)]
    if not freqs:
        return "sin rango"
    return f"{format_si(min(freqs), 'Hz')} a {format_si(max(freqs), 'Hz')}"


def _relative_spread(value: float, spread: float) -> str:
    if not math.isfinite(value) or abs(value) < 1e-30:
        return "n/a"
    return f"{100.0 * spread / abs(value):.3g}%"


def build_characterization_summary(points: list[MeasurementPoint], dut_type_label: str) -> dict[str, object]:
    if not points:
        return {
            "dut_type": dut_type_label,
            "lines": ["Resumen: sin datos"],
            "warnings": [],
            "used_points": 0,
        }

    warnings: list[str] = []
    total_points = len(points)

    def choose(preferred: list[MeasurementPoint], fallback: list[MeasurementPoint]) -> list[MeasurementPoint]:
        return preferred if len(preferred) >= 3 else fallback

    if dut_type_label == "Resistencia":
        candidates = [point for point in points if math.isfinite(point.r_ohm)]
        used = choose([point for point in candidates if abs(point.phase_deg) <= 10.0], candidates)
        value = _finite_median([point.r_ohm for point in used])
        spread = _finite_std([point.r_ohm for point in used])
        reactance_ratio = _finite_median(
            [abs(point.x_ohm) / max(abs(point.r_ohm), 1e-30) for point in used if math.isfinite(point.x_ohm)]
        )
        if math.isfinite(reactance_ratio) and reactance_ratio > 0.05:
            warnings.append("La reactancia no es despreciable; revisa frecuencia, cables o que el DUT sea resistivo.")
        lines = [
            f"Resumen Resistencia: {format_si(value, 'Ω')}",
            f"Puntos usados: {len(used)}/{total_points} ({_frequency_span(used)})",
            f"Dispersión: ±{format_si(spread, 'Ω')} ({_relative_spread(value, spread)})",
            f"Fase mediana: {_finite_median([point.phase_deg for point in used]):.3g}°",
        ]
    elif dut_type_label == "Capacitor":
        candidates = [
            point for point in points
            if math.isfinite(point.capacitance_f) and point.x_ohm < 0.0
        ]
        preferred = [point for point in candidates if point.phase_deg <= -75.0]
        used = choose(preferred, candidates)
        value = _finite_median([point.capacitance_f for point in used])
        spread = _finite_std([point.capacitance_f for point in used])
        series_r = _finite_median([point.r_ohm for point in used])
        if len(candidates) < max(3, total_points // 2):
            warnings.append("Pocos puntos se comportan como capacitor; revisa montaje o rango de frecuencia.")
        elif len(preferred) < len(candidates) // 2:
            warnings.append("Usa principalmente los puntos con fase cercana a -90° para reportar C.")
        lines = [
            f"Resumen Capacitor: {format_si(value, 'F')}",
            f"Puntos usados: {len(used)}/{total_points} ({_frequency_span(used)})",
            f"Dispersión: ±{format_si(spread, 'F')} ({_relative_spread(value, spread)})",
            f"Rserie equivalente: {format_si(series_r, 'Ω')}",
            f"Fase mediana: {_finite_median([point.phase_deg for point in used]):.3g}°",
        ]
    elif dut_type_label == "Inductor":
        candidates = [
            point for point in points
            if math.isfinite(point.inductance_h) and point.x_ohm > 0.0
        ]
        preferred = [point for point in candidates if point.phase_deg >= 20.0]
        used = choose(preferred, candidates)
        value = _finite_median([point.inductance_h for point in used])
        spread = _finite_std([point.inductance_h for point in used])
        series_r = _finite_median([point.r_ohm for point in used])
        if len(candidates) < max(3, total_points // 2):
            warnings.append("Pocos puntos se comportan como inductor; evita overload y usa frecuencias donde Xz sea positiva.")
        elif len(preferred) < len(candidates) // 2:
            warnings.append("La fase inductiva es baja en varios puntos; reporta L sólo en el rango indicado.")
        lines = [
            f"Resumen Inductor: {format_si(value, 'H')}",
            f"Puntos usados: {len(used)}/{total_points} ({_frequency_span(used)})",
            f"Dispersión: ±{format_si(spread, 'H')} ({_relative_spread(value, spread)})",
            f"Rserie equivalente: {format_si(series_r, 'Ω')}",
            f"Fase mediana: {_finite_median([point.phase_deg for point in used]):.3g}°",
        ]
    else:
        used = [point for point in points if math.isfinite(point.z_abs_ohm)]
        lines = [
            f"Resumen Impedancia: |Z| mediana {format_si(_finite_median([point.z_abs_ohm for point in used]), 'Ω')}",
            f"Puntos usados: {len(used)}/{total_points} ({_frequency_span(used)})",
            f"Re(Z) mediana: {format_si(_finite_median([point.r_ohm for point in used]), 'Ω')}",
            f"Xz mediana: {format_si(_finite_median([point.x_ohm for point in used]), 'Ω')}",
            f"Fase mediana: {_finite_median([point.phase_deg for point in used]):.3g}°",
        ]

    return {
        "dut_type": dut_type_label,
        "lines": lines,
        "warnings": warnings,
        "used_points": len(used),
        "total_points": total_points,
        "frequency_span": _frequency_span(used),
    }


class SR860Controller:
    """
    Encapsula la comunicación VISA.

    Mantener esta parte separada de la GUI hace el código más legible y
    también evita mezclar widgets con comandos SCPI.
    """

    def __init__(self) -> None:
        self.rm: Optional[pyvisa.ResourceManager] = None
        self.inst = None
        self.raw_handle = None
        self.transport = "visa"

    def list_resources(self, refresh_session: bool = True) -> tuple[str, ...]:
        if refresh_session:
            self.close()

        visa_resources: tuple[str, ...] = ()
        try:
            if self.rm is None:
                self.rm = pyvisa.ResourceManager()
            visa_resources = self.rm.list_resources("?*")
        except Exception:
            visa_resources = ()

        raw_resources = tuple(str(path) for path in Path("/dev").glob("usbtmc*"))
        return tuple(dict.fromkeys((*visa_resources, *raw_resources)))

    def connect(self, resource_name: str) -> str:
        self.close()

        if resource_name.startswith("/dev/usbtmc"):
            self.raw_handle = open(resource_name, "r+b", buffering=0)
            flags = fcntl.fcntl(self.raw_handle.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(self.raw_handle.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self.transport = "raw-usbtmc"
            return self.query("*IDN?")

        self.rm = pyvisa.ResourceManager()
        self.transport = "visa"
        self.inst = self.rm.open_resource(resource_name)
        self.inst.timeout = 10000
        self.inst.write_termination = "\n"
        self.inst.read_termination = "\n"
        return self.query("*IDN?")

    def close(self) -> None:
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
            self.inst = None
        if self.raw_handle is not None:
            try:
                self.raw_handle.close()
            except Exception:
                pass
            self.raw_handle = None
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
            self.rm = None
        self.transport = "visa"

    def require_connection(self) -> None:
        if self.inst is None and self.raw_handle is None:
            raise RuntimeError("No hay un SR860 conectado.")

    def write(self, command: str) -> None:
        self.require_connection()
        try:
            if self.transport == "raw-usbtmc":
                self._raw_write(command)
                return
            self.inst.write(command)
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Se perdió la comunicación con el SR860 al enviar {command!r}.") from exc

    def query(self, command: str) -> str:
        self.require_connection()
        try:
            if self.transport == "raw-usbtmc":
                self._raw_write(command)
                return self._raw_readline()
            return self.inst.query(command).strip()
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Se perdió la comunicación con el SR860 al consultar {command!r}.") from exc

    def _raw_write(self, command: str) -> None:
        if self.raw_handle is None:
            raise RuntimeError("No hay un dispositivo USBTMC abierto.")
        payload = f"{command}\n".encode("ascii", errors="strict")
        self.raw_handle.write(payload)

    def _raw_readline(self, timeout_s: float = 2.0) -> str:
        if self.raw_handle is None:
            raise RuntimeError("No hay un dispositivo USBTMC abierto.")

        fd = self.raw_handle.fileno()
        chunks = bytearray()
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    time.sleep(0.02)
                    continue
                raise
            if not chunk:
                time.sleep(0.02)
                continue

            chunks.extend(chunk)
            if b"\n" in chunk:
                break

        if not chunks:
            raise TimeoutError("El dispositivo USBTMC no respondió antes del timeout.")
        return chunks.split(b"\n", 1)[0].decode("ascii", errors="replace").strip()

    def query_float(self, command: str) -> float:
        return float(self.query(command))

    def query_int(self, command: str) -> int:
        # Algunos comandos pueden contestar "1\n" o "1.0".
        return int(float(self.query(command)))

    def apply_setup(self, config: SweepConfig) -> None:
        self.write(f"RSRC {REFERENCE_SOURCE_OPTIONS[config.reference_source_label]}")
        self.write("HARM 1")
        self.write(f"IVMD {INPUT_MODE_OPTIONS[config.input_mode_label]}")
        self._disable_display_math_for_raw_xy()

        if config.input_mode_label == "Current 1 MΩ":
            self.write("ICUR 0")
        elif config.input_mode_label == "Current 100 MΩ":
            self.write("ICUR 1")
        elif config.input_mode_label in {"A", "A-B"}:
            self.write(f"IRNG {INPUT_RANGE_OPTIONS[config.input_range_label]}")
            self.write(f"ICPL {1 if config.coupling_dc else 0}")
            self.write(f"IGND {1 if config.shield_grounded else 0}")

        self.write(f"SLVL {config.output_amplitude_v}")
        self.write(f"SOFF {config.dc_offset_v}")
        self.write(f"PHAS {config.output_phase_deg}")
        self.write(f"OFLT {TIME_CONSTANT_OPTIONS[config.time_constant_label]}")
        self.write(f"OFSL {FILTER_SLOPE_OPTIONS[config.filter_slope_label]}")
        self.write(f"SYNC {1 if config.sync_filter else 0}")

    def _disable_display_math_for_raw_xy(self) -> None:
        # Offset/ratio de X/Y pueden modificar consultas remotas; la impedancia
        # necesita los fasores RMS crudos referidos a la entrada.
        for channel in ("X", "Y", "R"):
            self.write(f"COFA {channel}, OFF")
            self.write(f"CRAT {channel}, OFF")
            self.write(f"CEXP {channel}, OFF")

    def read_effective_source_guess(self) -> float:
        # `SLVL?` devuelve la amplitud configurada en el instrumento.
        # Eso es mejor que dejar 1.0 V fijo, pero aún así la GUI permite
        # editar la amplitud efectiva si el cableado real no coincide.
        return float(self.query("SLVL?"))

    def read_setup_snapshot(self) -> dict[str, object]:
        """
        Lee desde el SR860 los parámetros que la GUI puede reflejar.

        La topología física de salida usada en el experimento
        (single-ended/differential y carga 50 Ω/high-Z) no es algo que el
        SR860 exponga por comando, así que esa parte sigue siendo una elección
        explícita del usuario en la GUI.
        """
        snapshot: dict[str, object] = {
            "frequency_hz": self.query_float("FREQ?"),
            "output_amplitude_v": self.query_float("SLVL?"),
            "dc_offset_v": self.query_float("SOFF?"),
            "phase_deg": self.query_float("PHAS?"),
            "reference_source_code": self.query_int("RSRC?"),
            "time_constant_code": self.query_int("OFLT?"),
            "filter_slope_code": self.query_int("OFSL?"),
            "sync_filter_code": self.query_int("SYNC?"),
            "input_mode_code": self.query_int("IVMD?"),
        }

        input_mode_code = int(snapshot["input_mode_code"])
        if input_mode_code in (0, 1):
            snapshot["input_range_code"] = self.query_int("IRNG?")
            snapshot["coupling_code"] = self.query_int("ICPL?")
            snapshot["ground_code"] = self.query_int("IGND?")
        elif input_mode_code in (2, 3):
            try:
                snapshot["current_gain_code"] = self.query_int("ICUR?")
            except Exception:
                snapshot["current_gain_code"] = 0 if input_mode_code == 2 else 1

        return snapshot

    def read_snapshot_xy(self) -> tuple[float, float]:
        reply = self.query("SNAP? X,Y")
        parts = [p.strip() for p in reply.split(",")]
        if len(parts) < 2:
            raise RuntimeError(f"Respuesta SNAP? inválida: {reply}")
        return float(parts[0]), float(parts[1])


class SR860ImpedanceApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SR860 Impedance Workbench")
        self.root.geometry("1500x920")
        self.root.minsize(1250, 780)

        self.controller = SR860Controller()
        self.measurements: list[MeasurementPoint] = []
        self.worker: Optional[threading.Thread] = None
        self.stop_requested = False
        self.gui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.sidebar_canvas: Optional[tk.Canvas] = None

        self._build_style()
        self._apply_window_icon()
        self._build_variables()
        self._build_layout()
        self._build_plot_area()
        self._poll_gui_queue()
        self.refresh_resources()

    def _build_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        self.colors = {
            "bg": "#f5f7fb",
            "panel": "#ffffff",
            "panel_alt": "#eef3f8",
            "text": "#1f2937",
            "muted": "#5b6472",
            "accent": "#0f766e",
            "accent_soft": "#d7f3ef",
            "line_1": "#0f766e",
            "line_2": "#2563eb",
            "line_3": "#f59e0b",
            "line_4": "#dc2626",
            "grid": "#d7dee8",
        }

        self.root.configure(bg=self.colors["bg"])
        style.configure(".", background=self.colors["bg"], foreground=self.colors["text"], font=("DejaVu Sans", 10))
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("AltPanel.TFrame", background=self.colors["panel_alt"])
        style.configure("Title.TLabel", background=self.colors["panel"], foreground=self.colors["text"], font=("DejaVu Sans", 17, "bold"))
        style.configure("Section.TLabelframe", background=self.colors["panel"], foreground=self.colors["text"])
        style.configure("Section.TLabelframe.Label", background=self.colors["panel"], foreground=self.colors["text"], font=("DejaVu Sans", 10, "bold"))
        style.configure("TLabel", background=self.colors["panel"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("TEntry", fieldbackground="#fbfcfe", foreground=self.colors["text"])
        style.configure("TCombobox", fieldbackground="#fbfcfe", foreground=self.colors["text"])
        style.configure("Accent.TButton", background=self.colors["accent"], foreground="white", borderwidth=0, focusthickness=3, focuscolor=self.colors["accent"])
        style.map("Accent.TButton", background=[("active", "#115e59"), ("pressed", "#134e4a")], foreground=[("disabled", "#d1d5db")])
        style.configure("Soft.TButton", background=self.colors["accent_soft"], foreground=self.colors["accent"], borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#c5ebe5")])
        style.configure("Treeview", background="#fbfcfe", fieldbackground="#fbfcfe", foreground=self.colors["text"], rowheight=24)
        style.configure("Treeview.Heading", background=self.colors["panel_alt"], foreground=self.colors["text"], font=("DejaVu Sans", 10, "bold"))

    def _apply_window_icon(self) -> None:
        ico_path = resource_path("assets/srs-1.ico")
        png_path = resource_path("assets/srs-1.png")

        try:
            if ico_path.exists():
                self.root.iconbitmap(default=str(ico_path))
        except Exception:
            pass

        try:
            if png_path.exists():
                icon_image = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, icon_image)
                self._icon_image = icon_image
        except Exception:
            pass

    def _build_variables(self) -> None:
        self.resource_var = tk.StringVar()
        self.idn_var = tk.StringVar(value="Sin conexión")
        self.status_var = tk.StringVar(value="Listo para conectar al SR860")
        self.progress_var = tk.StringVar(value="Sin mediciones")
        self.summary_var = tk.StringVar(value="Resumen: sin datos")

        self.dut_name_var = tk.StringVar(value="DUT")
        self.dut_type_var = tk.StringVar(value="Resistencia")
        self.plot_mode_var = tk.StringVar(value="Auto")

        self.start_freq_var = tk.StringVar(value="100")
        self.stop_freq_var = tk.StringVar(value="500000")
        self.points_var = tk.StringVar(value="60")
        self.log_sweep_var = tk.BooleanVar(value=True)

        self.series_resistor_var = tk.StringVar(value="220")
        self.source_series_var = tk.StringVar(value="50 Ω")
        self.output_amplitude_var = tk.StringVar(value="1.0")
        self.effective_source_var = tk.StringVar(value="1.0")
        self.output_connection_var = tk.StringVar(value="Single-ended")
        self.output_load_var = tk.StringVar(value="High-Z")
        self.phase_var = tk.StringVar(value="0.0")
        self.offset_var = tk.StringVar(value="0.0")
        self.settling_factor_var = tk.StringVar(value="5.0")

        self.time_constant_var = tk.StringVar(value="100 ms")
        self.filter_slope_var = tk.StringVar(value="24 dB/oct")
        self.reference_source_var = tk.StringVar(value="Internal")
        self.input_mode_var = tk.StringVar(value="A")
        self.input_range_var = tk.StringVar(value="300 mV")
        self.coupling_dc_var = tk.BooleanVar(value=False)
        self.shield_ground_var = tk.BooleanVar(value=False)
        self.sync_filter_var = tk.BooleanVar(value=False)
        self.export_chart_vars = {
            key: tk.BooleanVar(value=True)
            for key in EXPORT_CHART_OPTIONS
        }

        # La amplitud efectiva se recalcula automáticamente cuando cambia
        # la amplitud programada o la topología de conexión seleccionada.
        self.output_amplitude_var.trace_add("write", self._schedule_effective_source_refresh)
        self.output_connection_var.trace_add("write", self._schedule_effective_source_refresh)
        self.output_load_var.trace_add("write", self._schedule_effective_source_refresh)
        self.dut_type_var.trace_add("write", self._schedule_plot_refresh)
        self.plot_mode_var.trace_add("write", self._schedule_plot_refresh)

    def _build_layout(self) -> None:
        root_grid = ttk.Frame(self.root, style="Panel.TFrame", padding=14)
        root_grid.pack(fill="both", expand=True)
        root_grid.columnconfigure(0, weight=0)
        root_grid.columnconfigure(1, weight=1)
        root_grid.rowconfigure(0, weight=1)

        self.sidebar_outer = ttk.Frame(root_grid, style="Panel.TFrame", padding=(0, 0, 10, 0))
        self.sidebar_outer.grid(row=0, column=0, sticky="ns")
        self.sidebar_outer.rowconfigure(0, weight=1)
        self.sidebar_outer.columnconfigure(0, weight=1)

        self.sidebar_canvas = tk.Canvas(
            self.sidebar_outer,
            width=340,
            highlightthickness=0,
            background=self.colors["panel"],
        )
        self.sidebar_scrollbar = ttk.Scrollbar(self.sidebar_outer, orient="vertical", command=self.sidebar_canvas.yview)
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scrollbar.set)
        self.sidebar_canvas.grid(row=0, column=0, sticky="ns")
        self.sidebar_scrollbar.grid(row=0, column=1, sticky="ns")

        self.sidebar = ttk.Frame(self.sidebar_canvas, style="Panel.TFrame", padding=(0, 0, 8, 0))
        self.sidebar_window = self.sidebar_canvas.create_window((0, 0), window=self.sidebar, anchor="nw")
        self.sidebar.bind("<Configure>", self._sync_sidebar_scroll_region)
        self.sidebar_canvas.bind("<Configure>", self._sync_sidebar_width)
        self.sidebar_canvas.bind_all("<MouseWheel>", self._on_sidebar_mousewheel)

        self.content = ttk.Frame(root_grid, style="Panel.TFrame")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.rowconfigure(1, weight=1)
        self.content.columnconfigure(0, weight=1)

        title_frame = ttk.Frame(self.content, style="Panel.TFrame", padding=(0, 0, 0, 10))
        title_frame.grid(row=0, column=0, sticky="ew")
        title_frame.columnconfigure(0, weight=1)

        ttk.Label(title_frame, text="SR860 Impedance Workbench", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            title_frame,
            text="Barrido de DUT con GUI, setup remoto, cálculo de Z y Re(Z) para caracterización.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._build_connection_panel()
        self._build_setup_panel()
        self._build_actions_panel()
        self._build_status_panel()

    def _sync_sidebar_scroll_region(self, _event: tk.Event) -> None:
        if self.sidebar_canvas is not None:
            self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _sync_sidebar_width(self, event: tk.Event) -> None:
        if self.sidebar_canvas is not None:
            self.sidebar_canvas.itemconfigure(self.sidebar_window, width=event.width)

    def _on_sidebar_mousewheel(self, event: tk.Event) -> None:
        if self.sidebar_canvas is None:
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is not None and str(widget).startswith(str(self.sidebar_canvas)):
            self.sidebar_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_connection_panel(self) -> None:
        frame = ttk.LabelFrame(self.sidebar, text="Conexión", style="Section.TLabelframe", padding=12)
        frame.pack(fill="x", pady=(0, 10))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Recurso VISA").grid(row=0, column=0, sticky="w")
        self.resource_combo = ttk.Combobox(frame, textvariable=self.resource_var, state="readonly", width=34)
        self.resource_combo.grid(row=1, column=0, sticky="ew", pady=(4, 8))

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="Actualizar", style="Soft.TButton", command=self.refresh_resources).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Conectar", style="Accent.TButton", command=self.connect_instrument).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(frame, text="Diagnóstico de conexión", style="Soft.TButton", command=self.run_connection_diagnostic).grid(row=3, column=0, sticky="ew", pady=(10, 0))

        ttk.Label(frame, text="Instrumento").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Label(frame, textvariable=self.idn_var, wraplength=290, style="Muted.TLabel").grid(row=5, column=0, sticky="ew", pady=(4, 0))

    def _build_setup_panel(self) -> None:
        frame = ttk.LabelFrame(self.sidebar, text="Setup del Equipo", style="Section.TLabelframe", padding=12)
        frame.pack(fill="x", pady=(0, 10))
        frame.columnconfigure(1, weight=1)

        self._add_labeled_entry(frame, 0, "Nombre del DUT", self.dut_name_var)
        self._add_labeled_combo(frame, 1, "Tipo de DUT", self.dut_type_var, list(DUT_TYPE_OPTIONS))
        self._add_labeled_combo(frame, 2, "Vista de gráficas", self.plot_mode_var, list(PLOT_MODE_OPTIONS))

        self._add_labeled_entry(frame, 3, "Frecuencia inicial [Hz]", self.start_freq_var)
        self._add_labeled_entry(frame, 4, "Frecuencia final [Hz]", self.stop_freq_var)
        self._add_labeled_entry(frame, 5, "Número de puntos", self.points_var)

        ttk.Checkbutton(frame, text="Barrido logarítmico", variable=self.log_sweep_var).grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 8))

        self._add_labeled_entry(frame, 7, "Resistencia serie Rs [Ω]", self.series_resistor_var)
        self._add_labeled_combo(frame, 8, "Z serie fuente/equipo", self.source_series_var, list(SOURCE_SERIES_OPTIONS))
        self._add_labeled_entry(frame, 9, "Amplitud SR860 [V]", self.output_amplitude_var)
        self._add_labeled_combo(frame, 10, "Uso de salida", self.output_connection_var, list(OUTPUT_CONNECTION_OPTIONS))
        self._add_labeled_combo(frame, 11, "Carga estimada", self.output_load_var, list(OUTPUT_LOAD_OPTIONS))
        self._add_labeled_entry(frame, 12, "Amplitud efectiva en DUT [V]", self.effective_source_var)
        self._add_labeled_entry(frame, 13, "PHAS referencia [deg]", self.phase_var)
        self._add_labeled_entry(frame, 14, "Offset DC [V]", self.offset_var)
        self._add_labeled_entry(frame, 15, "Factor de asentamiento", self.settling_factor_var)

        self._add_labeled_combo(frame, 16, "Time constant", self.time_constant_var, list(TIME_CONSTANT_OPTIONS.keys()))
        self._add_labeled_combo(frame, 17, "Pendiente de filtro", self.filter_slope_var, list(FILTER_SLOPE_OPTIONS.keys()))
        self._add_labeled_combo(frame, 18, "Fuente de referencia", self.reference_source_var, list(REFERENCE_SOURCE_OPTIONS.keys()))
        self._add_labeled_combo(frame, 19, "Modo de entrada", self.input_mode_var, list(INPUT_MODE_OPTIONS.keys()))
        self._add_labeled_combo(frame, 20, "Rango de entrada", self.input_range_var, list(INPUT_RANGE_OPTIONS.keys()))

        ttk.Checkbutton(frame, text="Acoplamiento DC", variable=self.coupling_dc_var).grid(row=21, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(frame, text="Blindaje a tierra", variable=self.shield_ground_var).grid(row=22, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(frame, text="Sync filter", variable=self.sync_filter_var).grid(row=23, column=0, columnspan=2, sticky="w")

        ttk.Button(frame, text="Leer amplitud del SR860", style="Soft.TButton", command=self.load_source_from_instrument).grid(row=24, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(frame, text="Leer config del SR860", style="Soft.TButton", command=self.load_setup_from_instrument).grid(row=25, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(frame, text="Reestimar amplitud efectiva", style="Soft.TButton", command=self.refresh_effective_source_from_model).grid(row=26, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(frame, text="Aplicar setup al equipo", style="Accent.TButton", command=self.apply_setup_to_instrument).grid(row=27, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_actions_panel(self) -> None:
        frame = ttk.LabelFrame(self.sidebar, text="Medición y Exportación", style="Section.TLabelframe", padding=12)
        frame.pack(fill="x", pady=(0, 10))
        frame.columnconfigure((0, 1), weight=1)

        ttk.Button(frame, text="Medición única", style="Soft.TButton", command=self.take_single_measurement).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(frame, text="Iniciar barrido", style="Accent.TButton", command=self.start_sweep).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(frame, text="Detener", style="Soft.TButton", command=self.stop_sweep).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        ttk.Button(frame, text="Exportar sesión", style="Soft.TButton", command=self.export_session).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(8, 0))
        ttk.Button(frame, text="Exportar CSV", style="Soft.TButton", command=self.export_csv).grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        ttk.Button(frame, text="Exportar SVG", style="Soft.TButton", command=self.export_svg).grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=(8, 0))

        export_frame = ttk.Frame(frame, style="Panel.TFrame")
        export_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        export_frame.columnconfigure((0, 1), weight=1)

        for index, (key, label) in enumerate(EXPORT_CHART_OPTIONS.items()):
            ttk.Checkbutton(
                export_frame,
                text=label,
                variable=self.export_chart_vars[key],
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 8), pady=(2, 0))

    def _build_status_panel(self) -> None:
        frame = ttk.LabelFrame(self.sidebar, text="Estado", style="Section.TLabelframe", padding=12)
        frame.pack(fill="x")

        ttk.Label(frame, textvariable=self.status_var, wraplength=290).pack(anchor="w")
        ttk.Label(frame, textvariable=self.progress_var, wraplength=290, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, textvariable=self.summary_var, wraplength=290, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

    def _build_plot_area(self) -> None:
        plot_panel = ttk.Frame(self.content, style="Panel.TFrame")
        plot_panel.grid(row=1, column=0, sticky="nsew")
        plot_panel.rowconfigure(0, weight=1)
        plot_panel.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(11.5, 7.2), dpi=100, facecolor=self.colors["panel"])
        self.axes = self.figure.subplots(2, 2)
        self.figure.subplots_adjust(left=0.07, right=0.98, top=0.95, bottom=0.08, hspace=0.30, wspace=0.18)

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_panel)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        toolbar_frame = ttk.Frame(plot_panel, style="Panel.TFrame")
        toolbar_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        NavigationToolbar2Tk(self.canvas, toolbar_frame)

        table_frame = ttk.Frame(plot_panel, style="Panel.TFrame")
        table_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        columns = ("f", "x", "y", "r", "reactance", "z", "phase", "c", "l")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        headings = {
            "f": "Freq [Hz]",
            "x": "X [V]",
            "y": "Y [V]",
            "r": "Re(Z) [Ω]",
            "reactance": "Xz [Ω]",
            "z": "|Z| [Ω]",
            "phase": "Fase [deg]",
            "c": "C [F]",
            "l": "L [H]",
        }
        widths = {"f": 105, "x": 100, "y": 100, "r": 100, "reactance": 100, "z": 100, "phase": 95, "c": 120, "l": 120}
        for key in columns:
            self.table.heading(key, text=headings[key])
            self.table.column(key, width=widths[key], anchor="center")

        self.table.pack(fill="x")
        self._draw_empty_plots()

    def _add_labeled_entry(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(parent, textvariable=variable, width=16).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(0, 4))

    def _add_labeled_combo(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar, values: list[str]) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(0, 4))
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=14).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(0, 4))

    def _draw_empty_plots(self) -> None:
        labels = self._current_plot_labels()
        for index, (ax, (title, ylabel)) in enumerate(zip(self.axes.flat, labels)):
            ax.clear()
            ax.set_facecolor("#fbfcfe")
            ax.set_title(title, fontsize=11, fontweight="bold", color=self.colors["text"])
            if index < 2:
                ax.set_xlabel("")
            else:
                ax.set_xlabel("Frecuencia [Hz]", fontsize=9, labelpad=-6)
            ax.set_ylabel(ylabel)
            ax.grid(True, which="both", color=self.colors["grid"], alpha=0.9)
            ax.set_xscale("log")
        self.canvas.draw_idle()

    def _current_plot_mode(self) -> str:
        selected = self.plot_mode_var.get()
        if selected != "Auto":
            return selected
        if self.dut_type_var.get() == "Resistencia":
            return "R/X/Z/Fase"
        return "R/C/Z/L"

    def _current_plot_labels(self) -> list[tuple[str, str]]:
        if self._current_plot_mode() == "R/X/Z/Fase":
            return [
                ("Re(Z) vs Frecuencia", "Re(Z) [Ω]"),
                ("Xz vs Frecuencia", "Xz [Ω]"),
                ("|Z| vs Frecuencia", "|Z| [Ω]"),
                ("Fase de Z vs Frecuencia", "Fase [deg]"),
            ]
        return [
            ("Re(Z) vs Frecuencia", "Re(Z) [Ω]"),
            ("C vs Frecuencia", "C"),
            ("|Z| vs Frecuencia", "|Z| [Ω]"),
            ("L vs Frecuencia", "L"),
        ]

    def refresh_resources(self) -> None:
        previous_resource = self.resource_var.get().strip()
        try:
            resources = self.controller.list_resources(refresh_session=True)
        except Exception as exc:
            self.status_var.set(f"No se pudieron listar recursos VISA: {exc}")
            return

        self.resource_combo["values"] = resources
        self.idn_var.set("Sin conexión")
        if resources:
            self.resource_var.set(previous_resource if previous_resource in resources else resources[0])
            self.status_var.set(f"Se encontraron {len(resources)} recurso(s). Sesión de conexión reiniciada.")
        else:
            self.resource_var.set("")
            self.status_var.set("No se detectaron recursos. Conecta el SR860 y pulsa Actualizar.")

    def connect_instrument(self) -> None:
        resource = self.resource_var.get().strip()
        if not resource:
            messagebox.showwarning("Conexión", "Selecciona un recurso VISA.")
            return

        try:
            idn = self.controller.connect(resource)
        except PermissionError as exc:
            messagebox.showerror(
                "Conexión",
                "Se detectó el instrumento, pero Linux no permite abrirlo.\n\n"
                f"Recurso: {resource}\n"
                "El nodo USBTMC necesita permisos de usuario.\n\n"
                f"Detalle: {exc}",
            )
            self.status_var.set("El instrumento existe, pero falta permiso sobre el nodo USBTMC.")
            return
        except Exception as exc:
            messagebox.showerror("Conexión", f"No se pudo conectar al SR860.\n\n{exc}")
            self.status_var.set("Falló la conexión con el instrumento.")
            return

        self.idn_var.set(idn)
        self.status_var.set("Conexión establecida con el SR860.")

    def run_connection_diagnostic(self) -> None:
        resource = self.resource_var.get().strip()
        if not resource:
            messagebox.showwarning("Diagnóstico", "Selecciona un recurso antes de diagnosticar.")
            return

        try:
            try:
                if self.controller.inst is None and self.controller.raw_handle is None:
                    idn = self.controller.connect(resource)
                else:
                    idn = self.controller.query("*IDN?")
            except Exception:
                idn = self.controller.connect(resource)

            freq = self.controller.query("FREQ?")
            amplitude = self.controller.query("SLVL?")
            x_v, y_v = self.controller.read_snapshot_xy()
        except PermissionError as exc:
            messagebox.showerror(
                "Diagnóstico de conexión",
                "El instrumento fue detectado, pero el sistema no permite abrirlo.\n\n"
                f"{exc}",
            )
            self.status_var.set("Diagnóstico: falta permiso sobre el recurso seleccionado.")
            return
        except Exception as exc:
            messagebox.showerror("Diagnóstico de conexión", f"El diagnóstico falló.\n\n{exc}")
            self.status_var.set("Diagnóstico fallido: revisa conexión, permisos o backend VISA.")
            return

        self.idn_var.set(idn)
        self.status_var.set("Diagnóstico correcto: el instrumento responde y entrega X/Y.")
        messagebox.showinfo(
            "Diagnóstico de conexión",
            f"*IDN?: {idn}\n"
            f"FREQ?: {freq} Hz\n"
            f"SLVL?: {amplitude} V\n"
            f"SNAP? X,Y: X={x_v:.6e} V, Y={y_v:.6e} V",
        )

    def load_source_from_instrument(self) -> None:
        try:
            source = self.controller.read_effective_source_guess()
        except Exception as exc:
            messagebox.showerror("SR860", f"No se pudo leer la amplitud del equipo.\n\n{exc}")
            return

        pretty = f"{source:.6g}"
        self.output_amplitude_var.set(pretty)
        self.refresh_effective_source_from_model()
        self.status_var.set("La amplitud programada del SR860 se copió a la GUI.")

    def load_setup_from_instrument(self) -> None:
        try:
            snapshot = self.controller.read_setup_snapshot()
        except Exception as exc:
            messagebox.showerror("SR860", f"No se pudo leer la configuración actual.\n\n{exc}")
            return

        self._apply_snapshot_to_gui(snapshot)
        self.status_var.set("La configuración actual del SR860 se cargó en la GUI.")

    def _apply_snapshot_to_gui(self, snapshot: dict[str, object]) -> None:
        output_amplitude = float(snapshot["output_amplitude_v"])
        self.output_amplitude_var.set(f"{output_amplitude:.6g}")
        self.phase_var.set(f"{float(snapshot['phase_deg']):.6g}")
        self.offset_var.set(f"{float(snapshot['dc_offset_v']):.6g}")

        frequency = float(snapshot["frequency_hz"])
        self.start_freq_var.set(f"{frequency:.6g}")

        reference_code = int(snapshot["reference_source_code"])
        if reference_code in REFERENCE_SOURCE_FROM_CODE:
            self.reference_source_var.set(REFERENCE_SOURCE_FROM_CODE[reference_code])

        time_constant_code = int(snapshot["time_constant_code"])
        if time_constant_code in TIME_CONSTANT_FROM_CODE:
            self.time_constant_var.set(TIME_CONSTANT_FROM_CODE[time_constant_code])

        filter_slope_code = int(snapshot["filter_slope_code"])
        if filter_slope_code in FILTER_SLOPE_FROM_CODE:
            self.filter_slope_var.set(FILTER_SLOPE_FROM_CODE[filter_slope_code])

        self.sync_filter_var.set(bool(int(snapshot["sync_filter_code"])))

        input_mode_code = int(snapshot["input_mode_code"])
        if input_mode_code == 0:
            self.input_mode_var.set("A")
        elif input_mode_code == 1:
            self.input_mode_var.set("A-B")
        elif input_mode_code == 2:
            gain_code = int(snapshot.get("current_gain_code", 0))
            self.input_mode_var.set("Current 1 MΩ" if gain_code == 0 else "Current 100 MΩ")
        elif input_mode_code == 3:
            self.input_mode_var.set("Current 100 MΩ")

        input_range_code = snapshot.get("input_range_code")
        if input_range_code is not None:
            input_range_code = int(input_range_code)
            if input_range_code in INPUT_RANGE_FROM_CODE:
                self.input_range_var.set(INPUT_RANGE_FROM_CODE[input_range_code])

        if "coupling_code" in snapshot:
            self.coupling_dc_var.set(bool(int(snapshot["coupling_code"])))
        if "ground_code" in snapshot:
            self.shield_ground_var.set(bool(int(snapshot["ground_code"])))

        self.refresh_effective_source_from_model()

    def apply_setup_to_instrument(self) -> None:
        try:
            config = self._collect_config()
            self.controller.apply_setup(config)
        except Exception as exc:
            messagebox.showerror("Setup", f"No se pudo aplicar el setup.\n\n{exc}")
            self.status_var.set("El setup no se aplicó.")
            return

        self.status_var.set("Setup aplicado al SR860.")

    def _collect_config(self) -> SweepConfig:
        start_freq = float(self.start_freq_var.get())
        stop_freq = float(self.stop_freq_var.get())
        points = int(self.points_var.get())
        series_resistor = float(self.series_resistor_var.get())
        source_series = parse_ohms_label(self.source_series_var.get())
        effective_source = float(self.effective_source_var.get())
        output_amplitude = float(self.output_amplitude_var.get())
        phase_deg = float(self.phase_var.get())
        dc_offset = float(self.offset_var.get())
        settling_factor = float(self.settling_factor_var.get())
        tc_label = self.time_constant_var.get()

        if start_freq <= 0 or stop_freq <= 0:
            raise ValueError("Las frecuencias deben ser positivas.")
        if stop_freq <= start_freq:
            raise ValueError("La frecuencia final debe ser mayor que la inicial.")
        if points < 2:
            raise ValueError("El barrido necesita al menos 2 puntos.")
        if series_resistor <= 0:
            raise ValueError("Rs debe ser mayor a cero.")
        if source_series < 0:
            raise ValueError("La impedancia serie de fuente/equipo no puede ser negativa.")
        if effective_source <= 0:
            raise ValueError("La amplitud efectiva debe ser mayor a cero.")
        if tc_label not in TIME_CONSTANT_SECONDS:
            raise ValueError("Selecciona una time constant válida.")

        return SweepConfig(
            dut_name=self.dut_name_var.get().strip() or "DUT",
            dut_type_label=self.dut_type_var.get(),
            plot_mode_label=self.plot_mode_var.get(),
            start_freq_hz=start_freq,
            stop_freq_hz=stop_freq,
            points=points,
            logarithmic=self.log_sweep_var.get(),
            series_resistor_ohm=series_resistor,
            source_series_ohm=source_series,
            effective_source_v=effective_source,
            time_constant_seconds=TIME_CONSTANT_SECONDS[tc_label],
            settling_factor=settling_factor,
            output_amplitude_v=output_amplitude,
            output_connection_label=self.output_connection_var.get(),
            output_load_label=self.output_load_var.get(),
            output_phase_deg=phase_deg,
            dc_offset_v=dc_offset,
            time_constant_label=tc_label,
            filter_slope_label=self.filter_slope_var.get(),
            reference_source_label=self.reference_source_var.get(),
            input_mode_label=self.input_mode_var.get(),
            input_range_label=self.input_range_var.get(),
            coupling_dc=self.coupling_dc_var.get(),
            shield_grounded=self.shield_ground_var.get(),
            sync_filter=self.sync_filter_var.get(),
        )

    def _schedule_effective_source_refresh(self, *_args: object) -> None:
        # `trace_add` dispara mientras el usuario escribe. Usar `after_idle`
        # evita recalcular en medio de una edición parcial.
        self.root.after_idle(self.refresh_effective_source_from_model)

    def _schedule_plot_refresh(self, *_args: object) -> None:
        self.root.after_idle(self._refresh_plots)
        self.root.after_idle(self._update_characterization_summary)

    def refresh_effective_source_from_model(self) -> None:
        try:
            programmed_amplitude = float(self.output_amplitude_var.get())
        except ValueError:
            return

        estimated = self._estimate_effective_source_amplitude(
            programmed_amplitude,
            self.output_connection_var.get(),
            self.output_load_var.get(),
        )
        self.effective_source_var.set(f"{estimated:.6g}")

    def _estimate_effective_source_amplitude(
        self,
        programmed_amplitude_v: float,
        output_connection_label: str,
        output_load_label: str,
    ) -> float:
        """
        Convierte la amplitud programada `SLVL` a la amplitud esperada en el DUT.

        Según el manual oficial del SR860, la amplitud especificada es diferencial
        en carga de 50 Ω. A partir de eso:

        - Differential + 50 Ω -> amplitud = especificada
        - Differential + High-Z -> amplitud = 2 * especificada
        - Single-ended + 50 Ω -> amplitud = 0.5 * especificada
        - Single-ended + High-Z -> amplitud = especificada
        """
        if output_connection_label == "Differential" and output_load_label == "50 Ω":
            factor = 1.0
        elif output_connection_label == "Differential" and output_load_label == "High-Z":
            factor = 2.0
        elif output_connection_label == "Single-ended" and output_load_label == "50 Ω":
            factor = 0.5
        else:
            factor = 1.0

        return programmed_amplitude_v * factor

    def start_sweep(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Barrido", "Ya hay un barrido en progreso.")
            return

        try:
            config = self._collect_config()
        except Exception as exc:
            messagebox.showerror("Configuración", str(exc))
            return

        try:
            self.controller.require_connection()
        except Exception as exc:
            messagebox.showerror("Conexión", str(exc))
            return

        self.measurements.clear()
        self.stop_requested = False
        self._clear_table()
        self._draw_empty_plots()
        self.summary_var.set("Resumen: sin datos")
        self.progress_var.set("Iniciando barrido...")
        self.status_var.set("Aplicando setup y arrancando medición.")

        self.worker = threading.Thread(target=self._run_sweep, args=(config,), daemon=True)
        self.worker.start()

    def take_single_measurement(self) -> None:
        try:
            config = self._collect_config()
            self.controller.require_connection()
        except Exception as exc:
            messagebox.showerror("Medición única", str(exc))
            return

        try:
            self.controller.apply_setup(config)
            if config.reference_source_label == "Internal":
                self.controller.write(f"FREQ {config.start_freq_hz}")
            time.sleep(config.settling_factor * config.time_constant_seconds)
            x_v, y_v = self.controller.read_snapshot_xy()
            point = self._compute_measurement_point(config, config.start_freq_hz, x_v, y_v)
        except Exception as exc:
            messagebox.showerror("Medición única", f"No se pudo medir el punto.\n\n{exc}")
            self.status_var.set("Medición única fallida.")
            return

        self.measurements.append(point)
        self._append_table_row(point)
        self._refresh_plots()
        self._update_characterization_summary()
        self.status_var.set(
            f"Medición única: f={point.frequency_hz:.3f} Hz | Re(Z)={point.r_ohm:.3f} Ω | "
            f"Xz={point.x_ohm:.3f} Ω | fase={point.phase_deg:.3f}°"
        )
        self.progress_var.set(f"Se capturaron {len(self.measurements)} punto(s).")

    def stop_sweep(self) -> None:
        self.stop_requested = True
        self.status_var.set("Se solicitó detener el barrido.")

    def _run_sweep(self, config: SweepConfig) -> None:
        try:
            self.controller.apply_setup(config)
            frequencies = (
                np.logspace(np.log10(config.start_freq_hz), np.log10(config.stop_freq_hz), config.points)
                if config.logarithmic
                else np.linspace(config.start_freq_hz, config.stop_freq_hz, config.points)
            )

            for index, freq in enumerate(frequencies, start=1):
                if self.stop_requested:
                    self.gui_queue.put(("status", "Barrido detenido por el usuario."))
                    break

                if config.reference_source_label == "Internal":
                    self.controller.write(f"FREQ {freq}")

                time.sleep(config.settling_factor * config.time_constant_seconds)
                x_v, y_v = self.controller.read_snapshot_xy()
                point = self._compute_measurement_point(config, float(freq), x_v, y_v)

                self.gui_queue.put(("point", point))
                self.gui_queue.put(("progress", f"Punto {index}/{len(frequencies)} medido a {freq:.3f} Hz"))
        except Exception as exc:
            self.gui_queue.put(("error", str(exc)))
        else:
            self.gui_queue.put(("done", "Barrido completado."))

    def _compute_measurement_point(self, config: SweepConfig, freq_hz: float, x_v: float, y_v: float) -> MeasurementPoint:
        measured_v = complex(x_v, y_v)
        source_v = source_phasor_from_lockin_reference(config.effective_source_v, config.output_phase_deg)
        total_series_ohm = config.series_resistor_ohm + config.source_series_ohm
        z_complex = impedance_from_series_divider(total_series_ohm, source_v, measured_v)
        return MeasurementPoint(
            frequency_hz=freq_hz,
            x_v=x_v,
            y_v=y_v,
            source_v=config.effective_source_v,
            source_phase_deg=-config.output_phase_deg,
            external_series_ohm=config.series_resistor_ohm,
            source_series_ohm=config.source_series_ohm,
            total_series_ohm=total_series_ohm,
            z_complex=z_complex,
        )

    def _poll_gui_queue(self) -> None:
        while True:
            try:
                event, payload = self.gui_queue.get_nowait()
            except queue.Empty:
                break

            if event == "point":
                assert isinstance(payload, MeasurementPoint)
                self.measurements.append(payload)
                self._append_table_row(payload)
                self._refresh_plots()
                self._update_characterization_summary()
                self.status_var.set(
                    f"Último punto: {payload.frequency_hz:.3f} Hz | Re(Z)={payload.r_ohm:.3f} Ω | "
                    f"|Z|={payload.z_abs_ohm:.3f} Ω"
                )
            elif event == "progress":
                self.progress_var.set(str(payload))
            elif event == "status":
                self.status_var.set(str(payload))
            elif event == "error":
                messagebox.showerror("Barrido", str(payload))
                self.status_var.set("El barrido terminó con error.")
            elif event == "done":
                if self.measurements:
                    self.status_var.set(str(payload))
                    self._update_characterization_summary()
                else:
                    self.status_var.set("Barrido terminado sin puntos válidos.")
                    self.summary_var.set("Resumen: sin datos")
                self.progress_var.set(f"Se capturaron {len(self.measurements)} puntos.")

        self.root.after(120, self._poll_gui_queue)

    def _append_table_row(self, point: MeasurementPoint) -> None:
        values = (
            f"{point.frequency_hz:.6g}",
            f"{point.x_v:.6e}",
            f"{point.y_v:.6e}",
            f"{point.r_ohm:.6e}",
            f"{point.x_ohm:.6e}",
            f"{point.z_abs_ohm:.6e}",
            f"{point.phase_deg:.6e}",
            f"{point.capacitance_f:.6e}" if math.isfinite(point.capacitance_f) else "nan",
            f"{point.inductance_h:.6e}" if math.isfinite(point.inductance_h) else "nan",
        )
        self.table.insert("", "end", values=values)
        children = self.table.get_children()
        if children:
            self.table.see(children[-1])

    def _clear_table(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)

    def _current_summary(self) -> dict[str, object]:
        return build_characterization_summary(self.measurements, self.dut_type_var.get())

    def _update_characterization_summary(self) -> None:
        summary = self._current_summary()
        lines = [str(line) for line in summary.get("lines", [])]
        warnings = [f"Aviso: {warning}" for warning in summary.get("warnings", [])]
        self.summary_var.set("\n".join([*lines, *warnings]))

    def _refresh_plots(self) -> None:
        if not self.measurements:
            self._draw_empty_plots()
            return

        freqs = np.array([p.frequency_hz for p in self.measurements], dtype=float)
        r_values = np.array([p.r_ohm for p in self.measurements], dtype=float)
        x_values = np.array([p.x_ohm for p in self.measurements], dtype=float)
        phase_values = np.array([p.phase_deg for p in self.measurements], dtype=float)
        c_values = np.array([p.capacitance_f for p in self.measurements], dtype=float)
        z_values = np.array([p.z_abs_ohm for p in self.measurements], dtype=float)
        l_values = np.array([p.inductance_h for p in self.measurements], dtype=float)

        c_scaled, c_unit = self._auto_scale_series(c_values, "F")
        l_scaled, l_unit = self._auto_scale_series(l_values, "H")

        if self._current_plot_mode() == "R/X/Z/Fase":
            plot_specs = [
                (self.axes[0, 0], r_values, "Re(Z) vs Frecuencia", "Re(Z) [Ω]", self.colors["line_1"]),
                (self.axes[0, 1], x_values, "Xz vs Frecuencia", "Xz [Ω]", self.colors["line_2"]),
                (self.axes[1, 0], z_values, "|Z| vs Frecuencia", "|Z| [Ω]", self.colors["line_3"]),
                (self.axes[1, 1], phase_values, "Fase de Z vs Frecuencia", "Fase [deg]", self.colors["line_4"]),
            ]
        else:
            plot_specs = [
                (self.axes[0, 0], r_values, "Re(Z) vs Frecuencia", "Re(Z) [Ω]", self.colors["line_1"]),
                (self.axes[0, 1], c_scaled, "C vs Frecuencia", f"C [{c_unit}]", self.colors["line_2"]),
                (self.axes[1, 0], z_values, "|Z| vs Frecuencia", "|Z| [Ω]", self.colors["line_3"]),
                (self.axes[1, 1], l_scaled, "L vs Frecuencia", f"L [{l_unit}]", self.colors["line_4"]),
            ]

        for index, (ax, values, title, ylabel, color) in enumerate(plot_specs):
            ax.clear()
            ax.set_facecolor("#fbfcfe")
            ax.set_title(title, fontsize=11, fontweight="bold", color=self.colors["text"])
            if index < 2:
                ax.set_xlabel("")
            else:
                ax.set_xlabel("Frecuencia [Hz]", fontsize=9, labelpad=-6)
            ax.set_ylabel(ylabel)
            ax.set_xscale("log")
            ax.grid(True, which="both", color=self.colors["grid"], alpha=0.9)

            valid = np.isfinite(values) & np.isfinite(freqs)
            if np.any(valid):
                ax.plot(freqs[valid], values[valid], color=color, linewidth=2.2)

        self.canvas.draw_idle()

    def _auto_scale_series(self, values: np.ndarray, base_unit: str) -> tuple[np.ndarray, str]:
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            return values, base_unit

        peak = float(np.max(np.abs(finite)))
        scales = [
            (1e9, f"n{base_unit}"),
            (1e6, f"µ{base_unit}"),
            (1e3, f"m{base_unit}"),
            (1.0, base_unit),
            (1e-3, f"k{base_unit}"),
        ]

        for factor, unit in scales:
            scaled_peak = peak * factor
            if 0.1 <= scaled_peak < 1000:
                return values * factor, unit
        return values, base_unit

    def _session_payload(self) -> dict[str, object]:
        return {
            "app": "SR860 Impedance Workbench",
            "dut": {
                "name": self.dut_name_var.get().strip() or "DUT",
                "type": self.dut_type_var.get(),
            },
            "setup": {
                "start_freq_hz": self.start_freq_var.get(),
                "stop_freq_hz": self.stop_freq_var.get(),
                "points": self.points_var.get(),
                "logarithmic": self.log_sweep_var.get(),
                "series_resistor_ohm": self.series_resistor_var.get(),
                "source_series_ohm": self.source_series_var.get(),
                "total_series_note": "La medición usa Rs física externa + Z serie fuente/equipo.",
                "output_amplitude_v": self.output_amplitude_var.get(),
                "effective_source_v": self.effective_source_var.get(),
                "output_connection": self.output_connection_var.get(),
                "output_load": self.output_load_var.get(),
                "phase_deg": self.phase_var.get(),
                "dc_offset_v": self.offset_var.get(),
                "settling_factor": self.settling_factor_var.get(),
                "time_constant": self.time_constant_var.get(),
                "filter_slope": self.filter_slope_var.get(),
                "reference_source": self.reference_source_var.get(),
                "input_mode": self.input_mode_var.get(),
                "input_range": self.input_range_var.get(),
                "coupling_dc": self.coupling_dc_var.get(),
                "shield_grounded": self.shield_ground_var.get(),
                "sync_filter": self.sync_filter_var.get(),
                "plot_mode": self._current_plot_mode(),
                "selected_svg_exports": [
                    key for key, var in self.export_chart_vars.items()
                    if var.get()
                ],
            },
            "summary": self._current_summary(),
            "measurements": [
                {
                    "frequency_hz": point.frequency_hz,
                    "x_v": point.x_v,
                    "y_v": point.y_v,
                    "source_v": point.source_v,
                    "source_phase_deg": point.source_phase_deg,
                    "external_series_ohm": point.external_series_ohm,
                    "source_series_ohm": point.source_series_ohm,
                    "total_series_ohm": point.total_series_ohm,
                    "real_impedance_ohm": point.r_ohm,
                    "r_ohm": point.r_ohm,
                    "x_ohm": point.x_ohm,
                    "z_abs_ohm": point.z_abs_ohm,
                    "phase_deg": point.phase_deg,
                    "c_f": point.capacitance_f,
                    "l_h": point.inductance_h,
                }
                for point in self.measurements
            ],
        }

    def export_session(self) -> None:
        if not self.measurements:
            messagebox.showinfo("Exportar sesión", "Todavía no hay datos para exportar.")
            return

        filename = filedialog.asksaveasfilename(
            title="Guardar sesión",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="sr860_impedance_session.json",
        )
        if not filename:
            return

        with open(filename, "w", encoding="utf-8") as handle:
            json.dump(self._session_payload(), handle, indent=2, allow_nan=True)

        self.status_var.set(f"Sesión exportada en {filename}")

    def export_csv(self) -> None:
        if not self.measurements:
            messagebox.showinfo("Exportar CSV", "Todavía no hay datos para exportar.")
            return

        filename = filedialog.asksaveasfilename(
            title="Guardar CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="sr860_impedance_sweep.csv",
        )
        if not filename:
            return

        with open(filename, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "frequency_hz",
                    "x_v",
                    "y_v",
                    "source_v",
                    "source_phase_deg",
                    "external_series_ohm",
                    "source_series_ohm",
                    "total_series_ohm",
                    "real_impedance_ohm",
                    "r_ohm",
                    "x_ohm",
                    "z_abs_ohm",
                    "phase_deg",
                    "c_f",
                    "l_h",
                ]
            )
            for point in self.measurements:
                writer.writerow(
                    [
                        point.frequency_hz,
                        point.x_v,
                        point.y_v,
                        point.source_v,
                        point.source_phase_deg,
                        point.external_series_ohm,
                        point.source_series_ohm,
                        point.total_series_ohm,
                        point.r_ohm,
                        point.r_ohm,
                        point.x_ohm,
                        point.z_abs_ohm,
                        point.phase_deg,
                        point.capacitance_f,
                        point.inductance_h,
                    ]
                )

        self.status_var.set(f"CSV exportado en {filename}")

    def _svg_chart_payloads(self) -> dict[str, tuple[str, str, str, np.ndarray, np.ndarray, str]]:
        freqs = np.array([p.frequency_hz for p in self.measurements], dtype=float)
        return {
            "r": (
                "r_vs_freq.svg",
                "Re(Z) vs Frecuencia",
                "Re(Z) [Ω]",
                freqs,
                np.array([p.r_ohm for p in self.measurements], dtype=float),
                self.colors["line_1"],
            ),
            "xz": (
                "xz_vs_freq.svg",
                "Xz vs Frecuencia",
                "Xz [Ω]",
                freqs,
                np.array([p.x_ohm for p in self.measurements], dtype=float),
                self.colors["line_2"],
            ),
            "c": (
                "c_vs_freq.svg",
                "C vs Frecuencia",
                "C [F]",
                freqs,
                np.array([p.capacitance_f for p in self.measurements], dtype=float),
                self.colors["line_2"],
            ),
            "z": (
                "z_vs_freq.svg",
                "|Z| vs Frecuencia",
                "|Z| [Ω]",
                freqs,
                np.array([p.z_abs_ohm for p in self.measurements], dtype=float),
                self.colors["line_3"],
            ),
            "phase": (
                "phase_vs_freq.svg",
                "Fase de Z vs Frecuencia",
                "Fase [deg]",
                freqs,
                np.array([p.phase_deg for p in self.measurements], dtype=float),
                self.colors["line_4"],
            ),
            "l": (
                "l_vs_freq.svg",
                "L vs Frecuencia",
                "L [H]",
                freqs,
                np.array([p.inductance_h for p in self.measurements], dtype=float),
                self.colors["line_4"],
            ),
        }

    def export_svg(self) -> None:
        if not self.measurements:
            messagebox.showinfo("Exportar SVG", "Todavía no hay datos para exportar.")
            return

        selected_keys = [
            key for key, var in self.export_chart_vars.items()
            if var.get()
        ]
        if not selected_keys:
            messagebox.showinfo("Exportar SVG", "Selecciona al menos una gráfica para exportar.")
            return

        output_dir = filedialog.askdirectory(title="Selecciona la carpeta para los SVG")
        if not output_dir:
            return

        output_path = Path(output_dir)
        exported_files: list[str] = []

        if "dashboard" in selected_keys:
            dashboard_path = output_path / "sr860_dashboard.svg"
            self.figure.savefig(dashboard_path, format="svg", bbox_inches="tight")
            exported_files.append(dashboard_path.name)

        chart_payloads = self._svg_chart_payloads()

        for key in selected_keys:
            if key == "dashboard":
                continue
            filename, title, ylabel, freqs, values, color = chart_payloads[key]
            single_figure = Figure(figsize=(6.4, 4.0), dpi=100, facecolor="white")
            axis = single_figure.add_subplot(111)
            axis.set_facecolor("white")
            axis.set_title(title)
            axis.set_xlabel("Frecuencia [Hz]", fontsize=9, labelpad=-4)
            axis.set_ylabel(ylabel)
            axis.set_xscale("log")
            axis.grid(True, which="both", color=self.colors["grid"], alpha=0.9)

            valid = np.isfinite(freqs) & np.isfinite(values)
            if np.any(valid):
                axis.plot(freqs[valid], values[valid], color=color, linewidth=2.2)

            single_figure.savefig(output_path / filename, format="svg", bbox_inches="tight")
            exported_files.append(filename)

        self.status_var.set(f"SVG exportados en {output_path}: {', '.join(exported_files)}")


def main() -> None:
    root = tk.Tk()
    app = SR860ImpedanceApp(root)

    def on_close() -> None:
        if app.worker and app.worker.is_alive():
            if not messagebox.askyesno("Salir", "Hay un barrido en curso. ¿Deseas salir de todos modos?"):
                return
            app.stop_requested = True
        app.controller.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
