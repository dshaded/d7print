import logging
import os
import re
import time
from collections import deque
from threading import Lock, Thread
from time import sleep
from typing import List, Optional

from d7print.display import Display
from d7print.grbl import Grbl
from d7print.preprocessor import Preprocessor


class HwManager:
    """Main printer logic class.
    Manages Display, Grbl and Preprocessor instances.
    Handles GRBL hw reset GPIO.
    Maintains command queue and log.
    Runs a dedicated command execution thread."""

    def __init__(self, logger: logging.Logger, pack_dir: str):
        self._logger = logger
        self._pack_dir = pack_dir

        # Hard-coded configuration and subsystem initialization:
        self._comm_period = 0.05
        self._guard_file = '/var/run/d7print.guard'
        self._gpio_reset_path = '/sys/class/gpio/gpio7/value'
        open('/sys/class/gpio/export', 'w').write('7')
        open('/sys/class/gpio/gpio7/direction', 'w').write('high')
        self._display = Display(pack_dir, '/dev/fb0')
        self._grbl = Grbl('/dev/ttyS3', 115200, self._comm_period * 5)
        self._preprocessor = Preprocessor()

        # Runtime state
        self._commands: deque[str] = deque()
        self._immediate_command: str = ''
        self._await_response: bool = False
        self._holding: bool = False
        self._delay_end: float = 0.0

        # Misc
        self._run_log = deque(maxlen=100)
        self._log_lock = Lock()
        self._run_thread_obj: Optional[Thread] = None

        # Start the command execution thread
        self._ensure_running()

    def _reset_state(self):
        """Clear the runtime state. Normally called when GRBL is reset."""
        self._immediate_command: str = ''
        self._await_response: bool = False
        self._holding: bool = False
        self._delay_end: float = 0.0
        self._commands.clear()

    def set_image_pack(self, image_pack_file_name: str):
        """Selects an image pack archive. Empty line to clear."""
        self._preprocessor.set_image_pack(f'{self._pack_dir}/{image_pack_file_name}')
        self._display.set_image_pack(image_pack_file_name)

    def get_image_pack(self) -> str:
        """Gets currently selected image pack archive. Empty line if none."""
        return self._display.get_image_pack()

    def get_preprocessor_cfg(self):
        """Get a list of configured preprocessor directives (rules, layers, supports, etc.)"""
        return self._preprocessor.get_cfg()

    def get_preprocessor_cfg_version(self):
        """Get a version number of the preprocessor config."""
        return self._preprocessor.get_cfg_version()

    def add_commands(self, commands: List[str]):
        """Preprocess the commands and add the results to the execution queue."""
        self._ensure_running()
        try:
            processed = []
            for cmd in commands:
                processed.extend(self._preprocessor.preprocess_line(cmd))
            self._commands.extend(processed)
        except Exception as e:
            self._log_add(f'Failed to add commands: {e}', e)
            raise e

    def preprocess(self, commands: List[str]):
        """Preprocess the commands but do not add the results to the queue.
        Effectively only evaluate the preprocessor directives like @rule or @slice."""
        try:
            for cmd in commands:
                self._preprocessor.preprocess_line(cmd)
        except Exception as e:
            self._log_add(f'Failed to preprocess commands: {e}', e)
            raise e

    def get_commands(self) -> List[str]:
        """Get command queue contents."""
        return list(self._commands)

    def clear_commands(self, soft_reset=False):
        """Clear the commands queue. Also send "^X" if soft_reset is True."""
        self._commands.clear()
        if soft_reset and self._immediate_command != 'hwreset':
            self._ensure_running()
            self._immediate_command = 'reset'

    def hard_stop(self):
        """Immediately issue an HW reset to GRBL MCU."""
        self._reset_pin(0)
        self._ensure_running()
        self._immediate_command = 'hwreset'  # turn it off in the command loop

    def hold(self):
        """ASAP issue GRBL "!" hold command."""
        self._ensure_running()
        self._immediate_command = self._immediate_command or '!'

    def resume(self):
        """ASAP issue GRBL "~" resume command."""
        self._ensure_running()
        self._immediate_command = self._immediate_command or '~'

    def get_log(self):
        """Get current command log contents."""
        with self._log_lock:
            return list(self._run_log)

    def get_grbl_state_line(self):
        """Get the latest received GRBL status line. "(EXPIRED)" is appended if it is unavailable for a while."""
        suffix = '' if self._grbl.get_state() else ' (EXPIRED)'
        return self._grbl.get_status_line() + suffix

    # PRIVATE Section

    def _ensure_running(self):
        if not self._run_thread_obj or not self._run_thread_obj.is_alive():
            self._run_thread_obj = Thread(target=self._run_thread, name='hw_run', daemon=True)
            self._run_thread_obj.start()

    def _log_add(self, msg: str, e: Exception = None):
        """Saves the message to the logger and to the internal log queue."""
        if e:
            self._logger.error(msg, exc_info=e)
        else:
            self._logger.info(msg)

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
        """Executes the given command:
        Strips away comments.
        Dispatches the command to GRBL/Display/GPIO/System.
        Handles software delay command.

        If immediate is True the command is sent even when the system is in awaiting-response/hold/delay state.

        Returns True if the command was executed, False if the system is waiting and can not execute this command
        right now."""

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
                return False  # Most likely in Alarm state and waiting for $H or $X.
            if state.startswith('Halt') and cmd != '~':
                return False  # Only immediate resume command allowed
            self._grbl.send(cmd)
            self._holding = cmd.rfind('!') > cmd.rfind('~')  # check that there was no "resume" after "hold"
            if cmd not in ('?', '!', '~'):
                self._grbl.send('\n')  # send a new line if it's not an immediate single-char command
                self._await_response = True
        return True

    # THREADING Section

    def _run_thread(self):
        with open(self._guard_file, 'w') as f:
            f.write('d7print guard file')
        guard_time = os.path.getmtime(self._guard_file)
        # stop running only if a new instance is started (demon thread will be killed with the application)
        while os.path.getmtime(self._guard_file) == guard_time:
            try:
                sleep(self._comm_period)  # a small delay executed while we are waiting for GRBL or a soft delay
                self._run_loop()
            except Exception as e:  # log error, hold on for a second and try to start again
                self._log_add(f'Execution error: {e}', e)
                self._commands.clear()
                sleep(1)
        self._log_add('Hw manager thread stopped by guard file')
        self._grbl.close()

    def _is_waiting(self):
        return self._await_response or self._holding or self._delay_end > time.time()

    def _run_loop(self):
        # First - read GRBL's output.
        for line in self._grbl.receive():
            self._log_add(line)  # Log anything it sends us (except status lines, they are intercepted by Grbl.py)
            if line.startswith(('ok', 'error')):
                self._await_response = False  # Got our response
            if line.startswith('error'):  # It's an error. Enter a hold state just in case.
                self._exec('!', True)

        # Second - send an immediate command if present
        if self._immediate_command:
            self._exec(self._immediate_command, True)
            self._immediate_command = ''

        # Third - send commands until we hit a delay or something that requires a response from GRBL
        while self._commands and self._exec(cmd := self._commands[0]):
            self._log_add(f'> {cmd}')
            if self._commands:
                self._commands.popleft()
