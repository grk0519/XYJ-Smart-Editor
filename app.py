import os
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
import yaml
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from flask import Flask, flash, redirect, render_template, request, send_from_directory, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')
ALLOWED_EXTENSIONS = {'.xml', '.yaml', '.yml', '.json'}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
TAB_LEASE_SECONDS = 90

secret_key = os.environ.get('FLASK_SECRET_KEY')
if not secret_key and os.environ.get('FLASK_DEBUG') != '1':
    raise RuntimeError('Set FLASK_SECRET_KEY before starting the application.')

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secret_key or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    UPLOAD_FOLDER=str(BASE_DIR / 'uploads'),
    DOWNLOAD_FOLDER=str(BASE_DIR / 'downloads'),
    SESSION_DB=str(BASE_DIR / 'uploads' / '.session-tabs.sqlite3'),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('FLASK_HTTPS', '').lower() == 'true',
)
CSRFProtect(app)
Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
Path(app.config['DOWNLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def current_upload_path():
    upload_id = session.get('upload_id')
    if not upload_id or not session.get('file_type'):
        return None
    return Path(app.config['UPLOAD_FOLDER']) / upload_id


def clear_session_artifacts():
    upload_id = session.get('upload_id')
    if isinstance(upload_id, str) and re.fullmatch(r'[0-9a-f]{32}\.(xml|yaml|yml|json)', upload_id):
        upload_path = Path(app.config['UPLOAD_FOLDER']) / upload_id
        generated_path = Path(app.config['DOWNLOAD_FOLDER']) / f'modified_{upload_id}'
        upload_path.unlink(missing_ok=True)
        generated_path.unlink(missing_ok=True)
        with active_tabs_connection() as connection:
            connection.execute('DELETE FROM active_tabs WHERE upload_id = ?', (upload_id,))
    session.clear()


@contextmanager
def active_tabs_connection():
    connection = sqlite3.connect(app.config['SESSION_DB'], timeout=5)
    try:
        connection.execute(
            'CREATE TABLE IF NOT EXISTS active_tabs ('
            'upload_id TEXT NOT NULL, tab_id TEXT NOT NULL, last_seen REAL NOT NULL, '
            'PRIMARY KEY (upload_id, tab_id))'
        )
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def valid_tab_id(tab_id):
    try:
        return str(uuid.UUID(tab_id)) == tab_id
    except (ValueError, TypeError, AttributeError):
        return False


def xml_records(root):
    records = []

    def visit(element, depth):
        index = len(records)
        children = list(element)
        text = element.text or ''
        records.append({
            'index': index,
            'tag': element.tag,
            'depth': depth,
            'text': text,
            'editable_text': not children or bool(text.strip()),
            'attrib': dict(element.attrib),
        })

        for child in children:
            visit(child, depth + 1)

    visit(root, 0)
    return records


def render_editor():
    filepath = current_upload_path()
    if filepath is None or not filepath.is_file():
        session.clear()
        flash('Your upload is no longer available. Please upload the file again.', 'error')
        return redirect(url_for('home'))

    if session['file_type'] == 'xml':
        try:
            tree = ET.parse(str(filepath))
        except DefusedXmlException:
            flash('This XML file contains unsafe XML constructs and cannot be edited.', 'error')
            return redirect(url_for('home'))
        except ET.ParseError:
            flash('The uploaded XML file could not be parsed.', 'error')
            return redirect(url_for('home'))
        original_content = ET.tostring(tree.getroot(), encoding='unicode')
        return render_template('edit.html', form_data=xml_records(tree.getroot()), original_content=original_content)

    try:
        source = filepath.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        flash('The uploaded file could not be read as UTF-8.', 'error')
        return redirect(url_for('home'))
    if session['file_type'] == 'json':
        return render_template('edit_json.html', json_source=source)
    return render_template('edit_yaml.html', yaml_source=source)


@app.errorhandler(413)
def request_entity_too_large(_error):
    flash('The upload exceeds the 2 MB limit.', 'error')
    return redirect(url_for('home'))


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/clear-session', methods=['POST'])
def clear_session():
    clear_session_artifacts()
    flash('Your session files have been deleted.', 'success')
    return redirect(url_for('home'))


@app.route('/session-activity', methods=['POST'])
def session_activity():
    upload_id = session.get('upload_id')
    tab_id = request.form.get('tab_id')
    if not upload_id or not valid_tab_id(tab_id):
        return '', 204

    now = time.time()
    with active_tabs_connection() as connection:
        connection.execute(
            'DELETE FROM active_tabs WHERE upload_id = ? AND last_seen < ?',
            (upload_id, now - TAB_LEASE_SECONDS),
        )
        connection.execute(
            'INSERT INTO active_tabs (upload_id, tab_id, last_seen) VALUES (?, ?, ?) '
            'ON CONFLICT(upload_id, tab_id) DO UPDATE SET last_seen = excluded.last_seen',
            (upload_id, tab_id, now),
        )
    return '', 204


@app.route('/session-close', methods=['POST'])
def session_close():
    upload_id = session.get('upload_id')
    tab_id = request.form.get('tab_id')
    if not upload_id or not valid_tab_id(tab_id):
        return '', 204

    now = time.time()
    with active_tabs_connection() as connection:
        connection.execute(
            'DELETE FROM active_tabs WHERE upload_id = ? AND last_seen < ?',
            (upload_id, now - TAB_LEASE_SECONDS),
        )
        connection.execute(
            'DELETE FROM active_tabs WHERE upload_id = ? AND tab_id = ?',
            (upload_id, tab_id),
        )
        remaining_tabs = connection.execute(
            'SELECT COUNT(*) FROM active_tabs WHERE upload_id = ?', (upload_id,)
        ).fetchone()[0]
    if remaining_tabs == 0:
        clear_session_artifacts()
    return '', 204


@app.route('/edit', methods=['POST'])
def edit():
    uploaded_file = request.files.get('xmlfile')
    if uploaded_file is None:
        flash('Choose an XML, YAML, or JSON file to upload.', 'error')
        return redirect(url_for('home'))
    if not uploaded_file.filename:
        uploaded_file.close()
        flash('Choose an XML, YAML, or JSON file to upload.', 'error')
        return redirect(url_for('home'))

    original_filename = secure_filename(uploaded_file.filename)
    extension = Path(original_filename).suffix.lower()
    if not original_filename or extension not in ALLOWED_EXTENSIONS:
        uploaded_file.close()
        flash('Only .xml, .yaml, .yml, and .json files are supported.', 'error')
        return redirect(url_for('home'))

    uploaded_file.stream.seek(0, os.SEEK_END)
    upload_size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)
    if upload_size > MAX_UPLOAD_BYTES:
        uploaded_file.close()
        flash('The uploaded file exceeds the 2 MB limit.', 'error')
        return redirect(url_for('home'))

    clear_session_artifacts()
    upload_id = f'{uuid.uuid4().hex}{extension}'
    filepath = Path(app.config['UPLOAD_FOLDER']) / upload_id
    uploaded_file.save(filepath)
    uploaded_file.close()

    try:
        if extension == '.xml':
            ET.parse(str(filepath))
            file_type = 'xml'
        elif extension == '.json':
            json.loads(filepath.read_text(encoding='utf-8-sig'))
            file_type = 'json'
        else:
            yaml_source = filepath.read_text(encoding='utf-8-sig')
            yaml.safe_load(yaml_source)
            file_type = 'yaml'
    except ET.ParseError:
        filepath.unlink(missing_ok=True)
        flash('The uploaded XML file is not well-formed.', 'error')
        return redirect(url_for('home'))
    except yaml.YAMLError:
        filepath.unlink(missing_ok=True)
        flash('The uploaded YAML file is invalid.', 'error')
        return redirect(url_for('home'))
    except json.JSONDecodeError:
        filepath.unlink(missing_ok=True)
        flash('The uploaded JSON file is invalid.', 'error')
        return redirect(url_for('home'))
    except UnicodeError:
        filepath.unlink(missing_ok=True)
        flash('The uploaded YAML file must use UTF-8 encoding.', 'error')
        return redirect(url_for('home'))
    except DefusedXmlException:
        filepath.unlink(missing_ok=True)
        flash('This XML file contains unsafe XML constructs and cannot be edited.', 'error')
        return redirect(url_for('home'))

    session['upload_id'] = upload_id
    session['original_filename'] = original_filename
    session['file_type'] = file_type
    return render_editor()


@app.route('/preview', methods=['POST'])
def preview():
    filepath = current_upload_path()
    if filepath is None or not filepath.is_file():
        flash('Upload a file before editing.', 'error')
        return redirect(url_for('home'))

    if session['file_type'] == 'xml':
        try:
            tree = ET.parse(str(filepath))
        except Exception:
            flash('The uploaded XML file could not be parsed.', 'error')
            return redirect(url_for('home'))

        edited = []
        for index, element in enumerate(tree.getroot().iter()):
            attributes = {}
            for attribute_index, (key, value) in enumerate(element.attrib.items()):
                attributes[key] = request.form.get(f'attr_{index}_{attribute_index}', value)
            edited.append({
                'index': index,
                'tag': element.tag,
                'text': request.form.get(f'text_{index}', element.text or ''),
                'attrib': attributes,
            })
        return render_template('preview.html', edited=edited)

    if session['file_type'] == 'json':
        json_source = request.form.get('json_source')
        if json_source is None:
            flash('Enter JSON content before previewing.', 'error')
            return render_editor()
        try:
            json_data = json.loads(json_source)
            formatted_json = json.dumps(json_data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as error:
            return render_template('edit_json.html', json_source=json_source, error=f'Invalid JSON: {error}')
        return render_template('preview_json.html', json_source=formatted_json)

    yaml_source = request.form.get('yaml_source')
    if yaml_source is None:
        flash('Enter YAML content before previewing.', 'error')
        return render_editor()
    try:
        yaml_data = yaml.safe_load(yaml_source)
        normalized_yaml = yaml.safe_dump(yaml_data, sort_keys=False, allow_unicode=True)
    except yaml.YAMLError as error:
        flash(f'Invalid YAML: {error}', 'error')
        return render_editor()
    return render_template('preview_yaml.html', yaml_source=normalized_yaml)


@app.route('/result', methods=['POST'])
def result():
    if session.get('file_type') == 'yaml':
        return redirect(url_for('result_yaml'))
    if session.get('file_type') == 'json':
        return redirect(url_for('result_json'))

    filepath = current_upload_path()
    if session.get('file_type') != 'xml' or filepath is None or not filepath.is_file():
        flash('Upload an XML file before generating a result.', 'error')
        return redirect(url_for('home'))

    try:
        tree = ET.parse(str(filepath))
    except Exception:
        flash('The uploaded XML file could not be parsed.', 'error')
        return redirect(url_for('home'))

    for index, element in enumerate(tree.getroot().iter()):
        element.text = request.form.get(f'text_{index}', element.text or '')
        for attribute_index, key in enumerate(list(element.attrib)):
            element.set(key, request.form.get(f'attr_{index}_{attribute_index}', element.attrib[key]))

    output_filename = f"modified_{session['upload_id']}"
    output_path = Path(app.config['DOWNLOAD_FOLDER']) / output_filename
    tree.write(str(output_path), encoding='utf-8', xml_declaration=True)
    session['download_file'] = output_filename
    session['download_name'] = f"modified_{session['original_filename']}"
    return render_template('result.html')


@app.route('/result-yaml', methods=['POST'])
def result_yaml():
    if session.get('file_type') != 'yaml' or current_upload_path() is None:
        flash('Upload a YAML file before generating a result.', 'error')
        return redirect(url_for('home'))

    yaml_source = request.form.get('yaml_source')
    if yaml_source is None:
        flash('The YAML preview has expired. Please edit the file again.', 'error')
        return redirect(url_for('edit_again'))
    try:
        yaml_data = yaml.safe_load(yaml_source)
        output = yaml.safe_dump(yaml_data, sort_keys=False, allow_unicode=True)
    except yaml.YAMLError:
        flash('The YAML preview is invalid. Please edit the file again.', 'error')
        return redirect(url_for('edit_again'))

    output_filename = f"modified_{session['upload_id']}"
    output_path = Path(app.config['DOWNLOAD_FOLDER']) / output_filename
    output_path.write_text(output, encoding='utf-8')
    session['download_file'] = output_filename
    session['download_name'] = f"modified_{session['original_filename']}"
    return render_template('result_yaml.html')


@app.route('/result-json', methods=['POST'])
def result_json():
    if session.get('file_type') != 'json' or current_upload_path() is None:
        flash('Upload a JSON file before generating a result.', 'error')
        return redirect(url_for('home'))

    json_source = request.form.get('json_source')
    if json_source is None:
        flash('The JSON preview has expired. Please edit the file again.', 'error')
        return redirect(url_for('edit_again'))
    try:
        json_data = json.loads(json_source)
        output = json.dumps(json_data, indent=2, ensure_ascii=False) + '\n'
    except json.JSONDecodeError as error:
        return render_template('edit_json.html', json_source=json_source, error=f'Invalid JSON: {error}')

    output_filename = f"modified_{session['upload_id']}"
    output_path = Path(app.config['DOWNLOAD_FOLDER']) / output_filename
    output_path.write_text(output, encoding='utf-8')
    session['download_file'] = output_filename
    session['download_name'] = f"modified_{session['original_filename']}"
    return render_template('result_json.html')


@app.route('/download')
def download():
    filename = session.get('download_file')
    if filename:
        return send_from_directory(
            app.config['DOWNLOAD_FOLDER'],
            filename,
            as_attachment=True,
            download_name=session.get('download_name', filename),
        )
    return redirect(url_for('home'))


@app.route('/edit-again', methods=['GET', 'POST'])
def edit_again():
    if request.method == 'POST' and session.get('file_type') == 'xml':
        filepath = current_upload_path()
        if filepath is None or not filepath.is_file():
            return redirect(url_for('home'))
        try:
            tree = ET.parse(str(filepath))
        except (DefusedXmlException, ET.ParseError):
            return redirect(url_for('home'))
        form_data = xml_records(tree.getroot())
        for item in form_data:
            index = item['index']
            item['text'] = request.form.get(f'text_{index}', item['text'])
            item['attrib'] = {
                key: request.form.get(f'attr_{index}_{attribute_index}', value)
                for attribute_index, (key, value) in enumerate(item['attrib'].items())
            }
        original_content = ET.tostring(tree.getroot(), encoding='unicode')
        return render_template('edit.html', form_data=form_data, original_content=original_content)
    if request.method == 'POST' and session.get('file_type') == 'yaml':
        return render_template('edit_yaml.html', yaml_source=request.form.get('yaml_source', ''))
    if request.method == 'POST' and session.get('file_type') == 'json':
        return render_template('edit_json.html', json_source=request.form.get('json_source', ''))
    return render_editor()


@app.route('/contact')
def contact():
    return render_template('contact.html')


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1')
