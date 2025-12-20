import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re

# ==========================================
# ⚙️ 0. Page Config & CSS Styling
# ==========================================
st.set_page_config(page_title="Auto Scheduler Pro", layout="wide", page_icon="🎓")

# CSS สำหรับตกแต่งตารางสอนให้สวยงาม
st.markdown("""
<style>
    .schedule-container {
        display: grid;
        grid-template-columns: 80px repeat(11, 1fr); /* Time labels + 11 slots */
        gap: 2px;
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 8px;
        overflow-x: auto;
    }
    .header-cell {
        background-color: #262730;
        color: white;
        padding: 8px;
        text-align: center;
        font-weight: bold;
        border-radius: 4px;
        font-size: 0.85em;
    }
    .day-cell {
        background-color: #262730;
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        border-radius: 4px;
    }
    .class-card {
        padding: 6px;
        border-radius: 4px;
        font-size: 0.8em;
        line-height: 1.2;
        color: white;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        overflow: hidden;
        transition: transform 0.1s;
    }
    .class-card:hover {
        transform: scale(1.02);
        z-index: 10;
    }
    .type-Lec { background-color: #4CAF50; border-left: 4px solid #2E7D32; }
    .type-Lab { background-color: #2196F3; border-left: 4px solid #1565C0; }
</style>
""", unsafe_allow_html=True)

st.title("🎓 Automatic Course Scheduler (Refactored)")

# ==========================================
# 📂 1. Data Management Section
# ==========================================
def render_data_upload_section():
    st.info("📂 **Step 1: Data Preparation** | Please upload the required CSV files below.")
    
    # สร้าง Dictionary เพื่อเก็บไฟล์ที่อัปโหลด
    uploaded_data = {}
    
    # รายชื่อไฟล์ที่ต้องการ (Label, Key Map)
    file_configs = [
        ("1. Room Data (room.csv)", "df_room"),
        ("2. Teachers List (all_teachers.csv)", "all_teacher"),
        ("3. Teacher Courses (teacher_courses.csv)", "df_teacher_courses"),
        ("4. AI Courses IN (ai_in.csv)", "df_ai_in"),
        ("5. Cyber Courses IN (cy_in.csv)", "df_cy_in"),
        ("6. AI Courses OUT (ai_out.csv)", "df_ai_out"),
        ("7. Cyber Courses OUT (cy_out.csv)", "df_cy_out"),
    ]

    # จัดวาง Layout แบบ Grid (4 แถว 2 คอลัมน์)
    cols = st.columns(2)
    for i, (label, key) in enumerate(file_configs):
        with cols[i % 2]:
            file = st.file_uploader(label, type=['csv'], key=key)
            if file:
                uploaded_data[key] = pd.read_csv(file)
            else:
                # Default Logic (ถ้าไม่อัปโหลด ให้ลองหาไฟล์ Default)
                try:
                    # ⚠️ แก้ Path ตรงนี้ให้ตรงกับโฟลเดอร์จริงของคุณ
                    default_path = f"Web_schedule-main/Web_schedule-main/{label.split('(')[1].replace(')', '')}"
                    # uploaded_data[key] = pd.read_csv(default_path) # Uncomment บรรทัดนี้ถ้าจะใช้ Default
                    pass
                except:
                    pass
    
    # ตรวจสอบว่าครบไหม
    missing = [k for _, k in file_configs if k not in uploaded_data]
    
    if not missing:
        st.success(f"✅ All {len(uploaded_data)} datasets loaded successfully!")
        return uploaded_data
    else:
        st.warning(f"⚠️ Missing files: {len(missing)} files. Please upload to proceed.")
        return None

# ==========================================
# 🧠 2. Solver Logic (Core)
# ==========================================
def run_solver(data, config):
    # Unpack Data
    df_room = data['df_room']
    df_teacher_courses = data['df_teacher_courses']
    df_ai_in = data['df_ai_in']
    df_cy_in = data['df_cy_in']
    all_teacher = data['all_teacher']
    df_ai_out = data['df_ai_out']
    df_cy_out = data['df_cy_out']

    # --- 2.1 Time Slot Config ---
    SLOT_MAP = {}
    t_start = 8.5 # เริ่ม 08:30 (Slot 0) -> จบ 19:00
    idx = 0
    while t_start < 19.0:
        h = int(t_start)
        m = int((t_start - h) * 60)
        time_str = f"{h:02d}:{m:02d}"
        SLOT_MAP[idx] = {'time': time_str, 'val': t_start, 'is_lunch': (12.0 <= t_start < 13.0)}
        idx += 1
        t_start += 0.5
    
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    
    # --- 2.2 Pre-processing ---
    # (Clean Columns)
    for df in [df_room, df_teacher_courses, df_ai_in, df_cy_in, all_teacher, df_ai_out, df_cy_out]:
        df.columns = df.columns.str.strip()

    # (Merge Courses)
    df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
    
    # (Teacher Map)
    teacher_map = {}
    df_teacher_courses['course_code'] = df_teacher_courses['course_code'].astype(str).str.strip()
    for _, row in df_teacher_courses.iterrows():
        c = row['course_code']
        t = str(row['teacher_id']).strip()
        if c not in teacher_map: teacher_map[c] = []
        teacher_map[c].append(t)

    # (Room List)
    room_list = df_room.to_dict('records')
    room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

    # (Task Generation)
    tasks = []
    MAX_LEC_SESSION = 6 # Max contiguous slots
    
    for _, row in df_courses.iterrows():
        c_code = str(row['course_code']).strip()
        sec = row['section']
        # คำนวณ Slot (1 ชั่วโมง = 2 slots)
        lec_dur = int(math.ceil(row.get('lecture_hour', 0) * 2))
        lab_dur = int(math.ceil(row.get('lab_hour', 0) * 2))
        teachers = teacher_map.get(c_code, ['Unknown'])
        
        # Split Lec
        curr_lec = lec_dur
        p = 1
        while curr_lec > 0:
            dur = min(curr_lec, MAX_LEC_SESSION)
            tasks.append({
                'uid': f"{c_code}_S{sec}_L_P{p}", 'id': c_code, 'sec': sec, 'type': 'Lec',
                'dur': dur, 'std': row.get('enrollment_count', 30), 'teachers': teachers,
                'is_online': (row.get('lec_online', 0) == 1),
                'req_ai': False, 'req_net': False
            })
            curr_lec -= dur
            p += 1
            
        # Lab
        if lab_dur > 0:
            tasks.append({
                'uid': f"{c_code}_S{sec}_Lb", 'id': c_code, 'sec': sec, 'type': 'Lab',
                'dur': lab_dur, 'std': row.get('enrollment_count', 30), 'teachers': teachers,
                'is_online': (row.get('lab_online', 0) == 1),
                'req_ai': (row.get('require_lab_ai', 0) == 1),
                'req_net': (row.get('require_lab_network', 0) == 1)
            })

    # --- 2.3 Constraint Programming Model ---
    model = cp_model.CpModel()
    schedule = {}  # (uid, room, day, start_slot) -> BoolVar
    is_scheduled = {} 
    
    # Variables & Constraints
    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
        
        # Filter Valid Rooms
        valid_rooms = []
        for r in room_list:
            if t['is_online'] and r['room'] != 'Online': continue
            if not t['is_online'] and r['room'] == 'Online': continue
            if r['room'] != 'Online' and r['capacity'] < t['std']: continue
            if t['type'] == 'Lab' and 'lab' not in str(r.get('type','')).lower(): continue
            valid_rooms.append(r)

        # Create Slot Vars
        candidates = []
        for r in valid_rooms:
            for d in range(len(DAYS)): # 0-4
                for s in range(TOTAL_SLOTS - t['dur'] + 1):
                    # Time Constraints
                    s_val = SLOT_MAP[s]['val']
                    e_val = SLOT_MAP[s + t['dur'] - 1]['val'] + 0.5
                    
                    if config['MODE'] == 1: # Compact (09:00-16:00)
                        if s_val < 9.0 or e_val > 16.0: continue
                    
                    # Lunch Avoidance
                    overlap_lunch = False
                    for k in range(t['dur']):
                        if SLOT_MAP[s+k]['is_lunch']: overlap_lunch = True
                    if overlap_lunch: continue
                    
                    # Variable Creation
                    var = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                    schedule[(uid, r['room'], d, s)] = var
                    candidates.append(var)
        
        # Must be scheduled once
        if candidates:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
        else:
            model.Add(is_scheduled[uid] == 0)

    # Conflict: Room Overlap
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in room_list:
                if r['room'] == 'Online': continue
                # Find all tasks active at this time/room
                active_vars = []
                for t in tasks:
                    for k in range(t['dur']):
                        if s - k >= 0:
                            key = (t['uid'], r['room'], d, s - k)
                            if key in schedule: active_vars.append(schedule[key])
                if active_vars:
                    model.Add(sum(active_vars) <= 1)

    # Conflict: Teacher Overlap
    all_teachers_set = set(tea for t in tasks for tea in t['teachers'])
    for tea in all_teachers_set:
        if tea == 'Unknown': continue
        for d in range(len(DAYS)):
            for s in range(TOTAL_SLOTS):
                active_vars = []
                for t in tasks:
                    if tea in t['teachers']:
                        for r in room_list:
                            for k in range(t['dur']):
                                if s - k >= 0:
                                    key = (t['uid'], r['room'], d, s - k)
                                    if key in schedule: active_vars.append(schedule[key])
                if active_vars:
                    model.Add(sum(active_vars) <= 1)

    # Objective: Maximize Scheduled Classes
    model.Maximize(sum(is_scheduled.values()))

    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config['TIMEOUT']
    status = solver.Solve(model)

    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for t in tasks:
            uid = t['uid']
            if uid in is_scheduled and solver.Value(is_scheduled[uid]):
                # Find where it was scheduled
                for (tid, r, d, s), var in schedule.items():
                    if tid == uid and solver.Value(var):
                        start_time = SLOT_MAP[s]['time']
                        end_slot_idx = s + t['dur']
                        end_time = SLOT_MAP.get(end_slot_idx, {'time': '19:00'})['time']
                        
                        results.append({
                            'Day': DAYS[d],
                            'Start': start_time,
                            'End': end_time,
                            'StartVal': SLOT_MAP[s]['val'], # Numeric for sorting
                            'Duration': t['dur'], # in slots (0.5 hr units)
                            'Room': r,
                            'Course': t['id'],
                            'Sec': t['sec'],
                            'Type': t['type'],
                            'Teachers': ", ".join(t['teachers'])
                        })
                        break
    return pd.DataFrame(results)

# ==========================================
# 🎨 3. Visualization Helper (HTML Timetable)
# ==========================================
def generate_html_timetable(df, title):
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    # Time headers: 08:00 to 19:00
    times = list(range(8, 20)) 
    
    html = f"<h4 style='color:#333;'>📅 {title}</h4>"
    html += "<div class='schedule-container'>"
    
    # Header Row
    html += "<div></div>" # Top-left corner empty
    for t in times[:-1]: # 8 to 18
        html += f"<div class='header-cell' style='grid-column: span 1;'>{t:02d}:00</div>"
    
    # Rows for each Day
    for day in days:
        html += f"<div class='day-cell'>{day}</div>"
        
        # Filter tasks for this day
        day_tasks = df[df['Day'] == day].copy()
        
        # Grid placement logic
        # Grid starts at col 2 (col 1 is day label). 
        # 08:00 is col 2. Each hour is 1 column width? 
        # Better: Each hour is 2 slots. Let's simplify: Grid columns represent HOURS.
        # If class starts 09:30, we need finer grid. 
        # Let's make grid columns represent 30 mins.
        # 08:00 -> 19:00 is 11 hours = 22 slots.
        
        # Re-adjusting CSS for 30-min slots might be too wide. 
        # Let's use `left: %` and `width: %` relative to a container track instead of Grid for cells.
        # But for stability, let's stick to the current Grid of 1-hour slots and use margins for half-hours.
        
        # Creating a relative container for the day's timeline
        html += f"<div style='grid-column: 2 / span 11; position: relative; height: 60px; background: #fff; border-bottom: 1px solid #eee;'>"
        
        for _, row in day_tasks.iterrows():
            # Calculate position
            # Start 8.0 -> 0%
            # End 19.0 -> 100%
            # Total duration 11 hours
            start_offset = row['StartVal'] - 8.0
            duration_hr = row['Duration'] * 0.5
            
            left_pct = (start_offset / 11.0) * 100
            width_pct = (duration_hr / 11.0) * 100
            
            color_class = f"type-{row['Type']}"
            
            html += f"""
            <div class='class-card {color_class}' style='
                position: absolute; 
                left: {left_pct}%; 
                width: {width_pct}%; 
                top: 2px; bottom: 2px;
                font-size: 11px;
                overflow: hidden;
                z-index: 5;
            ' title='{row['Course']} ({row['Type']}) - {row['Room']}'>
                <b>{row['Course']}</b> ({row['Type']})<br>
                {row['Room']}
            </div>
            """
        html += "</div>"
    
    html += "</div>"
    return html

# ==========================================
# 🚀 Main Application Flow
# ==========================================

# Tabs Structure
tab1, tab2, tab3 = st.tabs(["1️⃣ Upload Data", "2️⃣ Settings & Run", "3️⃣ Results"])

with tab1:
    data_store = render_data_upload_section()

with tab2:
    st.header("⚙️ Configuration")
    
    c1, c2 = st.columns(2)
    with c1:
        mode = st.radio("Schedule Mode", [1, 2], 
                        format_func=lambda x: "Compact (09:00-16:00)" if x==1 else "Flexible (08:30-19:00)")
    with c2:
        timeout = st.slider("Max Calculation Time (seconds)", 10, 600, 60)
    
    st.divider()
    
    if st.button("🚀 Generate Schedule", type="primary"):
        if data_store:
            with st.spinner("🤖 AI is scheduling courses..."):
                config = {'MODE': mode, 'TIMEOUT': timeout}
                df_results = run_solver(data_store, config)
                
                if not df_results.empty:
                    st.session_state['schedule'] = df_results
                    st.success("✅ Schedule Generated Successfully! Go to the Results tab.")
                else:
                    st.error("❌ Could not find a feasible schedule. Try increasing time limits or relaxing constraints.")
        else:
            st.error("Please upload data in Tab 1 first.")

with tab3:
    if 'schedule' in st.session_state and not st.session_state['schedule'].empty:
        df = st.session_state['schedule']
        
        # Control Bar
        col_view, col_select = st.columns([1, 2])
        
        with col_view:
            view_type = st.radio("View Mode:", ["Room View", "Teacher View"], horizontal=True)
            
        with col_select:
            if view_type == "Room View":
                options = sorted(df['Room'].unique())
                selected = st.selectbox("Select Room:", options)
                df_filtered = df[df['Room'] == selected]
            else:
                # Extract unique teachers
                all_t = set()
                for t_str in df['Teachers']:
                    for t in t_str.split(','): all_t.add(t.strip())
                options = sorted(list(all_t))
                selected = st.selectbox("Select Teacher:", options)
                df_filtered = df[df['Teachers'].str.contains(selected, regex=False)]

        st.divider()
        
        # Render HTML Timetable
        html_view = generate_html_timetable(df_filtered, f"{view_type}: {selected}")
        st.markdown(html_view, unsafe_allow_html=True)
        
        st.divider()
        st.subheader("📋 Raw Data")
        st.dataframe(df_filtered, use_container_width=True)
        
    else:
        st.info("👈 Please run the scheduler in Tab 2 first.")
