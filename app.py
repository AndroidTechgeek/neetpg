import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime, timedelta
import os
import glob
import json
import re

app = Flask(__name__)
app.secret_key = 'v15_multiple_images_and_git'

@app.template_filter('format_solution')
def format_solution_filter(text):
    text = str(text)
    def create_html_table(match):
        table_content = match.group(1).strip()
        rows = table_content.split('\n')
        html = '<table class="solution-table">\n'
        try:
            headers = rows[0].split('|')
            html += '  <thead>\n    <tr>\n'
            for header in headers:
                html += f'      <th>{header.strip()}</th>\n'
            html += '    </tr>\n  </thead>\n'
            html += '  <tbody>\n'
            for row_text in rows[1:]:
                html += '    <tr>\n'
                cells = row_text.split('|')
                for i, cell in enumerate(cells):
                    if i < len(headers):
                        html += f'      <td>{cell.strip()}</td>\n'
                if len(cells) < len(headers):
                    html += f'      <td colspan="{len(headers) - len(cells)}"></td>\n'
                html += '    </tr>\n'
            html += '  </tbody>\n</table>'
            return html
        except IndexError:
            return "Error: Invalid table format"

    table_placeholders = {}
    placeholder_id = 0
    def replace_table_with_placeholder(match):
        nonlocal placeholder_id
        placeholder = f"__TABLE_PLACEHOLDER_{placeholder_id}__"
        table_html = create_html_table(match)
        table_placeholders[placeholder] = table_html
        placeholder_id += 1
        return placeholder

    processed_text = re.sub(r'\[TABLE\](.*?)\[/TABLE\]', replace_table_with_placeholder, text, flags=re.DOTALL | re.IGNORECASE)
    processed_text = processed_text.replace('\n', '<br>')
    for placeholder, table_html in table_placeholders.items():
        processed_text = processed_text.replace(placeholder, table_html)
    return processed_text

QUESTION_CACHE = {}

def robust_read_csv(filename):
    try: return pd.read_csv(filename, encoding='utf-8-sig', keep_default_na=False)
    except UnicodeDecodeError: return pd.read_csv(filename, encoding='cp1252', keep_default_na=False)

def load_questions(filename, force_reload=False):
    if filename in QUESTION_CACHE and not force_reload: return QUESTION_CACHE[filename]
    try:
        if not os.path.exists(filename): return f"ERROR_FILE_NOT_FOUND: '{filename}'"
        df = robust_read_csv(filename)
        if df.empty: return f"ERROR_FILE_IS_EMPTY: '{filename}'"
        
        df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
        required_columns = ['question', 'options', 'answer_key', 'solution']
        for col in required_columns:
            if col not in df.columns: return f"ERROR_MISSING_COLUMN: '{col}' in '{filename}'."
        
        df = df.fillna('')
        df.dropna(subset=['question'], inplace=True)
        df = df[df['question'].str.strip() != '']
        if df.empty: return f"ERROR_NO_VALID_QUESTIONS: No valid rows in '{filename}'."
        
        for col in required_columns:
             if col in df.columns: df[col] = df[col].astype(str)
        for col in ['image_q', 'image_a']:
             if col not in df.columns: df[col] = ''
             df[col] = df[col].astype(str).str.strip()
        
        QUESTION_CACHE[filename] = df.to_dict('records')
        return QUESTION_CACHE[filename]
    except Exception as e:
        return f"ERROR_UNEXPECTED: Loading '{filename}'. Details: {e}"

def get_questions(filename):
    if filename not in QUESTION_CACHE:
        QUESTION_CACHE[filename] = load_questions(filename)
    return QUESTION_CACHE.get(filename)

def natural_sort_key(filename):
    try:
        parts = ''.join(c if c.isdigit() else ' ' for c in filename).split()
        return tuple(map(int, parts))
    except (ValueError, IndexError):
        return (float('inf'), filename)

@app.route('/')
def home():
    csv_files = sorted([os.path.basename(file) for file in glob.glob('*.csv')], key=natural_sort_key)
    test_names = {}
    if os.path.exists('index.json'):
        try:
            with open('index.json', 'r', encoding='utf-8') as f:
                test_names = json.load(f)
        except json.JSONDecodeError: print("WARNING: index.json is malformed.")
    grouped_tests = {}
    for f in csv_files:
        match = re.match(r'^[A-Za-z]+', f)
        subject = match.group(0).title() if match else "Other"
        test_info = { "filename": f, "display_name": test_names.get(f, f.replace('.csv','').replace('_',' ').title())}
        grouped_tests.setdefault(subject, []).append(test_info)
    history = session.get('history', [])
    return render_template('index.html', history=history, grouped_tests=grouped_tests)

@app.route('/update_test_name', methods=['POST'])
def update_test_name():
    data = request.get_json()
    filename, new_name = data.get('filename'), data.get('new_name')
    if not filename or not new_name: return jsonify({"status": "error", "message": "Missing data"}), 400
    try:
        test_names = {}
        if os.path.exists('index.json'):
            with open('index.json', 'r', encoding='utf-8') as f:
                test_names = json.load(f)
        test_names[filename] = new_name
        with open('index.json', 'w', encoding='utf-8') as f:
            json.dump(test_names, f, indent=4)
        return jsonify({"status": "success", "message": "Name updated."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/start', methods=['POST'])
def start_test():
    session.clear()
    filename = request.form.get('filename')
    questions_or_error = get_questions(filename)
    if isinstance(questions_or_error, str): return render_template('error.html', error_message=questions_or_error)
    session['active_test_name'] = filename
    session['mode'] = request.form.get('mode')
    session['answers'] = {}
    session['flagged_questions'] = []
    test_duration_seconds = len(questions_or_error) * 60
    session['test_end_time'] = (datetime.now() + timedelta(seconds=test_duration_seconds)).isoformat()
    return redirect(url_for('show_question', q_id=0))

@app.route('/edit/<filename>')
def edit_test(filename):
    return redirect(url_for('edit_question_page', filename=filename, q_id=0))

@app.route('/edit/<filename>/<int:q_id>')
def edit_question_page(filename, q_id):
    questions = get_questions(filename)
    if isinstance(questions, str): return render_template('error.html', error_message=questions)
    if q_id >= len(questions): return redirect(url_for('home'))
    image_path = os.path.join('static', 'images')
    if not os.path.exists(image_path): os.makedirs(image_path)
    image_files_raw = [os.path.basename(f) for f in glob.glob(os.path.join(image_path, '*'))]
    all_images = sorted(image_files_raw, key=natural_sort_key)
    return render_template('edit_question.html', filename=filename, q_id=q_id, 
                           question=questions[q_id], total_questions=len(questions),
                           all_images=all_images)

@app.route('/save/<filename>/<int:q_id>', methods=['POST'])
def save_edit(filename, q_id):
    try:
        df = robust_read_csv(filename)
        original_columns = list(df.columns)
        df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
        for col in ['question', 'solution', 'image_q', 'image_a']:
            if col not in df.columns: df[col] = ''
        
        df.loc[q_id, 'question'] = request.form.get('question', '')
        df.loc[q_id, 'solution'] = request.form.get('solution', '')
        df.loc[q_id, 'image_q'] = request.form.get('image_q', '')
        df.loc[q_id, 'image_a'] = request.form.get('image_a', '')
        
        df.columns = original_columns
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        load_questions(filename, force_reload=True)
    except Exception as e:
        return render_template('error.html', error_message=f"Could not save file '{filename}'. Details: {e}")
    next_q_id = q_id + 1
    return redirect(url_for('edit_question_page', filename=filename, q_id=next_q_id))

# ... The rest of the routes are unchanged ...
# ... I will provide them in the details section for completeness ...