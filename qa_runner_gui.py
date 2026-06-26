"""
QA TC Runner - 로컬 실행기 (GUI)
PyInstaller로 exe 빌드 가능
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading, os, sys, json, base64, re, requests, tempfile

# ── 설정 ──────────────────────────────────────
EC2_API = 'http://54.180.98.47'
STG_CONFIGS = {
    '닥터바이스 어드민': {
        'base': 'https://staging-admin.doctorvice.co.kr',
        'login': True,
        'id': 'test@ikoob.com',
        'pw': 'test1234!'
    },
    '닥터바이스 클리닉': {
        'base': 'https://staging-clinic.doctorvice.co.kr',
        'login': False,
        'id': '',
        'pw': ''
    },
}
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

        # 서비스 선택
        self.service_var = tk.StringVar(value='닥터바이스 어드민')
        row('서비스', lambda f: ttk.Combobox(f, textvariable=self.service_var,
            values=list(STG_CONFIGS.keys()), state='readonly',
            font=('맑은 고딕', 10), width=38), 2)

        # 세션 ID
        self.session_var = tk.StringVar(value='')
        row('세션 ID', lambda f: tk.Entry(f, textvariable=self.session_var,
            font=('맑은 고딕', 10), width=40), 3)

        # 우선순위
        self.priority_var = tk.StringVar(value='P1')
        row('우선순위 필터', lambda f: ttk.Combobox(f, textvariable=self.priority_var,
            values=['P1', 'P2', 'P1+P2', '전체'], state='readonly',
            font=('맑은 고딕', 10), width=38), 4)

        # 최대 건수
        self.limit_var = tk.StringVar(value='0')
        row('최대 건수', lambda f: tk.Entry(f, textvariable=self.limit_var,
            font=('맑은 고딕', 10), width=40), 5)
        tk.Label(frame, text='(0 = 전체)', font=('맑은 고딕', 9),
                 bg=BG, fg='#aaa').grid(row=5, column=2, padx=4)

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

    def start_worker(self):
        if not self.session_var.get():
            messagebox.showerror('오류', '세션 ID를 입력하세요')
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
            service = self.service_var.get()
            session_id = int(self.session_var.get())
            priority = self.priority_var.get()
            limit = int(self.limit_var.get() or 0)
            stg_cfg = STG_CONFIGS.get(service, STG_CONFIGS['닥터바이스 어드민'])
            stg_base = stg_cfg['base']

            client = openai.OpenAI(api_key=api_key)

            # TC 목록
            self.log_msg(f'📋 TC 목록 조회 중... (세션 {session_id})', 'info')
            resp = requests.get(f'{ec2}/api/sessions/{session_id}/tcs', timeout=10)
            tcs = resp.json()

            # 필터
            if priority == 'P1':
                tcs = [t for t in tcs if t.get('priority') == 'P1']
            elif priority == 'P2':
                tcs = [t for t in tcs if t.get('priority') == 'P2']
            elif priority == 'P1+P2':
                tcs = [t for t in tcs if t.get('priority') in ('P1','P2')]
            tcs = [t for t in tcs if not t.get('result')]
            if limit: tcs = tcs[:limit]

            if not tcs:
                self.log_msg('✅ 검증할 TC가 없어요', 'pass')
                self._done()
                return

            self.log_msg(f'🎯 대상 TC: {len(tcs)}건 | {service} | {priority}', 'info')
            self.status_var.set(f'0 / {len(tcs)} 완료')

            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                ctx = browser.new_context(viewport={'width':1440,'height':900})
                page = ctx.new_page()

                # 로그인
                if stg_cfg['login']:
                    self.log_msg(f'🔐 {stg_base} 로그인 중...', 'info')
                    page.goto(f'{stg_base}/login', timeout=20000)
                    page.wait_for_load_state('networkidle', timeout=15000)
                    page.fill('input[type="text"]', stg_cfg['id'])
                    page.fill('input[type="password"]', stg_cfg['pw'])
                    page.click('button[type="submit"]')
                    page.wait_for_load_state('networkidle', timeout=15000)
                    page.wait_for_timeout(1500)
                    if '/login' in page.url:
                        self.log_msg('❌ 로그인 실패', 'fail')
                        browser.close()
                        self._done()
                        return
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
