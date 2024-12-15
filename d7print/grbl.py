import time
from typing import List

from serial import Serial, SerialException


class Grbl:

    def __init__(self, port: str, baudrate: int, state_request_period: float):
        self._serial = Serial()
        self._serial.port = port
        self._serial.baudrate = baudrate
        self._serial.timeout = 0

        self._state_request_period = state_request_period
        self._recv_buf = bytearray()

        self._state_response_expiry = 0.0
        self._last_state_request_time = 0.0
        self._await_response: bool = False
        self._homing: bool = False

        self._status_line: List[str] = ['', '', '']
        self._state: str = ''

    def send(self, cmd: str):
        try:
            if not self._serial.is_open:
                self._serial.open()
            self._serial.write(bytes(cmd, 'ascii'))
        except SerialException:
            # noinspection PyBroadException
            try:
                self._serial.close()
            except Exception:
                pass

    def receive(self) -> List[str]:
        try:
            if not self._serial.is_open:
                self._serial.open()
            if time.time() > self._last_state_request_time + self._state_request_period:
                self._last_state_request_time = time.time()
                self._serial.write(b'?')
            try:
                result = []
                for b in self._serial.read(4096):
                    self._parse_byte(b, result)
                return result
            except OSError as e:
                if e.errno != 11:  # device temporary unavailable
                    raise e
        except SerialException:
            # noinspection PyBroadException
            try:
                self._serial.close()
            except Exception:
                pass

    def get_status_line(self):
        return '|'.join(self._status_line)

    def get_state(self):
        """Idle, Run, Hold:x, Jog, Alarm, Door:x, Check, Home, Sleep"""
        return self._state if self._state_response_expiry > time.time() else ''

    def close(self):
        self._serial.close()

    def _parse_byte(self, b, parsed_lines: List[str]):
        if b == 13 or b == 0:
            return

        if b != 10:
            self._recv_buf.append(b)
            return

        line = str(self._recv_buf, 'ascii')
        self._recv_buf.clear()
        if line.startswith('<'):
            line_parts = line.strip('<> ').split('|')
            self._state = line_parts[0]
            self._status_line[0] = '|'.join(line_parts[0:4])
            if len(line_parts) > 4:
                self._status_line[1 if line_parts[4].startswith('Ov') else 2] = line_parts[4]
            self._state_response_expiry = time.time() + self._state_request_period * 2
        else:
            parsed_lines.append(line)
