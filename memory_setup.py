import sqlite3
import os

DB_PATH = "persona_memory.db"

def setup_persona_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Separate tables for each persona - fully isolated
    tables = [
        "CREATE TABLE IF NOT EXISTS sarah_memory (id INTEGER PRIMARY KEY, timestamp TEXT, type TEXT, content TEXT, metadata TEXT)",
        "CREATE TABLE IF NOT EXISTS andrew_memory (id INTEGER PRIMARY KEY, timestamp TEXT, type TEXT, content TEXT, metadata TEXT)",
        "CREATE TABLE IF NOT EXISTS gatekeeper_memory (id INTEGER PRIMARY KEY, timestamp TEXT, type TEXT, content TEXT, metadata TEXT)"
    ]
    
    for table in tables:
        cursor.execute(table)
    
    conn.commit()
    conn.close()
    print("Isolated memory tables created successfully for Sarah, Andrew, and Gatekeeper.")
    print("Each table can be dropped independently without affecting the others.")
    return True

if __name__ == "__main__":
    setup_persona_tables()
