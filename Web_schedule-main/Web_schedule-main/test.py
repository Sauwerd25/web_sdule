import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re
import html  # เพิ่ม Library นี้เพื่อป้องกัน HTML หลุด

# ==========================================
# ⚙️ 0. Page Config & CSS Styling
# ==========================================
st.set_page_config(page_title="Auto Scheduler Pro", layout="wide", page_icon="🎓")

# CSS สำหรับตกแต่ง
st.markdown("""
<style>
    .schedule-container {
        display: grid;
        grid-template-columns: 80px repeat(11, 1fr); 
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
        font-size: 0.75em;
        line-height: 1.2;
        color: white;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        overflow: hidden;
        transition: transform 0.1s;
        cursor: pointer;
    }
    .class-card:hover { transform: scale(1.05); z-index: 10; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }
    .type-Lec { background-color: #4CAF50; border-left: 4px solid #2E7D32; }
    .type-Lab { background-color: #2196F3; border-left: 4px solid #1565C0; }
</style>
""", unsafe_allow_html=True)

st.title("🎓 Automatic Course Scheduler (Pro + Refactored)")

# ==========================================
# 🔧 Session State Initialization
# ==========================================
if 'schedule' not in st.session_state:
    st.session_state['schedule'] = None
if 'unscheduled' not in st.session_state:
    st.session_state['unscheduled'] = []
if 'has_run' not in st.session_state:
    st.session_state['has_run'] = False

# ==========================================
# 🛠️ Helper Functions
# ==========================================
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']

def time_to_slot_index(time_str, slot_map):
    time_str = str(time_str).strip()
    match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
    if match:
        h, m = match.groups()
        t_val = int(h) + (int(m) / 60.0)
        for idx, info in slot_map.items():
            if abs(info['val'] - t_val) < 0.01:
                return idx
    return -1

def parse_unavailable_time(unavailable_input, slot_map):
    unavailable_slots_by_day = {d_idx: set() for d_idx in range(len(DAYS))}
    
    if pd.isna(unavailable_input) or unavailable_input == "":
        return unavailable_slots_by_day

    target_list = []
    if isinstance(unavailable_input, list): target_list = unavailable_input
    elif isinstance(unavailable_input, str): target_list = [unavailable_input]
    else: return unavailable_slots_by_day

    for item in target_list:
        if isinstance(item, list): ut_str = item[0] if len(item) > 0 else ""
        else: ut_str = str(item)

        ut_str = ut_str.replace('[', '').replace(']', '').replace("'", "").replace('"', "")
        match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", ut_str, re.IGNORECASE)
        if not match: continue

        day_abbr, start_time_str, end_time_str = match.groups()
        day_abbr = day_abbr.capitalize()

        try: day_idx = DAYS.index(day_abbr)
        except ValueError: continue

        start_slot = time_to_slot_index(start_time_str.replace('.', ':'), slot_map)
        end_slot = time_to_slot_index(end_time_str.replace('.', ':'), slot_map)

        if start_slot != -1 and end_slot != -1 and start_slot < end_slot:
            for slot in range(start_slot, end_slot):
                unavailable_slots_by_day[day_idx].add(slot)
                
    return unavailable_slots_by_day

# ==========================================
# 📂 1. Data Management
# ==========================================
def render_data_upload_section():
    st.info("📂 **Step 1: Data Preparation**")
    uploaded_data = {}
    
    # ⚠️ ตรวจสอบ Path นี้ให้ตรงกับเครื่องคุณ
    BASE_PATH = "Web_schedule-main/Web_schedule-main/" 
    
    file_configs = [
        ("1. Room Data", "df_room", "room.csv"),
        ("2. Teachers List", "all_teacher", "all_teachers.csv"),
        ("3. Teacher Courses", "df_teacher_courses", "teacher_courses.csv"),
        ("4. AI Courses IN", "df_ai_in", "ai_in_courses.csv"),
        ("5. Cyber Courses IN", "df_cy_in", "cy_in_courses.csv"),
        ("6. AI Courses OUT (Fixed)", "df_ai_out", "ai_out_courses.csv"),
        ("7. Cyber Courses OUT (Fixed)", "df_cy_out", "cy_out_courses.csv"),
    ]

    with st.expander("📂 Upload CSV files (Optional - Defaults available)", expanded=True):
        cols = st.columns(2)
        for i, (label, key, filename) in enumerate(file_configs):
            with cols[i % 2]:
                file = st.file_uploader(f"{label}", type=['csv'], key=key)
                if file:
                    try: uploaded_data[key] = pd.read_csv(file)
                    except Exception as e: st.error(f"Error reading {filename}: {e}")
                else:
                    try: uploaded_data[key] = pd.read_csv(f"{BASE_PATH}{filename}")
                    except: uploaded_data[key] = pd.DataFrame() # Empty if not found

    return uploaded_data

# ==========================================
# 🧠 2. Solver Logic
# ==========================================
def run_solver(data, config):
    df_room = data.get('df_room', pd.DataFrame())
    df_teacher_courses = data.get('df_teacher_courses', pd.DataFrame())
    all_teacher = data.get('all_teacher', pd.DataFrame())
    df_ai_in = data.get('df_ai_in', pd.DataFrame())
    df_cy_in = data.get('df_cy_in', pd.DataFrame())
    df_ai_out = data.get('df_ai_out', pd.DataFrame()) 
    df_cy_out = data.get('df_cy_out', pd.DataFrame()) 

    if df_room.empty or df_teacher_courses.empty:
        return None, [{"Reason": "Missing Critical Data (Room or Teachers)"}]

    # --- Time Slot Setup ---
    SLOT_MAP = {}
    t_start = 8.5
    idx = 0
    while t_start < 19.0:
        h = int(t_start)
        m = int((t_start - h) * 60)
        SLOT_MAP[idx] = {'time': f"{h:02d}:{m:02d}", 'val': t_start, 'is_lunch': (12.0 <= t_start < 13.0)}
        idx += 1
        t_start += 0.5
    TOTAL_SLOTS = len(SLOT_MAP)

    # --- Data Pre-processing ---
    for df in [df_room, df_teacher_courses, df_ai_in, df_cy_in, all_teacher, df_ai_out, df_cy_out]:
        if not df.empty: df.columns = df.columns.str.strip()

    TEACHER_UNAVAILABLE_SLOTS = {}
    if not all_teacher.empty and 'unavailable_times' in all_teacher.columns:
        all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
        for _, row in all_teacher.iterrows():
            TEACHER_UNAVAILABLE_SLOTS[row['teacher_id']] = parse_unavailable_time(row['unavailable_times'], SLOT_MAP)

    fixed_locks = {} 
    for df_fixed in [df_ai_out, df_cy_out]:
        if df_fixed.empty: continue
        for _, row in df_fixed.iterrows():
            try:
                c_code = str(row['course_code']).strip()
                sec = int(row['section'])
                day_str = str(row['day']).strip().capitalize()[:3]
                
                if row.get('lecture_hour', 0) > 0:
                    fixed_locks[(c_code, sec, 'Lec')] = {'day': day_str, 'start': str(row['start']), 'room': str(row['room'])}
                if row.get('lab_hour', 0) > 0:
                    fixed_locks[(c_code, sec, 'Lab')] = {'day': day_str, 'start': str(row['start']), 'room': str(row['room'])}
            except: continue

    df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True).fillna(0)
    teacher_map = {}
    df_teacher_courses['course_code'] = df_teacher_courses['course_code'].astype(str).str.strip()
    for _, row in df_teacher_courses.iterrows():
        c = row['course_code']
        t = str(row['teacher_id']).strip()
        if c not in teacher_map: teacher_map[c] = []
        teacher_map[c].append(t)

    room_list = df_room.to_dict('records')
    room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

    # --- Task Generation ---
    tasks = []
    MAX_LEC_SESSION = 6 

    for _, row in df_courses.iterrows():
        c_code = str(row['course_code']).strip()
        try: sec = int(row['section'])
        except: sec = row['section']
        
        enroll = row.get('enrollment_count', 30)
        teachers = teacher_map.get(c_code, ['Unknown'])
        is_opt = row.get('optional', 0)

        # Lecture
        lec_dur = int(math.ceil(row.get('lecture_hour', 0) * 2))
        if lec_dur > 0:
            lock_info = fixed_locks.get((c_code, sec, 'Lec'))
            curr_lec = lec_dur
            p = 1
            while curr_lec > 0:
                dur = min(curr_lec, MAX_LEC_SESSION)
                uid = f"{c_code}_S{sec}_L_P{p}"
                tasks.append({
                    'uid': uid, 'id': c_code, 'sec': sec, 'type': 'Lec',
                    'dur': dur, 'std': enroll, 'teachers': teachers,
                    'is_online': (row.get('lec_online', 0) == 1),
                    'is_optional': is_opt,
                    'fixed': lock_info
                })
                curr_lec -= dur
                p += 1
        
        # Lab
        lab_dur = int(math.ceil(row.get('lab_hour', 0) * 2))
        if lab_dur > 0:
            lock_info = fixed_locks.get((c_code, sec, 'Lab'))
            tasks.append({
                'uid': f"{c_code}_S{sec}_Lb", 'id': c_code, 'sec': sec, 'type': 'Lab',
                'dur': lab_dur, 'std': enroll, 'teachers': teachers,
                'is_online': (row.get('lab_online', 0) == 1),
                'req_ai': (row.get('require_lab_ai', 0) == 1),
                'req_net': (row.get('require_lab_network', 0) == 1),
                'is_optional': is_opt,
                'fixed': lock_info
            })

    # --- Model Building ---
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}
    task_vars = {} 
    objective_terms = []
    
    SCORE_FIXED = 1000000
    SCORE_CORE = 1000
    SCORE_ELEC = 100

    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
        
        t_day = model.NewIntVar(0, len(DAYS)-1, f"d_{uid}")
        t_start = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
        t_end = model.NewIntVar(0, TOTAL_SLOTS+10, f"e_{uid}")
        model.Add(t_end == t_start + t['dur'])
        task_vars[uid] = {'day': t_day, 'start': t_start}

        if t.get('fixed'):
            try:
                f_day_idx = DAYS.index(t['fixed']['day'])
                f_start_slot = time_to_slot_index(t['fixed']['start'], SLOT_MAP)
                model.Add(t_day == f_day_idx)
                model.Add(t_start == f_start_slot)
            except: pass

        candidates = []
        for r in room_list:
            if t.get('fixed') and t['fixed']['room'] != r['room']: continue
            if t['is_online']:
                if r['room'] != 'Online': continue
            else:
                if r['room'] == 'Online': continue
                if r['capacity'] < t['std']: continue
                if t['type'] == 'Lab':
                    if 'lab' not in str(r.get('type','')).lower(): continue
                    if t.get('req_ai') and r['room'] != 'lab_ai': continue
                    if t.get('req_net') and r['room'] != 'lab_network': continue
            
            for d in range(len(DAYS)):
                if t.get('fixed') and DAYS.index(t['fixed']['day']) != d: continue

                for s in range(TOTAL_SLOTS - t['dur'] + 1):
                    s_val = SLOT_MAP[s]['val']
                    
                    if t.get('fixed'):
                        f_start = time_to_slot_index(t['fixed']['start'], SLOT_MAP)
                        if s != f_start: continue
                    
                    if not t.get('fixed'):
                        e_val = SLOT_MAP[s + t['dur'] - 1]['val'] + 0.5
                        if config['MODE'] == 1 and (s_val < 9.0 or e_val > 16.0): continue
                        
                        overlap_lunch = False
                        for k in range(t['dur']):
                            if SLOT_MAP[s+k]['is_lunch']: overlap_lunch = True
                        if overlap_lunch: continue

                        teacher_conflict = False
                        for tea in t['teachers']:
                            if tea in TEACHER_UNAVAILABLE_SLOTS:
                                unavail_set = TEACHER_UNAVAILABLE_SLOTS[tea].get(d, set())
                                task_slots = set(range(s, s + t['dur']))
                                if not task_slots.isdisjoint(unavail_set): 
                                    teacher_conflict = True
                                    break
                        if teacher_conflict: continue

                    var = model.NewBoolVar(f"{uid}_{r['room']}_{d}_{s}")
                    schedule[(uid, r['room'], d, s)] = var
                    candidates.append(var)
                    
                    model.Add(t_day == d).OnlyEnforceIf(var)
                    model.Add(t_start == s).OnlyEnforceIf(var)

        if candidates:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())
        else:
            model.Add(is_scheduled[uid] == 0)

        if t.get('fixed'): score = SCORE_FIXED
        elif t.get('is_optional') == 0: score = SCORE_CORE
        else: score = SCORE_ELEC
        objective_terms.append(is_scheduled[uid] * score)

    # Conflict Constraints
    for d in range(len(DAYS)):
        for s in range(TOTAL_SLOTS):
            for r in room_list:
                if r['room'] == 'Online': continue
                active = []
                for t in tasks:
                    for k in range(t['dur']):
                        if s - k >= 0:
                            key = (t['uid'], r['room'], d, s - k)
                            if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)

    all_teachers_set = set(tea for t in tasks for tea in t['teachers'] if tea != 'Unknown')
    for tea in all_teachers_set:
        for d in range(len(DAYS)):
            for s in range(TOTAL_SLOTS):
                active = []
                for t in tasks:
                    if tea in t['teachers']:
                        for r in room_list:
                             for k in range(t['dur']):
                                if s - k >= 0:
                                    key = (t['uid'], r['room'], d, s - k)
                                    if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)

    model.Maximize(sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 4
    solver.parameters.max_time_in_seconds = config['TIMEOUT']
    
    status = solver.Solve(model)

    results = []
    unscheduled = []

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for t in tasks:
            uid = t['uid']
            if uid in is_scheduled and solver.Value(is_scheduled[uid]):
                d_val = solver.Value(task_vars[uid]['day'])
                s_val = solver.Value(task_vars[uid]['start'])
                
                r_name = "Unknown"
                for (tid, r, d, s), var in schedule.items():
                    if tid == uid and d == d_val and s == s_val and solver.Value(var):
                        r_name = r
                        break
                
                start_time = SLOT_MAP[s_val]['time']
                end_idx = s_val + t['dur']
                end_time = SLOT_MAP.get(end_idx, {'time': '19:00'})['time']
                
                results.append({
                    'Day': DAYS[d_val], 'Start': start_time, 'End': end_time,
                    'StartVal': SLOT_MAP[s_val]['val'], 'Duration': t['dur'],
                    'Room': r_name, 'Course': t['id'], 'Sec': t['sec'],
                    'Type': t['type'], 'Teachers': ", ".join(t['teachers'])
                })
            else:
                unscheduled.append({'Course': t['id'], 'Sec': t['sec'], 'Type': t['type'], 'Reason': 'Constraint Conflict'})
    
    return pd.DataFrame(results), unscheduled

# ==========================================
# 🎨 3. Visualization Helper (Fixed HTML Escape)
# ==========================================
def generate_html_timetable(df, title):
    times = list(range(8, 20)) 
    html_code = f"<h4 style='color:#333;'>📅 {title}</h4>"
    html_code += "<div class='schedule-container'>"
    html_code += "<div></div>"
    for t in times[:-1]: html_code += f"<div class='header-cell'>{t:02d}:00</div>"
    
    for day in DAYS:
        html_code += f"<div class='day-cell'>{day}</div>"
        day_tasks = df[df['Day'] == day].copy()
        html_code += f"<div style='grid-column: 2 / span 11; position: relative; height: 60px; background: #fff; border-bottom: 1px solid #eee;'>"
        
        for _, row in day_tasks.iterrows():
            start_offset = row['StartVal'] - 8.0
            duration_hr = row['Duration'] * 0.5
            left_pct = (start_offset / 11.0) * 100
            width_pct = (duration_hr / 11.0) * 100
            color_class = f"type-{row['Type']}"
            
            # ⚠️ แก้ไข: ใช้ html.escape เพื่อป้องกัน ' หรือ " ในชื่อวิชาทำ HTML พัง
            course_safe = html.escape(str(row['Course']))
            room_safe = html.escape(str(row['Room']))
            tooltip = f"{course_safe} ({row['Type']}) {row['Start']}-{row['End']} @ {room_safe}"
            
            html_code += f"""
            <div class='class-card {color_class}' style='position: absolute; left: {left_pct}%; width: {width_pct}%; top: 2px; bottom: 2px;' 
                 title="{tooltip}">
                <b>{course_safe}</b><br>{room_safe}
            </div>
            """
        html_code += "</div>"
    html_code += "</div>"
    return html_code

# ==========================================
# 🚀 Main App Flow
# ==========================================
tab1, tab2, tab3 = st.tabs(["1️⃣ Upload Data", "2️⃣ Settings & Run", "3️⃣ Results"])

with tab1:
    data_store = render_data_upload_section()

with tab2:
    st.header("⚙️ Configuration")
    c1, c2 = st.columns(2)
    with c1:
        mode = st.radio("Schedule Mode", [1, 2], format_func=lambda x: "Compact (09:00-16:00)" if x==1 else "Flexible (08:30-19:00)")
    with c2:
        timeout = st.slider("Max Calculation Time (seconds)", 10, 600, 120)
    
    if st.button("🚀 Generate Schedule", type="primary"):
        if data_store:
            with st.spinner("🤖 AI is crunching the numbers..."):
                config = {'MODE': mode, 'TIMEOUT': timeout}
                res_df, un_list = run_solver(data_store, config)
                
                if res_df is not None and not res_df.empty:
                    st.session_state['schedule'] = res_df
                    st.session_state['unscheduled'] = un_list
                    st.session_state['has_run'] = True
                    st.success(f"✅ Success! Scheduled {len(res_df)} classes.")
                else:
                    st.error("❌ Failed to find a valid schedule. Try increasing time or relaxing constraints.")
        else:
            st.error("Please upload data first.")

with tab3:
    if st.session_state.get('has_run', False) and st.session_state['schedule'] is not None:
        df = st.session_state['schedule']
        # ใช้ .get() เพื่อป้องกัน KeyError ในกรณีตัวแปรหาย
        un_list = st.session_state.get('unscheduled', [])
        
        # Summary Metrics
        c1, c2, c3 = st.columns(3)
        total = len(df) + len(un_list)
        c1.metric("Total Classes", total)
        c2.metric("Scheduled", len(df))
        c3.metric("Unscheduled", len(un_list), delta_color="inverse")

        st.divider()

        # View Control
        col_view, col_select = st.columns([1, 3])
        with col_view:
            view_type = st.radio("View Mode:", ["Room View", "Teacher View"], horizontal=True)
        with col_select:
            if view_type == "Room View":
                options = sorted(df['Room'].unique())
                label = "Room"
            else:
                all_t = set()
                for t_str in df['Teachers']:
                    for t in t_str.split(','): all_t.add(t.strip())
                options = sorted(list(all_t))
                label = "Teacher"
            
            selected = st.selectbox(f"Select {label}:", options)
            
            if view_type == "Room View":
                df_filtered = df[df['Room'] == selected]
            else:
                df_filtered = df[df['Teachers'].str.contains(selected, regex=False)]

        # Render HTML
        st.markdown(generate_html_timetable(df_filtered, f"{view_type}: {selected}"), unsafe_allow_html=True)
        
        # Unscheduled Section
        if un_list:
            st.divider()
            with st.expander(f"⚠️ Unscheduled Classes ({len(un_list)})", expanded=False):
                # ⚠️ แก้ไข: ใช้ width="stretch" แทน use_container_width เพื่อแก้ Warning
                st.dataframe(pd.DataFrame(un_list), width=1000) 

        # Download
        st.divider()
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Schedule CSV", data=csv, file_name="schedule_result.csv", mime="text/csv")
    else:
        st.info("👈 Go to Tab 2 to run the scheduler.")
