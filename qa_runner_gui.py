"""
QA TC Runner - 로컬 실행기 (GUI)
PyInstaller로 exe 빌드 가능
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading, os, sys, json, base64, re, requests, tempfile

# ── 설정 ──────────────────────────────────────
EC2_API = 'http://54.180.98.47'
# STG 설정은 세션에서 자동으로 가져옴 (하드코딩 없음)
STG_CONFIGS = {}
SHEET_URL_MAP = {
    '병원관리자':        '/contract/list',
    '병원관리자_계정생성':'/hospital/list',
    '병원관리자_계정관리':'/hospital/list',
    '병원관리자_병원정보':'/hospital/list',
    '서비스 관리_클리닉': '/service/clinic-notice/list',
    '서비스 관리_App':   '/service/care-notice/list',
    '문진 관리':         '/medical-management',
    '포인트':            '/point/clinic-point-history/list',
    '상담 관리':         '/care/user-list',
}
KEYWORD_URL_MAP = [
    ('공지사항', '/service/clinic-notice/list'),
    ('자료실',   '/service/clinic-library/list'),
    ('문진',     '/medical-management'),
    ('회원 관리','/care/user-list'),
    ('사용자 관리','/care/user-list'),
    ('병원 계약','/contract/list'),
    ('포인트 내역','/point/clinic-point-history/list'),
    ('콘텐츠',   '/content/item/list'),
    ('상담',     '/care/user-list'),
]


def clean_text(text):
    if not text: return ''
    s = str(text).replace('\n',' ').replace('\r',' ').replace('\t',' ').replace('ㄴ',' ')
    return re.sub(r'\s+', ' ', s).strip()

def get_target_url(stg_base, sheet_name, depth_path, expected=''):
    sheet = (sheet_name or '').strip()
    if sheet in SHEET_URL_MAP:
        return stg_base + SHEET_URL_MAP[sheet]
    cleaned = clean_text(depth_path) + ' ' + clean_text(expected)
    for key, path in KEYWORD_URL_MAP:
        if key in cleaned:
            return stg_base + path
    return stg_base + '/contract/list'


class QAWorkerApp:
    def __init__(self, root):
        self.root = root
        self.root.title('QA TC Runner - 자동 검증')
        self.root.geometry('700x600')
        self.root.resizable(False, False)
        self.running = False
        self._build_ui()

    def _build_ui(self):
        # 색상
        BG = '#F5F5F3'
        ACCENT = '#1D9E75'
        self.root.configure(bg=BG)

        # 타이틀
        tk.Label(self.root, text='🤖 QA TC Runner', font=('맑은 고딕', 16, 'bold'),
                 bg=BG, fg='#1a1a1a').pack(pady=(16, 4))
        tk.Label(self.root, text='사무실 PC에서 STG 자동 검증 실행', font=('맑은 고딕', 10),
                 bg=BG, fg='#888').pack(pady=(0, 12))

        # 설정 프레임
        frame = tk.LabelFrame(self.root, text=' 설정 ', font=('맑은 고딕', 10),
                               bg=BG, fg='#444', padx=16, pady=12)
        frame.pack(fill='x', padx=20, pady=(0, 8))

        def row(label, widget_fn, r):
            tk.Label(frame, text=label, font=('맑은 고딕', 10), bg=BG,
                     fg='#444', width=14, anchor='e').grid(row=r, column=0, pady=5, sticky='e')
            w = widget_fn(frame)
            w.grid(row=r, column=1, pady=5, padx=(8,0), sticky='ew')
            frame.columnconfigure(1, weight=1)
            return w

        # EC2 주소
        self.ec2_var = tk.StringVar(value=EC2_API)
        row('EC2 주소', lambda f: tk.Entry(f, textvariable=self.ec2_var,
            font=('맑은 고딕', 10), width=40), 0)

        # OpenAI API 키
        self.key_var = tk.StringVar(value=os.environ.get('OPENAI_API_KEY',''))
        row('OpenAI API 키', lambda f: tk.Entry(f, textvariable=self.key_var,
            font=('맑은 고딕', 10), show='*', width=40), 1)

        # STG URL (세션에서 자동으로 가져옴 - 표시용)
        self.stg_url_var = tk.StringVar(value='세션 선택 후 자동 설정됩니다')
        stg_label = tk.Entry(frame, textvariable=self.stg_url_var,
            font=('맑은 고딕', 10), width=40, state='readonly',
            readonlybackground='#F5F5F5', fg='#888')
        tk.Label(frame, text='STG URL', font=('맑은 고딕', 10), bg=BG,
                 fg='#444', width=14, anchor='e').grid(row=2, column=0, pady=5, sticky='e')
        stg_label.grid(row=2, column=1, pady=5, padx=(8,0), sticky='ew')

        # 세션 불러오기
        sess_frame = tk.Frame(frame, bg=BG)
        self.session_var = tk.StringVar(value='')
        self.session_combo = ttk.Combobox(sess_frame, textvariable=self.session_var,
            font=('맑은 고딕', 10), width=30, state='readonly')
        self.session_combo.pack(side='left')
        self.session_combo.bind('<<ComboboxSelected>>', self.on_session_select)
        tk.Button(sess_frame, text='🔄 불러오기', font=('맑은 고딕', 9),
            bg='#E3F2FD', fg='#1565C0', bd=0, padx=8, pady=4,
            cursor='hand2', command=self.load_sessions).pack(side='left', padx=(6,0))
        tk.Label(frame, text='세션 선택', font=('맑은 고딕', 10), bg=BG,
                 fg='#444', width=14, anchor='e').grid(row=3, column=0, pady=5, sticky='e')
        sess_frame.grid(row=3, column=1, pady=5, padx=(8,0), sticky='ew')
        self.session_map = {}  # 표시명 → ID

        # 시트 선택
        sheet_frame = tk.Frame(frame, bg=BG)
        self.sheet_var = tk.StringVar(value='전체')
        self.sheet_combo = ttk.Combobox(sheet_frame, textvariable=self.sheet_var,
            values=['전체'], state='readonly', font=('맑은 고딕', 10), width=30)
        self.sheet_combo.pack(side='left')
        tk.Button(sheet_frame, text='🔄', font=('맑은 고딕', 9),
            bg='#E3F2FD', fg='#1565C0', bd=0, padx=6, pady=4,
            cursor='hand2', command=self.load_sheets).pack(side='left', padx=(4,0))
        tk.Label(frame, text='시트 선택', font=('맑은 고딕', 10), bg=BG,
                 fg='#444', width=14, anchor='e').grid(row=4, column=0, pady=5, sticky='e')
        sheet_frame.grid(row=4, column=1, pady=5, padx=(8,0), sticky='ew')

        # 우선순위
        self.priority_var = tk.StringVar(value='P1')
        row('우선순위 필터', lambda f: ttk.Combobox(f, textvariable=self.priority_var,
            values=['P1', 'P2', 'P3', 'P4', 'P1+P2', 'P1+P2+P3', '전체'], state='readonly',
            font=('맑은 고딕', 10), width=38), 5)

        # 최대 건수
        self.limit_var = tk.StringVar(value='0')
        row('최대 건수', lambda f: tk.Entry(f, textvariable=self.limit_var,
            font=('맑은 고딕', 10), width=40), 6)
        tk.Label(frame, text='(0 = 전체)', font=('맑은 고딕', 9),
                 bg=BG, fg='#aaa').grid(row=6, column=2, padx=4)

        # TC 목록 체크박스 영역
        tc_frame = tk.LabelFrame(self.root, text=' TC 선택 (비우면 전체 실행) ', 
                                  font=('맑은 고딕', 10), bg=BG, fg='#444', padx=8, pady=6)
        tc_frame.pack(fill='x', padx=20, pady=(0, 6))

        tc_top = tk.Frame(tc_frame, bg=BG)
        tc_top.pack(fill='x', pady=(0,4))
        tk.Button(tc_top, text='📋 TC 목록 불러오기', font=('맑은 고딕', 9),
            bg='#E8F5E9', fg='#2E7D32', bd=0, padx=10, pady=4,
            cursor='hand2', command=self.load_tc_list).pack(side='left')
        tk.Button(tc_top, text='✅ 전체 선택', font=('맑은 고딕', 9),
            bg='#F5F5F5', fg='#444', bd=0, padx=8, pady=4,
            cursor='hand2', command=self.select_all_tc).pack(side='left', padx=4)
        tk.Button(tc_top, text='☐ 전체 해제', font=('맑은 고딕', 9),
            bg='#F5F5F5', fg='#444', bd=0, padx=8, pady=4,
            cursor='hand2', command=self.deselect_all_tc).pack(side='left')
        self.tc_count_label = tk.Label(tc_top, text='0건', font=('맑은 고딕', 9),
            bg=BG, fg='#888')
        self.tc_count_label.pack(side='right')

        # 체크박스 스크롤 영역
        tc_scroll_frame = tk.Frame(tc_frame, bg=BG)
        tc_scroll_frame.pack(fill='both', expand=True)
        tc_scrollbar = tk.Scrollbar(tc_scroll_frame)
        tc_scrollbar.pack(side='right', fill='y')
        self.tc_listbox = tk.Listbox(tc_scroll_frame, font=('맑은 고딕', 9),
            height=5, bg='#FAFAFA', selectmode='multiple',
            selectbackground='#1D9E75', selectforeground='white',
            yscrollcommand=tc_scrollbar.set)
        self.tc_listbox.pack(side='left', fill='both', expand=True)
        self.tc_listbox.bind('<<ListboxSelect>>', self.on_tc_select)
        tc_scrollbar.config(command=self.tc_listbox.yview)
        self.tc_data = []  # TC 목록 저장

        # 버튼
        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(pady=8)

        self.start_btn = tk.Button(btn_frame, text='▶  자동 검증 시작',
            font=('맑은 고딕', 11, 'bold'), bg=ACCENT, fg='white',
            padx=24, pady=8, bd=0, cursor='hand2',
            command=self.start_worker, relief='flat')
        self.start_btn.pack(side='left', padx=6)

        self.stop_btn = tk.Button(btn_frame, text='■  중지',
            font=('맑은 고딕', 11), bg='#E53935', fg='white',
            padx=24, pady=8, bd=0, cursor='hand2',
            command=self.stop_worker, relief='flat', state='disabled')
        self.stop_btn.pack(side='left', padx=6)

        # 진행률
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(self.root, variable=self.progress_var,
                                         maximum=100, length=660)
        self.progress.pack(padx=20, pady=(4, 0))

        self.status_var = tk.StringVar(value='대기 중...')
        tk.Label(self.root, textvariable=self.status_var,
                 font=('맑은 고딕', 10), bg=BG, fg='#666').pack(pady=4)

        # 로그
        log_frame = tk.LabelFrame(self.root, text=' 로그 ', font=('맑은 고딕', 10),
                                   bg=BG, fg='#444', padx=8, pady=8)
        log_frame.pack(fill='both', expand=True, padx=20, pady=(0, 16))

        self.log = scrolledtext.ScrolledText(log_frame, font=('Consolas', 9),
            height=10, bg='#1a1a1a', fg='#e0e0e0', insertbackground='white',
            state='disabled')
        self.log.pack(fill='both', expand=True)
        self.log.tag_config('pass', foreground='#69F0AE')
        self.log.tag_config('fail', foreground='#FF5252')
        self.log.tag_config('info', foreground='#82B1FF')
        self.log.tag_config('warn', foreground='#FFD740')

    def log_msg(self, msg, tag=''):
        self.log.configure(state='normal')
        self.log.insert('end', msg + '\n', tag)
        self.log.see('end')
        self.log.configure(state='disabled')

    def on_tc_select(self, event=None):
        selected = len(self.tc_listbox.curselection())
        total = self.tc_listbox.size()
        self.tc_count_label.config(text=f'{selected}/{total}건 선택')

    def on_session_select(self, event=None):
        selected = self.session_var.get()
        info = self.session_map.get(selected, {})
        stg_url = info.get('stg_url','') if isinstance(info, dict) else ''
        self.stg_url_var.set(stg_url or '세션에 STG URL 없음')

    def load_tc_list(self):
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            selected = self.session_var.get()
            info = self.session_map.get(selected, {})
            session_id = info.get('id', 0) if isinstance(info, dict) else 0
            if not session_id:
                messagebox.showwarning('알림', '먼저 세션을 선택하세요')
                return
            sheet = self.sheet_var.get()
            priority = self.priority_var.get()
            url = f'{ec2}/api/sessions/{session_id}/tcs'
            if sheet and sheet != '전체':
                import urllib.parse
                url += f'?sheet={urllib.parse.quote(sheet)}'
            resp = requests.get(url, timeout=10)
            all_tcs = resp.json()

            # 우선순위 필터
            if priority == 'P1': tcs = [t for t in all_tcs if t.get('priority')=='P1']
            elif priority == 'P2': tcs = [t for t in all_tcs if t.get('priority')=='P2']
            elif priority == 'P3': tcs = [t for t in all_tcs if t.get('priority')=='P3']
            elif priority == 'P4': tcs = [t for t in all_tcs if t.get('priority')=='P4']
            elif priority == 'P1+P2': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2')]
            elif priority == 'P1+P2+P3': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2','P3')]
            else: tcs = all_tcs

            # 미검증만
            tcs = [t for t in tcs if not t.get('result')]
            self.tc_data = tcs

            # 목록 표시
            self.tc_listbox.delete(0, 'end')
            for tc in tcs:
                prio = tc.get('priority','?')
                tc_id = tc.get('tc_id', tc.get('id','?'))
                depth = tc.get('depth_path','')
                parts = depth.split(' > ') if depth else []
                label = parts[-1] if parts else f'TC {tc_id}'
                self.tc_listbox.insert('end', f'[{prio}] TC{tc_id} - {label[:40]}')
            # 전체 선택
            self.tc_listbox.select_set(0, 'end')
            self.tc_count_label.config(text=f'{len(tcs)}건')
            self.log_msg(f'✅ TC {len(tcs)}건 로드됨 ({priority})', 'info')
        except Exception as e:
            messagebox.showerror('오류', f'TC 불러오기 실패: {e}')

    def select_all_tc(self):
        self.tc_listbox.select_set(0, 'end')

    def deselect_all_tc(self):
        self.tc_listbox.selection_clear(0, 'end')

    def load_sheets(self):
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            selected = self.session_var.get()
            info = self.session_map.get(selected, {})
            session_id = info.get('id', 0) if isinstance(info, dict) else 0
            if not session_id:
                messagebox.showwarning('알림', '먼저 세션을 선택하세요')
                return
            resp = requests.get(f'{ec2}/api/sessions/{session_id}/sheets', timeout=5)
            sheets = resp.json()
            names = ['전체'] + [s['name'] for s in sheets]
            self.sheet_combo['values'] = names
            self.sheet_combo.current(0)
            self.sheet_var.set('전체')
            self.log_msg(f'✅ 시트 {len(sheets)}개 로드됨', 'info')
        except Exception as e:
            messagebox.showerror('오류', f'시트 불러오기 실패: {e}')

    def load_sessions(self):
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            resp = requests.get(f'{ec2}/api/sessions', timeout=5)
            sessions = resp.json()
            self.session_map = {}
            names = []
            for s in sessions:
                total = s.get('total_tc', 0)
                ap = s.get('ai_pass', 0)
                af = s.get('ai_fail', 0)
                mp = s.get('manual_pass', 0)
                label = f"[{s['id']}] {s['name']} (TC {total}건 | 🤖{ap+af} 👤{mp})"
                self.session_map[label] = {
                    'id': s['id'],
                    'stg_url': s.get('stg_url',''),
                }
                names.append(label)
            self.session_combo['values'] = names
            if names:
                self.session_combo.current(0)
                self.session_var.set(names[0])
            self.log_msg(f'✅ 세션 {len(sessions)}개 로드됨', 'info')
        except Exception as e:
            messagebox.showerror('오류', f'세션 불러오기 실패: {e}')

    def start_worker(self):
        selected = self.session_var.get()
        if not selected:
            messagebox.showerror('오류', '세션을 선택하세요. 불러오기 버튼을 먼저 클릭하세요')
            return
        if not self.key_var.get():
            messagebox.showerror('오류', 'OpenAI API 키를 입력하세요')
            return
        self.running = True
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress_var.set(0)
        t = threading.Thread(target=self.run_worker, daemon=True)
        t.start()

    def stop_worker(self):
        self.running = False
        self.log_msg('⏹ 중지 요청됨...', 'warn')

    def run_worker(self):
        import openai
        try:
            ec2 = self.ec2_var.get().rstrip('/')
            api_key = self.key_var.get()
            selected = self.session_var.get()
            info = self.session_map.get(selected, {})
            session_id = info.get('id', 0) if isinstance(info, dict) else 0
            if not session_id:
                raise ValueError('세션을 선택하세요')

            # 세션 STG 설정 가져오기
            cfg_resp = requests.get(f'{ec2}/api/sessions/{session_id}/config', timeout=5)
            cfg = cfg_resp.json()
            stg_base = cfg.get('stg_url','').rstrip('/')
            stg_login = cfg.get('stg_login_required', True)
            stg_id = cfg.get('stg_account','')
            stg_pw = cfg.get('stg_password','')
            if not stg_base:
                raise ValueError('세션에 STG URL이 없습니다. 웹에서 세션 설정을 확인하세요')
            self.log_msg(f'🌐 STG: {stg_base}', 'info')
            priority = self.priority_var.get()
            limit = int(self.limit_var.get() or 0)

            client = openai.OpenAI(api_key=api_key)

            # TC 목록
            self.log_msg(f'📋 TC 목록 조회 중... (세션 {session_id})', 'info')
            # 체크박스에서 선택된 TC 사용
            selected_indices = list(self.tc_listbox.curselection())
            if self.tc_data and selected_indices:
                tcs = [self.tc_data[i] for i in selected_indices]
                self.log_msg(f'📋 선택된 TC: {len(tcs)}건', 'info')
            else:
                # TC 목록 없으면 API에서 가져오기
                sheet = self.sheet_var.get()
                import urllib.parse
                if sheet and sheet != '전체':
                    resp = requests.get(f'{ec2}/api/sessions/{session_id}/tcs?sheet={urllib.parse.quote(sheet)}', timeout=10)
                else:
                    resp = requests.get(f'{ec2}/api/sessions/{session_id}/tcs', timeout=10)
                all_tcs = resp.json()
                if priority == 'P1': tcs = [t for t in all_tcs if t.get('priority')=='P1']
                elif priority == 'P2': tcs = [t for t in all_tcs if t.get('priority')=='P2']
                elif priority == 'P3': tcs = [t for t in all_tcs if t.get('priority')=='P3']
                elif priority == 'P4': tcs = [t for t in all_tcs if t.get('priority')=='P4']
                elif priority == 'P1+P2': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2')]
                elif priority == 'P1+P2+P3': tcs = [t for t in all_tcs if t.get('priority') in ('P1','P2','P3')]
                else: tcs = all_tcs
                tcs = [t for t in tcs if not t.get('result')]
            if limit: tcs = tcs[:limit]

            if not tcs:
                self.log_msg('✅ 검증할 TC가 없어요', 'pass')
                self._done()
                return

            self.log_msg(f'🎯 대상 TC: {len(tcs)}건 | {priority}', 'info')
            self.status_var.set(f'0 / {len(tcs)} 완료')

            from playwright.sync_api import sync_playwright
            import sys, os
            # PyInstaller 번들 실행 시 Chromium 경로 설정
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
                os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(base_path, 'ms-playwright')
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                ctx = browser.new_context(viewport={'width':1440,'height':900})
                page = ctx.new_page()

                # 로그인
                if stg_login and stg_id:
                    self.log_msg(f'🔐 {stg_base} 로그인 중...', 'info')
                    page.goto(f'{stg_base}/login', timeout=20000)
                    page.wait_for_load_state('networkidle', timeout=15000)
                    page.fill('input[type="text"]', stg_id)
                    page.fill('input[type="password"]', stg_pw)
                    page.click('button[type="submit"]')
                    page.wait_for_load_state('networkidle', timeout=15000)
                    page.wait_for_timeout(1500)
                    if '/login' in page.url:
                        self.log_msg('❌ 로그인 실패 - ID/PW 확인하세요', 'fail')
                        browser.close()
                        self._done()
                        return
                    self.log_msg('✅ 로그인 성공', 'pass')
                else:
                    page.goto(stg_base, timeout=20000)
                    page.wait_for_load_state('networkidle', timeout=15000)
                    self.log_msg(f'✅ {stg_base} 접속 (로그인 없음)', 'pass')

                results = {'PASS':0,'FAIL':0,'ERROR':0}
                for i, tc in enumerate(tcs):
                    if not self.running: break
                    sheet = tc.get('sheet_name','')
                    tc_id = tc.get('tc_id', str(tc.get('id','')))
                    expected = clean_text(tc.get('expected',''))
                    precondition = clean_text(tc.get('precondition',''))

                    self.log_msg(f'\n[{i+1}/{len(tcs)}] TC {tc_id} | {tc.get("priority","?")} | {sheet}', 'info')

                    try:
                        target_url = get_target_url(stg_base, sheet,
                            tc.get('depth_path',''), tc.get('expected',''))
                        self.log_msg(f'  → {target_url}')

                        page.goto(target_url, timeout=20000)
                        page.wait_for_load_state('networkidle', timeout=15000)
                        page.wait_for_timeout(2000)

                        # HTML → GPT 액션 계획
                        actions_done = []
                        if expected or precondition:
                            html = page.inner_html('body')
                            html = re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.DOTALL)
                            html = re.sub(r'<style[^>]*>.*?</style>','',html,flags=re.DOTALL)
                            html = re.sub(r'style="[^"]*"','',html)
                            html = re.sub(r'\s+',' ',html)[:5000]

                            try:
                                prompt = f"""TC 검증을 위한 Playwright 액션을 JSON으로만 반환하세요.
[사전조건] {precondition[:300]}
[기대결과] {expected[:500]}
[HTML] {html[:4000]}
규칙: 텍스트 기반 셀렉터 우선(has-text), rgba/복잡한클래스 금지
JSON: {{"actions":[{{"type":"click","selector":"button:has-text('조회')","description":"조회"}}]}}
액션없으면: {{"actions":[]}}"""
                                r = client.chat.completions.create(
                                    model='gpt-4o-mini',
                                    messages=[{'role':'user','content':prompt}],
                                    max_tokens=600, temperature=0)
                                raw = re.sub(r'```json|```','',r.choices[0].message.content.strip()).strip()
                                actions = json.loads(raw).get('actions',[])
                                self.log_msg(f'  📋 액션 {len(actions)}건')

                                for action in actions:
                                    if not self.running: break
                                    atype = action.get('type','')
                                    desc = action.get('description', atype)
                                    sel = action.get('selector','')
                                    if atype == 'click':
                                        dangerous = ['rgba(','rgb(','style=']
                                        if any(d in sel for d in dangerous):
                                            self.log_msg(f'  ⚠ 셀렉터 차단: {desc}', 'warn')
                                            continue
                                        try:
                                            el = page.locator(sel).first
                                            if el.is_visible(timeout=3000):
                                                el.click(); page.wait_for_timeout(800)
                                                self.log_msg(f'  ✓ 클릭: {desc}')
                                                actions_done.append(f'클릭: {desc}')
                                            else:
                                                hints = re.findall(r'[가-힣a-zA-Z0-9]{2,}', desc)
                                                for t in hints[:2]:
                                                    for tag in ['button','label','a']:
                                                        try:
                                                            el2 = page.locator(f'{tag}:has-text("{t}")').first
                                                            if el2.is_visible(timeout=1500):
                                                                el2.click(); page.wait_for_timeout(800)
                                                                self.log_msg(f'  ✓ 클릭(폴백): {t}')
                                                                actions_done.append(f'클릭: {t}')
                                                                break
                                                        except: continue
                                        except Exception as e:
                                            self.log_msg(f'  ✗ 클릭 실패: {desc}', 'warn')
                                            actions_done.append(f'클릭 실패: {desc}')
                                    elif atype == 'fill':
                                        try:
                                            el = page.locator(sel).first
                                            if el.is_visible(timeout=3000):
                                                el.fill(str(action.get('value','')))
                                                page.wait_for_timeout(300)
                                                self.log_msg(f'  ✓ 입력: {desc}')
                                                actions_done.append(f'입력: {desc}')
                                        except: pass
                                    elif atype == 'wait':
                                        ms = min(int(action.get('ms',1000)),3000)
                                        page.wait_for_timeout(ms)
                                    elif atype in ('wait_network','wait_popup'):
                                        page.wait_for_timeout(1500)

                                try: page.wait_for_load_state('networkidle',timeout=5000)
                                except: pass

                            except Exception as e:
                                self.log_msg(f'  ⚠ 액션 오류: {str(e)[:80]}', 'warn')

                        # 스크린샷
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                            ss_path = f.name
                        page.screenshot(path=ss_path, full_page=False)

                        # GPT-4o 판정
                        with open(ss_path,'rb') as f:
                            img_b64 = base64.b64encode(f.read()).decode()
                        try: body_text = page.inner_text('body')[:3000]
                        except: body_text = ''

                        prompt = f"""QA 판정 전문가입니다.
[화면] {sheet}
[액션] {', '.join(actions_done) if actions_done else '없음'}
[사전조건] {precondition[:300]}
[기대결과] {expected[:500]}
[텍스트] {body_text[:2000]}
JSON으로만: {{"judgment":"PASS","reason":"근거"}} 또는 {{"judgment":"FAIL","reason":"근거"}}"""

                        r2 = client.chat.completions.create(
                            model='gpt-4o',
                            messages=[{'role':'user','content':[
                                {'type':'text','text':prompt},
                                {'type':'image_url','image_url':{'url':f'data:image/png;base64,{img_b64}','detail':'high'}}
                            ]}],
                            max_tokens=400, temperature=0)
                        raw2 = re.sub(r'```json|```','',r2.choices[0].message.content.strip()).strip()
                        parsed = json.loads(raw2)
                        judgment = parsed.get('judgment','FAIL')
                        reason = parsed.get('reason','')
                        if judgment not in ('PASS','FAIL'): judgment = 'FAIL'

                        os.unlink(ss_path)

                        # EC2 전송
                        requests.put(f'{ec2}/api/results/{tc["id"]}', json={
                            'result': judgment, 'result_type': 'ai',
                            'memo': '', 'ai_judgment': judgment,
                            'ai_reason': reason[:1000],
                            'screenshot_b64': img_b64,
                        }, timeout=30)

                        results[judgment] = results.get(judgment,0) + 1
                        tag = 'pass' if judgment=='PASS' else 'fail'
                        self.log_msg(f'  {"✅" if judgment=="PASS" else "❌"} {judgment} | {reason[:80]}', tag)

                    except Exception as e:
                        self.log_msg(f'  ❌ 오류: {str(e)[:100]}', 'fail')
                        results['ERROR'] = results.get('ERROR',0) + 1

                    # 진행률 업데이트
                    pct = (i+1) / len(tcs) * 100
                    self.progress_var.set(pct)
                    done = results['PASS']+results['FAIL']+results.get('ERROR',0)
                    self.status_var.set(
                        f'{done}/{len(tcs)} 완료 | ✅ {results["PASS"]} ❌ {results["FAIL"]}')

                browser.close()

            self.log_msg(f'\n{"="*50}', 'info')
            self.log_msg(f'✅ 완료! PASS: {results["PASS"]} | FAIL: {results["FAIL"]} | ERROR: {results.get("ERROR",0)}', 'pass')
            self.log_msg(f'결과 확인: {ec2}', 'info')
            messagebox.showinfo('완료', f'자동 검증 완료!\nPASS: {results["PASS"]}건\nFAIL: {results["FAIL"]}건')

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
