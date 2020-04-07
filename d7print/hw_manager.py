import re
import time
from collections import deque
from re import RegexFlag
from threading import Lock, Thread
from time import sleep
from typing import List
from zipfile import ZipFile

from PIL import Image
from flask import Flask
from serial import Serial, SerialException


class GrblStateException(Exception):
    pass


class HwManager:

    def __init__(self, app: Flask):
        self._app = app

        self._run_thread_obj = Thread(target=self._run_thread, name='hw_run', daemon=True)
        self._read_thread_obj = Thread(target=self._read_thread, name='hw_read', daemon=True)

        self._serial = Serial()
        self._serial.port = '/dev/ttyS3'
        self._serial.baudrate = 115200
        self._serial.timeout = 1

        self._hold = False
        self._force_soft_reset = False
        self._commands: deque[str] = deque()
        self._run_log = deque(maxlen=1000)
        self._log_lock = Lock()
        self._grbl_state_line: str = ''
        self._grbl_state: str = ''
        self._image_pack_path: str = ''
        with open('/sys/class/gpio/export', 'w') as exp:
            exp.write('7')
        with open('/sys/class/gpio/gpio7/direction', 'w') as direction:
            direction.write('high')

        self._run_thread_obj.run()
        self._read_thread_obj.run()

    def set_image_pack(self, image_pack_path: str):
        self._image_pack_path = image_pack_path

    def add_commands(self, commands: List[str]):
        self._commands.extend(commands)

    def get_commands(self) -> List[str]:
        return list(self._commands)

    def clear_commands(self, soft_reset=False):
        self._commands.clear()
        if soft_reset:
            self._force_soft_reset = True

    def hold(self):
        self._hold = True

    def resume(self):
        self._hold = False

    def is_on_hold(self):
        return self._hold

    def hard_stop(self):
        self._reset_pin(0)
        self._commands.clear()
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
        return self._grbl_state_line

    # PRIVATE Section

    def _log_add(self, msg: str, e: Exception = None):
        if e:
            self._app.logger.error(msg, exc_info=e)
        else:
            self._app.logger.info(msg)

        with self._log_lock:
            last_id = self._run_log[len(self._run_log) - 1] if self._run_log else 0
            self._run_log.append({
                'id': last_id + 1,
                'time': time.time(),
                'msg': msg,
            })

    # noinspection PyMethodMayBeStatic
    def _show_zip_image(self, image_pack_path: str, image_name: str):
        with open(image_pack_path) as pf, ZipFile(pf) as zf, zf.open(image_name) as zi, \
                Image.open(zi) as i, open('/dev/fb0', 'wb') as fb:
            fb.write(i.tobytes())

    # noinspection PyMethodMayBeStatic
    def _reset_pin(self, state):
        with open('/sys/class/gpio/gpio7/value', 'w') as val:
            val.write('1' if state else '0')

    def _serial_write(self, cmd: str):
        if not self._serial.is_open:
            self._serial.open()
        self._serial.write(bytes(cmd, 'ascii'))

    def _exec(self, cmd: str):
        cmd = cmd.partition(';')[0].strip()
        lcmd = cmd.lower()
        if not lcmd:
            return
        if lcmd == '?':
            self._serial_write('?')
            return

        self._log_add(f'>>> {cmd}')

        if lcmd == 'reset':
            self._serial_write('\x18')
        elif lcmd == 'hwreset':
            self._reset_pin(0)
            sleep(0.1)
            self._reset_pin(1)
        elif lcmd == 'hold':
            self._serial_write('!')
        elif lcmd == 'resume':
            self._serial_write('~')
        elif lcmd.startswith('slice'):
            img_name = re.findall(r'[0-9a-z.png]', cmd, RegexFlag.IGNORECASE)
            self._show_zip_image(self._image_pack_path, img_name[0] if img_name else 'blank')
        else:
            self._serial_write(cmd)
            self._serial_write('\n')

    # THREADING Section

    def _read_thread(self):
        while True:
            try:
                line = str(self._serial.readline(), 'ascii')
                if line.startswith('<'):
                    self._grbl_state_line = line
                    self._grbl_state = re.match(r'<(.+)\|', line).group(1)
                else:
                    self._log_add(line)
                    if line.startswith('error'):
                        self._hold = True
            except Exception as e:
                self._log_add(f'Read error: {e}', e)
                sleep(1)
        pass

    def _run_thread(self):
        while True:
            try:
                sleep(0.1)
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

    def _run_loop(self):
        idle = self._grbl_state == 'Idle'
        running = self._grbl_state == 'Run' \
            or self._grbl_state == 'Home' \
            or self._grbl_state == 'Hold:1'
        holding = self._grbl_state == 'Hold:0'

        if self._hold and (idle or running):  # ensure hold before soft-reset if both flags set
            self._exec('hold')
        elif self._force_soft_reset:
            self._exec('reset')
            self._force_soft_reset = False
        elif holding and not self._hold:
            self._exec('resume')
        elif idle and self._commands:
            self._exec(self._commands.popleft())
        elif not idle and not running and not holding:
            if self._commands:
                if self._commands[0].startswith('$'):
                    self._exec(self._commands.popleft())
                else:
                    raise GrblStateException(f'Invalid grbl state: {self._grbl_state}')

        self._exec('?')
