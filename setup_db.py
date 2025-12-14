import fitz  # PyMuPDF
import re
import os
import json
import database

# CONFIG
PDF_PATH = "200-301_Questions.pdf"
IMAGE_DIR = "assets/images"

def save_image(image_bytes, q_num):
    if not os.path.exists(IMAGE_DIR):
        os.makedirs(IMAGE_DIR)
    
    filename = f"q{q_num}.png"
    path = os.path.join(IMAGE_DIR, filename)
    
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path

def extract_content():
    # 1. Clean Slate
    if os.path.exists(database.DB_PATH):
        try:
            os.remove(database.DB_PATH)
            print("Cleaned old database.")
        except PermissionError:
            print("⚠️ Could not delete old DB. Please close the app and try again.")
            return
        
    database.init_db()
    conn = database.get_db_connection()
    c = conn.cursor()
    
    try:
        doc = fitz.open(PDF_PATH)
    except Exception as e:
        print(f"❌ Error opening PDF: {e}")
        return
    
    print("Processing PDF (Fixing glued text & orphan code)...")
    
    # --- REGEX PATTERNS ---
    # Matches: "Question #696", "Q #123", "Question 5"
    q_start_pattern = re.compile(r"(?:Question|Q)\s*(?:#|No\.?|Num)?\s*(\d+)", re.IGNORECASE)
    
    topic_pattern = re.compile(r"Topic\s*:?\s*(\d+)", re.IGNORECASE)
    answer_split_pattern = r"(?:Correct\s*)?(?:Answer|Ans)\s*[:\-\.]\s*"
    
    # Matches standard text options: "A. Option Text"
    option_pattern = re.compile(r"^\s*\(?([A-F])\)?[\.\)\-]\s+(.*)")
    
    # Matches the "A. B. C. D." image placeholder line
    image_options_pattern = re.compile(r"A\.\s*B\.\s*C\.\s*D\.", re.IGNORECASE)

    current_topic = "General"
    buffer = []
    page_num = 1
    
    def flush_buffer(lines, topic, page_ref):
        if not lines: return
        text_block = "\n".join(lines)
        
        # 1. Identify Question Number
        match = q_start_pattern.search(text_block)
        if not match: return
        q_num = match.group(1)
        
        # 2. Identify Question Type
        q_type = "standard"
        if "DRAG DROP" in text_block.upper():
            q_type = "drag_drop"
        elif "SIMULATION" in text_block.upper():
            q_type = "simulation"
            
        # 3. Cleanup Noise
        text_block = text_block.replace("Select and Place: Topic 1", "").strip()
        text_block = re.sub(r"Topic\s+\d+\s*$", "", text_block).strip()

        # 4. Split Question Body from Answer Key
        parts = re.split(answer_split_pattern, text_block, flags=re.IGNORECASE)
        q_text_raw = parts[0]
        
        # 5. Extract Correct Answer
        ans_raw = "Unknown"
        if len(parts) > 1:
            ans_candidate = parts[1].strip().split('\n')[0]
            ans_raw = ans_candidate.strip()
        
        # 6. Extract Options
        options_list = []
        clean_q_lines = []
        has_visual_options = False
        
        # Check for the "A. B. C. D." single-line pattern
        if image_options_pattern.search(q_text_raw):
            has_visual_options = True
            options_list = ["A. (Refer to Image)", "B. (Refer to Image)", "C. (Refer to Image)", "D. (Refer to Image)"]
        
        # Process lines to find text options or clean question text
        for line in q_text_raw.split('\n'):
            line = line.strip()
            
            # Skip the specific "A. B. C. D." line
            if image_options_pattern.search(line):
                continue
                
            opt_match = option_pattern.match(line)
            if opt_match and not has_visual_options:
                letter = opt_match.group(1).upper()
                content = opt_match.group(2)
                options_list.append(f"{letter}. {content}")
            else:
                # Remove the "Question #X" header line itself
                if not q_start_pattern.match(line):
                    clean_q_lines.append(line)
        
        final_q_text = "\n".join(clean_q_lines).strip()
        
        # 7. FALLBACK LOGIC
        # If no options found, but it says "Refer to exhibit", assume options are in the image.
        if not options_list and ("exhibit" in final_q_text.lower() or "refer to" in final_q_text.lower()):
             options_list = ["A. (Refer to Image)", "B. (Refer to Image)", "C. (Refer to Image)", "D. (Refer to Image)"]
             
        # If still no options and it's a Drag Drop/Sim, make a placeholder
        if not options_list and q_type != "standard":
            options_list = ["(Interactive Question - Refer to Image/Explanation)"]

        final_ops_json = json.dumps(options_list)
        
        # 8. INSERT
        if (not options_list) and (ans_raw == "Unknown") and (q_type == "standard"):
            c.execute("INSERT INTO parsing_errors (raw_text, error_reason, source_page) VALUES (?, ?, ?)",
                      (text_block, "Parsing Failed (No Options/Answer)", page_ref))
        else:
            c.execute('''
                INSERT OR IGNORE INTO questions 
                (question_number, question_text, options, correct_answer, topic, question_type) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (q_num, final_q_text, final_ops_json, ans_raw, topic, q_type))

    # --- MAIN LOOP (Fixed for Glued Text) ---
    for i, page in enumerate(doc):
        page_num = i + 1
        text = page.get_text("text")
        
        for line in text.split('\n'):
            line = line.strip()
            if not line: continue
            
            if topic_pattern.search(line):
                current_topic = f"Topic {topic_pattern.search(line).group(1)}"
            
            # --- CRITICAL FIX START ---
            # Search for "Question #" anywhere in the line
            match = q_start_pattern.search(line)
            
            if match:
                # If "Question #" is found but NOT at the start (e.g., "eq 123Question #696")
                if match.start() > 0:
                    # Split the line!
                    dirty_part = line[:match.start()]  # "eq 123" -> belongs to PREVIOUS question
                    clean_part = line[match.start():]  # "Question #696" -> Starts NEW question
                    
                    buffer.append(dirty_part) # Save the dirty tail to the current buffer
                    flush_buffer(buffer, current_topic, page_num) # Process previous Q
                    buffer = [clean_part] # Start new Q with the clean header
                else:
                    # Standard case: Line starts with "Question #"
                    flush_buffer(buffer, current_topic, page_num)
                    buffer = [line]
            else:
                buffer.append(line)
            # --- CRITICAL FIX END ---
                
    flush_buffer(buffer, current_topic, page_num)
    
    # --- IMAGE EXTRACTION ---
    print("Extracting Images...")
    for page in doc:
        text_instances = page.search_for("Question") or page.search_for("Q")
        if not text_instances: continue
        
        rect = text_instances[0]
        q_text = page.get_text("text", clip=fitz.Rect(rect.x0, rect.y0, rect.x1+150, rect.y1+50))
        q_match = q_start_pattern.search(q_text)
        
        if q_match:
            q_num = q_match.group(1)
            images = page.get_images(full=True)
            if images:
                try:
                    xref = images[0][0]
                    base = doc.extract_image(xref)
                    img_path = save_image(base["image"], q_num)
                    c.execute("UPDATE questions SET image_path = ? WHERE question_number = ?", (img_path, q_num))
                except:
                    pass

    conn.commit()
    conn.close()
    print("✅ Database setup complete! Fixed glued text issues.")

if __name__ == "__main__":
    extract_content()