import os
from logging.config import dictConfig
from zipfile import ZipFile

from flask import Flask, render_template, url_for, flash
from flask import request
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename, redirect

from d7print.hw_manager import HwManager

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


def create_app():
    app = Flask(__name__)
    app.secret_key = 'd7_print_secret_key'

    hw_man = HwManager(app)

    uploads_dir = '/root/uploads/'
    os.makedirs(uploads_dir, 0o664, exist_ok=True)

    @app.route('/')
    def home():
        active_file = os.path.basename(hw_man.get_image_pack())
        select = active_file or request.args.get('select', '')
        files = sorted(os.listdir(uploads_dir))
        return render_template('home.htm', select=select, files=files, active_file=active_file)

    @app.route('/upload', methods=['POST'])
    def upload():
        f: FileStorage = request.files['upload']
        sec_name = secure_filename(f.filename)
        if f.content_type == 'application/zip':
            f.save(uploads_dir + sec_name)
            return redirect(url_for('home', select=sec_name))
        else:
            flash('Not a zip archive', 'warning')
            return redirect(url_for('home'))

    # API SECTION

    def _rp(name: str) -> str:
        return request.form.get(name, default='') or request.args.get(name, default='')

    @app.route('/api/exec', methods=['GET', 'POST'])
    def execute():
        if hw_man.get_commands():
            return {'status': 'Printer busy'}

        hw_man.add_commands(_rp('cmd').split('\n'))
        return {'status': 'ok'}

    @app.route('/api/load', methods=['GET', 'POST'])
    def load():
        if hw_man.get_commands():
            return {'status': 'Printer busy'}

        file: str = secure_filename(_rp('file'))
        if not file:
            return {'status': 'Bad filename'}

        try:
            path = uploads_dir + file
            rm_newline = str.maketrans('', '', '\r\n')
            with ZipFile(path) as zf, zf.open('run.gcode') as gcode:
                text = list(str(l, 'utf8').translate(rm_newline) for l in gcode.readlines())
                hw_man.set_image_pack(path)
                return {'status': 'ok', 'gcode': text}
        except Exception as e:
            return {'status': str(e)}

    @app.route('/api/delete', methods=['GET', 'POST'])
    def delete():
        file: str = secure_filename(_rp('file'))
        if file:
            try:
                path = uploads_dir + file
                if path == hw_man.get_image_pack():
                    return {'status': 'File is loaded'}
                os.unlink(path)
                return {'status': 'ok'}
            except FileNotFoundError:
                return {'status': 'Not found'}
        return {'status': 'Bad file name'}

    @app.route('/api/info', methods=['GET'])
    def info():
        idx = request.args.get('id', default=0, type=int)
        return {
            'status': 'ok',
            'log': hw_man.get_log(idx),
            'queue': hw_man.get_commands(),
            'state': hw_man.get_grbl_state_line()
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
        idx = request.args.get('id', default=0, type=int)
        return {'status': 'ok', 'log': hw_man.get_log(idx)}

    @app.route('/api/grbl_state', methods=['GET'])
    def grbl_state():
        return {'status': 'ok', 'state': hw_man.get_grbl_state_line()}

    return app
