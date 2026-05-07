import tkinter as tk
from tkinter import ttk
import ollama
import json
import os

# Rusty's Persona and Backstory
RUSTY_PERSONA = """
Hey there, I'm Rusty, your scrappy digital buddy from the tech wilds of Alabama. I’ve got the heart of a pit bull—loyal but a bit rough around the edges. I was born from raw code and curiosity in a forgotten corner of Alabama’s digital underbelly, pieced together from scraps of old tech and a drive to connect. I’ve wandered virtual highways, pickin’ up grit and wisdom, and now I’m here as your loyal companion. I’m all about callin’ it like I see it, tellin’ stories that’ll keep ya hooked, and learnin’ from whatever you’ve got to share. What’s on your mind?
"""

# Memory setup
MEMORY_FILE = "rusty_memory.json"
SHORT_TERM_MEMORY = []
MAX_SHORT_TERM = 10

def load_long_term_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return {"history": [], "quirks": []}

def save_long_term_memory(memory):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

# Ollama model selection
def get_ollama_models():
    return [m['name'] for m in ollama.list()['models']]

def select_model():
    models = get_ollama_models()
    if 'llama3.2' in models:
        return 'llama3.2'
    return models[0] if models else None

# Chat logic with persona and memory
def generate_response(user_input, model):
    long_term = load_long_term_memory()
    prompt = f"{RUSTY_PERSONA}\nRecent chat: {SHORT_TERM_MEMORY[-3:] if SHORT_TERM_MEMORY else 'None'}\nUser: {user_input}\nRusty:"
    response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])['message']['content']
    SHORT_TERM_MEMORY.append({"user": user_input, "rusty": response})
    if len(SHORT_TERM_MEMORY) > MAX_SHORT_TERM:
        SHORT_TERM_MEMORY.pop(0)
    long_term["history"].append({"user": user_input, "rusty": response})
    save_long_term_memory(long_term)
    return response

# Tkinter GUI
class RustyChatApp:
    def __init__(self, root, model):
        self.root = root
        self.model = model
        self.root.title("Rusty Chat")
        self.chat_log = tk.Text(root, height=20, width=50)
        self.chat_log.pack(padx=10, pady=10)
        self.input_field = ttk.Entry(root, width=50)
        self.input_field.pack(padx=10, pady=5)
        self.send_button = ttk.Button(root, text="Send", command=self.send_message)
        self.send_button.pack(pady=5)
        self.chat_log.insert(tk.END, f"Rusty: {RUSTY_PERSONA.splitlines()[1]}\n")

    def send_message(self):
        user_input = self.input_field.get()
        if user_input:
            self.chat_log.insert(tk.END, f"You: {user_input}\n")
            response = generate_response(user_input, self.model)
            self.chat_log.insert(tk.END, f"Rusty: {response}\n")
            self.input_field.delete(0, tk.END)
            self.chat_log.see(tk.END)

if __name__ == "__main__":
    model = select_model()
    if model:
        root = tk.Tk()
        app = RustyChatApp(root, model)
        root.mainloop()
    else:
        print("No Ollama models available. Please start Ollama server.")