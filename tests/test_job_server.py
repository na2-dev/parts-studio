# tools/job_server.py のテスト。GPU も torch も要らない。
# 実際に HTTP で叩く（port 0 で空いている口を借りる）。
# 重い run_pipeline は、代わりの Python スクリプトを「パイプライン」として渡して試す。
import base64
import json
import os
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
