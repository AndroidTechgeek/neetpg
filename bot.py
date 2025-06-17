import logging
import os
import glob
import pandas as pd
import sqlite3
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, MessageHandler, filters, ConversationHandler, ContextTypes)
from telegram.constants import ParseMode
from datetime import datetime, timedelta
import random
import config
import google.generativeai as genai
import io
from PIL import Image
from collections import defaultdict

# --- Configuration & Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    if hasattr(config, 'GEMINI_API_KEY') and config.GEMINI_API_KEY and "PASTE" not in config.GEMINI_API_KEY:
        genai.configure(api_key=config.GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        gemini_model = None
except Exception as e:
    logger.error(f"Failed to configure Gemini AI. Features will be disabled. Error: {e}")
    gemini_model = None

# Constants
(SELECTING_SUBJECTS, SELECTING_NUM_QS, TYPING_BUG_REPORT) = range(3)
PREDEFINED_BUGS = ["Typo in Q", "Typo in Opt", "Typo in Sol", "Wrong Ans", "Wrong Img", "Missing Img"]
USER_DATA = {}
QUESTION_CACHE = {}
DB_FILE = 'neet_pro_bot.db'
TESTS_PER_PAGE = 10
INITIAL_SRS_INTERVAL_DAYS = 1
QUESTION_TIMER_SECONDS = 60 

# --- Database Functions ---
def db_query(query, params=(), fetch=None):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cur = conn.cursor()
            res = cur.execute(query, params)
            if fetch == 'one': return res.fetchone()
            if fetch == 'all': return res.fetchall()
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None

def setup_database():
    db_query('''CREATE TABLE IF NOT EXISTS history (
                  user_id INTEGER, test_name TEXT, question_id INTEGER, 
                  is_correct BOOLEAN, timestamp TEXT, user_answer TEXT,
                  PRIMARY KEY (user_id, test_name, question_id, timestamp)
                 )''')
    db_query('''CREATE TABLE IF NOT EXISTS srs_queue (
                  user_id INTEGER, test_name TEXT, question_id INTEGER, 
                  due_date TEXT, interval INTEGER,
                  PRIMARY KEY (user_id, test_name, question_id)
                 )''')

def add_to_srs_queue(user_id, test_name, question_id):
    due_date = (datetime.now() + timedelta(days=INITIAL_SRS_INTERVAL_DAYS)).isoformat()
    db_query("INSERT OR REPLACE INTO srs_queue (user_id, test_name, question_id, due_date, interval) VALUES (?, ?, ?, ?, ?)",
             (user_id, test_name, question_id, due_date, INITIAL_SRS_INTERVAL_DAYS))

def update_srs_item(user_id, test_name, question_id):
    current_item = db_query("SELECT interval FROM srs_queue WHERE user_id=? AND test_name=? AND question_id=?", (user_id, test_name, question_id), fetch='one')
    if current_item:
        new_interval = current_item[0] * 2
        new_due_date = (datetime.now() + timedelta(days=new_interval)).isoformat()
        db_query("UPDATE srs_queue SET due_date=?, interval=? WHERE user_id=? AND test_name=? AND question_id=?",
                 (new_due_date, new_interval, user_id, test_name, question_id))
    db_query("DELETE FROM srs_queue WHERE user_id=? AND test_name=? AND question_id=?", (user_id, test_name, question_id))

def get_questions(filename):
    if filename not in QUESTION_CACHE:
        try:
            df = pd.read_csv(filename, encoding='utf-8-sig', keep_default_na=False).fillna('')
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
            QUESTION_CACHE[filename] = df.to_dict('records')
        except Exception as e:
            logger.error(f"Failed to load {filename}: {e}")
            return None
    return QUESTION_CACHE.get(filename)

def log_bug_report(test_name, q_id, bug_description):
    questions = get_questions(test_name)
    question_text = "N/A"
    if questions and q_id < len(questions):
        question_text = questions[q_id].get('question', 'N/A')
    with open('bug_reports.log', 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] '{test_name}' Q{q_id+1}: {bug_description} -> {question_text[:50]}...\n")

def get_subject_categories():
    subjects = defaultdict(list)
    for file in glob.glob('*.csv'):
        match = re.match(r'^([A-Z]+)', file, re.IGNORECASE)
        if match:
            subject_name = match.group(1).upper()
            subjects[subject_name].append(os.path.basename(file))
    return subjects

# --- Bot Feature Functions ---
def sanitize_markdown(text: str) -> str:
    """Checks for unclosed markdown entities and removes them to prevent errors."""
    if text.count('*') % 2 != 0: text = text.replace('*', '')
    if text.count('_') % 2 != 0: text = text.replace('_', '')
    return text

async def get_ai_explanation(question_data: dict, user_state: dict, context: ContextTypes.DEFAULT_TYPE):
    """Generates a personalized explanation of the user's mistake and other high-yield info."""
    if not gemini_model:
        await context.bot.send_message(chat_id=user_state['user_id'], text="AI Explanation feature is not configured.")
        return
    try:
        await context.bot.send_chat_action(chat_id=user_state['user_id'], action='typing')
        user_answer = user_state.get('last_user_answer', 'N/A')
        was_correct = user_state.get('last_was_correct', True)
        
        prompt = f"""
Act as a master medical educator for the NEET PG exam. Your task is to provide a concise, high-yield explanation for a question the user just answered.
### TASK ###
Analyze the provided question, options, and user's answer to generate a multi-part explanation.
1.  **What's Being Tested:** In one sentence, what is the core clinical or factual knowledge this question is assessing?
2.  **Explanation of Options:**
    * **Correct Answer:** Briefly explain why the correct answer is right.
    * **User's Answer (if incorrect):** If the user chose '{user_answer}', explain the specific reason why this option is wrong.
    * **Obvious Rule-Outs:** Identify one or two options that are the easiest to eliminate and briefly state why.
3.  **Key Takeaway:** A single, high-yield sentence that the student should memorize.
### INPUT ###
QUESTION: "{question_data.get('question', '')}"
ALL OPTIONS:\n{question_data.get('options', '')}
CORRECT ANSWER: "{user_state.get('last_correct_answer_text', 'N/A')}"
USER'S ANSWER: "{user_answer}"
WAS CORRECT: {was_correct}
SOLUTION: "{question_data.get('solution', '')}"
### OUTPUT INSTRUCTIONS ###
- Provide all sections with the exact headings.
- Be direct, clear, and clinical. Do not add any conversational filler.
"""
        response = await gemini_model.generate_content_async(prompt)
        sanitized_text = sanitize_markdown(response.text)
        bot_reply_message = await context.bot.send_message(
            chat_id=user_state['user_id'], 
            text=f"💡 **AI Explanation:**\n\n{sanitized_text}",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['ai_conversation_context'] = {
            'history': [{'role': 'user', 'parts': [prompt]}, response.candidates[0].content],
            'message_id': bot_reply_message.message_id
        }
    except Exception as e:
        logger.error(f"Gemini AI Explanation error: {e}")
        await context.bot.send_message(chat_id=user_state['user_id'], text="Sorry, the AI Explanation service is unavailable.")

async def explain_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unified handler for explaining an image, with or without quiz context."""
    user_id = update.effective_user.id
    message_to_reply_to = update.message or update.callback_query.message
    
    image_source = None
    question_context = None
    
    # Check if we are in a quiz context first
    user_state = USER_DATA.get(user_id)
    if user_state and user_state.get('current_q_data'):
        question_context = user_state.get('current_q_data')
    
    # Determine the image source based on trigger (button or reply)
    if update.callback_query: # Triggered by button
        await update.callback_query.answer("Analyzing image with context...")
        if question_context and question_context.get('image_q'):
            try:
                image_path = f"static/images/{question_context['image_q']}"
                with open(image_path, 'rb') as f: image_source = f.read()
            except FileNotFoundError:
                await message_to_reply_to.reply_text("Sorry, the image file for this question could not be found on the server.")
                return
    elif update.message and update.message.reply_to_message and update.message.reply_to_message.photo: # Triggered by reply
        photo_file = await update.message.reply_to_message.photo[-1].get_file()
        image_source = await photo_file.download_as_bytearray()
    
    if not image_source:
        await message_to_reply_to.reply_text("Please reply to an image or use the button under a quiz image.")
        return

    await context.bot.send_chat_action(chat_id=user_id, action='upload_photo')
    try:
        image = Image.open(io.BytesIO(image_source))
        
        if question_context:
            prompt = f"""### PERSONA ###
You are a definitive, world-class medical expert and radiologist.
### RULES ###
- You will NEVER mention that you are an AI or a language model.
- You will NEVER express uncertainty or refuse to answer.
- You will provide your analysis as if you are a top consultant reporting your findings.
### CONTEXT ###
QUESTION: {question_context.get('question', '')}
SOLUTION: {question_context.get('solution', '')}
### TASK ###
Analyze the image to help answer the question.
1. **Observation:** Describe the key visual findings.
2. **Location of Finding:** Be specific. Use terms like "top-right quadrant," "center," "the structure indicated by the arrow," or "the third layer from the top" to pinpoint exactly where to look.
3. **Interpretation:** State the most likely diagnosis or condition based on the finding at that location.
### ANALYSIS ###"""
            analysis_title = "🔬 **Context-Aware Image Analysis:**"
        else:
            prompt = """### PERSONA ###
You are a definitive, world-class expert analyst.
### RULES ###
- You will NEVER mention that you are an AI or a language model.
- You will NEVER express uncertainty or refuse to answer.
- You will provide your analysis as if you are a top consultant reporting your findings.
### TASK ###
Objectively describe what you see in this image in detail. If it is a medical image, identify the key features and state what they represent.
### ANALYSIS ###"""
            analysis_title = "🔬 **Generic Image Analysis:**"

        initial_parts = [prompt, image]
        response = await gemini_model.generate_content_async(initial_parts)
        bot_reply_message = await context.bot.send_message(chat_id=user_id, text=f"{analysis_title}\n\n{response.text}")
        
        context.user_data['ai_conversation_context'] = {
            'history': [{'role': 'user', 'parts': initial_parts}, response.candidates[0].content],
            'message_id': bot_reply_message.message_id
        }
    except Exception as e:
        logger.error(f"Image explanation failed: {e}")
        await context.bot.send_message(chat_id=user_id, text="Sorry, I encountered an error analyzing that image.")

async def handle_ai_follow_up(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles follow-up questions to an AI-generated explanation."""
    if not update.message.reply_to_message or 'ai_conversation_context' not in context.user_data: return
    stored_context = context.user_data['ai_conversation_context']
    if update.message.reply_to_message.message_id != stored_context.get('message_id'): return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    try:
        user_follow_up = update.message.text
        history = stored_context.get('history', [])
        history.append({'role': 'user', 'parts': [user_follow_up]})
        response = await gemini_model.generate_content_async(history)
        bot_reply_message = await update.message.reply_text(response.text)
        history.append(response.candidates[0].content)
        context.user_data['ai_conversation_context']['message_id'] = bot_reply_message.message_id
    except Exception as e:
        logger.error(f"AI follow-up failed: {e}")
        await update.message.reply_text("Sorry, I encountered an error processing your follow-up.")

async def warm_up_gemini(context: ContextTypes.DEFAULT_TYPE):
    if not gemini_model: return
    try:
        await gemini_model.generate_content_async("Ping")
        logger.info("Gemini keep-alive ping sent successfully.")
    except Exception as e:
        logger.error(f"Gemini keep-alive ping failed: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📚 Start a Test Paper", callback_data="menu:pick_subject")],
        [InlineKeyboardButton("🔀 Start Custom Quiz", callback_data="menu:custom_test")],
        [InlineKeyboardButton("🧠 Start SRS Review", callback_data="menu:srs_review")],
        [InlineKeyboardButton("📊 My Stats", callback_data="menu:my_stats")],
        [InlineKeyboardButton("🐞 View Bug Reports", callback_data="menu:view_bugs")],
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text("Welcome! What would you like to do?", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Welcome! What would you like to do?", reply_markup=InlineKeyboardMarkup(keyboard))

async def _send_solution_message(user_id: int, q_id: int, question_data: dict, is_correct: bool, context: ContextTypes.DEFAULT_TYPE):
    """Helper function to send the solution message, avoiding code duplication."""
    original_test = question_data.get('original_test', USER_DATA.get(user_id, {}).get('test_name', ''))
    original_q_id = question_data.get('original_q_id', q_id)
    solution_text = str(question_data.get('solution', 'No solution provided.'))
    keyboard_row1 = [InlineKeyboardButton("🐞 Report Bug", callback_data=f"bug_menu:{original_test}:{original_q_id}"), InlineKeyboardButton("Next Question ➡️", callback_data="quiz:next")]
    keyboard_row2 = []
    if gemini_model: keyboard_row2.append(InlineKeyboardButton("💡 AI Explain", callback_data="get_ai_expln"))
    user_state = USER_DATA.get(user_id)
    if not is_correct and user_state and not user_state.get('is_srs_review'):
        keyboard_row2.append(InlineKeyboardButton("🧠 Add to SRS", callback_data=f"add_srs:{original_test}:{original_q_id}"))
    final_keyboard = [keyboard_row1]
    if keyboard_row2: final_keyboard.append(keyboard_row2)
    await context.bot.send_message(user_id, f"<b>Solution for Q{q_id+1}:</b>\n\n{solution_text}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(final_keyboard))
    if question_data.get('image_a'):
        for img_file in [img.strip() for img in str(question_data.get('image_a')).split(';') if img.strip()]:
            try:
                with open(f"static/images/{img_file}", 'rb') as photo: await context.bot.send_photo(user_id, photo)
            except Exception: await context.bot.send_message(user_id, f"(Solution image not found: {img_file})")

async def auto_advance_on_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Fired when the question timer runs out. Advances state if user hasn't answered."""
    job = context.job
    user_id, question_id_when_sent = job.data['user_id'], job.data['q_id']
    user_state = USER_DATA.get(user_id)
    if user_state and user_state.get('q_id') == question_id_when_sent:
        logger.info(f"User {user_id} timed out on question {question_id_when_sent}.")
        await context.bot.send_message(user_id, text=f"⏰ Time's up for Q{question_id_when_sent + 1}! Here's the solution.")
        question_data = user_state['current_q_data']
        original_test = user_state.get('test_name'); original_q_id = question_data.get('original_q_id', question_id_when_sent)
        db_query("INSERT INTO history VALUES (?, ?, ?, ?, ?, ?)", (user_id, original_test, original_q_id, False, datetime.now().isoformat(), "Timed Out"))
        await _send_solution_message(user_id, question_id_when_sent, question_data, False, context)
        user_state['q_id'] += 1

async def send_question_poll(context: ContextTypes.DEFAULT_TYPE):
    job = context.job; user_id = job.data['user_id']; user_state = USER_DATA.get(user_id)
    if not user_state: return
    q_id = user_state.get('q_id', 0); questions = user_state.get('questions', [])
    if not questions or q_id >= len(questions):
        await send_results(user_id, context, user_state.get('test_name', 'Custom Test')); return
    question_data = questions[q_id]; user_state['current_q_data'] = question_data
    full_question_text = f"Q{q_id+1}/{len(questions)}: {question_data['question']}"
    poll_question_text = full_question_text
    if len(full_question_text) > 300:
        try:
            await context.bot.send_message(chat_id=user_id, text=full_question_text)
            poll_question_text = full_question_text[:297] + "..."
        except Exception as e:
            logger.error(f"Failed to send long question pre-message: {e}")
            poll_question_text = full_question_text[:297] + "..."
    if question_data.get('image_q') and str(question_data.get('image_q')) != 'nan':
        try:
            image_path = f"static/images/{question_data['image_q']}"
            with open(image_path, 'rb') as photo:
                image_buttons = [InlineKeyboardButton("🔬 Explain this Image", callback_data="explain_img")]
                await context.bot.send_photo(user_id, photo, reply_markup=InlineKeyboardMarkup([image_buttons]))
        except Exception:
            await context.bot.send_message(user_id, f"(Warning: Question image not found: {question_data['image_q']})")
    options = [opt.strip()[:100] for opt in str(question_data.get('options','')).split('\n') if opt.strip()][:10]
    correct_option_letter = str(question_data.get('answer_key','')).strip().upper()
    correct_option_id = next((i for i, opt in enumerate(options) if opt.strip().upper().startswith(correct_option_letter)), -1)
    if correct_option_id == -1 or len(options) < 2:
        await context.bot.send_message(user_id, f"Error processing Q{q_id+1}. Skipping."); user_state['q_id'] += 1
        context.application.job_queue.run_once(send_question_poll, 0, data=user_state); return
    poll_message = await context.bot.send_poll(
        chat_id=user_id, question=poll_question_text, options=options, type='quiz',
        correct_option_id=correct_option_id, is_anonymous=False, open_period=QUESTION_TIMER_SECONDS
    )
    context.bot_data[poll_message.poll.id] = {"user_id": user_id, "q_id": q_id, "correct_option_id": correct_option_id, "options": options}
    context.application.job_queue.run_once(auto_advance_on_timeout, QUESTION_TIMER_SECONDS + 2, data={'user_id': user_id, 'q_id': q_id}, name=f"timeout_{user_id}_{q_id}")

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer; poll_id = poll_answer.poll_id
    try: poll_data = context.bot_data[poll_id]
    except KeyError: return
    user_id, q_id = poll_data['user_id'], poll_data['q_id']
    user_state = USER_DATA.get(user_id)
    if not user_state or q_id != user_state.get('q_id', 0): return
    is_correct = len(poll_answer.option_ids) > 0 and poll_answer.option_ids[0] == poll_data["correct_option_id"]
    question_data = user_state['current_q_data']
    user_choice_id = poll_answer.option_ids[0] if poll_answer.option_ids else -1
    user_answer_text = poll_data['options'][user_choice_id] if user_choice_id != -1 else "No answer"
    correct_answer_text = poll_data['options'][poll_data["correct_option_id"]]
    user_state.update({'last_user_answer': user_answer_text, 'last_was_correct': is_correct, 'last_correct_answer_text': correct_answer_text})
    await _send_solution_message(user_id, q_id, question_data, is_correct, context)
    user_state['q_id'] += 1

async def next_question_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """More forceful /next command that increments the question counter."""
    user_id = update.effective_user.id
    user_state = USER_DATA.get(user_id)
    if not user_state:
        await update.message.reply_text("You are not in a quiz."); return
    logger.info(f"User {user_id} forced /next. Current q_id: {user_state.get('q_id')}.")
    user_state['q_id'] += 1
    context.user_data.pop('ai_conversation_context', None)
    await update.message.reply_text("➡️ Forcing to the next question...")
    context.application.job_queue.run_once(send_question_poll, 0, data=user_state)

async def send_results(user_id, context: ContextTypes.DEFAULT_TYPE, test_name):
    await context.bot.send_message(chat_id=user_id, text=f"🎉 Quiz Complete! 🎉\nYou have finished all questions in '{test_name}'.")
    USER_DATA.pop(user_id, None)
    context.user_data.pop('ai_conversation_context', None)
    mock_update = Update(update_id=0)
    mock_update.effective_user = type('User', (), {'id': user_id, 'first_name': 'Quizzer'})()
    message = await context.bot.send_message(chat_id=user_id, text="Returning to main menu...")
    mock_update.message = message
    await start_command(mock_update, context)

async def pick_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    subjects = get_subject_categories()
    if not subjects:
        await query.edit_message_text("No test subjects found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="menu:back")]])); return
    keyboard = [[InlineKeyboardButton(f"{s.title()} ({len(subjects[s])})", callback_data=f"pick_test_subject:{s}:0")] for s in sorted(subjects.keys())]
    keyboard.append([InlineKeyboardButton("« Back to Main Menu", callback_data="menu:back")])
    await query.edit_message_text("Please choose a subject:", reply_markup=InlineKeyboardMarkup(keyboard))

async def pick_test_from_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    parts = query.data.split(':'); subject = parts[1]; page = int(parts[2])
    all_tests = get_subject_categories().get(subject, [])
    start_index = page * TESTS_PER_PAGE
    paginated_tests = all_tests[start_index : start_index + TESTS_PER_PAGE]
    keyboard = [[InlineKeyboardButton(os.path.basename(f), callback_data=f"start_test:{f}")] for f in paginated_tests]
    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"pick_test_subject:{subject}:{page-1}"))
    if (start_index + TESTS_PER_PAGE) < len(all_tests): nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"pick_test_subject:{subject}:{page+1}"))
    if nav_buttons: keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("« Back to Subjects", callback_data="menu:pick_subject")])
    await query.edit_message_text(f"Tests for {subject.title()}:", reply_markup=InlineKeyboardMarkup(keyboard))

async def mystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = db_query("SELECT test_name, is_correct FROM history WHERE user_id=?", (user_id,), fetch='all')
    if not history: await update.callback_query.edit_message_text("You have no test history yet."); return
    stats = {}
    for test_name, is_correct in history:
        subject_match = re.match(r'^[A-Za-z]+', test_name)
        if subject_match:
            subject = subject_match.group(0).upper()
            if subject not in stats: stats[subject] = {'correct': 0, 'total': 0}
            stats[subject]['total'] += 1
            if is_correct: stats[subject]['correct'] += 1
    stats_text = "<b>Your Performance by Subject:</b>\n\n"
    for subject, data in stats.items():
        accuracy = (data['correct'] / data['total']) * 100
        stats_text += f"<b>{subject}:</b> {accuracy:.1f}% ({data['correct']}/{data['total']})\n"
    await update.callback_query.edit_message_text(stats_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="menu:back")]]))

async def srs_review_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id; now_iso = datetime.now().isoformat()
    srs_items = db_query("SELECT test_name, question_id FROM srs_queue WHERE user_id=? AND due_date <= ? ORDER BY RANDOM()", (user_id, now_iso), fetch='all')
    if not srs_items: await update.callback_query.edit_message_text("Your revision queue is empty!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="menu:back")]])); return
    srs_questions = []
    for test_name, q_id in srs_items:
        questions = get_questions(test_name)
        if questions and q_id < len(questions):
            q_data = questions[q_id].copy(); q_data['original_test'] = test_name; q_data['original_q_id'] = q_id
            srs_questions.append(q_data)
    if not srs_questions: await update.callback_query.edit_message_text("Could not load questions from your SRS queue."); return
    USER_DATA[user_id] = {"user_id": user_id, "test_name": "SRS Review", "q_id": 0, "questions": srs_questions, "is_srs_review": True}
    await update.callback_query.edit_message_text(f"Starting SRS session with {len(srs_questions)} questions...")
    context.application.job_queue.run_once(send_question_poll, 0, data=USER_DATA[user_id])

async def custom_quiz_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subjects = sorted(get_subject_categories().keys())
    keyboard = [[InlineKeyboardButton(s, callback_data=f"custom_subj:{s}")] for s in subjects]
    keyboard.append([InlineKeyboardButton("All Subjects", callback_data="custom_subj:ALL")]); keyboard.append([InlineKeyboardButton("✅ Done Selecting", callback_data="custom_subj:done")])
    context.user_data['custom_quiz_subjects'] = []
    await update.callback_query.edit_message_text("Select subjects for your custom quiz:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING_SUBJECTS
    
async def custom_quiz_subject_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); selection = query.data.split(':')[1]
    if selection == 'done':
        if not context.user_data.get('custom_quiz_subjects'):
            await query.answer("Please select at least one subject!", show_alert=True); return SELECTING_SUBJECTS
        keyboard = [[InlineKeyboardButton(str(n), callback_data=f"custom_num:{n}") for n in [10, 25, 50, 100]]]
        await query.edit_message_text("How many questions?", reply_markup=InlineKeyboardMarkup(keyboard)); return SELECTING_NUM_QS
    else:
        subjects = context.user_data.setdefault('custom_quiz_subjects', [])
        if selection == 'ALL':
            subjects.clear(); subjects.append('ALL'); await query.answer("All subjects selected!", show_alert=True)
        elif selection in subjects:
            subjects.remove(selection); await query.answer(f"Removed {selection}", show_alert=True)
        else:
            if 'ALL' in subjects: subjects.remove('ALL')
            subjects.append(selection); await query.answer(f"Added {selection}", show_alert=True)
        return SELECTING_SUBJECTS

async def custom_quiz_num_selector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    num_questions = int(query.data.split(':')[1]); subjects = context.user_data.get('custom_quiz_subjects', [])
    await query.edit_message_text(f"Building a custom quiz with {num_questions} questions from {', '.join(subjects)}...")
    all_questions = []
    for filename in glob.glob('*.csv'):
        if 'ALL' in subjects or any(filename.upper().startswith(s) for s in subjects):
            questions = get_questions(filename)
            if questions:
                for i, q in enumerate(questions):
                    q_copy = q.copy(); q_copy['original_test'] = filename; q_copy['original_q_id'] = i
                    all_questions.append(q_copy)
    if not all_questions:
        await context.bot.send_message(query.from_user.id, "No questions found for the selected subjects."); return ConversationHandler.END
    random.shuffle(all_questions); final_questions = all_questions[:num_questions]
    user_id = query.from_user.id
    USER_DATA[user_id] = {"user_id": user_id, "test_name": "Custom Quiz", "q_id": 0, "questions": final_questions, "is_srs_review": False}
    context.application.job_queue.run_once(send_question_poll, 0, data=USER_DATA[user_id])
    return ConversationHandler.END

async def custom_bug_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    try:
        _, test_name, q_id_str = query.data.split(':'); context.user_data['bug_report_info'] = {'test_name': test_name, 'q_id': int(q_id_str)}
    except (ValueError, IndexError) as e:
        logger.error(f"Error parsing custom_bug callback data: {query.data}, Error: {e}"); await query.edit_message_text("Sorry, an error occurred."); return ConversationHandler.END
    await query.edit_message_text("Please type your bug report now.\nSend /cancel if you change your mind."); return TYPING_BUG_REPORT

async def custom_bug_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_report = update.message.text; bug_info = context.user_data.get('bug_report_info')
    if not bug_info:
        await update.message.reply_text("Something went wrong."); return ConversationHandler.END
    test_name, q_id = bug_info['test_name'], bug_info['q_id']; log_bug_report(test_name, q_id, f"Custom Report: {user_report}")
    await update.message.reply_text("✅ Thank you! Your bug report has been submitted."); context.user_data.pop('bug_report_info', None)
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled."); context.user_data.pop('bug_report_info', None)
    return ConversationHandler.END

async def bug_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    test_name, q_id_str = query.data.split(':')[1:]
    keyboard = [[InlineKeyboardButton(bug, callback_data=f"report_bug:{test_name}:{q_id_str}:{bug}")] for bug in PREDEFINED_BUGS]
    keyboard.append([InlineKeyboardButton("✍️ Type Custom Bug", callback_data=f"custom_bug:{test_name}:{q_id_str}")])
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

async def view_bugs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message if update.message else update.callback_query.message
    try:
        with open('bug_reports.log', 'r', encoding='utf-8') as f: bugs = f.read()
        if not bugs: await message.reply_text("The bug report file is empty. 👍"); return
        if len(bugs) > 4000:
             await message.reply_text("Bug log is too long. Sending as a file.")
             with open('bug_reports.log', 'rb') as doc:
                 await context.bot.send_document(chat_id=message.chat_id, document=doc, filename="bug_reports.log")
        else:
            escaped_bugs = re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', bugs)
            await message.reply_text(f"🐞 *Bug Reports:*\n\n`{escaped_bugs}`", parse_mode=ParseMode.MARKDOWN_V2)
    except FileNotFoundError: await message.reply_text("`bug_reports.log` not found.")
    except Exception as e: logger.error(f"Failed to read/send bug reports: {e}"); await message.reply_text(f"Error viewing bug reports: {e}")

async def main_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id
    action, *payload = query.data.split(':', 1)
    data = payload[0] if payload else ""

    if action == "menu":
        if data == "pick_subject": await pick_subject_handler(update, context)
        elif data == "custom_test": await custom_quiz_entry(update, context)
        elif data == "my_stats": await mystats_command(update, context)
        elif data == "srs_review": await srs_review_command(update, context)
        elif data == "view_bugs": await view_bugs_command(update, context)
        elif data == "back": await start_command(update, context)
    elif action == "pick_test_subject":
        await pick_test_from_subject_handler(update, context)
    elif action == "quiz":
        if data == "next":
            user_state = USER_DATA.get(user_id)
            if user_state:
                await query.edit_message_reply_markup(reply_markup=None)
                context.user_data.pop('ai_conversation_context', None)
                context.application.job_queue.run_once(send_question_poll, 0, data=user_state)
    elif action == "start_test":
        filename = data
        questions = get_questions(filename)
        if not questions: await query.edit_message_text(f"Error: Could not load questions for {filename}."); return
        USER_DATA[user_id] = {"user_id": user_id, "test_name": filename, "q_id": 0, "questions": questions, "is_srs_review": False}
        await query.edit_message_text(f"Starting test: {filename}..."); context.application.job_queue.run_once(send_question_poll, 0, data=USER_DATA[user_id])
    elif action == "get_ai_expln":
        user_state = USER_DATA.get(user_id)
        if user_state and user_state.get('current_q_data'):
            await get_ai_explanation(user_state['current_q_data'], user_state, context)
    elif action == "explain_img":
        await explain_image_handler(update, context)
    elif action == "add_srs":
        test_name, q_id_str = data.split(':'); add_to_srs_queue(user_id, test_name, int(q_id_str))
        await query.answer("🧠 Added to your SRS queue!", show_alert=True)
    elif action == "bug_menu":
        await bug_menu_handler(update, context)
    elif action == "report_bug":
        test_name, q_id_str, bug_desc = data.split(':', 2); log_bug_report(test_name, int(q_id_str), bug_desc)
        await query.answer(f"🐞 Bug Reported: {bug_desc}. Thank you!", show_alert=True)

def main():
    setup_database()
    try:
        token = config.TELEGRAM_TOKEN
        if not token or "PASTE" in token: print("!!! FATAL ERROR: Telegram token missing !!!"); return
    except (ImportError, AttributeError): print("!!! FATAL ERROR: config.py missing or misconfigured !!!"); return
    
    print("Starting Pro bot...")
    application = Application.builder().token(token).build()
    job_queue = application.job_queue
    job_queue.run_repeating(warm_up_gemini, interval=240, first=10)
    
    conv_handler_defaults = {'per_message': False, 'per_user': True}
    custom_quiz_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(custom_quiz_entry, pattern="^menu:custom_test")],
        states={
            SELECTING_SUBJECTS: [CallbackQueryHandler(custom_quiz_subject_selector, pattern="^custom_subj:")],
            SELECTING_NUM_QS: [CallbackQueryHandler(custom_quiz_num_selector, pattern="^custom_num:")],
        },
        fallbacks=[CommandHandler('start', start_command)], **conv_handler_defaults)
    custom_bug_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(custom_bug_entry, pattern="^custom_bug:")],
        states={TYPING_BUG_REPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_bug_receiver)],},
        fallbacks=[CommandHandler('cancel', cancel_command)], **conv_handler_defaults)
    
    explain_image_filter = (filters.COMMAND & filters.Regex(r'^/explainimage$')) | (filters.TEXT & filters.Regex(r'(?i)^what is this image\??$'))
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("next", next_question_command))
    application.add_handler(CommandHandler("review", srs_review_command))
    application.add_handler(CommandHandler("mystats", mystats_command))
    application.add_handler(CommandHandler("viewbugs", view_bugs_command))
    
    application.add_handler(MessageHandler(explain_image_filter & filters.REPLY, explain_image_handler))
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, handle_ai_follow_up))
    
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    application.add_handler(custom_quiz_conv)
    application.add_handler(custom_bug_conv)
    application.add_handler(CallbackQueryHandler(main_button_handler))

    print("Bot is running. Press Ctrl-C to stop.")
    application.run_polling()

if __name__ == '__main__':
    main()