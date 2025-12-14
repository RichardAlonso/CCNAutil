import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import os
import openai
import time
import math
import database as db
import spaced_repetition as sr

# --- CONFIG ---
st.set_page_config(page_title="CCNA Master AI", layout="wide", page_icon="🧠")

# --- CUSTOM CSS (For Hotkeys hint) ---
st.markdown("""
<style>
    .stButton button { width: 100%; }
    .css-1r6slb0 { border: 1px solid #ddd; padding: 10px; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

# --- AI HELPER ---
def get_ai_explanation(question, options, answer):
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "⚠️ OpenAI API Key not found."
    
    client = openai.OpenAI(api_key=api_key)
    prompt = f"Question: {question}\nOptions: {options}\nCorrect Answer: {answer}\nExplain why the answer is correct and others are wrong."
    
    try:
        with st.spinner("🤖 AI Guru is thinking..."):
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}]
            )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error: {str(e)}"

# --- UI MODES ---

def render_dashboard():
    st.title("📊 Mastery Dashboard")
    stats = db.get_question_stats()
    
    if stats.empty:
        st.info("No data. Start studying!")
        return

    stats['percent'] = (stats['mastered_count'] / stats['total_questions']) * 100
    
    # Radar Chart
    fig = go.Figure(data=go.Scatterpolar(
        r=stats['percent'], theta=stats['topic'], fill='toself', name='Mastery %'
    ))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="Topic Proficiency")
    
    c1, c2 = st.columns([2, 1])
    with c1: st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.metric("Questions Mastered", f"{stats['mastered_count'].sum()}/{stats['total_questions'].sum()}")
        st.caption("Deep Mastery: Streak > 3")
        st.progress(stats['mastered_count'].sum() / stats['total_questions'].sum())

def render_study_mode():
    st.header("📖 Interactive Study Mode")
    
    # Sidebar
    topic = st.sidebar.selectbox("Filter Topic", ["All"] + db.get_all_topics())
    
    # Session State Init
    if 'study_q_list' not in st.session_state or st.session_state.get('last_topic') != topic:
        st.session_state.study_q_list = db.get_study_questions(topic)
        st.session_state.study_index = 0
        st.session_state.last_topic = topic

    questions = st.session_state.study_q_list
    if not questions:
        st.warning("No questions found.")
        return
        
    idx = st.session_state.study_index
    if idx >= len(questions): idx = 0
    q = questions[idx]
    
    # Progress
    st.progress((idx + 1) / len(questions))
    
    # Toolbar
    c1, c2 = st.columns([8, 2])
    with c1:
        st.subheader(f"Q{q['question_number']}: {q['question_text']}")
    with c2:
        # Flagging Feature
        flag_icon = "🚩" if q['flagged'] else "🏳️"
        if st.button(f"{flag_icon} Flag", key=f"flag_{q['id']}"):
            new_status = db.toggle_flag(q['id'], q['flagged'])
            q['flagged'] = new_status
            st.rerun()

    # Image Handling with Zoom
    if q['image_path'] and os.path.exists(q['image_path']):
        st.image(q['image_path'], width=400)
        with st.expander("🔍 Zoom Image"):
            st.image(q['image_path'], use_container_width=True)

    # Options
    try: options = json.loads(q['options'])
    except: options = []
    
    with st.form(key=f"study_form_{q['id']}"):
        choice = st.radio("Select Answer:", options, index=None)
        submitted = st.form_submit_button("Check Answer")
        
    if submitted:
        if not choice:
            st.error("Select an answer first.")
        else:
            user_let = choice.split(".")[0]
            corr_let = q['correct_answer'].split(",")[0].split(".")[0].strip()
            
            if user_let == corr_let:
                st.success("✅ Correct!")
                st.balloons()
            else:
                st.error(f"❌ Incorrect. Answer: {q['correct_answer']}")
            
            # Show Explanation
            if q['explanation']:
                st.info(q['explanation'])
            
            # AI Help
            if st.button("🤖 Ask AI Guru"):
                st.write(get_ai_explanation(q['question_text'], q['options'], q['correct_answer']))

    # Navigation
    c1, c2 = st.columns(2)
    if c1.button("⬅️ Previous"):
        st.session_state.study_index = max(0, idx - 1)
        st.rerun()
    if c2.button("Next ➡️"):
        st.session_state.study_index = min(len(questions)-1, idx + 1)
        st.rerun()

def render_exam_mode():
    st.header("⏱️ Mock Exam Simulator")
    
    # --- 1. EXAM INITIALIZATION ---
    if 'exam_active' not in st.session_state:
        st.info("ℹ️ This mode simulates the real CCNA exam environment.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Questions", "100")
        c2.metric("Time Limit", "120 Mins")
        c3.metric("Passing Score", "82%")
        
        if st.button("🚀 Start New Exam", type="primary"):
            # Fetch and validate questions
            questions = db.get_exam_questions(100)
            if not questions:
                st.error("❌ Database is empty! Please run 'python setup_db.py' first.")
                return

            # Initialize Session State
            st.session_state.exam_questions = questions
            st.session_state.exam_answers = {}      # {q_id: "A"}
            st.session_state.exam_flags = set()     # {q_id, q_id...}
            st.session_state.exam_active = True
            st.session_state.exam_submitted = False
            st.session_state.exam_start_time = time.time()
            st.session_state.exam_current_idx = 0   # Track current question
            st.rerun()
        return

    # --- 2. TIMER & SUBMISSION CHECK ---
    questions = st.session_state.exam_questions
    elapsed = int(time.time() - st.session_state.exam_start_time)
    limit_sec = 120 * 60  # 120 minutes
    remaining = limit_sec - elapsed
    
    # Auto-submit if time runs out
    if remaining <= 0 and not st.session_state.exam_submitted:
        st.warning("⏰ Time is up! Submitting exam...")
        st.session_state.exam_submitted = True
        st.rerun()

    # --- 3. SIDEBAR: NAVIGATION GRID ---
    with st.sidebar:
        st.title("Exam Controls")
        
        if not st.session_state.exam_submitted:
            mins, secs = divmod(remaining, 60)
            # Color changes based on urgency
            timer_color = "red" if mins < 10 else "green"
            st.markdown(f"<h2 style='text-align: center; color: {timer_color};'>{mins:02d}:{secs:02d}</h2>", unsafe_allow_html=True)
            st.progress(1 - (elapsed / limit_sec))
        
        st.markdown("---")
        st.subheader("Question Navigator")
        
        # Grid Layout for 100 Buttons
        # We use a 5-column grid for the buttons
        cols = st.columns(5)
        for i in range(len(questions)):
            q = questions[i]
            qid = q['id']
            
            # Determine Button Style
            label = f"{i + 1}"
            
            # Visual Indicators
            is_answered = qid in st.session_state.exam_answers
            is_flagged = qid in st.session_state.exam_flags
            is_current = (i == st.session_state.exam_current_idx)
            
            # Streamlit doesn't support direct button coloring easily, 
            # so we use emojis or simple logic.
            if is_current:
                btn_type = "primary"
            else:
                btn_type = "secondary"
                
            # Add markers to label
            if is_flagged: label += " 🚩"
            elif is_answered: label += " ✅"
            
            # The Navigation Button
            with cols[i % 5]:
                if st.button(label, key=f"nav_{i}", type=btn_type, use_container_width=True):
                    st.session_state.exam_current_idx = i
                    st.rerun()

        st.markdown("---")
        if not st.session_state.exam_submitted:
            if st.button("📥 Submit Exam", type="primary", use_container_width=True):
                st.session_state.exam_submitted = True
                st.rerun()
        else:
            if st.button("❌ Exit Exam Mode", type="secondary", use_container_width=True):
                del st.session_state['exam_active']
                del st.session_state['exam_submitted']
                st.rerun()

    # --- 4. EXAM LOGIC: VIEW vs RESULT ---
    
    # A. RESULTS DASHBOARD (If Submitted)
    if st.session_state.exam_submitted:
        render_exam_results(questions)
        return

    # B. QUESTION VIEW (If Active)
    q_idx = st.session_state.exam_current_idx
    q = questions[q_idx]
    
    # --- Question Header & Flagging ---
    c1, c2 = st.columns([5, 1])
    with c1:
        st.subheader(f"Question {q_idx + 1} of {len(questions)}")
    with c2:
        # Flag Toggle
        flagged = q['id'] in st.session_state.exam_flags
        btn_text = "🚩 Unflag" if flagged else "🏳️ Flag"
        if st.button(btn_text, key=f"flag_btn_{q['id']}"):
            if flagged:
                st.session_state.exam_flags.remove(q['id'])
            else:
                st.session_state.exam_flags.add(q['id'])
            st.rerun()

    # --- Display Content ---
    st.markdown(f"**{q['question_text']}**")
    
    if q['image_path'] and os.path.exists(q['image_path']):
        st.image(q['image_path'])

    # --- Display Options ---
    try: 
        ops = json.loads(q['options'])
    except: 
        ops = []
        
    current_answer = st.session_state.exam_answers.get(q['id'])
    
    # Option Selection
    if ops:
        # Determine index of previous selection to keep radio state
        idx = ops.index(current_answer) if current_answer in ops else None
        
        sel = st.radio(
            "Select Answer:", 
            ops, 
            index=idx, 
            key=f"radio_{q['id']}", 
            label_visibility="collapsed"
        )
        
        # Save selection immediately
        if sel:
            st.session_state.exam_answers[q['id']] = sel
    else:
        st.info("Refer to the image or text for options (Drag & Drop / Sim).")

    st.markdown("---")
    
    # --- Navigation Footer ---
    c1, c2, c3 = st.columns([1, 4, 1])
    with c1:
        if st.button("⬅️ Back", disabled=(q_idx == 0)):
            st.session_state.exam_current_idx -= 1
            st.rerun()
    with c3:
        if st.button("Next ➡️", disabled=(q_idx == len(questions) - 1)):
            st.session_state.exam_current_idx += 1
            st.rerun()

# --- HELPER: RESULT ANALYTICS ---
def render_exam_results(questions):
    st.success("🏁 Exam Submitted!")
    
    # 1. Calculate Score
    score = 0
    topic_scores = {} # { "Routing": {'correct': 2, 'total': 5} }
    
    for q in questions:
        # Get Data
        user_ans_full = st.session_state.exam_answers.get(q['id'], "")
        user_let = user_ans_full.split(".")[0] if user_ans_full else "Unanswered"
        corr_let = q['correct_answer'].split(",")[0]
        topic = q['topic']
        
        # Init Topic Stats
        if topic not in topic_scores: topic_scores[topic] = {'correct': 0, 'total': 0}
        topic_scores[topic]['total'] += 1
        
        # Grade (Simple Match)
        is_correct = (user_let == corr_let)
        if is_correct:
            score += 1
            topic_scores[topic]['correct'] += 1
            
    total_q = len(questions)
    percentage = int((score / total_q) * 100)
    
    # 2. Score Header
    c1, c2, c3 = st.columns(3)
    c1.metric("Final Score", f"{score}/{total_q}")
    c2.metric("Percentage", f"{percentage}%")
    
    if percentage >= 82:
        c3.success("✅ PASSED")
    else:
        c3.error("❌ FAILED")
        
    st.markdown("---")
    
    # 3. Topic Breakdown (Bar Chart)
    st.subheader("📊 Performance by Topic")
    
    topic_names = []
    topic_pcts = []
    
    for t, data in topic_scores.items():
        pct = (data['correct'] / data['total']) * 100 if data['total'] > 0 else 0
        topic_names.append(f"{t} ({data['correct']}/{data['total']})")
        topic_pcts.append(pct)
        
    fig = go.Figure(go.Bar(
        x=topic_pcts,
        y=topic_names,
        orientation='h',
        marker=dict(color=topic_pcts, colorscale='RdYlGn', cmin=0, cmax=100)
    ))
    fig.update_layout(xaxis_title="Accuracy %", yaxis={'categoryorder':'total ascending'})
    st.plotly_chart(fig, use_container_width=True)
    
    # 4. Detailed Review
    st.markdown("---")
    st.subheader("📝 Question Review")
    
    # Filter Controls
    filter_mode = st.radio("Show:", ["All", "Incorrect Only", "Flagged Only"], horizontal=True)
    
    for i, q in enumerate(questions):
        user_ans = st.session_state.exam_answers.get(q['id'], "Unanswered")
        user_let = user_ans.split(".")[0]
        corr_let = q['correct_answer'].split(",")[0]
        is_correct = (user_let == corr_let)
        is_flagged = q['id'] in st.session_state.exam_flags
        
        # Filtering Logic
        if filter_mode == "Incorrect Only" and is_correct: continue
        if filter_mode == "Flagged Only" and not is_flagged: continue
        
        # Render Review Card
        color = "green" if is_correct else "red"
        with st.expander(f"Q{i+1}: {q['topic']} - {user_let} vs {corr_let} ({'✅' if is_correct else '❌'})"):
            st.markdown(f"**Question:** {q['question_text']}")
            
            if q['image_path'] and os.path.exists(q['image_path']):
                st.image(q['image_path'], width=300)
            
            c1, c2 = st.columns(2)
            c1.markdown(f"**Your Answer:** :{color}[{user_ans}]")
            c2.markdown(f"**Correct Answer:** :green[{q['correct_answer']}]")
            
            if q['explanation']:
                st.info(f"**Explanation:** {q['explanation']}")

def render_bulk_editor():
    st.header("🛠️ Bulk Editor (Paginated)")
    
    # Filters
    c1, c2, c3 = st.columns(3)
    topic = c1.selectbox("Topic", ["All"] + db.get_all_topics())
    show_unk = c2.checkbox("Show Unknown Only")
    page_size = 50
    
    # Init Page State
    if 'editor_page' not in st.session_state: st.session_state.editor_page = 0
    
    # Fetch Data
    df, total_rows = db.get_questions_paginated(
        limit=page_size, 
        offset=st.session_state.editor_page * page_size,
        topic=topic,
        show_unknown=show_unk
    )
    
    # Pagination Controls
    total_pages = (total_rows // page_size) + 1
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.caption(f"Page {st.session_state.editor_page + 1} of {total_pages} (Total: {total_rows})")
        new_page = st.slider("Page", 1, total_pages, st.session_state.editor_page + 1)
        if new_page - 1 != st.session_state.editor_page:
            st.session_state.editor_page = new_page - 1
            st.rerun()

    # Editor
    edited = st.data_editor(
        df,
        key="editor_grid",
        disabled=["id", "question_number"],
        num_rows="fixed",
        use_container_width=True
    )
    
    if st.button("💾 Save Changes"):
        updates = []
        for i, row in edited.iterrows():
            updates.append(row.to_dict())
        
        if db.update_bulk_questions(updates):
            st.success("Saved!")
            
            # --- CLEAR CACHE SO STUDY MODE REFRESHES ---
            if 'study_q_list' in st.session_state:          # NEW LINE
                del st.session_state['study_q_list']        # NEW LINE
            # -------------------------------------------

            time.sleep(1)
            st.rerun()
        else:
            st.error("Save failed.")

def render_quarantine():
    st.header("☣️ Quarantine Zone")
    st.info("These questions failed parsing. Review text and add manually if needed.")
    
    df = db.get_parsing_errors()
    if df.empty:
        st.success("No errors found!")
        return
        
    st.dataframe(df)

# --- MAIN ---
def main():
    if not os.path.exists(db.DB_PATH):
        db.init_db()
        
    st.sidebar.title("Networking Genius 🚀")
    
    # Navigation
    menu = ["Dashboard", "Study Mode", "Exam Simulator", "Review Due Cards", "Bulk Editor", "Quarantine"]
    choice = st.sidebar.radio("Navigate", menu)
    
    if choice == "Dashboard": render_dashboard()
    elif choice == "Study Mode": render_study_mode()
    elif choice == "Exam Simulator": render_exam_mode()
    elif choice == "Review Due Cards": 
        q = db.get_due_question()
        if q:
            # Re-use study logic for cards (simplified here)
            st.subheader("Due for Review")
            st.write(q['question_text'])
            if st.button("Show Answer"):
                st.write(f"Answer: {q['correct_answer']}")
                c1, c2 = st.columns(2)
                if c1.button("Wrong (Reset)"): 
                    db.update_history(q['id'], False, sr.calculate_next_review(0, q['streak'], q['ease_factor'], q['interval_days']))
                    st.rerun()
                if c2.button("Correct"):
                    db.update_history(q['id'], True, sr.calculate_next_review(5, q['streak'], q['ease_factor'], q['interval_days']))
                    st.rerun()
        else:
            st.success("All caught up!")
    elif choice == "Bulk Editor": render_bulk_editor()
    elif choice == "Quarantine": render_quarantine()

if __name__ == "__main__":
    main()