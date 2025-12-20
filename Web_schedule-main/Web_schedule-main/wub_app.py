import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import math
import re

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="Automatic Scheduler Pro", layout="wide", page_icon="🎓")
st.title("🎓 Automatic Course Scheduler (Pro Version)")

# ==========================================
# 📂 ส่วนที่ 1: Sidebar - Upload & Config
# ==========================================
st.header("📂 1. Upload Data Files")

# 1.1 File Uploader: ให้ผู้ใช้เลือกไฟล์ CSV เอง
uploaded_files = st.file_uploader(
    "Upload CSV files (room, teacher, courses, etc.)", 
    accept_multiple_files=True, 
    type=['csv']
)

# ตัวแปรเก็บข้อมูล (Data Store)
data_store = {}
required_keys = ['df_room', 'df_teacher_courses', 'df_ai_in', 'df_cy_in', 'all_teacher', 'df_ai_out', 'df_cy_out']

# Logic การจับคู่ไฟล์ที่อัปโหลดเข้าตัวแปร
if uploaded_files:
    for file in uploaded_files:
        fname = file.name.lower()
        if 'room' in fname: data_store['df_room'] = pd.read_csv(file)
        elif 'teacher_courses' in fname: data_store['df_teacher_courses'] = pd.read_csv(file)
        elif 'ai_in' in fname: data_store['df_ai_in'] = pd.read_csv(file)
        elif 'cy_in' in fname: data_store['df_cy_in'] = pd.read_csv(file)
        elif 'all_teachers' in fname: data_store['all_teacher'] = pd.read_csv(file)
        elif 'ai_out' in fname: data_store['df_ai_out'] = pd.read_csv(file)
        elif 'cy_out' in fname: data_store['df_cy_out'] = pd.read_csv(file)
    
    # เช็คว่าไฟล์ครบไหม
    uploaded_count = len(data_store)
    if uploaded_count == 7:
        st.sidebar.success(f"✅ All files uploaded ({uploaded_count}/7)")
    else:
        st.sidebar.warning(f"⚠️ Missing files ({uploaded_count}/7). Please upload all required CSVs.")


st.header("⚙️ 2. Settings")

# 1.2 Configuration: ตั้งค่าพารามิเตอร์
schedule_mode_desc = {
    1: "Compact Mode (09:00 - 16:00)",
    2: "Flexible Mode (08:30 - 19:00)"
}
SCHEDULE_MODE = st.sidebar.radio(
    "Scheduling Mode:",
    options=[1, 2],
    format_func=lambda x: schedule_mode_desc[x]
)


solver_limit = st.slider(
    "Max Calculation Time (seconds)", 
    min_value=10, max_value=600, value=120
)

# รวบรวมค่า Config
config_params = {
    'SOLVER_TIME': solver_limit,
    'MODE': SCHEDULE_MODE
}

run_button = st.button("🚀 Run Scheduler", type="primary")

# ==========================================
# 🧠 ส่วนที่ 2: ฟังก์ชันคำนวณ (Calculation Core)
# ==========================================
def calculate_schedule(data_store, config):
    # ตรวจสอบว่าไฟล์ครบหรือไม่
    if len(data_store) < 7:
        st.error("❌ Missing required CSV files. Please upload them in the sidebar.")
        return None, None

    # --- Time Slot Setup (ใช้ค่าจาก Config) ---
    SLOT_MAP = {}
    t_start = 8.5
    idx = 0
    LUNCH_START = config['LUNCH_START']
    LUNCH_END = config['LUNCH_END']

    while t_start < 19.0:
        hour = int(t_start)
        minute = int((t_start - hour) * 60)
        time_str = f"{hour:02d}:{minute:02d}"
        SLOT_MAP[idx] = {
            'time': time_str, 'val': t_start,
            'is_lunch': (t_start >= LUNCH_START and t_start < LUNCH_END)
        }
        idx += 1
        t_start += 0.5
    
    TOTAL_SLOTS = len(SLOT_MAP)
    DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    SLOT_TO_INDEX = {v['time']: k for k, v in SLOT_MAP.items()}

    def time_to_slot_index(time_str):
        time_str = str(time_str).strip()
        match = re.search(r"(\d{1,2})[:.](\d{2})", time_str)
        if match:
            h, m = match.groups()
            time_str = f"{int(h):02d}:{int(m):02d}"
            if time_str in SLOT_TO_INDEX:
                return SLOT_TO_INDEX[time_str]
        return -1

    def parse_unavailable_time(unavailable_input):
        unavailable_slots_by_day = {d_idx: set() for d_idx in range(len(DAYS))}
        target_list = []
        if isinstance(unavailable_input, list): target_list = unavailable_input
        elif isinstance(unavailable_input, str): target_list = [unavailable_input]
        else: return unavailable_slots_by_day

        for item in target_list:
            if isinstance(item, list): ut_str = item[0] if len(item) > 0 else ""
            else: ut_str = str(item)

            ut_str = ut_str.replace('[', '').replace(']', '').replace("'", "").replace('"', "")
            match = re.search(r"(\w{3})\s+(\d{1,2}[:.]\d{2})-(\d{1,2}[:.]\d{2})", ut_str)
            if not match: continue

            day_abbr, start_time_str, end_time_str = match.groups()
            start_time_str = start_time_str.replace('.', ':')
            end_time_str = end_time_str.replace('.', ':')

            try: day_idx = DAYS.index(day_abbr)
            except ValueError: continue

            start_slot = time_to_slot_index(start_time_str)
            end_slot = time_to_slot_index(end_time_str)

            if start_slot == -1 or end_slot == -1 or start_slot >= end_slot: continue

            for slot in range(start_slot, end_slot):
                unavailable_slots_by_day[day_idx].add(slot)
        return unavailable_slots_by_day

    # --- Data Unpacking (ดึงข้อมูลจากตัวแปรที่รับมา) ---
    df_room = data_store['df_room']
    df_teacher_courses = data_store['df_teacher_courses']
    df_ai_in = data_store['df_ai_in']
    df_cy_in = data_store['df_cy_in']
    all_teacher = data_store['all_teacher']
    df_ai_out = data_store['df_ai_out']
    df_cy_out = data_store['df_cy_out']
    
    room_list = df_room.to_dict('records')
    room_list.append({'room': 'Online', 'capacity': 9999, 'type': 'virtual'})

    # --- Data Cleaning & Prep ---
    df_teacher_courses.columns = df_teacher_courses.columns.str.strip()
    df_ai_in.columns = df_ai_in.columns.str.strip()
    df_cy_in.columns = df_cy_in.columns.str.strip()
    
    # Progress Bar UI
    progress_text = "Operation in progress. Please wait."
    my_bar = st.progress(0, text=progress_text)
    my_bar.progress(10, text="Cleaning Data...")

    df_courses = pd.concat([df_ai_in, df_cy_in], ignore_index=True)
    if 'lec_online' not in df_courses.columns: df_courses['lec_online'] = 0
    if 'lab_online' not in df_courses.columns: df_courses['lab_online'] = 0
    if 'optional' not in df_courses.columns: df_courses['optional'] = 1
    df_courses = df_courses.fillna(0)
    
    df_teacher_courses['course_code'] = df_teacher_courses['course_code'].astype(str).str.strip()
    df_courses['course_code'] = df_courses['course_code'].astype(str).str.strip()
    teacher_map = {}
    for _, row in df_teacher_courses.iterrows():
        c_code = row['course_code']
        t_id = str(row['teacher_id']).strip()
        if c_code not in teacher_map: teacher_map[c_code] = []
        teacher_map[c_code].append(t_id)

    # Teacher Unavailability
    all_teacher['teacher_id'] = all_teacher['teacher_id'].astype(str).str.strip()
    all_teacher['unavailable_times'] = all_teacher['teacher_id'].apply(lambda x: None) # Reset or use logic if exists
    
    TEACHER_UNAVAILABLE_SLOTS = {}
    if 'unavailable_times' in all_teacher.columns:
        for index, row in all_teacher.iterrows():
            parsed = parse_unavailable_time(row['unavailable_times'])
            TEACHER_UNAVAILABLE_SLOTS[row['teacher_id']] = parsed

    # Fixed Schedule Logic
    fixed_schedule = []
    for df_fixed in [df_ai_out, df_cy_out]:
        for index, row in df_fixed.iterrows():
             try:
                day_str = str(row['day']).strip()[:3]
                course_code = str(row['course_code']).strip()
                sec_str = str(row['section']).strip()
                if not sec_str or not sec_str.isdigit(): continue
                sec = int(sec_str)
                room = str(row['room']).strip()
                start_time = str(row['start']).strip()
                lec_h = row['lecture_hour'] if not pd.isna(row['lecture_hour']) else 0
                lab_h = row['lab_hour'] if not pd.isna(row['lab_hour']) else 0
                
                if lec_h > 0:
                    duration = int(math.ceil(lec_h * 2))
                    fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lec', 'room': room, 'day': day_str, 'start': start_time, 'duration': duration})
                if lab_h > 0:
                    duration = int(math.ceil(lab_h * 2))
                    fixed_schedule.append({'course': course_code, 'sec': sec, 'type': 'Lab', 'room': room, 'day': day_str, 'start': start_time, 'duration': duration})
             except Exception: continue

    # Task Preparation
    tasks = []
    MAX_LEC_SESSION_SLOTS = 6
    course_optional_map = df_courses.set_index(['course_code', 'section'])['optional'].to_dict()

    for lock in fixed_schedule:
        uid = f"{lock['course']}_S{lock['sec']}_{lock['type']}"
        course_match = df_courses[(df_courses['course_code'] == lock['course']) & (df_courses['section'] == lock['sec'])]
        is_online_lec = course_match['lec_online'].iloc[0] == 1 if not course_match.empty else False
        is_online_lab = course_match['lab_online'].iloc[0] == 1 if not course_match.empty else False
        is_task_online = is_online_lec if lock['type'] == 'Lec' else is_online_lab
        optional_val = course_optional_map.get((lock['course'], lock['sec']), 1)
        tasks.append({
            'uid': uid, 'id': lock['course'], 'sec': lock['sec'], 'type': lock['type'],
            'dur': lock['duration'], 'std': course_match['enrollment_count'].iloc[0] if not course_match.empty else 50,
            'teachers': teacher_map.get(lock['course'], ['External_Faculty']),
            'is_online': is_task_online, 'is_optional': optional_val, 'fixed_room': True
        })

    for _, row in df_courses.iterrows():
        lec_slots = int(math.ceil(row['lecture_hour'] * 2))
        lab_slots = int(math.ceil(row['lab_hour'] * 2))
        teachers = teacher_map.get(row['course_code'], ['Unknown'])
        
        current_lec_slots = lec_slots
        part = 1
        while current_lec_slots > 0:
            session_dur = min(current_lec_slots, MAX_LEC_SESSION_SLOTS)
            uid = f"{row['course_code']}_S{row['section']}_Lec_P{part}"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': row['course_code'], 'sec': row['section'], 'type': 'Lec',
                    'dur': session_dur, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lec_online'] == 1), 'is_optional': row['optional']
                })
            current_lec_slots -= session_dur
            part += 1
        
        if lab_slots > 0:
            uid = f"{row['course_code']}_S{row['section']}_Lab"
            if not any(t['uid'] == uid for t in tasks):
                tasks.append({
                    'uid': uid, 'id': row['course_code'], 'sec': row['section'], 'type': 'Lab',
                    'dur': lab_slots, 'std': row['enrollment_count'], 'teachers': teachers,
                    'is_online': (row['lab_online'] == 1), 'is_optional': row['optional'],
                    'req_ai': (row.get('require_lab_ai', 0) == 1),
                    'req_network': (row.get('require_lab_network', 0) == 1)
                })

    # --- Solver ---
    my_bar.progress(30, text="Building Model...")
    model = cp_model.CpModel()
    schedule = {}
    is_scheduled = {}
    task_vars = {}
    penalty_vars = []
    objective_terms = []
    
    SCORE_FIXED = 1000000
    SCORE_CORE_COURSE = 1000
    SCORE_ELECTIVE_COURSE = 100

    for t in tasks:
        uid = t['uid']
        is_scheduled[uid] = model.NewBoolVar(f"sched_{uid}")
        t_day = model.NewIntVar(0, len(DAYS)-1, f"d_{uid}")
        t_start = model.NewIntVar(0, TOTAL_SLOTS-1, f"s_{uid}")
        t_end = model.NewIntVar(0, TOTAL_SLOTS+10, f"e_{uid}")
        model.Add(t_end == t_start + t['dur'])
        task_vars[uid] = {'day': t_day, 'start': t_start, 'end': t_end}

        candidates = []
        for r in room_list:
            if t['is_online']:
                if r['room'] != 'Online': continue
            else:
                if r['room'] == 'Online': continue
                if r['capacity'] < t['std']: continue
                if t['type'] == 'Lab' and 'lab' not in r['type']: continue
                if t.get('req_ai', False) and r['room'] != 'lab_ai': continue
                if t.get('req_network', False) and r['room'] != 'lab_network': continue

            for d_idx, day in enumerate(DAYS):
                for s_idx in SLOT_MAP:
                    s_val = SLOT_MAP[s_idx]['val']
                    e_val = s_val + (t['dur'] * 0.5)

                    # Mode Check
                    if config['MODE'] == 1:
                        if s_val < 9.0 or e_val > 16.0: continue
                    else:
                        if s_idx + t['dur'] > TOTAL_SLOTS: continue

                    # Lunch Check
                    overlaps_lunch = False
                    for i in range(t['dur']):
                        if SLOT_MAP.get(s_idx + i, {}).get('is_lunch', False):
                            overlaps_lunch = True; break
                    if overlaps_lunch: continue

                    # Teacher Conflict
                    teacher_conflict = False
                    for teacher_id in t['teachers']:
                        if teacher_id in ['External_Faculty', 'Unknown']: continue
                        if teacher_id in TEACHER_UNAVAILABLE_SLOTS:
                            unavailable_set = TEACHER_UNAVAILABLE_SLOTS[teacher_id].get(d_idx, set())
                            task_slots = set(range(s_idx, s_idx + t['dur']))
                            if not task_slots.isdisjoint(unavailable_set): teacher_conflict = True; break
                    if teacher_conflict: continue

                    var = model.NewBoolVar(f"{uid}_{r['room']}_{day}_{s_idx}")
                    schedule[(uid, r['room'], d_idx, s_idx)] = var
                    candidates.append(var)
                    model.Add(t_day == d_idx).OnlyEnforceIf(var)
                    model.Add(t_start == s_idx).OnlyEnforceIf(var)

                    if config['MODE'] == 2 and (s_val < 9.0 or e_val > 16.0):
                        penalty_vars.append(var)

        if not candidates:
            model.Add(is_scheduled[uid] == 0)
        else:
            model.Add(sum(candidates) == 1).OnlyEnforceIf(is_scheduled[uid])
            model.Add(sum(candidates) == 0).OnlyEnforceIf(is_scheduled[uid].Not())

        if 'fixed_room' in t: objective_terms.append(is_scheduled[uid] * SCORE_FIXED)
        elif t.get('is_optional') == 0: objective_terms.append(is_scheduled[uid] * SCORE_CORE_COURSE)
        else: objective_terms.append(is_scheduled[uid] * SCORE_ELECTIVE_COURSE)

    # Conflict Constraints
    for d in range(len(DAYS)):
        for s in SLOT_MAP:
            for r in room_list:
                if r['room'] == 'Online': continue
                active = []
                for t in tasks:
                    for offset in range(t['dur']):
                        if s - offset >= 0:
                            key = (t['uid'], r['room'], d, s - offset)
                            if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)
            
            all_teachers_set = set(tea for t in tasks for tea in t['teachers'] if tea != 'Unknown')
            for tea in all_teachers_set:
                active = []
                for t in tasks:
                    if tea in t['teachers']:
                        for r in room_list:
                             for offset in range(t['dur']):
                                if s - offset >= 0:
                                    key = (t['uid'], r['room'], d, s - offset)
                                    if key in schedule: active.append(schedule[key])
                if active: model.Add(sum(active) <= 1)

    model.Maximize(sum(objective_terms) - sum(penalty_vars))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 4
    solver.parameters.max_time_in_seconds = config['SOLVER_TIME']
    
    my_bar.progress(60, text="Solving... (This may take a while)")
    status = solver.Solve(model)
    my_bar.progress(100, text="Done!")
    my_bar.empty()

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        results = []
        unscheduled = []

        for t in tasks:
            uid = t['uid']
            if uid in is_scheduled and solver.Value(is_scheduled[uid]):
                d = solver.Value(task_vars[uid]['day'])
                s = solver.Value(task_vars[uid]['start'])
                dur = t['dur']
                r_name = "Unknown"
                
                for (tid, r, d_idx, s_idx), var in schedule.items():
                    if tid == uid and d_idx == d and s_idx == s and solver.Value(var):
                        r_name = r
                        break
                
                results.append({
                    'Day': DAYS[d], 
                    'Start': SLOT_MAP[s]['time'], 
                    'End': SLOT_MAP.get(s + dur, {'time': '19:00'})['time'],
                    'Room': r_name, 
                    'Course': t['id'], 
                    'Sec': t['sec'], 
                    'Type': t['type'],
                    'Teacher': ",".join(t['teachers'])
                })
            else:
                unscheduled.append({
                    'Course': t['id'], 
                    'Sec': t['sec'], 
                    'Reason': 'Constraint/Penalty'
                })
        
        return results, unscheduled
    else:
        return None, None

# ==========================================
# 📊 ส่วนที่ 3: ส่วนควบคุมและแสดงผล (Controller & View)
# ==========================================

# ปุ่ม Run ทำงาน
if run_button:
    res_list, un_list = calculate_schedule(data_store, config_params)
    
    if res_list is not None:
        st.session_state['schedule_results'] = pd.DataFrame(res_list)
        st.session_state['unscheduled_results'] = un_list if un_list else []
        st.session_state['has_run'] = True
        st.toast("Calculation Complete!", icon="✅")
    else:
        st.error("❌ Cannot schedule in current mode or files missing.")

# ส่วนแสดงผล
if st.session_state.get('has_run', False):
    df_res = st.session_state['schedule_results']
    unscheduled = st.session_state['unscheduled_results']
    
    if df_res.empty:
         st.warning("⚠️ Solver found a solution, but NO classes were scheduled.")
    else:
        # Sort Data
        day_order = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4}
        df_res['DayIdx'] = df_res['Day'].map(day_order)
        df_res = df_res.sort_values(by=['DayIdx', 'Start'])

        # --- 3.1 Dashboard Summary (สรุปผล) ---
        st.divider()
        st.markdown("### 📊 Scheduling Summary")
        total_tasks = len(df_res) + len(unscheduled)
        success_rate = (len(df_res) / total_tasks) * 100 if total_tasks > 0 else 0
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Classes", total_tasks)
        c2.metric("Scheduled", len(df_res), delta=f"{success_rate:.1f}% Success")
        c3.metric("Unscheduled", len(unscheduled), delta_color="inverse")
        c4.metric("Mode", schedule_mode_desc[SCHEDULE_MODE])

        # --- 3.2 Visualization Mode (เลือกมุมมอง) ---
        st.divider()
        st.header("📅 Timetable View")
        
        # Radio ปุ่มเลือกมุมมอง
        view_mode = st.radio("Select View Mode:", ["🏫 Room View", "👨‍🏫 Teacher View"], horizontal=True)

        if view_mode == "🏫 Room View":
            all_items = sorted(df_res['Room'].unique())
            label = "Select Room:"
            filter_col = "Room"
        else:
            # ดึงรายชื่ออาจารย์ทั้งหมด
            teachers_set = set()
            for t_str in df_res['Teacher']:
                if pd.isna(t_str): continue
                for t in t_str.split(','):
                    teachers_set.add(t.strip())
            all_items = sorted(list(teachers_set))
            label = "Select Teacher:"
            filter_col = "Teacher"

        selected_item = st.selectbox(label, all_items)

        # ฟังก์ชันสร้างตาราง (Generic)
        def create_timetable_grid(df, item_name, mode):
            slots = []
            for h in range(8, 20): 
                if h < 19:
                    slots.append({"label": f"{h:02d}:00-{h+1:02d}:00", "start": float(h), "end": float(h+1)})
            
            col_names = [s['label'] for s in slots]
            days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
            df_grid = pd.DataFrame('', index=days, columns=col_names)

            # Filter ข้อมูล
            if mode == "Room":
                item_df = df[df['Room'] == item_name]
            else:
                item_df = df[df['Teacher'].str.contains(item_name, regex=False, na=False)]

            for _, row in item_df.iterrows():
                try:
                    s_parts = row['Start'].split(':')
                    e_parts = row['End'].split(':')
                    start_val = int(s_parts[0]) + (int(s_parts[1]) / 60.0)
                    end_val = int(e_parts[0]) + (int(e_parts[1]) / 60.0)
                except: continue
                
                short_start = f"{int(s_parts[0]):02d}:{int(s_parts[1]):02d}"
                # แสดงข้อมูลในช่อง (ถ้าดูอาจารย์ ให้โชว์ห้อง / ถ้าดูห้อง ให้โชว์ชื่ออาจารย์)
                info_detail = row['Room'] if mode == "Teacher" else row['Teacher']
                course_info = f"({short_start}) {row['Course']} ({row['Type']})\n{info_detail}"

                for s in slots:
                    if max(start_val, s['start']) < min(end_val, s['end']):
                        col_name = s['label']
                        if df_grid.at[row['Day'], col_name] == '':
                            df_grid.at[row['Day'], col_name] = course_info
                        else:
                            if course_info not in df_grid.at[row['Day'], col_name]:
                                df_grid.at[row['Day'], col_name] += ' / ' + course_info
            return df_grid

        if selected_item:
            st.subheader(f"📍 Timetable for: {selected_item}")
            mode_arg = "Room" if view_mode == "🏫 Room View" else "Teacher"
            grid_df = create_timetable_grid(df_res, selected_item, mode_arg)
            st.dataframe(grid_df, use_container_width=True, height=300)

        # Download CSV
        st.divider()
        csv = df_res.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Full Schedule CSV", data=csv, file_name="full_schedule.csv", mime="text/csv")
    
    if unscheduled:
        st.divider()
        st.warning(f"⚠️ Unscheduled Tasks ({len(unscheduled)})")
        st.dataframe(pd.DataFrame(unscheduled))
