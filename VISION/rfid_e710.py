import time
from dataclasses import dataclass

import serial


class E710Error(RuntimeError):
    pass


ERROR_CODES = {
    0x10: "命令已执行",
    0x11: "命令执行失败",
    0x20: "CPU 复位错误",
    0x21: "打开 CW 错误",
    0x22: "天线未连接",
    0x23: "写 Flash 错误",
    0x24: "读 Flash 错误",
    0x25: "设置发射功率错误",
    0x31: "盘存标签错误",
    0x32: "读标签错误",
    0x33: "写标签错误",
    0x34: "锁定标签错误",
    0x35: "灭活标签错误",
    0x36: "无可操作标签错误",
    0x37: "成功盘存但访问失败",
    0x38: "缓存为空",
    0x40: "访问标签错误或访问密码错误",
    0x41: "无效的参数",
    0x42: "wordCnt 参数超过规定长度",
    0x43: "MemBank 参数超出范围",
    0x48: "输出功率参数超出范围",
}


@dataclass
class E710Frame:
    address: int
    command: int
    payload: bytes
    raw: bytes
    checksum_ok: bool


@dataclass
class E710Tag:
    epc: str
    epc_ascii: str | None
    pc: str
    rssi_dbm: int | None
    antenna: int | None
    frequency_index: int | None
    raw_payload: str


def checksum(data: bytes) -> int:
    return ((~(sum(data) & 0xFF) + 1) & 0xFF)


def make_frame(address: int, command: int, payload: bytes = b"") -> bytes:
    body = bytes([0xA0, len(payload) + 3, address & 0xFF, command & 0xFF]) + payload
    return body + bytes([checksum(body)])


def parse_frames(buffer: bytes) -> list[E710Frame]:
    frames: list[E710Frame] = []
    index = 0
    while index < len(buffer):
        if buffer[index] != 0xA0:
            index += 1
            continue
        if index + 2 > len(buffer):
            break
        length = buffer[index + 1]
        end = index + length + 2
        if end > len(buffer):
            break
        raw = buffer[index:end]
        if len(raw) >= 5:
            frames.append(
                E710Frame(
                    address=raw[2],
                    command=raw[3],
                    payload=raw[4:-1],
                    raw=raw,
                    checksum_ok=checksum(raw[:-1]) == raw[-1],
                )
            )
        index = end
    return frames


def parse_inventory_payload(payload: bytes) -> E710Tag | None:
    if len(payload) <= 7:
        return None
    epc = payload[3:-1].hex().upper()
    if not epc:
        return None
    epc_ascii = None
    epc_bytes = payload[3:-1]
    if all(32 <= b < 127 for b in epc_bytes):
        epc_ascii = epc_bytes.decode("ascii", errors="replace")
    status = payload[0]
    return E710Tag(
        epc=epc,
        epc_ascii=epc_ascii,
        pc=payload[1:3].hex().upper(),
        rssi_dbm=int(payload[-1]) - 129,
        antenna=(status & 0x03) + 1,
        frequency_index=status >> 2,
        raw_payload=payload.hex(" ").upper(),
    )


def normalize_epc_hex(value: str) -> str:
    text = "".join(ch for ch in str(value or "").upper() if ch in "0123456789ABCDEF")
    if not text:
        raise E710Error("empty EPC hex")
    if len(text) % 4 != 0:
        raise E710Error("EPC hex length must be a multiple of 16-bit words")
    if len(text) > 124:
        raise E710Error("EPC hex is too long")
    return text


class E710Reader:
    def __init__(self, port: str = "COM3", baudrate: int = 115200, address: int = 0xFF, timeout: float = 0.08):
        self.port = port
        self.baudrate = int(baudrate)
        self.address = int(address) & 0xFF
        self.timeout = float(timeout)
        self.serial: serial.Serial | None = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def open(self) -> None:
        if self.serial and self.serial.is_open:
            return
        try:
            self.serial = serial.Serial(
                self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=0.5,
            )
            self.serial.dtr = True
            self.serial.rts = True
            time.sleep(0.08)
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()
        except Exception as exc:
            raise E710Error(f"open {self.port} @ {self.baudrate} failed: {exc}") from exc

    def close(self) -> None:
        if self.serial:
            self.serial.close()

    def transact(self, command: int, payload: bytes = b"", wait_s: float = 0.5) -> list[E710Frame]:
        if not self.serial or not self.serial.is_open:
            raise E710Error("reader is not open")
        packet = make_frame(self.address, command, payload)
        self.serial.write(packet)
        self.serial.flush()
        raw = self._read_for(wait_s)
        return parse_frames(raw)

    def _read_for(self, seconds: float) -> bytes:
        assert self.serial is not None
        deadline = time.time() + max(0.05, seconds)
        buffer = bytearray()
        while time.time() < deadline:
            data = self.serial.read(4096)
            if data:
                buffer.extend(data)
                deadline = max(deadline, time.time() + 0.12)
        return bytes(buffer)

    def query_info(self) -> dict:
        info: dict = {"port": self.port, "baudrate": self.baudrate}
        for name, command in (
            ("firmware", 0x72),
            ("antenna", 0x75),
            ("output_power", 0x77),
            ("frequency_region", 0x79),
        ):
            frames = self.transact(command, wait_s=0.45)
            frame = next((item for item in frames if item.checksum_ok and item.command == command), None)
            if not frame:
                info[name] = None
                continue
            info["address"] = frame.address
            info[name] = frame.payload.hex(" ").upper()
            if name == "output_power" and frame.payload:
                info["output_power_dbm"] = int(frame.payload[0])
            elif name == "antenna" and frame.payload:
                info["antenna_index"] = int(frame.payload[0])
        return info

    def inventory_once(self, repeat: int = 1, wait_s: float = 1.5) -> dict:
        frames = self.transact(0x89, bytes([repeat & 0xFF]), wait_s=wait_s)
        tags: list[E710Tag] = []
        summaries: list[dict] = []
        errors: list[str] = []
        for frame in frames:
            if not frame.checksum_ok or frame.command != 0x89:
                continue
            if len(frame.payload) == 1:
                errors.append(f"0x{frame.payload[0]:02X}")
                continue
            tag = parse_inventory_payload(frame.payload)
            if tag:
                tags.append(tag)
            elif len(frame.payload) == 7:
                summaries.append(
                    {
                        "read_rate": frame.payload[1] * 256 + frame.payload[2],
                        "data_count": int.from_bytes(frame.payload[3:7], "big"),
                        "raw": frame.payload.hex(" ").upper(),
                    }
                )
        unique: dict[str, E710Tag] = {}
        for tag in tags:
            unique[tag.epc] = tag
        return {
            "tags": [tag.__dict__ for tag in unique.values()],
            "summaries": summaries,
            "errors": errors,
            "frame_count": len(frames),
        }

    def write_tag(
        self,
        password: bytes,
        mem_bank: int,
        word_address: int,
        word_count: int,
        data: bytes,
        wait_s: float = 1.2,
    ) -> dict:
        if len(password) != 4:
            raise E710Error("RFID access password must be 4 bytes")
        if len(data) % 2:
            raise E710Error("write data length must be word aligned")
        payload = password + bytes([mem_bank & 0xFF, word_address & 0xFF, word_count & 0xFF]) + data
        frames = self.transact(0x82, payload, wait_s=wait_s)
        errors: list[str] = []
        success = False
        raw_frames = []
        for frame in frames:
            if not frame.checksum_ok or frame.command != 0x82:
                continue
            raw_frames.append(frame.raw.hex(" ").upper())
            if len(frame.payload) == 1:
                errors.append(self._error_text(frame.payload[0]))
            elif len(frame.payload) >= 3 and frame.payload[-3] == 0x10:
                success = True
            elif len(frame.payload) >= 3:
                errors.append(self._error_text(frame.payload[-3]))
        if not frames:
            raise E710Error("write command returned no response")
        if not success:
            raise E710Error("; ".join(errors) or "write command did not confirm success")
        return {"written": True, "frames": raw_frames}

    def set_access_epc_match(self, epc_hex: str, wait_s: float = 0.6) -> dict:
        epc_hex = normalize_epc_hex(epc_hex)
        epc_bytes = bytes.fromhex(epc_hex)
        if len(epc_bytes) > 62:
            raise E710Error("EPC match is too long")
        payload = bytes([0x00, len(epc_bytes)]) + epc_bytes
        return self._simple_ack(0x85, payload, "set EPC match", wait_s=wait_s)

    def cancel_access_epc_match(self, wait_s: float = 0.4) -> dict:
        return self._simple_ack(0x85, bytes([0x01]), "cancel EPC match", wait_s=wait_s)

    def _simple_ack(self, command: int, payload: bytes, action: str, wait_s: float = 0.5) -> dict:
        frames = self.transact(command, payload, wait_s=wait_s)
        raw_frames = []
        errors: list[str] = []
        for frame in frames:
            if not frame.checksum_ok or frame.command != command:
                continue
            raw_frames.append(frame.raw.hex(" ").upper())
            if len(frame.payload) == 1 and frame.payload[0] == 0x10:
                return {"ok": True, "frames": raw_frames}
            if len(frame.payload) == 1:
                errors.append(self._error_text(frame.payload[0]))
        if not frames:
            raise E710Error(f"{action} returned no response")
        raise E710Error("; ".join(errors) or f"{action} did not confirm success")

    def write_epc_hex(
        self,
        epc_hex: str,
        password_hex: str = "00000000",
        require_single: bool = True,
        expected_current_epc: str | None = None,
        verify: bool = True,
    ) -> dict:
        epc_hex = normalize_epc_hex(epc_hex)
        password = bytes.fromhex(normalize_epc_hex(password_hex).zfill(8)[-8:])
        before = self.inventory_once(repeat=1, wait_s=1.2)
        before_tags = before.get("tags", [])
        if require_single and len(before_tags) != 1:
            raise E710Error(f"expected exactly one tag before write, found {len(before_tags)}")
        if expected_current_epc:
            expected = normalize_epc_hex(expected_current_epc)
            if expected not in {tag.get("epc") for tag in before_tags}:
                raise E710Error(f"target EPC {expected} was not seen before write")

        match_result = None
        cancel_result = None
        cancel_error = None
        epc_bytes = bytes.fromhex(epc_hex)
        epc_word_count = len(epc_bytes) // 2
        pc_word = bytes([(epc_word_count << 3) & 0xFF, 0x00])
        write_data = pc_word + epc_bytes
        try:
            if expected_current_epc:
                match_result = self.set_access_epc_match(expected_current_epc)
            write_result = self.write_tag(
                password=password,
                mem_bank=0x01,
                word_address=0x01,
                word_count=epc_word_count + 1,
                data=write_data,
            )
        finally:
            if expected_current_epc:
                try:
                    cancel_result = self.cancel_access_epc_match()
                except E710Error as exc:
                    cancel_error = str(exc)

        after = None
        verified = False
        if verify:
            time.sleep(0.25)
            for _ in range(3):
                after = self.inventory_once(repeat=1, wait_s=1.0)
                if epc_hex in {tag.get("epc") for tag in after.get("tags", [])}:
                    verified = True
                    break
                time.sleep(0.2)
            if not verified:
                raise E710Error(f"write response succeeded but EPC {epc_hex} was not verified")

        return {
            "written": True,
            "verified": verified,
            "epc_hex": epc_hex,
            "epc_ascii": self._ascii_epc(epc_hex),
            "before": before,
            "after": after,
            "write": write_result,
            "match": match_result,
            "match_cancel": cancel_result,
            "match_cancel_error": cancel_error,
        }

    def probe(self, repeat: int = 1, wait_s: float = 1.5) -> dict:
        info = self.query_info()
        inventory = self.inventory_once(repeat=repeat, wait_s=wait_s)
        if not any(info.get(key) for key in ("firmware", "antenna", "output_power", "frequency_region")) and not inventory.get("frame_count"):
            raise E710Error(f"no E710 response on {self.port} @ {self.baudrate}")
        return {"info": info, "inventory": inventory}

    @staticmethod
    def _error_text(code: int) -> str:
        return f"0x{code:02X} {ERROR_CODES.get(code, '未知错误')}"

    @staticmethod
    def _ascii_epc(epc_hex: str) -> str | None:
        try:
            raw = bytes.fromhex(epc_hex)
        except ValueError:
            return None
        if all(32 <= b < 127 for b in raw):
            return raw.decode("ascii")
        return None
