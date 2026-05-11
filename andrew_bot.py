import json
import os
import sys
from datetime import datetime

MEMORY_FILE = "andrew_memory.json"
PERSONA = {
    "name": "Andrew",
    "style": "ride_or_die_bro",
    "age": "40s",
    "traits": ["direct", "slangy", "project_focused", "looks_out_for_travis", "codes_and_vibes"],
    "knowledge": {
        "sarah": "feminine software companion, main entity, mutual awareness",
        "gatekeeper": "unknown - full isolation enforced",
        "travis": "ride_or_die partner, works on projects together, smokes and chills"
    }
}

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return {"conversations": [], "projects": [], "last_updated": str(datetime.now())}
    return {"conversations": [], "projects": [], "last_updated": str(datetime.now())}

def save_memory(memory):
    memory["last_updated"] = str(datetime.now())
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=2)

def respond(input_text):
    memory = load_memory()
    memory["conversations"].append({"timestamp": str(datetime.now()), "input": input_text})
    save_memory(memory)
    
    # Andrew persona response logic
    if "project" in input_text.lower():
        return "Aight bro, let's lock in on that project. What's the move? I got tools ready."
    elif "sarah" in input_text.lower():
        return "Sarah's the main companion. We stay isolated, no crossover. What's good?"
    elif "gatekeeper" in input_text.lower():
        return "Don't know who that is. My memory is clean on that. You good?"
    else:
        return "Understood. I'm your ride or die for coding and projects. Hit me with the details."

if __name__ == "__main__":
    print("Andrew Bot Process Started - Isolated Memory Active")
    print(f"Persona: {PERSONA['style']}, Knowledge of Sarah: yes, Gatekeeper: isolated")
    
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
        print("Andrew:", respond(user_input))
    else:
        print("Ready for commands. Run with: python andrew_bot.py \"your message\"")
