"""
QA TC Runner - 검증 결과 뷰어
- STG 브라우저 숨김 (headless)
- 전/후 스크린샷 나란히 표시
- 드래그 동작 지원
- 모든 액션 타입 전후 캡처
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from PIL import Image, ImageTk
import threading, os, sys, json, base64, re, requests, tempfile, io

EC2_API = 'http://54.180.98.47'
SHEET_URL_MAP = {
    '병원관리자':           '/contract/list',
    '병원관리자_계정생성':   '/hospital/list',
    '병원관리자_계정관리':   '/hospital/list',
    '병원관리자_병원정보':   '/hospital/list',
    '서비스 관리_클리닉':    '/service/clinic-notice/list',
    '서비스 관리_App':      '/service/care-notice/list',
    '문진 관리':            '/medical-management',
    '포인트':               '/point/clinic-point-history/list',
    '상담 관리':            '/care/user-list',
}
KEYWORD_URL_MAP = [
    ('공지사항', '/service/clinic-notice/list'),
    ('문진', '/medical-management'),
    ('회원 관리', '/care/user-list'),
    ('병원 계약', '/contract/list'),
    ('병원 관리', '/hospital/list'),
    ('포인트', '/point/clinic-point-history/list'),
    ('상담', '/care/user-list'),
]

def clean_text(text):
    if not text: return ''
    s = str(text).replace('\n',' ').replace('\r',' ').replace('\t',' ').replace('ㄴ',' ')
    return re.sub(r'\s+',' ',s).strip()

def get_target_url(stg_base, sheet_name, depth_path, expected=''):
    sheet = (sheet_name or '').strip()
    if sheet in SHEET_URL_MAP:
        return stg_base + SHEET_URL_MAP[sheet]
    cleaned = clean_text(depth_path) + ' ' + clean_text(expected)
    for key, path in KEYWORD_URL_MAP:
        if key in cleaned:
            return stg_base + path
    return stg_base + '/contract/list'

def needs_before_after(depth_path, verify_type):
    """전후 스크린샷이 필요한 TC 유형"""
    if '[입력전후_스크린샷]' in (depth_path or ''):
        return True
    return verify_type in ('INPUT', 'CLICK', 'RADIO', 'POPUP')

def b64_to_photoimage(b64_str, max_w=480, max_h=320):
    """base64 → PIL → PhotoImage"""
    try:
        img_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_data))
        # 비율 유지하며 리사이즈
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception as e:
        return None


class QAWorkerApp:
    def __init__(self, root):
        self.root = root
        self.root.title('QA TC Runner - 자동 검증')
        self.root.geometry('560x900')
        self.root.resizable(True, True)
        self.running = False
        self.session_map = {}
        self.tc_data = []
        self.current_photos = []  # GC 방지
        self._build_ui()

    def _build_ui(self):
        BG = '#F5F5F3'
        ACCENT = '#1D9E75'
        self.root.configure(bg=BG)

        # 단순 레이아웃
        left = tk.Frame(self.root, bg=BG)
        left.pack(fill='both', expand=True)

        tk.Label(left, text='🤖 QA TC Runner', font=('맑은 고딕',15,'bold'),
                 bg=BG, fg='#1a1a1a').pack(pady=(12,2))
        tk.Label(left, text='사무실 PC 자동 검증', font=('맑은 고딕',10),
                 bg=BG, fg='#888').pack(pady=(0,8))

        # 설정 프레임
        frame = tk.LabelFrame(left, text=' 설정 ', font=('맑은 고딕',10),
                               bg=BG, fg='#444', padx=12, pady=8)
        frame.pack(fill='x', padx=16, pady=(0,6))

        def lbl(text, r):
            tk.Label(frame, text=text, font=('맑은 고딕',10), bg=BG,
                     fg='#555', width=13, anchor='e').grid(row=r, column=0, pady=4, sticky='e')

        def ent(var, r, show=''):
            e = tk.Entry(frame, textvariable=var, font=('맑은 고딕',10), width=32, show=show)
            e.grid(row=r, column=1, pady=4, padx=(6,0), sticky='ew')
            frame.columnconfigure(1, weight=1)
            return e

        # EC2
        self.ec2_var = tk.StringVar(value=EC2_API)
        lbl('EC2 주소', 0); ent(self.ec2_var, 0)

        # API 키
        self.key_var = tk.StringVar(value=os.environ.get('OPENAI_API_KEY',''))
        lbl('OpenAI API 키', 1); ent(self.key_var, 1, show='*')

        # STG URL (읽기전용)
        self.stg_url_var = tk.StringVar(value='세션 선택 후 자동 설정')
        lbl('STG URL', 2)
        tk.Entry(frame, textvariable=self.stg_url_var, font=('맑은 고딕',10),
                 width=32, state='readonly', readonlybackground='#f0f0f0',
                 fg='#888').grid(row=2, column=1, pady=4, padx=(6,0), sticky='ew')

        # 세션
        lbl('세션', 3)
        sf = tk.Frame(frame, bg=BG)
        sf.grid(row=3, column=1, pady=4, padx=(6,0), sticky='ew')
        self.session_var = tk.StringVar()
        self.session_combo = ttk.Combobox(sf, textvariable=self.session_var,
            font=('맑은 고딕',10), width=24, state='readonly')
        self.session_combo.pack(side='left')
        self.session_combo.bind('<<ComboboxSelected>>', self.on_session_select)
        tk.Button(sf, text='🔄', font=('맑은 고딕',9), bg='#E3F2FD', fg='#1565C0',
            bd=0, padx=6, pady=3, cursor='hand2',
            command=self.load_sessions).pack(side='left', padx=(4,0))

        # 시트
        lbl('시트', 4)
        shf = tk.Frame(frame, bg=BG)
        shf.grid(row=4, column=1, pady=4, padx=(6,0), sticky='ew')
        self.sheet_var = tk.StringVar(value='전체')
        self.sheet_combo = ttk.Combobox(shf, textvariable=self.sheet_var,
            values=['전체'], font=('맑은 고딕',10), width=24, state='readonly')
        self.sheet_combo.pack(side='left')
        tk.Button(shf, text='🔄', font=('맑은 고딕',9), bg='#E3F2FD', fg='#1565C0',
            bd=0, padx=6, pady=3, cursor='hand2',
            command=self.load_sheets).pack(side='left', padx=(4,0))

        # 우선순위
        lbl('우선순위', 5)
        self.priority_var = tk.StringVar(value='P2')
        ttk.Combobox(frame, textvariable=self.priority_var,
            values=['P1','P2','P3','P4','P1+P2','P1+P2+P3','전체'],
            state='readonly', font=('맑은 고딕',10), width=31
        ).grid(row=5, column=1, pady=4, padx=(6,0), sticky='ew')

        # 최대 건수
        lbl('최대 건수', 6)
        mf = tk.Frame(frame, bg=BG)
        mf.grid(row=6, column=1, pady=4, padx=(6,0), sticky='ew')
        self.limit_var = tk.StringVar(value='0')
        tk.Entry(mf, textvariable=self.limit_var, font=('맑은 고딕',10), width=10).pack(side='left')
        tk.Label(mf, text='(0=전체)', font=('맑은 고딕',9), bg=BG, fg='#aaa').pack(side='left', padx=4)

        # TC 선택
        tc_frame = tk.LabelFrame(left, text=' TC 선택 ', font=('맑은 고딕',10),
                                  bg=BG, fg='#444', padx=8, pady=6)
        tc_frame.pack(fill='x', padx=16, pady=(0,6))
        tc_top = tk.Frame(tc_frame, bg=BG)
        tc_top.pack(fill='x', pady=(0,4))
        tk.Button(tc_top, text='📋 불러오기', font=('맑은 고딕',9),
            bg='#E8F5E9', fg='#2E7D32', bd=0, padx=8, pady=3,
            cursor='hand2', command=self.load_tc_list).pack(side='left')
        tk.Button(tc_top, text='✅ 전체', font=('맑은 고딕',9),
            bg='#F5F5F5', fg='#444', bd=0, padx=6, pady=3,
            cursor='hand2', command=self.select_all_tc).pack(side='left', padx=3)
        tk.Button(tc_top, text='☐ 해제', font=('맑은 고딕',9),
            bg='#F5F5F5', fg='#444', bd=0, padx=6, pady=3,
            cursor='hand2', command=self.deselect_all_tc).pack(side='left')
        self.tc_count_label = tk.Label(tc_top, text='0건', font=('맑은 고딕',9),
            bg=BG, fg='#888')
        self.tc_count_label.pack(side='right')
        tc_sf = tk.Frame(tc_frame, bg=BG)
        tc_sf.pack(fill='both', expand=True)
        tc_sb = tk.Scrollbar(tc_sf)
        tc_sb.pack(side='right', fill='y')
        self.tc_listbox = tk.Listbox(tc_sf, font=('맑은 고딕',9), height=6,
            bg='#FAFAFA', selectmode='multiple',
            selectbackground='#1D9E75', selectforeground='white',
            yscrollcommand=tc_sb.set)
        self.tc_listbox.pack(side='left', fill='both', expand=True)
        tc_sb.config(command=self.tc_listbox.yview)
        self.tc_listbox.bind('<<ListboxSelect>>', self.on_tc_select)

        # 버튼
        btn_f = tk.Frame(left, bg=BG)
        btn_f.pack(pady=6)
        self.start_btn = tk.Button(btn_f, text='▶  자동 검증 시작',
            font=('맑은 고딕',11,'bold'), bg=ACCENT, fg='white',
            padx=20, pady=7, bd=0, cursor='hand2', relief='flat',
            command=self.start_worker)
        self.start_btn.pack(side='left', padx=5)
        self.stop_btn = tk.Button(btn_f, text='■  중지',
            font=('맑은 고딕',11), bg='#E53935', fg='white',
            padx=20, pady=7, bd=0, cursor='hand2', relief='flat',
            command=self.stop_worker, state='disabled')
        self.stop_btn.pack(side='left', padx=5)

        # 진행률
        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(left, variable=self.progress_var,
                         maximum=100, length=380).pack(padx=16, pady=(2,0))
        self.status_var = tk.StringVar(value='대기 중...')
        tk.Label(left, textvariable=self.status_var,
                 font=('맑은 고딕',9), bg=BG, fg='#666').pack(pady=2)

        # 로그
        log_f = tk.LabelFrame(left, text=' 로그 ', font=('맑은 고딕',10),
                               bg=BG, fg='#444', padx=6, pady=6)
        log_f.pack(fill='both', expand=True, padx=16, pady=(0,12))
        self.log = scrolledtext.ScrolledText(log_f, font=('Consolas',8),
            height=8, bg='#1a1a1a', fg='#e0e0e0', state='disabled')
        self.log.pack(fill='both', expand=True)
        self.log.tag_config('pass', foreground='#69F0AE')
        self.log.tag_config('fail', foreground='#FF5252')
        self.log.tag_config('info', foreground='#82B1FF')
        self.log.tag_config('warn', foreground='#FFD740')

        # 결과는 웹 대시보드에서 확인
        self.before_label = None
        self.after_label = None

    def log_msg(self, msg, tag=''):
        self.log.configure(state='normal')
        self.log.insert('end', msg+'\n', tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def update_viewer(self, tc_info, judgment, reason, before_b64, after_b64):
        """결과는 웹 대시보드에서 확인"""
        pass

    def on_session_select(self, event=None):
        selected = self.session_var.get()
        info = self.session_map.get(selected, {})
        stg_url = info.get('stg_url','') if isinstance(info, dict) else ''
        self.stg_url_var.set(stg_url or '세션에 STG URL 없음')

    def on_tc_select(self, event=None):
        sel = len(self.tc_listbox.curselection())
        total = self.tc_listbox.size()
        self.tc_count_label.config(text=f'{sel}/{total}건 선택')

    def load_sessions(self):
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            resp = requests.get(f'{ec2}/api/sessions', timeout=5)
            sessions = resp.json()
            self.session_map = {}
            names = []
            for s in sessions:
                total = s.get('total_tc',0)
                ap = s.get('ai_pass',0); af = s.get('ai_fail',0)
                mp = s.get('manual_pass',0)
                label = f"[{s['id']}] {s['name']} (TC {total}건)"
                self.session_map[label] = {'id': s['id'], 'stg_url': s.get('stg_url','')}
                names.append(label)
            self.session_combo['values'] = names
            if names: self.session_combo.current(0); self.session_var.set(names[0]); self.on_session_select()
            self.log_msg(f'✅ 세션 {len(sessions)}개 로드됨', 'info')
        except Exception as e:
            messagebox.showerror('오류', f'세션 불러오기 실패: {e}')

    def load_sheets(self):
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            info = self.session_map.get(self.session_var.get(), {})
            sid = info.get('id',0) if isinstance(info,dict) else 0
            if not sid: messagebox.showwarning('알림','먼저 세션을 선택하세요'); return
            resp = requests.get(f'{ec2}/api/sessions/{sid}/sheets', timeout=5)
            sheets = resp.json()
            names = ['전체'] + [s['name'] for s in sheets]
            self.sheet_combo['values'] = names
            self.sheet_combo.current(0); self.sheet_var.set('전체')
            self.log_msg(f'✅ 시트 {len(sheets)}개 로드됨', 'info')
        except Exception as e:
            messagebox.showerror('오류', f'시트 불러오기 실패: {e}')

    def load_tc_list(self):
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            info = self.session_map.get(self.session_var.get(), {})
            sid = info.get('id',0) if isinstance(info,dict) else 0
            if not sid: messagebox.showwarning('알림','먼저 세션을 선택하세요'); return
            sheet = self.sheet_var.get(); priority = self.priority_var.get()
            import urllib.parse
            url = f'{ec2}/api/sessions/{sid}/tcs'
            if sheet and sheet != '전체': url += f'?sheet={urllib.parse.quote(sheet)}'
            resp = requests.get(url, timeout=10)
            all_tcs = resp.json()
            if priority == 'P1': tcs = [t for t in all_tcs if t.get('priority')=='P1']
            elif priority == 'P2': tcs = [t for t in all_tcs if t.get('priority')=='P2']
            elif priority == 'P3': tcs = [t for t in all_tcs if t.get('priority')=='P3']
            elif priority == 'P4': tcs = [t for t in all_tcs if t.get('priority')=='P4']
            elif priority == 'P1+P2': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2')]
            elif priority == 'P1+P2+P3': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2','P3')]
            else: tcs = all_tcs
            tcs = [t for t in tcs if not t.get('result')]
            self.tc_data = tcs
            self.tc_listbox.delete(0,'end')
            for tc in tcs:
                prio = tc.get('priority','?')
                tc_id = tc.get('tc_id', tc.get('id','?'))
                depth = tc.get('depth_path','')
                parts = depth.split(' > ') if depth else []
                label = parts[-1][:45] if parts else f'TC {tc_id}'
                self.tc_listbox.insert('end', f'[{prio}] TC{tc_id} - {label}')
            self.tc_listbox.select_set(0,'end')
            self.tc_count_label.config(text=f'{len(tcs)}/{len(tcs)}건 선택')
            self.log_msg(f'✅ TC {len(tcs)}건 로드됨 ({priority})', 'info')
        except Exception as e:
            messagebox.showerror('오류', f'TC 불러오기 실패: {e}')

    def select_all_tc(self): self.tc_listbox.select_set(0,'end'); self.on_tc_select()
    def deselect_all_tc(self): self.tc_listbox.selection_clear(0,'end'); self.on_tc_select()

    def start_worker(self):
        if not self.session_var.get(): messagebox.showerror('오류','세션을 선택하세요'); return
        if not self.key_var.get(): messagebox.showerror('오류','OpenAI API 키를 입력하세요'); return
        self.running = True
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress_var.set(0)
        threading.Thread(target=self.run_worker, daemon=True).start()

    def stop_worker(self):
        self.running = False
        self.log_msg('⏹ 중지 요청됨...', 'warn')

    def run_worker(self):
        import openai
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            api_key = self.key_var.get()
            limit = int(self.limit_var.get() or 0)
            info = self.session_map.get(self.session_var.get(), {})
            session_id = info.get('id',0) if isinstance(info,dict) else 0
            if not session_id: raise ValueError('세션을 선택하세요')

            # 세션 STG 설정
            cfg = requests.get(f'{ec2}/api/sessions/{session_id}/config', timeout=5).json()
            stg_base = cfg.get('stg_url','').rstrip('/')
            stg_login = cfg.get('stg_login_required', True)
            stg_id = cfg.get('stg_account','')
            stg_pw = cfg.get('stg_password','')
            if not stg_base: raise ValueError('세션에 STG URL이 없습니다')
            self.log_msg(f'🌐 STG: {stg_base}', 'info')

            priority = self.priority_var.get()
            client = openai.OpenAI(api_key=api_key)

            # 선택된 TC
            selected_indices = list(self.tc_listbox.curselection())
            if self.tc_data and selected_indices:
                tcs = [self.tc_data[i] for i in selected_indices]
            else:
                import urllib.parse
                sheet = self.sheet_var.get()
                url = f'{ec2}/api/sessions/{session_id}/tcs'
                if sheet and sheet != '전체': url += f'?sheet={urllib.parse.quote(sheet)}'
                all_tcs = requests.get(url, timeout=10).json()
                if priority == 'P1': tcs = [t for t in all_tcs if t.get('priority')=='P1']
                elif priority == 'P2': tcs = [t for t in all_tcs if t.get('priority')=='P2']
                elif priority == 'P3': tcs = [t for t in all_tcs if t.get('priority')=='P3']
                elif priority == 'P4': tcs = [t for t in all_tcs if t.get('priority')=='P4']
                elif priority == 'P1+P2': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2')]
                elif priority == 'P1+P2+P3': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2','P3')]
                else: tcs = all_tcs
                tcs = [t for t in tcs if not t.get('result')]
            if limit: tcs = tcs[:limit]
            if not tcs: self.log_msg('검증할 TC가 없어요', 'warn'); self._done(); return

            self.log_msg(f'🎯 대상 TC: {len(tcs)}건', 'info')
            self.status_var.set(f'0 / {len(tcs)} 완료')

            from playwright.sync_api import sync_playwright
            import sys, os, glob

            # PyInstaller 번들 시 Chromium 경로 자동 설정
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
                ms_playwright = os.path.join(base_path, 'ms-playwright')
                if os.path.exists(ms_playwright):
                    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = ms_playwright
                    self.log_msg(f'  Chromium 경로: {ms_playwright}', 'info')
                else:
                    # 대안: 사용자 AppData에서 찾기
                    user_playwright = os.path.join(os.environ.get('LOCALAPPDATA',''),
                        'ms-playwright')
                    if os.path.exists(user_playwright):
                        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = user_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False
                )
                ctx = browser.new_context(viewport={'width':1920,'height':1080})
                page = ctx.new_page()

                # 로그인
                if stg_login and stg_id:
                    self.log_msg(f'🔐 로그인 중...', 'info')
                    page.goto(f'{stg_base}/login', timeout=20000)
                    page.wait_for_load_state('networkidle', timeout=15000)
                    page.fill('input[type="text"]', stg_id)
                    page.fill('input[type="password"]', stg_pw)
                    page.click('button[type="submit"]')
                    page.wait_for_load_state('networkidle', timeout=15000)
                    page.wait_for_timeout(1500)
                    if '/login' in page.url:
                        self.log_msg('❌ 로그인 실패', 'fail')
                        browser.close(); self._done(); return
                    self.log_msg('✅ 로그인 성공', 'pass')
                else:
                    page.goto(stg_base, timeout=20000)
                    page.wait_for_load_state('networkidle', timeout=15000)
                    self.log_msg(f'✅ {stg_base} 접속', 'pass')

                results = {'PASS':0,'FAIL':0,'ERROR':0}
                for i, tc in enumerate(tcs):
                    if not self.running: break
                    sheet = tc.get('sheet_name','')
                    tc_id = tc.get('tc_id', str(tc.get('id','')))
                    depth = tc.get('depth_path','')
                    expected = clean_text(tc.get('expected',''))
                    precondition = clean_text(tc.get('precondition',''))
                    verify_type = tc.get('verify_type','DISPLAY')

                    self.log_msg(f'\n[{i+1}/{len(tcs)}] TC {tc_id} | {tc.get("priority","?")} | {sheet}', 'info')

                    try:
                        target_url = get_target_url(stg_base, sheet, depth, tc.get('expected',''))
                        self.log_msg(f'  → {target_url}')
                        page.goto(target_url, timeout=20000)
                        page.wait_for_load_state('networkidle', timeout=15000)
                        page.wait_for_timeout(2000)
                        self.log_msg(f'  🌐 이동: {page.url}')

                        # 전후 스크린샷 필요 여부
                        do_before_after = needs_before_after(depth, verify_type)
                        before_b64 = None
                        actions_done = []

                        # ── 액션 전 스크린샷 ──
                        if do_before_after:
                            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                                before_path = f.name
                            page.screenshot(path=before_path, full_page=True)
                            with open(before_path,'rb') as f:
                                before_b64 = base64.b64encode(f.read()).decode()
                            try: os.unlink(before_path)
                            except: pass

                        # ── HTML 분석 → GPT 액션 계획 ──
                        html = page.inner_html('body')
                        html = re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.DOTALL)
                        html = re.sub(r'<style[^>]*>.*?</style>','',html,flags=re.DOTALL)
                        html = re.sub(r'style="[^"]*"','',html)
                        html = re.sub(r'\s+',' ',html)[:5000]

                        prompt_action = f"""TC 검증을 위한 Playwright 액션을 JSON으로만 반환하세요.
[기능경로] {depth[:300]}
[사전조건] {precondition[:200]}
[기대결과] {expected[:300]}
[HTML] {html[:4000]}

규칙:
1. 텍스트 기반 셀렉터 우선: button:has-text(), label:has-text()
2. rgba/복잡한클래스 절대 금지
3. 드래그가 필요하면 type:drag 사용
4. 입력이 필요하면 type:fill 사용

JSON: {{"actions":[
  {{"type":"click","selector":"button:has-text('조회')","description":"조회"}},
  {{"type":"fill","selector":"input[placeholder*='검색']","value":"테스트","description":"검색어 입력"}},
  {{"type":"drag","source":"셀렉터","target":"셀렉터","description":"드래그"}},
  {{"type":"wait","ms":500}}
]}}
액션없으면: {{"actions":[]}}"""

                        try:
                            r = client.chat.completions.create(
                                model='gpt-4o-mini',
                                messages=[{'role':'user','content':prompt_action}],
                                max_tokens=600, temperature=0)
                            raw = re.sub(r'```json|```','',r.choices[0].message.content.strip()).strip()
                            actions = json.loads(raw).get('actions',[])
                            self.log_msg(f'  📋 액션 {len(actions)}건')
                        except: actions = []

                        # ── 액션 실행 ──
                        for action in actions:
                            if not self.running: break
                            atype = action.get('type','')
                            desc = action.get('description', atype)
                            sel = action.get('selector','')
                            dangerous = ['rgba(','rgb(','style=','!important']

                            if atype == 'click':
                                if any(d in sel for d in dangerous):
                                    self.log_msg(f'  ⚠ 셀렉터 차단: {desc}', 'warn'); continue
                                clicked = False
                                try:
                                    el = page.locator(sel).first
                                    if el.is_visible(timeout=3000):
                                        el.click(); page.wait_for_timeout(800)
                                        self.log_msg(f'  ✓ 클릭: {desc}')
                                        actions_done.append(f'클릭: {desc}'); clicked = True
                                except: pass
                                if not clicked:
                                    hints = re.findall(r'[가-힣a-zA-Z0-9]{2,}', desc)
                                    for t in hints[:2]:
                                        for tag in ['button','label','a','span']:
                                            try:
                                                el2 = page.locator(f'{tag}:has-text("{t}")').first
                                                if el2.is_visible(timeout=1500):
                                                    el2.click(); page.wait_for_timeout(800)
                                                    self.log_msg(f'  ✓ 클릭(폴백): {t}')
                                                    actions_done.append(f'클릭: {t}'); clicked = True; break
                                            except: continue
                                        if clicked: break
                                if not clicked:
                                    self.log_msg(f'  ✗ 클릭 실패: {desc}', 'warn')
                                    actions_done.append(f'클릭 실패: {desc}')

                            elif atype == 'fill':
                                val = str(action.get('value','테스트'))
                                try:
                                    el = page.locator(sel).first
                                    if el.is_visible(timeout=3000):
                                        el.fill(val); page.wait_for_timeout(500)
                                        self.log_msg(f'  ✓ 입력: {desc}')
                                        actions_done.append(f'입력: {desc}')
                                except Exception as e:
                                    self.log_msg(f'  ✗ 입력 실패: {desc}', 'warn')

                            elif atype == 'drag':
                                src = action.get('source','')
                                tgt = action.get('target','')
                                try:
                                    page.drag_and_drop(src, tgt)
                                    page.wait_for_timeout(800)
                                    self.log_msg(f'  ✓ 드래그: {desc}')
                                    actions_done.append(f'드래그: {desc}')
                                except Exception as e:
                                    self.log_msg(f'  ✗ 드래그 실패: {desc}', 'warn')
                                    actions_done.append(f'드래그 실패: {desc}')

                            elif atype == 'wait':
                                ms = min(int(action.get('ms',1000)),3000)
                                page.wait_for_timeout(ms)

                            try: page.wait_for_load_state('networkidle', timeout=3000)
                            except: pass

                        # ── 액션 후 스크린샷 ──
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                            ss_path = f.name
                        page.screenshot(path=ss_path, full_page=True)
                        with open(ss_path,'rb') as f:
                            after_b64 = base64.b64encode(f.read()).decode()
                        try: os.unlink(ss_path)
                        except: pass

                        try: body_text = page.inner_text('body')[:3000]
                        except: body_text = ''

                        # ── GPT-4o 판정 ──
                        prompt_judge = f"""QA 판정 전문가입니다.
[화면] {sheet} - {depth[:100]}
[액션] {', '.join(actions_done) if actions_done else '없음'}
[사전조건] {precondition[:300]}
[기대결과] {expected[:500]}
[텍스트] {body_text[:1500]}"""

                        if do_before_after and before_b64:
                            prompt_judge += '\n[첫번째=액션전, 두번째=액션후] 전후 변화를 비교하여 판정하세요.'
                            content_msgs = [
                                {'type':'text','text':prompt_judge},
                                {'type':'image_url','image_url':{'url':f'data:image/png;base64,{before_b64}','detail':'high'}},
                                {'type':'image_url','image_url':{'url':f'data:image/png;base64,{after_b64}','detail':'high'}}
                            ]
                        else:
                            content_msgs = [
                                {'type':'text','text':prompt_judge},
                                {'type':'image_url','image_url':{'url':f'data:image/png;base64,{after_b64}','detail':'high'}}
                            ]
                        content_msgs.append({'type':'text','text':'JSON으로만: {"judgment":"PASS","reason":"근거"} 또는 {"judgment":"FAIL","reason":"근거"}'})

                        r2 = client.chat.completions.create(
                            model='gpt-4o',
                            messages=[{'role':'user','content':content_msgs}],
                            max_tokens=400, temperature=0)
                        raw2 = re.sub(r'```json|```','',r2.choices[0].message.content.strip()).strip()
                        parsed = json.loads(raw2)
                        judgment = parsed.get('judgment','FAIL')
                        reason = parsed.get('reason','')
                        if judgment not in ('PASS','FAIL'): judgment='FAIL'

                        # EC2 전송 (전/후 스크린샷 포함)
                        payload = {
                            'result': judgment, 'result_type': 'ai',
                            'memo': '', 'ai_judgment': judgment,
                            'ai_reason': reason[:1000],
                            'screenshot_b64': after_b64,
                        }
                        if before_b64:
                            payload['screenshot_before_b64'] = before_b64
                        requests.put(f'{ec2}/api/results/{tc["id"]}', json=payload, timeout=30)

                        results[judgment] = results.get(judgment,0)+1
                        tag = 'pass' if judgment=='PASS' else 'fail'
                        self.log_msg(f'  {"✅" if judgment=="PASS" else "❌"} {judgment} | {reason[:80]}', tag)

                        # 뷰어 업데이트 (메인 스레드)
                        tc_info = f'TC {tc_id} | {tc.get("priority","?")} | {sheet}\n{depth[:80]}'
                        self.root.after(0, self.update_viewer,
                            tc_info, judgment, reason,
                            before_b64 if do_before_after else None,
                            after_b64)

                    except Exception as e:
                        self.log_msg(f'  ❌ 오류: {str(e)[:100]}', 'fail')
                        results['ERROR'] = results.get('ERROR',0)+1

                    # 진행률
                    pct = (i+1)/len(tcs)*100
                    self.progress_var.set(pct)
                    done = results['PASS']+results['FAIL']+results.get('ERROR',0)
                    self.status_var.set(f'{done}/{len(tcs)} | ✅{results["PASS"]} ❌{results["FAIL"]}')

                browser.close()

            self.log_msg(f'\n{"="*40}', 'info')
            self.log_msg(f'✅ 완료! PASS:{results["PASS"]} FAIL:{results["FAIL"]} ERROR:{results.get("ERROR",0)}', 'pass')
            self.log_msg(f'결과: {ec2}', 'info')
            messagebox.showinfo('완료', f'PASS: {results["PASS"]}건\nFAIL: {results["FAIL"]}건')

        except Exception as e:
            self.log_msg(f'❌ 오류: {str(e)}', 'fail')
            messagebox.showerror('오류', str(e))
        finally:
            self._done()

    def _done(self):
        self.running = False
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')


if __name__ == '__main__':
    root = tk.Tk()
    app = QAWorkerApp(root)
    root.mainloop()
