import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime, timedelta
import os
import glob
import json 
import re   

app = Flask(__name__)
app.secret_key = 'v13_organization_and_editing'

# --- NEW: Custom Filter for a Robust Table and Text Formatter ---
@app.template_filter('format_solution')
def format_solution_filter(text):
    text = str(text)
    # This function is called for each [TABLE]...[/TABLE] block
    def create_html_table(match):
        table_content = match.group(1).strip()
        rows = table_content.split('\n')
        html = '<table class="solution-table">\n'
        # Header row
        try:
            headers = rows[0].split('|')
            html += '  <thead>\n    <tr>\n'
            for header in headers:
                html += f'      <th>{header.strip()}</th>\n'
            html += '    </tr>\n  </thead>\n'
            # Body rows
            html += '  <tbody>\n'
            for row_text in rows[1:]:
                html += '    <tr>\n'
                cells = row_text.split('|')
                for i, cell in enumerate(cells):
                    # Only add cell if it's within the header count
                    if i < len(headers):
                        html += f'      <td>{cell.strip()}</td>\n'
                # If a row has fewer cells than the header, pad it
                if len(cells) < len(headers):
                    html += f'      <td colspan="{len(headers) - len(cells)}"></td>\n'

                html += '    </tr>\n'
            html += '  </tbody>\n</table>'
            return html
        except IndexError:
            return "Error: Invalid table format"

    # Use a placeholder to protect tables from having their newlines converted to <br>
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

    # Convert newlines to <br> in the non-table parts
    processed_text = processed_text.replace('\n', '<br>')

    # Restore the HTML tables
    for placeholder, table_html in table_placeholders.items():
        processed_text = processed_text.replace(placeholder, table_html)
            
    return processed_text

# --- The rest of the app... ---

QUESTION_CACHE = {}

def robust_read_csv(filename):
    try: return pd.read_csv(filename, encoding='utf-8-sig')
    except UnicodeDecodeError: return pd.read_csv(filename, encoding='cp1252')

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
        df.dropna(subset=['question'], inplace=True)
        df = df[df['question'].str.strip() != '']
        if df.empty: return f"ERROR_NO_VALID_QUESTIONS: No valid rows in '{filename}'."
        for col in required_columns:
             if col in df.columns: df[col] = df[col].astype(str)
        for col in ['image_q', 'image_a']:
             if col not in df.columns: df[col] = ''
             df[col] = df[col].astype(str).str.strip().fillna('')
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
        except json.JSONDecodeError:
            print("WARNING: index.json is malformed.")
            
    grouped_tests = {}
    for f in csv_files:
        match = re.match(r'^[A-Za-z]+', f)
        subject = match.group(0).title() if match else "Other"
        
        test_info = {
            "filename": f,
            "display_name": test_names.get(f, f.replace('.csv','').replace('_',' ').title())
        }
        
        grouped_tests.setdefault(subject, []).append(test_info)

    history = session.get('history', [])
    return render_template('index.html', history=history, grouped_tests=grouped_tests)

@app.route('/update_test_name', methods=['POST'])
def update_test_name():
    data = request.get_json()
    filename = data.get('filename')
    new_name = data.get('new_name')
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

@app.route('/question/<int:q_id>', methods=['GET', 'POST'])
def show_question(q_id):
    active_test = session.get('active_test_name')
    if not active_test: return redirect(url_for('home'))
    questions = get_questions(active_test)
    if isinstance(questions, str): return render_template('error.html', error_message=questions)

    if request.method == 'POST':
        user_answer = request.form.get('option')
        if user_answer: session['answers'][str(q_id)] = user_answer
        session.modified = True
        next_q_id = q_id + 1
        return redirect(url_for('show_question', q_id=next_q_id)) if next_q_id < len(questions) else redirect(url_for('results'))

    if q_id >= len(questions): return redirect(url_for('results'))
    time_remaining = 0
    if 'test_end_time' in session:
        time_remaining = max(0, int((datetime.fromisoformat(session.get('test_end_time')) - datetime.now()).total_seconds()))
    return render_template('test.html', question=questions[q_id], q_id=q_id, mode=session['mode'], 
        total_questions=len(questions), user_answers=session.get('answers', {}),
        flagged_questions=session.get('flagged_questions', []), time_remaining=time_remaining)
    
@app.route('/results')
def results():
    active_test = session.get('active_test_name')
    if not active_test: return redirect(url_for('home'))
    questions = get_questions(active_test) 
    if isinstance(questions, str): return render_template('error.html', error_message=questions)
    
    score = 0
    user_answers = session.get('answers', {})
    for q_id_str, user_answer in user_answers.items():
        if user_answer.upper() == questions[int(q_id_str)]['answer_key'].strip().upper(): score += 1
    
    test_names = {}
    if os.path.exists('index.json'):
        with open('index.json', 'r', encoding='utf-8') as f:
            test_names = json.load(f)
    display_name = test_names.get(active_test, active_test)

    if 'history' not in session: session['history'] = []
    session['history'].insert(0, {
        "date": datetime.now().strftime("%B %d, %Y - %I:%M %p"), "score": score,
        "total": len(questions), "answers": user_answers,
        "flagged": session.get('flagged_questions', []), "test_name": active_test,
        "display_name": display_name
    })
    session.modified = True
    return render_template('results.html', score=score, total_questions=len(questions),
        questions=questions, user_answers=user_answers)

@app.route('/review/<int:test_index>')
def review(test_index):
    history = session.get('history', [])
    if test_index >= len(history): return redirect(url_for('home'))
    
    past_test = history[test_index]
    test_filename = past_test.get('test_name')
    if not test_filename: return render_template('error.html', error_message="This historic test result is missing a filename.")
    
    questions = get_questions(test_filename)
    if isinstance(questions, str): return render_template('error.html', error_message=questions)
        
    return render_template('review.html', test_info=past_test, questions=questions)

@app.route('/toggle_flag/<int:q_id>', methods=['POST'])
def toggle_flag(q_id):
    if 'flagged_questions' not in session: return jsonify({'status': 'error'}), 400
    flagged = session.get('flagged_questions', [])
    if q_id in flagged: flagged.remove(q_id)
    else: flagged.append(q_id)
    session['flagged_questions'] = flagged
    session.modified = True
    return jsonify({'status': 'success', 'flagged_questions': flagged})

@app.route('/check_answer/<int:q_id>', methods=['POST'])
def check_answer(q_id):
    active_test = session.get('active_test_name')
    if not active_test: return jsonify({'status': 'error'}), 400
    questions = get_questions(active_test)
    if isinstance(questions, str): return jsonify({'status': 'error'}), 400
    
    question = questions[q_id]
    user_answer = request.get_json().get('option')
    is_correct = (user_answer.upper() == question['answer_key'].strip().upper())
    
    explanation_html = format_solution_filter(question['solution'])
    
    return jsonify({'correct': is_correct, 'correct_answer': question['answer_key'].strip().upper(),
        'explanation': explanation_html, 'image_a': question['image_a']})

if __name__ == '__main__':
    app.run(debug=True)