"""What sits at each end of a signal net (`h.net(..., signal=True)`), and the limits that device puts on the net.

A signal net is graded against the devices at its ends, never against a figure for some other device:
    a microcontroller board's I/O pin   automatic for a board connector (ARDUINO-UNO, ARDUINO-NANO), or h.mcu_pin(pin, board="uno") for
                                        a pin reached through another connector (a Dupont header): the board's per-pin limit (eitri.power)
    h.plc_input(pin, iec_type=1|2|3)    a PLC digital input of IEC 61131-2 type 1, 2 or 3 (24 V DC, current sinking)
    h.sensor_output(pin, part=...)      a sensor's switching output: a catalogue part's datasheet rating, or the model's own figures
With no declared end the signal-net check does not run, and says what to declare.

IEC 61131-2 input types. The standard itself (IEC 61131-2:2017) is paywalled and was not read. The limits below are read off the
"Switching Characteristics for IEC61131-2 Type 1, 2, and 3 Proximity Switches" chart that Texas Instruments draws in its ISO1228
data sheet (SLLSFN5B, June 2023, revised September 2025, Figure 8-1 on page 27, https://www.ti.com/lit/ds/symlink/iso1228.pdf, read
2026-11-12). TI prints them as a chart, not as numbers: they were measured on a 6000-pixel render of the page against the chart's 1 V
and 1 mA ticks (about 23 pixels per volt and 39 pixels per mA). The same data sheet prints, for Type 1 on that page, "VOFF_MAX = 5V",
"VON_MIN = 15V" and "IL(min) = 2mA".
    ON (signal 1)   Type 1: 15.2-30 V at 2-15 mA    Type 2: 11.2-30 V at 6-30 mA    Type 3: 11.2-30 V at 2-15 mA
    OFF (signal 0)  -3 to 5 V at any current up to the ON maximum, or up to 15 V (Type 1) / 11 V (Types 2, 3) at a current of at
                    most 0.48 mA (Type 1), 1.99 mA (Type 2), 1.37 mA (Type 3)
The ON voltage's lower edge is drawn about 0.2 V above the OFF region's top (15.0 V, 11.0 V); by the conservative-figure rule the
higher reading is used as the least voltage an input is sure to read as ON. The OFF-state currents are rounded down to 0.1 mA (a
limit: the lower figure): 0.4, 1.9 and 1.3 mA.
"""
from __future__ import annotations

from dataclasses import dataclass

IEC_SOURCE_SHORT = "IEC 61131-2 input types, as drawn in TI ISO1228 data sheet SLLSFN5B Figure 8-1 p.27 (graph-read)"
IEC_SOURCE_URL = "https://www.ti.com/lit/ds/symlink/iso1228.pdf"
IEC_SOURCE_REV = "TI SLLSFN5B (June 2023, revised September 2025), Figure 8-1, page 27; read 2026-11-12"


@dataclass(frozen=True)
class InputType:
    iec_type: int
    on_V_min: float          # the least voltage the input is sure to read as signal 1
    on_V_max: float
    on_mA_min: float         # an input of this type draws at least this when ON
    on_mA_max: float         # ... and at most this
    off_V_max: float         # signal 0 at any current up to on_mA_max
    off_mA_max: float        # signal 0 up to off_V_hi when the current is at most this (a 2-wire sensor's leakage must fit under it)
    off_V_hi: float


IEC_TYPES = {
    1: InputType(1, 15.2, 30.0, 2.0, 15.0, 5.0, 0.4, 15.0),
    2: InputType(2, 11.2, 30.0, 6.0, 30.0, 5.0, 1.9, 11.0),
    3: InputType(3, 11.2, 30.0, 2.0, 15.0, 5.0, 1.3, 11.0),
}

KINDS = ("mcu_pin", "plc_input", "sensor_output")


@dataclass(eq=False)
class SignalEnd:
    """A device declared at one pin of a signal net."""
    pin: object                          # PinRef
    kind: str                            # mcu_pin | plc_input | sensor_output
    iec_type: int | None = None          # plc_input
    board: str = ""                      # mcu_pin: eitri.power's board key ("uno", "nano")
    part: str = ""
    max_output_A: float | None = None    # sensor_output: the most its output may carry
    wires: int | None = None             # sensor_output: 2 (in series with the input) or 3 (PNP / NPN with its own supply)
    drop_V: float | None = None          # sensor_output: its voltage drop when ON
    leakage_A: float | None = None       # sensor_output (2-wire): the current it lets through when OFF
    source: str = ""                     # where the sensor's figures come from: a short citation, or "the model file"
    url: str = ""
    rev: str = ""

    @property
    def label(self) -> str:
        if self.kind == "mcu_pin":
            return f"{self.board} I/O pin {self.pin}"
        if self.kind == "plc_input":
            return f"PLC input {self.pin} (IEC 61131-2 type {self.iec_type}{', ' + self.part if self.part else ''})"
        return f"sensor output {self.pin} ({self.part or 'sensor'}, {self.wires}-wire)"

    def to_json(self) -> dict:
        return {"pin": str(self.pin), "kind": self.kind, "board": self.board, "iec_type": self.iec_type, "part": self.part, "max_output_A": self.max_output_A,
                "wires": self.wires, "drop_V": self.drop_V, "leakage_A": self.leakage_A, "source": self.source}


def sensor_from_catalog(part: str) -> dict | None:
    """A catalogue sensor's output figures (eitri.pneumatics.catalog: read-only here), or None when the part is not in it."""
    from ..pneumatics import catalog as pc
    try:
        f, series = pc.lookup("sensor", part)
    except pc.CatalogError:
        return None
    s = pc.SOURCES[series.sources[0]]
    wires = 2 if f.get("output") == "2-wire" else 3
    return {"max_output_A": f["load_mA_max"] / 1000.0, "wires": wires, "drop_V": f.get("drop_V"),
            "leakage_A": None if f.get("leakage_mA") is None else f["leakage_mA"] / 1000.0,
            "source": f"{s.doc}: output {f.get('output')}, load current up to {f['load_mA_max']:g} mA",
            "url": s.url, "rev": f"{s.doc}; fetched {s.fetched}"}
