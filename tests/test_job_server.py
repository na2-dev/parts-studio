# tools/job_server.py のテスト。GPU も torch も要らない。
# 実際に HTTP で叩く（port 0 で空いている口を借りる）。
# 重い run_pipeline は、代わりの Python スクリプトを「パイプライン」として渡して試す。
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import job_server                                            # noqa: E402

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32


def b64(raw=PNG):
    return base64.b64encode(raw).decode('ascii')


def good_request(**params):
    return {'images': {v: b64() for v in job_server.VIEWS},
            'params': params}


# ---- 投入の検証（HTTP を立てずに直接） --------------------------------------

def test_4枚そろえば通る():
    decoded, params = job_server.validate_request(good_request())
    assert set(decoded) == set(job_server.VIEWS)
    assert all(raw.startswith(b'\x89PNG') for raw in decoded.values())


def test_1枚でも欠けると積む前に弾く():
    # ★4 View すべてが揃ってはじめて Job が成立する（CONTEXT.md）。
    #   ここで弾かないと、320〜440 秒走ったあとに正面が無いと分かる
    req = good_request()
    del req['images']['back']
    with pytest.raises(ValueError) as e:
        job_server.validate_request(req)
    assert 'back' in str(e.value)
    assert '4 View' in str(e.value)


def test_知らないViewは弾く():
    req = good_request()
    req['images']['top'] = b64()
    with pytest.raises(ValueError) as e:
        job_server.validate_request(req)
    assert 'top' in str(e.value)


def test_base64でないものは弾く():
    req = good_request()
    req['images']['front'] = '%%%こわれている%%%'
    with pytest.raises(ValueError) as e:
        job_server.validate_request(req)
    assert 'front' in str(e.value)


def test_PNGでないものは弾く():
    req = good_request()
    req['images']['front'] = b64(b'JFIF-NOT-A-PNG')
    with pytest.raises(ValueError) as e:
        job_server.validate_request(req)
    assert 'PNG' in str(e.value)


def test_知らない設定は弾く():
    with pytest.raises(ValueError) as e:
        job_server.validate_request(good_request(gpu='8枚'))
    assert 'gpu' in str(e.value)


@pytest.mark.parametrize('key,bad', [
    ('res', 1000), ('res', '1024'), ('margin', 0.6),
    ('voxel', 0.5), ('texsize', 0), ('no_fixviews', 'yes'),
])
def test_使えない値は積む前に弾く(key, bad):
    # ★run_pipeline と同じ制約。通すと数分あとに argparse が落とす
    with pytest.raises(ValueError) as e:
        job_server.validate_request(good_request(**{key: bad}))
    assert key in str(e.value)


@pytest.mark.parametrize('params', [
    {}, {'res': 1536, 'seed': 7}, {'margin': 0.02, 'voxel': 0.009},
    {'no_fixviews': True}, {'texsize': 2048},
])
def test_使える設定は通る(params):
    _, got = job_server.validate_request(good_request(**params))
    assert got == params


# ---- コマンドの組み立て -----------------------------------------------------

def test_コマンドはrun_pipelineを別プロセスで呼ぶ(tmp_path):
    job = job_server.Job(str(tmp_path), 'abc123', {'res': 1536, 'no_fixviews': True})
    cmd = job_server.build_command(job, python='PY.exe')
    assert cmd[0] == 'PY.exe'
    assert cmd[1].endswith('run_pipeline.py')
    assert cmd[cmd.index('--out') + 1] == job.out_path
    for v in job_server.VIEWS:
        assert cmd[cmd.index(f'--{v}') + 1] == job.image_path(v)
    assert cmd[cmd.index('--res') + 1] == '1536'
    assert '--no-fixviews' in cmd


def test_設定しなかったものは渡さない(tmp_path):
    # ★既定は run_pipeline 側が正。二重定義しない
    job = job_server.Job(str(tmp_path), 'abc123', {})
    cmd = job_server.build_command(job, python='PY.exe')
    for opt in ('--res', '--seed', '--texsize', '--margin', '--voxel'):
        assert opt not in cmd
    assert '--no-fixviews' not in cmd


def test_workはJobのディレクトリの中(tmp_path):
    # ★Job ごとに分ける。共有すると別の題材の中間ファイルと混ざる
    job = job_server.Job(str(tmp_path), 'abc123', {})
    cmd = job_server.build_command(job, python='PY.exe')
    work = cmd[cmd.index('--work') + 1]
    assert work.startswith(job.dir)


# ---- HTTP ごしの通し --------------------------------------------------------

FAKE_OK = '''
import os, sys, time
args = sys.argv[1:]
out = args[args.index('--out') + 1]
print('=== 1) 形を作る', flush=True)
print('=== 2) リトポロジー', flush=True)
print('=== 3) パーツづくり', flush=True)
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, 'wb').write(b'GLB DATA')
print('できました', flush=True)
'''

FAKE_FAIL = '''
import sys
print('=== 1) 形を作る', flush=True)
print('だめでした', flush=True)
sys.exit(2)
'''

FAKE_SLOW = '''
import sys, time
print('=== 1) 形を作る', flush=True)
time.sleep(60)
'''

FAKE_LIAR = '''
import sys
print('=== 1) 形を作る', flush=True)
sys.exit(0)     # 0 で終わるが出力を書かない
'''


@pytest.fixture
def served(tmp_path, monkeypatch):
    """port 0 でサーバーを立て、偽のパイプラインを差し込めるようにする。"""
    import threading
    made = {}

    def start(fake_script):
        fake = tmp_path / 'fake_pipeline.py'
        fake.write_text(fake_script, encoding='utf-8')
        monkeypatch.setattr(job_server, 'PIPELINE', str(fake))
        server = job_server.make_server(0, str(tmp_path / 'jobs'),
                                        python=sys.executable)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        made['server'] = server
        return f'http://127.0.0.1:{server.server_address[1]}'

    yield start
    if 'server' in made:
        made['server'].shutdown()
        made['server'].runner.q.put(None)


def call(base, path, payload=None, method=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def wait_state(base, job_id, states, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, snap = call(base, f'/jobs/{job_id}')
        if snap['state'] in states:
            return snap
        time.sleep(0.05)
    pytest.fail(f'{states} にならない: {snap}')


def test_投入から完了まで(served):
    base = served(FAKE_OK)
    code, snap = call(base, '/jobs', good_request())
    assert code == 201
    assert snap['state'] == 'queued'
    job_id = snap['id']
    snap = wait_state(base, job_id, ('done',))
    assert snap['message'] == 'できました'
    assert snap['has_result'] is True
    # 出来上がりを取れる
    req = urllib.request.Request(f'{base}/jobs/{job_id}/result')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert r.headers['Content-Type'] == 'model/gltf-binary'
        assert r.read() == b'GLB DATA'
    # ログも取れる
    req = urllib.request.Request(f'{base}/jobs/{job_id}/log')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert '=== 1) 形を作る' in r.read().decode('utf-8')


def test_進み具合が段階として見える(served):
    base = served(FAKE_OK)
    _, snap = call(base, '/jobs', good_request())
    snap = wait_state(base, snap['id'], ('done',))
    assert snap['step'] is None                 # 終わったら工程は消える
    # 一覧にも出る
    _, listing = call(base, '/jobs')
    assert [j['id'] for j in listing['jobs']] == [snap['id']]


def test_失敗はfailedになりログを案内する(served):
    base = served(FAKE_FAIL)
    _, snap = call(base, '/jobs', good_request())
    snap = wait_state(base, snap['id'], ('failed',))
    assert '終了コード 2' in snap['error']
    assert 'log' in snap['error']
    assert snap['has_result'] is False


def test_終了コード0でも出力が無ければ失敗(served):
    # ★成功の条件は「glb がある」こと。0 を信じない
    base = served(FAKE_LIAR)
    _, snap = call(base, '/jobs', good_request())
    snap = wait_state(base, snap['id'], ('failed',))
    assert snap['has_result'] is False


def test_実行中の取り消し(served):
    base = served(FAKE_SLOW)
    _, snap = call(base, '/jobs', good_request())
    wait_state(base, snap['id'], ('running',))
    code, got = call(base, f'/jobs/{snap["id"]}/cancel', method='POST')
    assert code == 200
    snap = wait_state(base, snap['id'], ('canceled',))
    assert snap['state'] == 'canceled'


def test_終わったものは取り消せない(served):
    base = served(FAKE_OK)
    _, snap = call(base, '/jobs', good_request())
    wait_state(base, snap['id'], ('done',))
    code, got = call(base, f'/jobs/{snap["id"]}/cancel', method='POST')
    assert code == 409


def test_待っているJobの取り消し(served):
    # ★実行中の1件の後ろで待っている Job も取り消せる
    base = served(FAKE_SLOW)
    _, first = call(base, '/jobs', good_request())
    wait_state(base, first['id'], ('running',))
    _, second = call(base, '/jobs', good_request())
    assert second['state'] == 'queued'
    code, _ = call(base, f'/jobs/{second["id"]}/cancel', method='POST')
    assert code == 200
    snap = wait_state(base, second['id'], ('canceled',))
    assert snap['started'] is None              # 走らずに終わった
    call(base, f'/jobs/{first["id"]}/cancel', method='POST')


def test_不正な投入は400で理由を返す(served):
    base = served(FAKE_OK)
    req = good_request()
    del req['images']['front']
    code, body = call(base, '/jobs', req)
    assert code == 400
    assert 'front' in body['error']


def test_JSONでないものは400(served):
    base = served(FAKE_OK)
    req = urllib.request.Request(base + '/jobs', data=b'\xff\xfe not json',
                                 headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
        pytest.fail('通ってしまった')
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_無いJobは404(served):
    base = served(FAKE_OK)
    code, body = call(base, '/jobs/nai')
    assert code == 404
    code, body = call(base, '/jobs/nai/result')
    assert code == 404


def test_出来上がる前のresultは409(served):
    base = served(FAKE_SLOW)
    _, snap = call(base, '/jobs', good_request())
    code, body = call(base, f'/jobs/{snap["id"]}/result')
    assert code == 409
    assert 'まだ' in body['error']
    call(base, f'/jobs/{snap["id"]}/cancel', method='POST')


def test_知らない経路は404(served):
    base = served(FAKE_OK)
    code, _ = call(base, '/nandarou')
    assert code == 404


def test_同時に1つしか走らない(served):
    # ★GPU は1枚。2つ目は queued のまま待つ
    base = served(FAKE_SLOW)
    _, a = call(base, '/jobs', good_request())
    wait_state(base, a['id'], ('running',))
    _, b = call(base, '/jobs', good_request())
    time.sleep(0.3)
    _, snap = call(base, f'/jobs/{b["id"]}')
    assert snap['state'] == 'queued'
    for j in (a, b):
        call(base, f'/jobs/{j["id"]}/cancel', method='POST')


def test_入力の絵はJobのディレクトリに残る(served, tmp_path):
    # ★何から作ったかを後から確かめられる（--from の manifest と同じ思想）
    base = served(FAKE_OK)
    _, snap = call(base, '/jobs', good_request())
    wait_state(base, snap['id'], ('done',))
    d = tmp_path / 'jobs' / snap['id'] / 'input'
    for v in job_server.VIEWS:
        assert (d / f'{v}.png').read_bytes().startswith(b'\x89PNG')


def test_statusはディスクにも残る(served, tmp_path):
    # ★サーバーを立て直しても何が起きたか追える
    base = served(FAKE_OK)
    _, snap = call(base, '/jobs', good_request())
    wait_state(base, snap['id'], ('done',))
    path = tmp_path / 'jobs' / snap['id'] / 'status.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['state'] == 'done'
    assert data['id'] == snap['id']


# ---- 引数 -------------------------------------------------------------------

def test_portの既定は8787():
    assert job_server.parse_args([]).port == 8787


@pytest.mark.parametrize('bad', ['0', '65536', '-1'])
def test_おかしなportは拒む(bad):
    with pytest.raises(SystemExit):
        job_server.parse_args(['--port', bad])


def test_apiは内部のパスを出さない(served, tmp_path):
    # ★ADR-0001: GPU がローカルか遠隔かを UI に持ち込まない。
    #   ローカルのパスが漏れると、UI がそれに依存し始める
    base = served(FAKE_OK)
    _, snap = call(base, '/jobs', good_request())
    snap = wait_state(base, snap['id'], ('done',))
    text = json.dumps(snap)
    assert str(tmp_path) not in text
    assert 'C:\\\\' not in text and '/Users/' not in text


# ---- 変異テストで逃げた穴 ---------------------------------------------------

FAKE_FAIL_BUT_WRITES = '''
import os, sys
args = sys.argv[1:]
out = args[args.index('--out') + 1]
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, 'wb').write(b'HALF DONE')
sys.exit(2)     # 出力は書いたが異常終了
'''

FAKE_STEP1_SLOW = '''
import sys, time
print('=== 1) 形を作る', flush=True)
sys.stdout.flush()
time.sleep(60)
'''


def test_出力があっても終了コードが0でなければ失敗(served):
    # ★途中まで書いて落ちたものを「できました」にしない
    base = served(FAKE_FAIL_BUT_WRITES)
    _, snap = call(base, '/jobs', good_request())
    snap = wait_state(base, snap['id'], ('failed', 'done'))
    assert snap['state'] == 'failed'
    assert '終了コード 2' in snap['error']


def test_実行中に工程が見える(served):
    # ★これが無いと、UI は数分のあいだ「running」としか出せない
    base = served(FAKE_STEP1_SLOW)
    _, snap = call(base, '/jobs', good_request())
    t0 = time.time()
    while time.time() - t0 < 15:
        _, got = call(base, f'/jobs/{snap["id"]}')
        if got['step'] == 'shape':
            break
        time.sleep(0.05)
    else:
        pytest.fail(f'工程が伝わってこない: {got}')
    assert got['message'] == '形を作っています'
    call(base, f'/jobs/{snap["id"]}/cancel', method='POST')


def test_取り消した待ちJobは順番が来ても走らない(served):
    # ★取り消しの見た目（state）だけでなく、実際に走らないことまで見る。
    #   Runner 側の確認が消えると、canceled のまま裏で320秒走る
    base = served(FAKE_STEP1_SLOW)
    _, first = call(base, '/jobs', good_request())
    wait_state(base, first['id'], ('running',))
    _, second = call(base, '/jobs', good_request())
    call(base, f'/jobs/{second["id"]}/cancel', method='POST')
    call(base, f'/jobs/{first["id"]}/cancel', method='POST')   # 先頭を空ける
    wait_state(base, first['id'], ('canceled',))
    time.sleep(0.5)                             # Runner が次を取り出す猶予
    _, snap = call(base, f'/jobs/{second["id"]}')
    assert snap['state'] == 'canceled'
    assert snap['started'] is None, '取り消したのに走った'


def test_大きすぎる投入は413(served, monkeypatch):
    monkeypatch.setattr(job_server, 'MAX_BODY', 100)
    base = served(FAKE_OK)
    code, body = call(base, '/jobs', good_request())
    assert code == 413
    assert '大きすぎます' in body['error']


# ---- レビューで見つかった穴（2周目） ----------------------------------------

FAKE_WITH_GRANDCHILD = '''
import os, subprocess, sys, time
args = sys.argv[1:]
out = args[args.index('--out') + 1]
os.makedirs(os.path.dirname(out), exist_ok=True)
# 孫プロセス（GPU を使う工程の代役）を起こし、その pid を書き残す
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
open(out + '.grandchild', 'w').write(str(child.pid))
print('=== 1) 形を作る', flush=True)
child.wait()
'''


def pid_alive(pid):
    """★Windows の os.kill(pid, 0) は生存確認に使えない（signal 0 が
    パラメーター違反になるか、値によってはプロセスを殺す）。tasklist で見る。"""
    if os.name == 'nt':
        import subprocess
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                           capture_output=True, text=True)
        return f' {pid} ' in ' ' + r.stdout.replace('\r', ' ') + ' ' or \
               f' {pid} ' in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_取り消しは孫プロセスごと止める(served, tmp_path):
    # ★GPU を使う実体（リトポロジー・塗り）は run_pipeline の孫で走る。
    #   直下だけ殺すと、孫が GPU を使い続けたまま「取り消し」になる。
    #   さらに孫が stdout のパイプを握るので、Runner が孫の完走まで固まる
    base = served(FAKE_WITH_GRANDCHILD)
    _, snap = call(base, '/jobs', good_request())
    marker = tmp_path / 'jobs' / snap['id'] / 'model.glb.grandchild'
    t0 = time.time()
    while not marker.is_file() and time.time() - t0 < 15:
        time.sleep(0.05)
    assert marker.is_file(), '孫が起きていない'
    gpid = int(marker.read_text())
    assert pid_alive(gpid)
    call(base, f'/jobs/{snap["id"]}/cancel', method='POST')
    snap = wait_state(base, snap['id'], ('canceled',), timeout=15)
    t0 = time.time()
    while pid_alive(gpid) and time.time() - t0 < 10:
        time.sleep(0.1)
    assert not pid_alive(gpid), '孫が生き残っている（GPU が空かない）'


def test_文字列でない絵は400(served):
    # ★TypeError のままだと、応答なしの切断になって理由が消える
    base = served(FAKE_OK)
    req = good_request()
    req['images']['front'] = 12345
    code, body = call(base, '/jobs', req)
    assert code == 400
    assert 'front' in body['error']


@pytest.mark.parametrize('key,val', [('seed', True), ('texsize', True), ('res', True)])
def test_boolはintのふりをできない(key, val):
    # ★Python では isinstance(True, int) が真。通すと --seed True を組んで
    #   run_pipeline の argparse が落ち、理由が利用者に伝わらない
    with pytest.raises(ValueError) as e:
        job_server.validate_request(good_request(**{key: val}))
    assert key in str(e.value)


@pytest.mark.parametrize('bad', [[], 0, False, 'x'])
def test_paramsの型違いは黙って無視しない(bad):
    req = good_request()
    req['params'] = bad
    with pytest.raises(ValueError) as e:
        job_server.validate_request(req)
    assert 'params' in str(e.value)


def test_取り消しの受け付けが応答から分かる(served):
    # ★kill は非同期なので state はまだ running かもしれない。
    #   cancel_requested が無いと、UI は受理されたのか判別できない
    base = served(FAKE_SLOW)
    _, snap = call(base, '/jobs', good_request())
    wait_state(base, snap['id'], ('running',))
    code, body = call(base, f'/jobs/{snap["id"]}/cancel', method='POST')
    assert code == 200
    assert body['cancel_requested'] is True
    wait_state(base, snap['id'], ('canceled',))


def test_取り消した待ちJobは後続が動いても走らない(served):
    # ★2つの競合を避ける。
    #   1. sleep 頼みにしない（負荷の高い環境で空振り合格になる）
    #   2. 先頭を速い Job にしない（cancel が届く前に b が走り出せてしまう。
    #      Windows で実際に競合した）
    #   遅い a で Runner を塞いだまま b を取り消し、a を取り消して流す。
    #   c が running になる＝Runner が b を確実に通過した、を合図にする
    base = served(FAKE_SLOW)
    _, a = call(base, '/jobs', good_request())
    wait_state(base, a['id'], ('running',))     # Runner は a で塞がっている
    _, b = call(base, '/jobs', good_request())
    call(base, f'/jobs/{b["id"]}/cancel', method='POST')
    _, c = call(base, '/jobs', good_request())
    call(base, f'/jobs/{a["id"]}/cancel', method='POST')
    wait_state(base, c['id'], ('running',))     # b を通過した合図
    _, snap = call(base, f'/jobs/{b["id"]}')
    assert snap['state'] == 'canceled'
    assert snap['started'] is None, '取り消したのに走った'
    call(base, f'/jobs/{c["id"]}/cancel', method='POST')


# ---- CORS と経路（唯一の想定クライアントはブラウザ） ------------------------

def test_CORSは開けない(served):
    # ★UI は同一オリジンで配るので CORS は要らない。認証が無いので、
    #   開けると利用者のブラウザで開いた任意のサイトが Job を読めて投入もできる
    base = served(FAKE_OK)
    for path in ('/', '/jobs'):
        req = urllib.request.Request(base + path)
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.headers.get('Access-Control-Allow-Origin') is None, path


def test_クエリ文字列が付いても経路は同じ(served):
    # ★UI がキャッシュ避けに ?t=... を付けた瞬間 404 になってはいけない
    base = served(FAKE_OK)
    _, snap = call(base, '/jobs', good_request())
    code, got = call(base, f'/jobs/{snap["id"]}?t=123')
    assert code == 200
    assert got['id'] == snap['id']


def test_実行中でもログが読める(served):
    # ★flush が無いと、実行中の /log が空になる
    base = served(FAKE_STEP1_SLOW)
    _, snap = call(base, '/jobs', good_request())
    t0 = time.time()
    text = ''
    while time.time() - t0 < 15:
        req = urllib.request.Request(f'{base}/jobs/{snap["id"]}/log')
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode('utf-8')
        except urllib.error.HTTPError:
            pass
        if '=== 1)' in text:
            break
        time.sleep(0.05)
    assert '=== 1)' in text, '実行中にログが見えない'
    call(base, f'/jobs/{snap["id"]}/cancel', method='POST')


def test_工程の目印は3つとも対応が正しい():
    # ★2) 3) の対応を入れ替えても通しのテストでは気づけないので、表そのものを固定する
    assert job_server.STEP_MARKS['=== 1)'][0] == 'shape'
    assert job_server.STEP_MARKS['=== 2)'][0] == 'retopo'
    assert job_server.STEP_MARKS['=== 3)'][0] == 'parts'
    assert set(job_server.STEP_MARKS) == {'=== 1)', '=== 2)', '=== 3)'}


def test_想定外の失敗でも内部のパスを出さない(served, tmp_path, monkeypatch):
    # ★エラー文にローカルの絶対パスが混ざると、UI がそれに依存し始める
    def boom(*a, **k):
        raise OSError(f'{tmp_path}/secret が壊れた')

    base = served(FAKE_OK)
    monkeypatch.setattr(job_server.subprocess, 'Popen', boom)
    _, snap = call(base, '/jobs', good_request())
    snap = wait_state(base, snap['id'], ('failed',))
    assert str(tmp_path) not in json.dumps(snap)


# ---- run_one を直接叩く（外からは踏めない隙間） -----------------------------

def make_runner_job(tmp_path, monkeypatch, script):
    fake = tmp_path / 'fake_pipeline.py'
    fake.write_text(script, encoding='utf-8')
    monkeypatch.setattr(job_server, 'PIPELINE', str(fake))
    store = job_server.JobStore(str(tmp_path / 'jobs'))
    decoded = {v: PNG for v in job_server.VIEWS}
    job = store.create(decoded, {})
    runner = job_server.Runner(store, python=sys.executable)
    return runner, job


def test_起動の途中で取り消されても走らせ続けない(tmp_path, monkeypatch):
    # ★cancel が「Runner の取り出し後〜proc を差すまで」の隙間に入ると、
    #   誰も殺さないまま裏で数分走る。run_one は proc を差した直後に
    #   cancel_requested を見て自分で殺す
    runner, job = make_runner_job(tmp_path, monkeypatch, FAKE_SLOW)
    job.cancel_requested = True                 # 隙間で cancel が入った状態
    t0 = time.time()
    runner.run_one(job)                         # 60秒待たされたら負け
    took = time.time() - t0
    assert took < 20, f'取り消したのに {took:.0f} 秒走った'
    assert job.state == 'canceled'


FAKE_PID_SLOW = """
import os, sys, time
args = sys.argv[1:]
out = args[args.index('--out') + 1]
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out + '.pid', 'w').write(str(os.getpid()))
print('=== 1) 形を作る', flush=True)
time.sleep(120)
"""


def test_読み取りが失敗しても子を見捨てない(tmp_path, monkeypatch):
    # ★except で proc を放すと、GPU を使ったまま Runner が次の Job を始めて
    #   1枚の GPU に2本載る。「早く返る」だけでなく【子が死んだ】ことまで見る
    runner, job = make_runner_job(tmp_path, monkeypatch, FAKE_PID_SLOW)

    class Boom:
        def items(self):
            raise RuntimeError('読み取りで壊れた')

    monkeypatch.setattr(job_server, 'STEP_MARKS', Boom())
    t0 = time.time()
    runner.run_one(job)                         # 子が生きたままなら wait で固まる
    took = time.time() - t0
    assert took < 20, f'子を見捨てて {took:.0f} 秒待った'
    assert job.state == 'failed'
    assert job.proc is None
    pid_file = pathlib.Path(job.out_path + '.pid')
    assert pid_file.is_file(), '子が pid を書く前に落ちた（テストの前提が崩れた）'
    pid = int(pid_file.read_text())
    t0 = time.time()
    while pid_alive(pid) and time.time() - t0 < 10:
        time.sleep(0.1)
    assert not pid_alive(pid), '子が生き残っている（GPU が空かない）'


# ---- ブラウザ UI（B-2）の配りかた --------------------------------------------

def test_ルートでUIを配る(served):
    # ★同一オリジンにする。UI は相対パスで API を叩けばよく、CORS も要らない
    base = served(FAKE_OK)
    req = urllib.request.Request(base + '/')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert r.headers['Content-Type'].startswith('text/html')
        html = r.read().decode('utf-8')
    assert 'parts-studio' in html
    # ★View の枠は実行時に作るので、素の HTML には配列の定義があるはず
    assert "['front', 'left', 'right', 'back']" in html
    assert "fetch('jobs'" in html or 'fetch(`jobs' in html


def test_UIは相対パスでAPIを叩く():
    # ★絶対 URL や 127.0.0.1 を書くと、ポートフォワード越しに開いたとき壊れる
    html = pathlib.Path(job_server.UI).read_text(encoding='utf-8')
    assert 'http://127.0.0.1' not in html
    assert 'localhost' not in html


def test_faviconは404にしない(served):
    # ★404 のままだとコンソールにエラーが出て、本物の異常が埋もれる
    base = served(FAKE_OK)
    req = urllib.request.Request(base + '/favicon.ico')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 204


def test_UIが無ければ404の理由を返す(served, monkeypatch):
    base = served(FAKE_OK)
    monkeypatch.setattr(job_server, 'UI',
                        str(pathlib.Path(job_server.UI).parent / 'nai.html'))
    code, body = call(base, '/')
    assert code == 404
    assert 'web/index.html' in body['error']


def test_UIは投入のPOSTを持つ():
    # ★「fetch('jobs'」だけだと一覧の GET にもマッチして、投入を消しても緑になる
    html = pathlib.Path(job_server.UI).read_text(encoding='utf-8')
    assert "method: 'POST'" in html
    assert 'JSON.stringify({ images, params' in html


def test_UIは4Viewが揃うまで投入できない():
    # ★ボタンの disabled 制御が missing の数に紐づいている
    html = pathlib.Path(job_server.UI).read_text(encoding='utf-8')
    assert 'btn.disabled = missing.length > 0' in html
    assert '4 View すべてが揃ってはじめて' in html


# ---- 3D ビューア（B-3） -------------------------------------------------------

def test_同梱のビューアを配る(served):
    base = served(FAKE_OK)
    req = urllib.request.Request(base + '/vendor/model-viewer.min.js')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        assert r.headers['Content-Type'].startswith('text/javascript')
        head = r.read(2048).decode('utf-8', errors='replace')
    # ★先頭はライセンスの帯。実体の目印はそちらで見る
    assert 'Copyright 2019 Google LLC' in head


def test_vendorはjs以外を配らない(served):
    base = served(FAKE_OK)
    code, body = call(base, '/vendor/model-viewer.LICENSE')
    assert code == 404


def test_vendorは外へ出られない(served, tmp_path):
    # ★.. で web/vendor の外のファイルを取らせない
    base = served(FAKE_OK)
    for path in ('/vendor/..%2F..%2Ftools%2Fjob_server.py',
                 '/vendor/../index.html'):
        code, body = call(base, path)
        assert code == 404, path


def test_UIはビューアを同梱から読む():
    # ★CDN を実行時に引かない（オフラインでも動く・供給元に左右されない）
    html = pathlib.Path(job_server.UI).read_text(encoding='utf-8')
    assert 'vendor/model-viewer.min.js' in html
    assert 'unpkg.com' not in html and 'googleapis.com' not in html and 'cdn.' not in html


def test_UIは出来上がるまで3Dボタンを出さない():
    html = pathlib.Path(job_server.UI).read_text(encoding='utf-8')
    assert "el.querySelector('.viewbtn').hidden = !snap.has_result" in html


def test_ビューアの実体が同梱されている():
    p = pathlib.Path(job_server.VENDOR) / 'model-viewer.min.js'
    assert p.is_file() and p.stat().st_size > 500_000
    lic = pathlib.Path(job_server.VENDOR) / 'model-viewer.LICENSE'
    assert lic.is_file() and 'Apache License' in lic.read_text(encoding='utf-8')
