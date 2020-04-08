from flask import Flask, render_template
from flask import request
from werkzeug.utils import secure_filename, redirect

from d7print.hw_manager import HwManager


def create_app():
    app = Flask(__name__)

    hw_man = HwManager(app)

    @app.route('/')
    def home():
        return render_template('home.htm')

    @app.route('/upload', methods=['POST'])
    def upload_file():
        f = request.files['upload']
        f.save('/root/uploads/' + secure_filename(f.filename))
        return redirect('home')

    @app.route('/log', methods=['GET'])
    def log():
        # hw_manager.clear_commands(True)
        # hw_manager.get_commands()
        # hw_manager.hard_stop()
        # hw_manager.hold()
        # hw_manager.is_on_hold()
        # hw_manager.resume()
        # hw_manager.set_image_pack()
        return {'log': hw_man.get_log(request.args.get('id', default=0, type=int))}

    @app.route('/exec', methods=['GET'])
    def execute():
        cmd = request.args.get('cmd', default='')
        hw_man.add_commands([cmd])
        return cmd

    @app.route('/resume', methods=['GET'])
    def resume():
        hw_man.resume()
        return 'OK'

    @app.route('/grbl_state', methods=['GET'])
    def grbl_state():
        return {'state': hw_man.get_grbl_state_lines()}

    return app
