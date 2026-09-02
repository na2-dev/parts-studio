# ジョブサーバー（ADR-0001）。ブラウザは推論せず、ここに Job を投げるだけ。
#
# ★標準ライブラリだけで動く
#   GPU 機の venv に新しい依存を入れない。http.server + threading で足りる規模
#   （利用者は1人、GPU は1枚、同時実行は1ジョブ）。
#
# ★Job は run_pipeline.py を【別プロセス】で実行する
#   1. 途中で落ちてもサーバーは生きる（クラッシュ隔離）
#   2. プロセスが終われば VRAM が必ず返る（同一プロセスだと確保が残る実測がある）
#   3. 進み具合は標準出力を1行ずつ拾って伝えられる
#
# ★API の境界（ADR-0001: GPU がローカルか遠隔かを UI に持ち込まない）
#   POST /jobs            絵4枚（base64）と設定を JSON で受け、Job を積む
#   GET  /jobs            Job の一覧
#   GET  /jobs/<id>       1つの Job の状態（queued / running / done / failed / canceled）
#   GET  /jobs/<id>/log   標準出力そのまま
#   GET  /jobs/<id>/result  出来上がりの glb
#   POST /jobs/<id>/cancel  取り消し（実行中ならプロセスを止める）
#
#   絵は multipart ではなく JSON + base64 で受ける。multipart の解析は
#   標準ライブラリだと壊れやすく、絵4枚（数MB）なら base64 で十分軽い。
#
# 使いかた:
#   venv\Scripts\python.exe tools\job_server.py [--port=8787] [--jobs=out\jobs]
import argparse
import base64
import binascii
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

VIEWS = ('front', 'left', 'right', 'back')
UI = os.path.join(ROOT, 'web', 'index.html')     # ブラウザ UI（B-2）。単一ファイル
STATES = ('queued', 'running', 'done', 'failed', 'canceled')
PIPELINE = os.path.join(HERE, 'run_pipeline.py')

# 受け取る設定と、その検証。★run_pipeline の引数へそのまま流すので、
#   ここで通したものは 320〜440 秒あとではなく【積む前】に弾く
def _int(v):
    """int だけ。★bool は int の部分型なので明示的に外す。
    通すと build_command が --seed True を組み、数分後ではなく即・意味不明に落ちる"""
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


KNOWN_PARAMS = {
    'res': lambda v: _int(v) and v >= 1024 and (v - 1024) % 128 == 0,
    'seed': _int,
    'texsize': lambda v: _int(v) and v >= 1,
    'margin': lambda v: _num(v) and -0.5 < v < 0.5,
    'voxel': lambda v: _num(v) and 0.001 <= v <= 0.1,
    'no_fixviews': lambda v: isinstance(v, bool),
}

# 進み具合。run_pipeline の「=== N)」の行から拾う
# 投入の上限。絵4枚（base64）なら十分。★定数にしてテストから検証できるようにする
MAX_BODY = 64 * 1024 * 1024

STEP_MARKS = {
    '=== 1)': ('shape', '形を作っています'),
    '=== 2)': ('retopo', 'リトポロジーをしています'),
    '=== 3)': ('parts', 'パーツを塗っています'),
}


class Job:
    """1つの生成処理。状態はこのオブジェクトが正で、jobs/<id>/status.json に写す。"""

    def __init__(self, jobs_dir, job_id, params):
        self.id = job_id
        self.dir = os.path.join(jobs_dir, job_id)
        self.params = params
        self.state = 'queued'
        self.step = None                    # いまの工程（shape / retopo / parts）
        self.message = '順番を待っています'
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.proc = None                    # 実行中のプロセス
        self.cancel_requested = False
        self.lock = threading.Lock()

    # ---- 置き場所 ----
    def image_path(self, view):
        return os.path.join(self.dir, 'input', f'{view}.png')

    @property
    def out_path(self):
        return os.path.join(self.dir, 'model.glb')

    @property
    def log_path(self):
        return os.path.join(self.dir, 'log.txt')

    # ---- 状態 ----
    def snapshot(self):
        """API で返す形。★内部のパスは出さない（境界の外に意味が無い）。"""
        with self.lock:
            return {
                'id': self.id,
                'state': self.state,
                'step': self.step,
                'message': self.message,
                'error': self.error,
                'created': self.created,
                'started': self.started,
                'finished': self.finished,
                'params': self.params,
                'cancel_requested': self.cancel_requested,
                'has_result': self.state == 'done' and os.path.isfile(self.out_path),
            }

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)
        self.save()

    def save(self):
        # ★書き出しまで lock の中で行う。外に出すと、取り消し（HTTPスレッド）と
        #   Runner が同じ .tmp に同時に書いて JSON が混線する
        with self.lock:
            data = {k: getattr(self, k) for k in
                    ('id', 'state', 'step', 'message', 'error',
                     'created', 'started', 'finished', 'params')}
            path = os.path.join(self.dir, 'status.json')
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)           # ★書きかけの status.json を読ませない


def kill_tree(proc):
    """プロセスを【子孫ごと】止める。

    ★proc.kill() だけでは足りない。GPU を使う実体（リトポロジー・塗り）は
      run_pipeline が起こす孫プロセスで走る。直下だけ殺すと、
      1. 孫が生き残って GPU を使い続ける（取り消しても空かない）
      2. 孫が stdout のパイプを握ったままなので EOF が来ず、
         Runner が孫の完走までブロックして後続の Job も進まない
      Windows の TerminateProcess は子孫に伝播しないので taskkill /T を使う。
    """
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       capture_output=True)
    else:
        # Popen を start_new_session=True で起こしているので、
        # プロセスグループごと止められる
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()                         # 念のため直下も
    except OSError:
        pass


def validate_request(data):
    """投入の中身を確かめて (絵のバイト列, 設定) を返す。だめなら ValueError。

    ★4 View すべてが揃ってはじめて Job が成立する（CONTEXT.md）。
      ここで弾かないと、320〜440 秒走ったあとに正面が無いと分かる。
    """
    if not isinstance(data, dict):
        raise ValueError('JSON のオブジェクトを送ってください')
    images = data.get('images')
    if not isinstance(images, dict):
        raise ValueError('images が要ります（{view: base64} の形）')
    missing = [v for v in VIEWS if not images.get(v)]
    if missing:
        raise ValueError(f'絵が足りません: {", ".join(missing)}。'
                         '4 View すべてが揃ってはじめて Job が成立します')
    unknown = sorted(set(images) - set(VIEWS))
    if unknown:
        raise ValueError(f'知らない View です: {", ".join(unknown)}。'
                         f'使えるのは {"/".join(VIEWS)}')
    decoded = {}
    for v in VIEWS:
        if not isinstance(images[v], str):
            # ★TypeError にすると 400 ではなく応答なしの切断になる
            raise ValueError(f'{v} の絵は base64 の文字列で送ってください')
        try:
            raw = base64.b64decode(images[v], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f'{v} の絵が base64 として読めません')
        if not raw.startswith(b'\x89PNG'):
            # ★PNG 限定。背景ぬき済みの透過 PNG が前提（ADR-0003）
            raise ValueError(f'{v} の絵が PNG ではありません（透過 PNG を送ってください）')
        decoded[v] = raw

    params = data.get('params')
    if params is None:
        params = {}
    if not isinstance(params, dict):
        # ★falsy（[] や 0）を黙って {} にしない。型違いは全部ここで弾く
        raise ValueError('params はオブジェクトで送ってください')
    bad = sorted(set(params) - set(KNOWN_PARAMS))
    if bad:
        raise ValueError(f'知らない設定です: {", ".join(bad)}。'
                         f'使えるのは {", ".join(sorted(KNOWN_PARAMS))}')
    for k, check in KNOWN_PARAMS.items():
        if k in params and not check(params[k]):
            raise ValueError(f'設定 {k} の値が使えません: {params[k]!r}')
    return decoded, params


def build_command(job, python=None):
    """Job から run_pipeline のコマンドを組む。"""
    cmd = [python or sys.executable, PIPELINE,
           '--out', job.out_path,
           '--work', os.path.join(job.dir, 'work')]
    for v in VIEWS:
        cmd += [f'--{v}', job.image_path(v)]
    p = job.params
    for k in ('res', 'seed', 'texsize'):
        if k in p:
            cmd += [f'--{k}', str(p[k])]
    for k in ('margin', 'voxel'):
        if k in p:
            cmd += [f'--{k}', str(p[k])]
    if p.get('no_fixviews'):
        cmd += ['--no-fixviews']
    return cmd


class Runner(threading.Thread):
    """キューから Job を1つずつ取り出して実行する。★GPU は1枚なので直列。"""

    def __init__(self, store, python=None):
        super().__init__(daemon=True)
        self.store = store
        self.python = python
        self.q = queue.Queue()

    def submit(self, job):
        self.q.put(job.id)

    def run(self):
        while True:
            job_id = self.q.get()
            if job_id is None:              # 止めるための合図（テスト用）
                return
            job = self.store.get(job_id)
            if job is None:
                continue
            with job.lock:                  # 待っている間に取り消された
                skip = job.cancel_requested
                already = job.state == 'canceled'
            if skip:
                if not already:             # ★2回 canceled にしない（時刻が上書きされる）
                    job.set(state='canceled', message='取り消されました',
                            finished=time.time())
                continue
            self.run_one(job)

    def run_one(self, job):
        job.set(state='running', started=time.time(), message='始めました')
        cmd = build_command(job, self.python)
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        proc = None
        try:
            with open(job.log_path, 'w', encoding='utf-8') as log:
                # ★POSIX では自分のプロセスグループで起こす（子孫ごと止めるため）
                proc = subprocess.Popen(
                    cmd, cwd=ROOT, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace',
                    start_new_session=(os.name != 'nt'))
                # ★取り消しとの受け渡し。cancel は「cancel_requested を立てて、
                #   proc があれば殺す」。こちらは「proc を差してから、
                #   既に立っていれば自分で殺す」。どちらの順で走っても、
                #   両方が揃った側が必ず殺す（殺されない窓を作らない）
                with job.lock:
                    job.proc = proc
                    cancel_now = job.cancel_requested
                if cancel_now:
                    kill_tree(proc)
                for line in proc.stdout:    # ★1行ずつ拾って進み具合に写す
                    log.write(line)
                    log.flush()
                    for mark, (step, msg) in STEP_MARKS.items():
                        if line.startswith(mark):
                            job.set(step=step, message=msg)
                code = proc.wait()
        except Exception as e:
            # ★ここで proc を見捨てない。孤児にすると GPU を使い続けたまま
            #   Runner が次の Job を始めて、1枚の GPU に2本載る
            if proc is not None:
                kill_tree(proc)
                proc.wait()
            # ★エラーにローカルのパスを混ぜない（API は内部のパスを返さない約束）
            job.set(state='failed', error=f'{type(e).__name__}（サーバー側の異常）',
                    message='失敗しました', finished=time.time(), proc=None)
            return
        job.set(proc=None)
        if job.cancel_requested:
            job.set(state='canceled', message='取り消されました', finished=time.time())
        elif code == 0 and os.path.isfile(job.out_path):
            job.set(state='done', step=None, message='できました', finished=time.time())
        else:
            # ★終了コード 0 でも出力が無ければ失敗。成功の条件は「glb がある」こと
            job.set(state='failed', finished=time.time(),
                    error=f'終了コード {code}。詳しくは log を見てください',
                    message='失敗しました')


class JobStore:
    """Job の入れ物。★dict の出し入れだけ lock で守る（中身は Job 自身が守る）。"""

    def __init__(self, jobs_dir):
        self.jobs_dir = jobs_dir
        self.jobs = {}
        self.lock = threading.Lock()

    def create(self, decoded_images, params):
        job_id = uuid.uuid4().hex[:12]
        job = Job(self.jobs_dir, job_id, params)
        os.makedirs(os.path.join(job.dir, 'input'), exist_ok=True)
        for v, raw in decoded_images.items():
            with open(job.image_path(v), 'wb') as f:
                f.write(raw)
        job.save()
        with self.lock:
            self.jobs[job_id] = job
        return job

    def get(self, job_id):
        with self.lock:
            return self.jobs.get(job_id)

    def list(self):
        with self.lock:
            jobs = list(self.jobs.values())
        return sorted((j.snapshot() for j in jobs), key=lambda s: s['created'])


def cancel(job):
    """取り消す。実行中ならプロセスを【子孫ごと】止める。もう終わっていれば False。"""
    with job.lock:
        if job.state in ('done', 'failed', 'canceled'):
            return False
        job.cancel_requested = True
        proc = job.proc
        queued = job.state == 'queued'
    if proc is not None:
        kill_tree(proc)                     # ★生成の途中に「きれいな止め方」は無い
    elif queued:
        job.set(state='canceled', message='取り消されました', finished=time.time())
    else:
        # ★running なのに proc がまだ無い（起動の途中）。ここでは殺せないが、
        #   run_one が proc を差した直後に cancel_requested を見て殺す
        job.set(message='取り消しています')
    return True


class Handler(BaseHTTPRequestHandler):
    """API の入り口。store と runner は server に持たせる。"""

    # ---- 返しかた ----
    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, code, message):
        self.send_json(code, {'error': message})

    def log_message(self, fmt, *args):      # 標準の2重ログを黙らせる
        pass

    # ---- 経路 ----
    def parse_path(self):
        parts = [p for p in self.path.split('?')[0].split('/') if p]
        return parts

    def do_GET(self):
        parts = self.parse_path()
        if parts == ['favicon.ico']:
            # ★ブラウザが勝手に取りに来る。404 のままだとコンソールに
            #   エラーが出て、本物の異常が埋もれる
            self.send_response(204)
            self.end_headers()
            return
        if not parts:
            # ★UI もこのサーバーが配る。同一オリジンになり、UI は相対パスで
            #   API を叩けばよく、CORS も接続先の設定も要らない。
            #   ★CORS は開けない。認証が無いので、開けると利用者のブラウザで
            #     開いた任意のサイトが Job の一覧・結果を読めて投入もできてしまう
            return self.send_file(UI, 'text/html; charset=utf-8',
                                  'UI が見つかりません（web/index.html）')
        if parts == ['jobs']:
            return self.send_json(200, {'jobs': self.server.store.list()})
        if len(parts) >= 2 and parts[0] == 'jobs':
            job = self.server.store.get(parts[1])
            if job is None:
                return self.send_error_json(404, f'Job がありません: {parts[1]}')
            if len(parts) == 2:
                return self.send_json(200, job.snapshot())
            if parts[2] == 'log':
                return self.send_file(job.log_path, 'text/plain; charset=utf-8',
                                      'ログはまだありません')
            if parts[2] == 'result':
                if job.snapshot()['state'] != 'done':
                    return self.send_error_json(409, 'まだできていません')
                return self.send_file(job.out_path, 'model/gltf-binary',
                                      '出来上がりが見つかりません')
        return self.send_error_json(404, f'知らない経路です: {self.path}')

    def send_file(self, path, ctype, missing):
        if not os.path.isfile(path):
            return self.send_error_json(404, missing)
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parts = self.parse_path()
        if parts == ['jobs']:
            return self.post_job()
        if len(parts) == 3 and parts[0] == 'jobs' and parts[2] == 'cancel':
            job = self.server.store.get(parts[1])
            if job is None:
                return self.send_error_json(404, f'Job がありません: {parts[1]}')
            if cancel(job):
                return self.send_json(200, job.snapshot())
            return self.send_error_json(409, 'もう終わっています')
        return self.send_error_json(404, f'知らない経路です: {self.path}')

    def post_job(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            return self.send_error_json(400, 'Content-Length が読めません')
        if length <= 0:
            return self.send_error_json(400, '中身がありません')
        if length > MAX_BODY:
            return self.send_error_json(
                413, f'大きすぎます（上限 {MAX_BODY // (1024 * 1024)}MB）')
        try:
            data = json.loads(self.rfile.read(length).decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_error_json(400, 'JSON として読めません')
        try:
            decoded, params = validate_request(data)
        except ValueError as e:
            return self.send_error_json(400, str(e))
        except Exception as e:
            # ★想定外でも応答は返す。返さないとクライアントには
            #   「ネットワークエラー」しか見えず、理由が消える
            return self.send_error_json(500, f'サーバー側の異常（{type(e).__name__}）')
        try:
            job = self.server.store.create(decoded, params)
            self.server.runner.submit(job)
        except OSError as e:
            return self.send_error_json(500, f'Job を保存できません（{type(e).__name__}）')
        return self.send_json(201, job.snapshot())


def make_server(port, jobs_dir, python=None):
    """サーバーを組み立てて返す（★まだ動かさない。テストが port 0 で使う）。"""
    os.makedirs(jobs_dir, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.store = JobStore(jobs_dir)
    server.runner = Runner(server.store, python)
    server.runner.start()
    return server


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description='ジョブサーバー（ブラウザは推論せず、ここに Job を投げる）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--port', type=int, default=8787, help='待ち受ける口')
    p.add_argument('--jobs', default=os.path.join(ROOT, 'out', 'jobs'),
                   help='Job の置き場所（入力・ログ・出来上がり）')
    a = p.parse_args(argv)
    if not 1 <= a.port <= 65535:
        p.error(f'--port は 1〜65535 にすること。受け取った値: {a.port}')
    return a


def main(argv=None):
    args = parse_args(argv)
    server = make_server(args.port, os.path.abspath(args.jobs))
    print(f'ジョブサーバー: http://127.0.0.1:{args.port}/ '
          f'/ Job の置き場所 {args.jobs}', flush=True)
    print('ブラウザでこの URL を開くと UI が出ます', flush=True)
    print('止めるには Ctrl-C', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('止めます', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
