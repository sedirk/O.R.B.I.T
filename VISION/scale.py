import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import serial
import serial.tools.list_ports


@dataclass
class ScaleReading:
    weight_g: float
    stable: bool
    raw: str
    unit: str = "g"
    timestamp: float = 0.0


class ElectronicScaleReader:
    """Serial reader for common RS232 bench scales.

    The LENQI scale in the project photo usually appears through a USB serial
    adapter. Industrial scales tend to emit ASCII frames such as
    "ST,GS,+000264.10g" or "US,+0.264kg"; the parser below intentionally keeps
    that loose so it can be tuned from real captured frames.
    """

    WEIGHT_RE = re.compile(
        r"(?P<sign>[+-])?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|lb)?",
        re.IGNORECASE,
    )

    def __init__(
        self,
        port: Optional[str] = "auto",
        baudrate: int = 9600,
        timeout: float = 0.2,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: int = 1,
    ):
        self.port = self.resolve_port(port)
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial = serial.Serial(
            port=self.port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
        )

    @staticmethod
    def available_ports() -> list[dict]:
        return [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in serial.tools.list_ports.comports()
        ]

    @classmethod
    def resolve_port(cls, port: Optional[str]) -> str:
        if port and port.lower() != "auto":
            return port

        ports = cls.available_ports()
        preferred = [
            p
            for p in ports
            if p["device"].upper() != "COM1"
            and re.search(r"USB|Serial|FTDI|CP210|CH340|Prolific", p["description"] + p["hwid"], re.I)
        ]
        if preferred:
            return preferred[0]["device"]
        if ports:
            return ports[0]["device"]
        raise RuntimeError("No serial ports found for the electronic scale")

    def close(self) -> None:
        if self.serial and self.serial.is_open:
            self.serial.close()

    def read_once(self) -> Optional[ScaleReading]:
        raw_bytes = self.serial.readline()
        if not raw_bytes:
            return None
        raw = raw_bytes.decode("ascii", errors="ignore").strip()
        if not raw:
            return None
        reading = self.parse(raw)
        if reading:
            reading.timestamp = time.time()
        return reading

    @classmethod
    def parse(cls, raw: str) -> Optional[ScaleReading]:
        normalized = raw.strip()
        matches = list(cls.WEIGHT_RE.finditer(normalized))
        if not matches:
            return None
        match = matches[-1]

        value = float(match.group("value"))
        if match.group("sign") == "-":
            value = -value

        unit = (match.group("unit") or "g").lower()
        if unit == "kg":
            weight_g = value * 1000.0
        elif unit == "lb":
            weight_g = value * 453.59237
        else:
            weight_g = value

        upper = normalized.upper()
        stable = bool(re.search(r"\bST\b|\bS\b|稳定", normalized, re.I))
        if re.search(r"\bUS\b|\bU\b|不稳定", upper, re.I):
            stable = False

        return ScaleReading(weight_g=weight_g, stable=stable, raw=raw, unit=unit, timestamp=time.time())

    def readings(self) -> Iterable[ScaleReading]:
        while True:
            reading = self.read_once()
            if reading:
                yield reading

    def wait_for_stable(
        self,
        min_weight_g: float = 5.0,
        stable_count: int = 3,
        tolerance_g: float = 0.3,
        cooldown_s: float = 2.0,
    ) -> ScaleReading:
        recent: list[ScaleReading] = []
        while True:
            reading = self.read_once()
            if not reading:
                continue

            print(
                f"Scale {reading.weight_g:.1f} g "
                f"{'stable' if reading.stable else 'moving'} raw={reading.raw}"
            )
            if not reading.stable or reading.weight_g < min_weight_g:
                recent.clear()
                continue

            recent.append(reading)
            recent = recent[-stable_count:]
            if len(recent) < stable_count:
                continue

            weights = [r.weight_g for r in recent]
            if max(weights) - min(weights) <= tolerance_g:
                time.sleep(cooldown_s)
                return recent[-1]
