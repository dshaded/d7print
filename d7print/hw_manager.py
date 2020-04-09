import os
import re
import time
from collections import deque
from re import RegexFlag
from threading import Lock, Thread
from time import sleep
from typing import List, Optional
from zipfile import ZipFile

from PIL import Image
from flask import Flask
from serial import Serial, SerialException


class GrblStateException(Exception):
    pass


class HwManager:

    def __init__(self, app: Flask):
        self._app = app

        self._serial = Serial()
        self._serial.port = '/dev/ttyS3'
        self._serial.baudrate = 115200
        self._serial.timeout = 0
        self._recv_buf = bytearray()
        self._guard_file = '/var/run/d7print.guard'
        self._guard_time = 0
        self._fb_device = '/dev/fb0'
        self._gpio_reset_path = '/sys/class/gpio/gpio7/value'
        open('/sys/class/gpio/export', 'w').write('7')
        open('/sys/class/gpio/gpio7/direction', 'w').write('high')
        self._display_size = 1440, 2560

        self._state_missing_counter = 0
        self._hold = False
        self._force_soft_reset = False
        self._commands: deque[str] = deque()
        self._run_log = deque(maxlen=1000)
        self._log_lock = Lock()
        self._await_response: bool = False
        self._grbl_homing: bool = False
        self._grbl_state_line: str = ''
        self._grbl_ov_line: str = ''
        self._grbl_wco_line: str = ''
        self._grbl_state: str = ''
        self._grbl_prev_state: str = ''
        self._image_pack_path: str = ''

        self._img_mask: Image.Image = Image.open(os.path.dirname(os.path.abspath(__file__)) + 'mask.png').convert('L')

        self._run_thread_obj: Optional[Thread] = None
        self._ensure_running()

    def set_image_pack(self, image_pack_path: str):
        self._image_pack_path = image_pack_path

    def get_image_pack(self) -> str:
        return self._image_pack_path

    def add_commands(self, commands: List[str]):
        self._ensure_running()
        self._commands.extend(commands)

    def get_commands(self) -> List[str]:
        return list(self._commands)

    def clear_commands(self, soft_reset=False):
        self._commands.clear()
        if soft_reset:
            self._ensure_running()
            self._force_soft_reset = True

    def hold(self):
        self._ensure_running()
        self._hold = True

    def resume(self):
        self._ensure_running()
        self._hold = False

    def is_on_hold(self):
        return self._hold

    def hard_stop(self):
        self._reset_pin(0)
        self._commands.clear()
        self._hold = False
        self._reset_pin(1)

    def get_log(self, first_log_entry_id: int):
        with self._log_lock:
            log_list = list(self._run_log)

        first_pos = 0
        if log_list:
            first_id: int = log_list[0]['id']
            if first_log_entry_id > first_id:
                first_pos = first_log_entry_id - first_id
        return log_list[first_pos:]

    def get_grbl_state_line(self):
        return self._grbl_state_line + self._grbl_wco_line + self._grbl_ov_line

    # PRIVATE Section

    def _ensure_running(self):
        if not self._run_thread_obj or not self._run_thread_obj.is_alive():
            self._run_thread_obj = Thread(target=self._run_thread, name='hw_run', daemon=True)
            self._run_thread_obj.start()

    def _log_add(self, msg: str, e: Exception = None):
        if e:
            self._app.logger.error(msg, exc_info=e)
        else:
            self._app.logger.info(msg)

        with self._log_lock:
            last_id = self._run_log[len(self._run_log) - 1]['id'] if self._run_log else 0
            self._run_log.append({
                'id': last_id + 1,
                'time': time.time(),
                'msg': msg,
            })

    # noinspection PyMethodMayBeStatic
    def _show_zip_image(self, image_pack_path: str, image_name: str):
        with open(self._fb_device, 'wb') as fb:
            img = Image.new('RGBA', self._display_size, (0x00, 0x00, 0x00, 0xff))
            if image_name:
                with ZipFile(image_pack_path) as zf, zf.open(image_name) as zi, Image.open(zi) as i:
                    img.paste(i, mask=self._img_mask)
            fb.write(img.tobytes())

    # noinspection PyMethodMayBeStatic
    def _reset_pin(self, state):
        open(self._gpio_reset_path, 'w').write('1' if state else '0')

    def _serial_write(self, cmd: str):
        self._serial.write(bytes(cmd, 'ascii'))

    def _exec(self, raw_cmd: str):
        cmd = raw_cmd.partition(';')[0].strip()
        lcmd = cmd.lower()
        if not lcmd:
            self._log_add(f'>>> {raw_cmd}')
            return
        if lcmd == '?':
            self._serial_write('?')
            return

        self._log_add(f'>>> {raw_cmd}')

        if lcmd == 'reset' or lcmd == '\x18':
            self._serial_write('\x18')
        elif lcmd == 'hwreset':
            self._reset_pin(0)
            sleep(0.1)
            self._reset_pin(1)
        elif lcmd == 'reboot':
            self._reset_pin(0)
            os.system('systemctl reboot')
        elif lcmd == 'shutdown':
            self._reset_pin(0)
            os.system('systemctl poweroff')
        elif lcmd == '!':
            self._hold = True
            self._serial_write('!')
        elif lcmd == '~':
            self._hold = False
            self._serial_write('~')
        elif lcmd.startswith('slice'):
            img_name = re.findall(r'[0-9a-z_-]+.png', cmd, RegexFlag.IGNORECASE)
            self._show_zip_image(self._image_pack_path, img_name[0] if img_name else '')
        else:
            if lcmd.startswith('$h'):
                self._grbl_homing = True
            self._await_response = True
            self._serial_write(cmd)
            self._serial_write('\n')

    # THREADING Section

    def _shutdown_guard_init(self):
        with open(self._guard_file, 'w') as f:
            f.write('d7print guard file')
        self._guard_time = os.path.getmtime(self._guard_file)

    def _check_shutdown(self):
        return os.path.getmtime(self._guard_file) != self._guard_time

    def _run_thread(self):
        self._shutdown_guard_init()

        while True:
            if self._check_shutdown():
                self._log_add('Hw manager thread stopped by guard file')
                self._serial.close()
                return
            try:
                if not self._serial.is_open:
                    self._serial.open()
                sleep(0.05)
                self._exec('?')
                sleep(0.05)
                self._read_loop()
                self._run_loop()
            except Exception as e:
                self._log_add(f'Execution error: {e}', e)
                self._commands.clear()
                if e is SerialException:
                    try:
                        self._serial.close()
                    except Exception as ec:
                        self._log_add(f'Serial close error: {ec}', ec)
                        pass
                sleep(1)

    def _parse_byte(self, b):
        if b == 13 or b == 0:
            return
        elif b == 10:
            line = str(self._recv_buf, 'ascii')
            self._recv_buf.clear()
            if line.startswith('<'):
                wco_pos = line.find('|WCO:')
                ov_pos = line.find('|Ov:')
                if wco_pos > 0:
                    self._grbl_wco_line = line[wco_pos:-1]
                    self._grbl_state_line = line[1:wco_pos]
                elif ov_pos > 0:
                    self._grbl_ov_line = line[ov_pos:-1]
                    self._grbl_state_line = line[1:ov_pos]
                else:
                    self._grbl_state_line = line[1:-1]

                new_state = re.match(r'<(.+?)\|', line).group(1)
                if new_state == self._grbl_prev_state:
                    self._grbl_state = new_state  # do not log single state switches run-idle-run
                if new_state != self._grbl_prev_state and new_state == self._grbl_state:
                    self._log_add(new_state)
                self._grbl_prev_state = self._grbl_state
                self._grbl_state = new_state
                self._state_missing_counter = 0
            else:
                self._log_add(line)
                if line.startswith('error'):
                    self._grbl_homing = False
                    self._await_response = False
                    self._hold = True
                elif line.startswith('ok'):
                    self._grbl_homing = False
                    self._await_response = False

        else:
            self._recv_buf.append(b)

    def _read_loop(self):
        if self._state_missing_counter < 10:
            if not self._grbl_homing:
                self._state_missing_counter += 1
        else:
            self._grbl_state = 'NoInfo'
            self._grbl_state_line = ''
            self._grbl_ov_line = ''
            self._grbl_wco_line = ''
        while True:
            try:
                data = self._serial.read(4096)
                for b in data:
                    self._parse_byte(b)
                break
            except OSError as e:
                if e.errno == 11:
                    continue  # device temporary unavailable
                else:
                    raise e

    def _run_loop(self):
        idle = self._grbl_state == 'Idle'
        running = self._grbl_state == 'Run' \
            or self._grbl_state == 'Home' \
            or self._grbl_state == 'Hold:1'
        holding = self._grbl_state == 'Hold:0'

        if self._hold and (idle or running):  # ensure hold before soft-reset if both flags set
            self._exec('!')
        elif self._force_soft_reset:
            self._exec('reset')
            self._force_soft_reset = False
            self._hold = False
        elif holding and not self._hold:
            self._exec('~')
        elif idle and not self._await_response and self._commands:
            self._exec(self._commands.popleft())
        elif not idle and not running and not holding:
            if self._commands:
                cmd = self._commands[0]
                if cmd.startswith('$') or cmd in ['reset', 'hwreset', 'reboot', 'shutdown']:
                    self._exec(self._commands.popleft())
                else:
                    raise GrblStateException(f'Invalid grbl state: {self._grbl_state} for {cmd}')
