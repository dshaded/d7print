import os
from logging.config import dictConfig
from zipfile import ZipFile

from flask import Flask, render_template, url_for, flash
from flask import request
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename, redirect

from d7print.hw_manager import HwManager


def create_app():
    dictConfig({
        'version': 1,
        'formatters': {'default': {
            'format': '[%(asctime)s] %(levelname)s in %(name)s: %(message)s',
        }},
        'handlers': {'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'default',
            'filename': '/var/log/d7print.log',
            'maxBytes': 1048576,
            'backupCount': 5
        }},
        'root': {
            'level': 'INFO',
            'handlers': ['file']
        },
        'loggers': {
            'werkzeug': {'level': 'WARNING'},
        }
    })

    app = Flask(__name__)
    app.secret_key = 'd7_print_secret_key'
    uploads_dir = '/root/uploads/'
    os.makedirs(uploads_dir, 0o664, exist_ok=True)

    hw_man = HwManager(app.logger, uploads_dir)

    @app.route('/')
    def home():
        active_file = hw_man.get_image_pack()
        select = request.args.get('select', '') or active_file
        files = sorted(os.listdir(uploads_dir))
        return render_template('home.htm', select=select, files=files, active_file=active_file)

    @app.route('/upload', methods=['POST'])
    def upload():
        f: FileStorage = request.files['upload']
        sec_name = secure_filename(f.filename)
        if not sec_name:
            flash('Invalid file name', 'warning')
            return redirect(url_for('home'))
        f.save(uploads_dir + sec_name)
        return redirect(url_for('home', select=sec_name))

    # API SECTION

    def _rp(name: str) -> str:
        return request.form.get(name, default='') or request.args.get(name, default='')

    @app.route('/api/exec', methods=['GET', 'POST'])
    def execute():
        if hw_man.get_commands():
            return {'status': 'Printer busy'}
        try:
            hw_man.add_commands(_rp('cmd').split('\n'))
            return {'status': 'ok'}
        except Exception as e:
            return {'status': str(e)}

    @app.route('/api/load', methods=['GET', 'POST'])
    def load():
        if hw_man.get_commands():
            return {'status': 'Printer busy'}

        file: str = _rp('file')
        if not file:
            hw_man.set_image_pack('')
            return {'status': 'ok'}

        file = secure_filename(file)
        if not file:
            return {'status': 'Bad filename'}

        try:
            lines = []
            if file.lower().endswith('.gcode'):
                with open(uploads_dir + file) as gcode:
                    lines = gcode.readlines()
            else:
                with ZipFile(uploads_dir + file) as zf:
                    hw_man.set_image_pack(file)
                    if scripts := list(n for n in zf.namelist() if n.lower().endswith('.gcode')):
                        with zf.open(scripts[0]) as gcode:
                            lines = [str(line, 'utf8') for line in gcode.readlines()]
                        if lines and lines[0].strip().lower().startswith('mapfile'):
                            hw_man.preprocess(lines)
                            lines = None
            if lines:
                lines = [line.rstrip() for line in lines]
            return {'status': 'ok', 'gcode': lines}
        except Exception as e:
            return {'status': str(e)}

    @app.route('/api/delete', methods=['GET', 'POST'])
    def delete():
        file: str = secure_filename(_rp('file'))
        if file:
            try:
                if file == hw_man.get_image_pack():
                    if hw_man.get_commands():
                        return {'status': 'File is in use by printer'}
                    else:
                        hw_man.set_image_pack('')
                os.unlink(uploads_dir + file)
                return {'status': 'ok'}
            except FileNotFoundError:
                return {'status': 'Not found'}
        return {'status': 'Bad file name'}

    @app.route('/api/info', methods=['GET'])
    def info():
        time = request.args.get('time', default=0, type=int)
        send_cfg = request.args.get('cfg_version', default=0, type=int) != hw_man.get_preprocessor_cfg_version()
        return {
            'status': 'ok',
            'log': [l for l in hw_man.get_log() if l['time'] >= time],
            'queue': hw_man.get_commands(),
            'file': hw_man.get_image_pack(),
            'state': hw_man.get_grbl_state_line(),
            'cfg': hw_man.get_preprocessor_cfg() if send_cfg else None,
            'cfg_version': hw_man.get_preprocessor_cfg_version()
        }

    @app.route('/api/command', methods=['GET', 'POST'])
    def command():
        cmd = _rp('cmd')
        if cmd == 'hold':
            hw_man.hold()
        elif cmd == 'resume':
            hw_man.resume()
        elif cmd == 'clear':
            hw_man.clear_commands()
        elif cmd == 'stop':
            hw_man.clear_commands(True)
        elif cmd == 'hardstop':
            hw_man.hard_stop()
        else:
            return {'status': 'Unknown command: ' + cmd}

        return {'status': 'ok'}

    # UNUSED API SECTION

    @app.route('/api/ls', methods=['GET'])
    def ls():
        return {'status': 'ok', 'files': sorted(os.listdir(uploads_dir))}

    @app.route('/api/log', methods=['GET'])
    def log():
        return {'status': 'ok', 'log': hw_man.get_log()}

    @app.route('/api/grbl_state', methods=['GET'])
    def grbl_state():
        return {'status': 'ok', 'state': hw_man.get_grbl_state_line()}

    return app
