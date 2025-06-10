import os
import json
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
import markdown

# --- Config ---
QUESTIONS_FILE = "questions.json"
IMAGES_FOLDER = "static/images"

app = Flask(__name__)
app.secret_key = "your_secret_key_here"

# --- Helpers ---
def load_questions():
    if not os.path.exists(QUESTIONS_FILE):
        return []
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_questions(questions):
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)

def get_all_images():
    if not os.path.exists(IMAGES_FOLDER):
        return []
    return [f for f in os.listdir(IMAGES_FOLDER) if os.path.isfile(os.path.join(IMAGES_FOLDER, f)) and not f.startswith('.')]

def format_solution(solution):
    # Render markdown with tables extension, fallback to HTML
    return markdown.markdown(solution or "", extensions=['tables'])

app.jinja_env.filters['format_solution'] = format_solution

# --- Routes ---
@app.route("/")
def home():
    questions = load_questions()
    all_images = get_all_images()
    stats = {
        "total_questions": len(questions),
        "total_images": len(all_images),
        "last_edited": "",
        "recent_activity": ""
    }
    # Example: last_edited from file mod time
    if os.path.exists(QUESTIONS_FILE):
        last_edit_dt = datetime.fromtimestamp(os.path.getmtime(QUESTIONS_FILE))
        stats["last_edited"] = last_edit_dt.strftime("%Y-%m-%d %H:%M")
    # Example: recent_activity from questions list (customize as you wish)
    if questions:
        q = questions[-1]
        stats["recent_activity"] = f'Edited Q{len(questions)}: "{q.get("question", "")[:40]}..."'
    return render_template("home.html", stats=stats)

@app.route("/edit", methods=["GET"])
def edit_questions():
    questions = load_questions()
    all_images = get_all_images()
    return render_template("edit_question.html",
                           filename=QUESTIONS_FILE,
                           total_questions=len(questions),
                           question=questions[0] if questions else {},
                           q_id=0,
                           all_images=all_images)

@app.route("/edit/<int:q_id>", methods=["GET"])
def edit_question_page(q_id):
    questions = load_questions()
    all_images = get_all_images()
    if q_id < 0 or q_id >= len(questions):
        flash("Question not found.")
        return redirect(url_for("edit_questions"))
    return render_template("edit_question.html",
                           filename=QUESTIONS_FILE,
                           total_questions=len(questions),
                           question=questions[q_id],
                           q_id=q_id,
                           all_images=all_images)

@app.route("/edit/<int:q_id>", methods=["POST"])
def save_edit(q_id):
    questions = load_questions()
    all_images = get_all_images()
    if q_id < 0 or q_id >= len(questions):
        flash("Question not found.")
        return redirect(url_for("edit_questions"))

    question = questions[q_id]
    question["question"] = request.form.get("question") or ""
    question["image_q"] = request.form.get("image_q") or ""
    # Validate image_q
    if question["image_q"] and question["image_q"] not in all_images:
        question["image_q"] = ""

    # Validate image_a (semicolon separated)
    image_a = request.form.get("image_a") or ""
    image_a_valid = []
    for img in [s.strip() for s in image_a.split(";") if s.strip()]:
        if img in all_images:
            image_a_valid.append(img)
    question["image_a"] = ";".join(image_a_valid)

    question["solution"] = request.form.get("solution") or ""
    questions[q_id] = question
    save_questions(questions)
    flash("Question saved.")
    if q_id + 1 < len(questions):
        return redirect(url_for("edit_question_page", q_id=q_id + 1))
    return redirect(url_for("edit_question_page", q_id=q_id))

@app.route("/add_question", methods=["GET", "POST"])
def add_question():
    all_images = get_all_images()
    questions = load_questions()
    if request.method == "POST":
        new_q = {
            "question": request.form.get("question") or "",
            "image_q": request.form.get("image_q") or "",
            "image_a": request.form.get("image_a") or "",
            "solution": request.form.get("solution") or "",
            "options": request.form.get("options") or "A. \nB. \nC. \nD. ",
            "answer_key": request.form.get("answer_key") or "A"
        }
        # Validate images as above
        if new_q["image_q"] and new_q["image_q"] not in all_images:
            new_q["image_q"] = ""
        image_a_valid = []
        for img in [s.strip() for s in new_q["image_a"].split(";") if s.strip()]:
            if img in all_images:
                image_a_valid.append(img)
        new_q["image_a"] = ";".join(image_a_valid)
        questions.append(new_q)
        save_questions(questions)
        flash("Question added!")
        return redirect(url_for("edit_question_page", q_id=len(questions)-1))
    return render_template("edit_question.html",
                           filename=QUESTIONS_FILE,
                           total_questions=len(questions)+1,
                           question={},
                           q_id=len(questions),
                           all_images=all_images)

@app.route("/review")
def review_results():
    questions = load_questions()
    all_images = get_all_images()
    # Dummy data for options/user_answers/answer_key/correct for demonstration
    for q in questions:
        q.setdefault("options", "A. Option 1\nB. Option 2\nC. Option 3\nD. Option 4")
        q.setdefault("answer_key", "A")
        q.setdefault("correct", True)
    user_answers = {str(i): "A" for i in range(len(questions))}
    return render_template("review.html",
                           questions=questions,
                           all_images=all_images,
                           user_answers=user_answers)

@app.route("/results")
def results():
    questions = load_questions()
    all_images = get_all_images()
    for q in questions:
        q.setdefault("options", "A. Option 1\nB. Option 2\nC. Option 3\nD. Option 4")
        q.setdefault("answer_key", "A")
        q.setdefault("correct", True)
    user_answers = {str(i): "A" for i in range(len(questions))}
    return render_template("results.html",
                           questions=questions,
                           all_images=all_images,
                           user_answers=user_answers)

@app.route("/start_test")
def start_test():
    # Placeholder: implement your test mode here
    return "<h1>Start Test (not implemented in this example)</h1>"

@app.route("/upload_images", methods=["GET", "POST"])
def upload_images():
    if request.method == "POST":
        if "image" not in request.files:
            flash("No file part")
            return redirect(request.url)
        file = request.files["image"]
        if file.filename == "":
            flash("No selected file")
            return redirect(request.url)
        if file:
            filename = file.filename
            save_path = os.path.join(IMAGES_FOLDER, filename)
            file.save(save_path)
            flash("Image uploaded!")
            return redirect(url_for("upload_images"))
    images = get_all_images()
    return render_template("upload_images.html", images=images)

@app.route("/images/<filename>")
def serve_image(filename):
    return send_from_directory(IMAGES_FOLDER, filename)

if __name__ == "__main__":
    app.run(debug=True)