import os
import re
import time
from collections import deque
from threading import Lock, Thread
from time import sleep
from typing import List, Optional

from flask import Flask

from d7print.display import Display
from d7print.grbl import Grbl


class GrblStateException(Exception):
    pass


class HwManager:

    def __init__(self, app: Flask, pack_dir: str):
        self._comm_period = 0.05
        self._guard_file = '/var/run/d7print.guard'
        self._gpio_reset_path = '/sys/class/gpio/gpio7/value'
        open('/sys/class/gpio/export', 'w').write('7')
        open('/sys/class/gpio/gpio7/direction', 'w').write('high')

        self._app = app
        self._display = Display(pack_dir, '/dev/fb0')
        self._grbl = Grbl('/dev/ttyS3', 115200, self._comm_period * 5)

        self._commands: deque[str] = deque()
        self._immediate_command: str = ''
        self._await_response: bool = False
        self._holding: bool = False
        self._delay_end: float = 0.0

        self._run_log = deque(maxlen=100)
        self._log_lock = Lock()
        self._run_thread_obj: Optional[Thread] = None

        self._ensure_running()

    def _reset_state(self):
        self._immediate_command: str = ''
        self._await_response: bool = False
        self._holding: bool = False
        self._delay_end: float = 0.0
        self._commands.clear()

    def set_image_pack(self, image_pack_path: str):
        self._display.set_image_pack(image_pack_path)

    def get_image_pack(self) -> str:
        return self._display.get_image_pack()

    def add_commands(self, commands: List[str]):
        self._ensure_running()
        self._commands.extend(commands)

    def get_commands(self) -> List[str]:
        return list(self._commands)

    def clear_commands(self, soft_reset=False):
        self._commands.clear()
        if soft_reset and self._immediate_command != 'hwreset':
            self._ensure_running()
            self._immediate_command = 'reset'

    def hard_stop(self):
        self._reset_pin(0)
        self._ensure_running()
        self._immediate_command = 'hwreset'

    def hold(self):
        self._ensure_running()
        self._immediate_command = self._immediate_command or '!'

    def resume(self):
        self._ensure_running()
        self._immediate_command = self._immediate_command or '~'

    def get_log(self):
        with self._log_lock:
            return list(self._run_log)

    def get_grbl_state_line(self):
        suffix = '' if self._grbl.get_state() else ' (EXPIRED)'
        return self._grbl.get_status_line() + suffix

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

    def _reset_pin(self, state):
        open(self._gpio_reset_path, 'w').write('1' if state else '0')

    def _exec(self, raw_cmd: str, immediate=False) -> bool:
        cmd = raw_cmd.partition(';')[0].strip()
        lcmd = cmd.lower()

        if lcmd.startswith('preload'):
            self._display.preload(cmd[7:].strip())
        elif self._is_waiting() and not immediate:
            return False
        elif lcmd == 'reset' or '\x18' in cmd:
            self._grbl.send('\x18')
            self._reset_state()
        elif lcmd == 'hwreset':
            self._reset_pin(0)
            sleep(0.1)
            self._reset_pin(1)
            self._reset_state()
        elif lcmd == 'reboot':
            self._reset_pin(0)
            os.system('systemctl reboot')
            self._reset_state()
        elif lcmd in ('shutdown', 'poweroff'):
            self._reset_pin(0)
            os.system('systemctl poweroff')
            self._reset_state()
        elif lcmd.startswith('blank'):
            self._display.blank()
        elif lcmd.startswith('slice'):
            self._display.show(cmd[5:].strip())
        elif lcmd.startswith('delay'):
            millis = re.findall(r'[0-9]+', lcmd)
            self._delay_end = time.time() + int(millis[0] if millis else 0) / 1000
        elif lcmd:
            state = self._grbl.get_state()
            if state.startswith(('Alarm', 'Door', 'Sleep')) and not lcmd.startswith(('$', '#')):
                return False
            if state.startswith('Halt') and cmd != '~':
                return False
            self._grbl.send(cmd)
            self._holding = cmd.rfind('!') > cmd.rfind('~')
            if cmd not in ('?', '!', '~'):
                self._grbl.send('\n')
                self._await_response = True
        return True

    # THREADING Section

    def _run_thread(self):
        with open(self._guard_file, 'w') as f:
            f.write('d7print guard file')
        guard_time = os.path.getmtime(self._guard_file)
        while os.path.getmtime(self._guard_file) == guard_time:
            try:
                sleep(self._comm_period)
                self._run_loop()
            except Exception as e:
                self._log_add(f'Execution error: {e}', e)
                self._commands.clear()
                sleep(1)
        self._log_add('Hw manager thread stopped by guard file')
        self._grbl.close()

    def _is_waiting(self):
        return self._await_response or self._holding or self._delay_end > time.time()

    def _run_loop(self):
        for line in self._grbl.receive():
            self._log_add(line)
            if line.startswith(('ok', 'error')):
                self._await_response = False
            if line.startswith('error'):
                self._exec('!', True)

        if self._immediate_command:
            self._exec(self._immediate_command, True)
            self._immediate_command = ''

        while self._commands and self._exec(cmd := self._commands[0]):
            self._log_add(f'> {cmd}')
            if self._commands:
                self._commands.popleft()
