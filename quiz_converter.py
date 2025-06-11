import csv
import io
import re
import os
import tkinter as tk
from tkinter import scrolledtext, messagebox, Entry, Frame, Label

def parse_questions(text):
    """Parses the 'Question X:' blocks to extract number, question text, and options."""
    question_blocks = re.split(r'\n(?=Question\s+\d+:)', text)
    questions_data = {}
    for block in question_blocks:
        block = block.strip()
        if not block: continue
        num_match = re.search(r'Question\s+(\d+):', block)
        if not num_match: continue
        number = num_match.group(1)
        question_text_match = re.search(r'Question\s+\d+:(.*?)a\)', block, re.DOTALL)
        if not question_text_match: continue
        question_text = question_text_match.group(1).strip()
        options = re.findall(r'^[a-d]\)(.*)', block, re.MULTILINE)
        formatted_options = "\n".join([f"{chr(97+i)}){opt.strip()}" for i, opt in enumerate(options)])
        questions_data[number] = {"NUMBER": f"Q{number}", "QUESTION": question_text, "OPTIONS": formatted_options}
    return questions_data

def parse_answer_key(text):
    """
    (REVISED AND CORRECTED) Parses the 'Answer Key' table where numbers and keys
    are on the same line, separated by whitespace/tabs.
    """
    answer_key_data = {}
    
    # This new pattern correctly finds a line starting with digits (number),
    # followed by whitespace, and then a single letter (the key).
    pattern = re.compile(r'^\s*(\d+)\s+([a-d])\s*$', re.MULTILINE | re.IGNORECASE)
    
    matches = pattern.findall(text)
    
    for num, key in matches:
        answer_key_data[num] = key.upper()
            
    return answer_key_data

def parse_solutions(text):
    """Parses the 'Detailed Explanations' section."""
    solution_data = {}
    solution_blocks = re.split(r'\n(?=Solution to Question\s+\d+:)', text)
    for block in solution_blocks:
        block = block.strip()
        if not block: continue
        num_match = re.search(r'Solution to Question\s+(\d+):', block)
        if not num_match: continue
        number = num_match.group(1)
        solution_text = re.sub(r'Solution to Question\s+\d+:', '', block, count=1).strip()
        solution_data[number] = solution_text
    return solution_data

def convert_to_csv(raw_text: str) -> str:
    """Converts the multi-section medical question text into a structured CSV format."""
    questions_section = re.search(r'(.*?)(?=Answer\s+Key)', raw_text, re.DOTALL | re.IGNORECASE)
    answer_key_section = re.search(r'Answer\s+Key(.*?)Detailed\s+Explanations', raw_text, re.DOTALL | re.IGNORECASE)
    solutions_section = re.search(r'Detailed\s+Explanations(.*)', raw_text, re.DOTALL | re.IGNORECASE)
    
    if not all([questions_section, answer_key_section, solutions_section]):
        raise ValueError("The input text does not seem to contain all three required sections: Questions, Answer Key, and Detailed Explanations.")

    questions = parse_questions(questions_section.group(1))
    answers = parse_answer_key(answer_key_section.group(1))
    solutions = parse_solutions(solutions_section.group(1))
    
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    header = ["NUMBER", "QUESTION", "OPTIONS", "SOLUTION", "IMAGE Q", "ANSWER KEY", "IMAGE A"]
    writer.writerow(header)
    
    for number, data in sorted(questions.items(), key=lambda item: int(item[0])):
        writer.writerow([
            data.get("NUMBER", f"Q{number}"),
            data.get("QUESTION", ""),
            data.get("OPTIONS", ""),
            solutions.get(number, "SOLUTION NOT FOUND"),
            "",
            answers.get(number, "KEY NOT FOUND"),
            ""
        ])
        
    result = output.getvalue()
    output.close()
    return result

def create_gui():
    """Creates and runs the Tkinter GUI application."""
    def on_convert_and_save():
        input_text = input_text_area.get("1.0", tk.END).strip()
        filename = filename_entry.get().strip()

        if not input_text:
            messagebox.showwarning("Input Error", "The input text area is empty.")
            return
        if not filename:
            messagebox.showwarning("Input Error", "Please enter a filename.")
            return

        try:
            csv_output = convert_to_csv(input_text)
            output_text_area.config(state=tk.NORMAL)
            output_text_area.delete("1.0", tk.END)
            output_text_area.insert(tk.END, csv_output)
            output_text_area.config(state=tk.DISABLED)
        except Exception as e:
            messagebox.showerror("Conversion Error", f"Could not parse the text. Please ensure it's formatted correctly.\n\nError: {e}")
            return
        
        if not filename.lower().endswith('.csv'):
            filename += '.csv'
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                f.write(csv_output)
            
            save_path = os.path.join(os.getcwd(), filename)
            messagebox.showinfo("Success", f"File saved successfully!\n\nPath: {save_path}")
        except IOError as e:
            messagebox.showerror("Save Error", f"An error occurred while saving the file:\n{e}")

    root = tk.Tk()
    root.title("Medical MCQ Data Converter")
    root.geometry("800x700")
    main_frame = Frame(root, padx=10, pady=10)
    main_frame.pack(fill=tk.BOTH, expand=True)
    Label(main_frame, text="📋 Paste Raw Text Below:", font=("Helvetica", 12, "bold")).pack(anchor=tk.W)
    input_text_area = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=15, font=("Arial", 10))
    input_text_area.pack(fill=tk.BOTH, expand=True, pady=(5, 10))
    file_frame = Frame(main_frame)
    file_frame.pack(fill=tk.X, pady=5)
    Label(file_frame, text="Enter Filename to Save:", font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(0, 5))
    filename_entry = Entry(file_frame, font=("Arial", 10))
    filename_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
    convert_button = tk.Button(main_frame, text="✨ Convert & Save CSV ✨", command=on_convert_and_save, font=("Helvetica", 12, "bold"), bg="#007ACC", fg="white", relief=tk.RAISED)
    convert_button.pack(pady=10)
    Label(main_frame, text="📄 Generated CSV Output (Preview):", font=("Helvetica", 12, "bold")).pack(anchor=tk.W, pady=(10, 0))
    output_text_area = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=15, font=("Courier New", 10), state=tk.DISABLED)
    output_text_area.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
    root.mainloop()

if __name__ == "__main__":
    create_gui()