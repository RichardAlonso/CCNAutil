import sqlite3
import pandas as pd
from datetime import datetime
import json

DB_PATH = "study_app.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("PRAGMA foreign_keys = ON;")
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_number TEXT UNIQUE,
            question_text TEXT,
            options TEXT, 
            image_path TEXT,
            correct_answer TEXT DEFAULT 'Unknown',
            topic TEXT DEFAULT 'General',
            explanation TEXT,
            question_type TEXT DEFAULT 'standard',
            flagged BOOLEAN DEFAULT 0
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            question_id INTEGER PRIMARY KEY,
            times_correct INTEGER DEFAULT 0,
            times_wrong INTEGER DEFAULT 0,
            last_seen TIMESTAMP,
            next_review_due TIMESTAMP,
            streak INTEGER DEFAULT 0,
            ease_factor REAL DEFAULT 2.5,
            interval_days INTEGER DEFAULT 0,
            FOREIGN KEY(question_id) REFERENCES questions(id)
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_notes (
            question_id INTEGER PRIMARY KEY,
            note_text TEXT,
            FOREIGN KEY(question_id) REFERENCES questions(id)
        )
    ''')

    # NEW: Quarantine zone for bad parsing
    c.execute('''
        CREATE TABLE IF NOT EXISTS parsing_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text TEXT,
            error_reason TEXT,
            source_page INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()

def get_question_stats(topic_filter=None):
    conn = get_db_connection()
    base_query = """
        SELECT 
            q.topic,
            COUNT(q.id) as total_questions,
            SUM(CASE WHEN h.times_correct > 0 THEN 1 ELSE 0 END) as mastered_count,
            SUM(CASE WHEN h.streak > 3 THEN 1 ELSE 0 END) as deep_mastery
        FROM questions q
        LEFT JOIN history h ON q.id = h.question_id
    """
    if topic_filter:
        placeholders = ','.join('?' for _ in topic_filter)
        base_query += f" WHERE q.topic IN ({placeholders})"
        params = topic_filter
    else:
        params = ()
    base_query += " GROUP BY q.topic"
    df = pd.read_sql(base_query, conn, params=params)
    conn.close()
    return df

def get_due_question():
    conn = get_db_connection()
    query = """
        SELECT q.*, h.ease_factor, h.streak, h.interval_days
        FROM questions q 
        JOIN history h ON q.id = h.question_id 
        WHERE h.next_review_due <= ? 
        ORDER BY h.next_review_due ASC LIMIT 1
    """
    row = conn.execute(query, (datetime.now(),)).fetchone()
    conn.close()
    return dict(row) if row else None

# --- FIXED: Logic moved inside function ---
def update_history(q_id, is_correct, sm2_data):
    conn = get_db_connection()
    now = datetime.now()
    
    # Check if exists
    exists = conn.execute("SELECT 1 FROM history WHERE question_id = ?", (q_id,)).fetchone()
    
    if exists:
        if is_correct:
            sql = '''UPDATE history SET times_correct = times_correct + 1, last_seen = ?, 
                     next_review_due = ?, streak = ?, ease_factor = ?, interval_days = ? 
                     WHERE question_id = ?'''
        else:
            sql = '''UPDATE history SET times_wrong = times_wrong + 1, last_seen = ?, 
                     next_review_due = ?, streak = ?, ease_factor = ?, interval_days = ? 
                     WHERE question_id = ?'''
        
        params = (now, sm2_data['next_due'], sm2_data['repetitions'], 
                  sm2_data['ease_factor'], sm2_data['interval'], q_id)
        conn.execute(sql, params)
    else:
        # First time seeing it
        sql = '''INSERT INTO history (question_id, times_correct, times_wrong, last_seen, 
                 next_review_due, streak, ease_factor, interval_days)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)'''
        tc = 1 if is_correct else 0
        tw = 0 if is_correct else 1
        conn.execute(sql, (q_id, tc, tw, now, sm2_data['next_due'], 
                           sm2_data['repetitions'], sm2_data['ease_factor'], sm2_data['interval']))
    
    conn.commit()
    conn.close()

def get_all_topics():
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT DISTINCT topic FROM questions ORDER BY topic").fetchall()
        return [r['topic'] for r in rows]
    except:
        return []
    finally:
        conn.close()

def get_study_questions(topic="All"):
    conn = get_db_connection()
    try:
        if topic and topic != "All":
            rows = conn.execute("SELECT * FROM questions WHERE topic = ? ORDER BY id", (topic,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# --- NEW: Pagination for Bulk Editor ---
def get_questions_paginated(limit=50, offset=0, topic=None, show_unknown=False):
    conn = get_db_connection()
    query = "SELECT id, question_number, topic, question_text, correct_answer, explanation FROM questions"
    params = []
    conditions = []
    
    if topic and topic != "All":
        conditions.append("topic = ?")
        params.append(topic)
    
    if show_unknown:
        conditions.append("correct_answer LIKE '%Unknown%'")
        
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
        
    query += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    df = pd.read_sql(query, conn, params=params)
    
    # Get total count for pagination UI
    count_query = "SELECT COUNT(*) FROM questions"
    if conditions:
        count_query += " WHERE " + " AND ".join(conditions)
    
    total = conn.execute(count_query, params[:-2]).fetchone()[0]
    
    conn.close()
    return df, total

def update_bulk_questions(updates):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        for u in updates:
            q_id = u.pop("id")
            if not u: continue
            
            set_clause = ", ".join([f"{k} = ?" for k in u.keys()])
            values = list(u.values()) + [q_id]
            cursor.execute(f"UPDATE questions SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    except Exception as e:
        print(f"Bulk Update Error: {e}")
        return False
    finally:
        conn.close()

# --- NEW: Exam Mode Support ---
def get_exam_questions(limit=100):
    """Get random selection of questions for mock exam"""
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM questions ORDER BY RANDOM() LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- NEW: Flagging Support ---
def toggle_flag(q_id, current_status):
    conn = get_db_connection()
    new_status = not current_status
    conn.execute("UPDATE questions SET flagged = ? WHERE id = ?", (new_status, q_id))
    conn.commit()
    conn.close()
    return new_status

# --- NEW: Quarantine Access ---
def get_parsing_errors():
    conn = get_db_connection()
    df = pd.read_sql("SELECT * FROM parsing_errors", conn)
    conn.close()
    return df