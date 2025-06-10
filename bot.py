import logging
import os
import glob
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, ContextTypes
from telegram.constants import ParseMode
import markdown
import config # ADDED: Import the new config file

# --- Configuration & Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# REMOVED: The token is now in config.py
# TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN_HERE" 

USER_DATA = {}
QUESTION_CACHE = {}

# --- Data Loading (Unchanged) ---
def robust_read_csv(filename):
    try:
        return pd.read_csv(filename, encoding='utf-8-sig')
    except UnicodeDecodeError:
        return pd.read_csv(filename, encoding='cp1252')

def get_questions(filename):
    if filename not in QUESTION_CACHE:
        try:
            if not os.path.exists(filename): return None
            df = robust_read_csv(filename)
            if df.empty: return None
            df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
            df.dropna(subset=['question'], inplace=True)
            df = df[df['question'].str.strip() != '']
            for col in ['question', 'options', 'answer_key', 'solution']:
                df[col] = df[col].astype(str)
            for col in ['image_q', 'image_a']:
                if col not in df.columns: df[col] = ''
                df[col] = df[col].astype(str).str.strip().fillna('')
            QUESTION_CACHE[filename] = df.to_dict('records')
        except Exception as e:
            logger.error(f"Failed to load {filename}: {e}")
            return None
    return QUESTION_CACHE.get(filename)

# --- Bot Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_DATA[user_id] = {} 
    csv_files = [os.path.basename(file) for file in glob.glob('*.csv')]
    if not csv_files:
        await update.message.reply_text("Welcome! I couldn't find any test files (.csv) in my folder.")
        return
    keyboard = [[InlineKeyboardButton(f.replace('.csv', '').replace('_', ' ').title(), callback_data=f"select_test:{f}")] for f in csv_files]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Welcome to the Test Bot! Please choose a test to begin:', reply_markup=reply_markup)

async def send_next_question(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.chat_id
    user_state = USER_DATA.get(user_id)
    if not user_state: return

    q_id = user_state.get('q_id', 0)
    questions = get_questions(user_state['test_name'])
    
    if q_id >= len(questions):
        if user_state['mode'] == 'test':
            await send_results(user_id, context)
        else:
            await context.bot.send_message(chat_id=user_id, text="🎉 You have completed all questions in this study set! Use /start to begin a new one.")
        USER_DATA[user_id] = {}
        return

    if user_state['mode'] == 'test':
        await send_test_poll(user_id, q_id, context)
    elif user_state['mode'] == 'study':
        await send_study_question(user_id, q_id, context)

# --- Test Mode Functions ---

async def send_test_poll(user_id, q_id, context: ContextTypes.DEFAULT_TYPE):
    user_state = USER_DATA[user_id]
    questions = get_questions(user_state['test_name'])
    question_data = questions[q_id]

    if question_data.get('image_q') and question_data['image_q'] != 'nan':
        try:
            with open(f"static/images/{question_data['image_q']}", 'rb') as photo_file:
                await context.bot.send_photo(chat_id=user_id, photo=photo_file)
        except Exception as e:
            logger.error(f"Failed to send question image: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"(Image not found: {question_data['image_q']})")
    
    options = [opt.strip()[:100] for opt in question_data['options'].split('\n') if opt.strip()][:10]
    correct_option_letter = question_data['answer_key'].strip().upper()
    
    correct_option_id = -1
    for i, opt in enumerate(options):
        if opt.strip().upper().startswith(correct_option_letter):
            correct_option_id = i
            break
            
    if correct_option_id == -1 or len(options) < 2:
        await send_study_question(user_id, q_id, context)
        return
        
    question_prefix = f"Q{q_id+1}: "
    question_text = question_data['question']
    max_q_len = 300 - len(question_prefix)
    if len(question_text) > max_q_len:
        question_text = question_text[:max_q_len - 3] + "..."
        
    poll_message = await context.bot.send_poll(
        chat_id=user_id,
        question=f"{question_prefix}{question_text}",
        options=options,
        type='quiz',
        correct_option_id=correct_option_id,
        is_anonymous=False
    )
    context.bot_data[poll_message.poll.id] = {"user_id": user_id, "q_id": q_id, "correct_option_id": correct_option_id}

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id
    try:
        poll_data = context.bot_data[poll_id]
    except KeyError:
        return

    user_id = poll_data['user_id']
    q_id = poll_data['q_id']
    user_state = USER_DATA.get(user_id)
    if not user_state or q_id != user_state.get('q_id', 0):
        return
    
    if len(poll_answer.option_ids) > 0 and poll_answer.option_ids[0] == poll_data["correct_option_id"]:
        user_state['score'] = user_state.get('score', 0) + 1

    questions = get_questions(user_state['test_name'])
    question_data = questions[q_id]
    
    # --- BUG FIX: Clean the HTML from markdown before sending ---
    solution_html = markdown.markdown(question_data['solution'], extensions=['tables'])
    cleaned_html = solution_html.replace("<p>", "").replace("</p>", "")

    await context.bot.send_message(chat_id=user_id, text=f"<b>Solution for Q{q_id+1}:</b>\n\n{cleaned_html}", parse_mode=ParseMode.HTML)
    
    if question_data.get('image_a') and question_data['image_a'] != 'nan':
        try:
            with open(f"static/images/{question_data['image_a']}", 'rb') as photo_file:
                await context.bot.send_photo(chat_id=user_id, photo=photo_file)
        except Exception as e:
            logger.error(f"Failed to send solution image: {e}")

    user_state['q_id'] += 1
    context.application.job_queue.run_once(send_next_question, 2, chat_id=user_id, name=f"nextq_{user_id}")

# --- Study Mode Functions ---
async def send_study_question(user_id, q_id, context: ContextTypes.DEFAULT_TYPE):
    user_state = USER_DATA[user_id]
    questions = get_questions(user_state['test_name'])
    question_data = questions[q_id]
    if question_data.get('image_q') and question_data['image_q'] != 'nan':
        try:
            with open(f"static/images/{question_data['image_q']}", 'rb') as photo_file:
                await context.bot.send_photo(chat_id=user_id, photo=photo_file)
        except Exception as e:
            logger.error(f"Failed to send study image: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"(Image not found: {question_data['image_q']})")
    
    options = [opt.strip() for opt in question_data['options'].split('\n') if opt.strip()]
    keyboard = [[InlineKeyboardButton(opt, callback_data=f"answer_study:{q_id}:{opt[0]}")] for opt in options]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=user_id, text=f"Q{q_id+1}: {question_data['question']}", reply_markup=reply_markup)

# --- General Functions & Callback Handlers ---
async def send_results(user_id, context: ContextTypes.DEFAULT_TYPE):
    user_state = USER_DATA.get(user_id)
    if not user_state: return
    score = user_state.get('score', 0)
    total = len(get_questions(user_state['test_name']))
    await context.bot.send_message(chat_id=user_id, text=f"🎉 Test Complete! 🎉\n\nYour final score is: {score} / {total}")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data.split(':')
    action = data[0]
    if action == "select_test":
        filename = data[1]
        keyboard = [[InlineKeyboardButton("📖 Study Mode", callback_data=f"select_mode:study:{filename}"),
                     InlineKeyboardButton("🚀 Test Mode", callback_data=f"select_mode:test:{filename}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=f"You have selected '{filename}'. Choose your mode:", reply_markup=reply_markup)
    
    elif action == "select_mode":
        mode, filename = data[1], data[2]
        USER_DATA[user_id] = {"test_name": filename, "mode": mode, "q_id": 0, "score": 0}
        await query.edit_message_text(text=f"Starting test '{filename}' in {mode} mode...")
        context.application.job_queue.run_once(send_next_question, 0, chat_id=user_id, name=f"nextq_{user_id}")
    
    elif action == "answer_study":
        q_id, answer = int(data[1]), data[2]
        user_state = USER_DATA.get(user_id)
        if not user_state or q_id != user_state.get('q_id', 0):
            await context.bot.send_message(chat_id=user_id, text="This is an old question.")
            return

        questions = get_questions(user_state['test_name'])
        question_data = questions[q_id]
        correct_answer = question_data['answer_key'].strip().upper()
        if answer.upper() == correct_answer:
            await query.edit_message_text(text=f"✅ Correct! {query.message.text}", reply_markup=None)
        else:
            await query.edit_message_text(text=f"❌ Incorrect. Correct was {correct_answer}.\n\n{query.message.text}", reply_markup=None)
        
        # --- BUG FIX: Clean the HTML from markdown before sending ---
        solution_html = markdown.markdown(question_data['solution'], extensions=['tables'])
        cleaned_html = solution_html.replace("<p>", "").replace("</p>", "")
        
        await context.bot.send_message(chat_id=user_id, text=f"<b>Solution:</b>\n\n{cleaned_html}", parse_mode=ParseMode.HTML)
        
        if question_data.get('image_a') and question_data['image_a'] != 'nan':
            try:
                with open(f"static/images/{question_data['image_a']}", 'rb') as photo_file:
                    await context.bot.send_photo(chat_id=user_id, photo=photo_file)
            except Exception as e:
                logger.error(f"Failed to send solution image: {e}")
        
        user_state['q_id'] += 1
        context.application.job_queue.run_once(send_next_question, 2, chat_id=user_id, name=f"nextq_{user_id}")

# --- Main Application Execution ---
def main():
    try:
        # Get token from the config file
        token = config.TELEGRAM_TOKEN
        if not token or token == "YOUR_TELEGRAM_TOKEN_HERE":
            print("!!! FATAL ERROR: Your Telegram API token is missing from config.py !!!")
            input("\nPress Enter to exit.")
            return
    except (ImportError, AttributeError):
        print("!!! FATAL ERROR: config.py file not found or does not contain TELEGRAM_TOKEN. !!!")
        print("Please create a config.py file with the line: TELEGRAM_TOKEN = 'YOUR_TOKEN'")
        input("\nPress Enter to exit.")
        return
        
    print("Starting bot...")
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    application.add_handler(PollAnswerHandler(handle_poll_answer))

    print("Bot is running. Press Ctrl-C to stop.")
    application.run_polling()

if __name__ == '__main__':
    main()