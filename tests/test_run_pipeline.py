# tools/run_pipeline.py のテスト。GPU も torch も要らない。
# 重い工程は差し替えて、【並びと渡す値】と【途中から始める】を見る。
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import run_pipeline                                          # noqa: E402


@pytest.fixture
def imgs(tmp_path):
    made = {}
    for v in run_pipeline.VIEWS:
        p = tmp_path / f'{v}.png'
        p.write_bytes(b'\x89PNG\r\n\x1a\n')
        made[v] = str(p)
    return made


def base_argv(tmp_path, imgs, **over):
    argv = ['--out', str(tmp_path / 'model.glb'), '--work', str(tmp_path / 'w')]
    for v, p in imgs.items():
        argv += [f'--{v}', p]
    for k, val in over.items():
        argv += [f'--{k}', str(val)]
    return argv


# ---- 工程の並び -----------------------------------------------------------

def test_工程は3つ():
    assert run_pipeline.STEPS == ('shape', 'retopo', 'parts')


@pytest.mark.parametrize('start,expect', [
    ('shape', ('shape', 'retopo', 'parts')),
    ('retopo', ('retopo', 'parts')),
    ('parts', ('parts',)),
])
def test_途中から始める(start, expect):
    assert run_pipeline.steps_from(start) == expect


def test_知らない工程は拒む(tmp_path, imgs):
    with pytest.raises(SystemExit):
        run_pipeline.parse_args(base_argv(tmp_path, imgs) + ['--from', 'paint'])


# ---- 引数 -----------------------------------------------------------------

def test_既定値(tmp_path, imgs):
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs))
    assert (a.res, a.mode, a.seed) == (1024, 'multidiffusion', 1234)
    assert (a.voxel, a.texsize, a.margin) == (0.009, 4096, 0.01)
    assert a.start == 'shape'


def test_既定は形づくりから(tmp_path, imgs):
    assert run_pipeline.parse_args(base_argv(tmp_path, imgs)).start == 'shape'


@pytest.mark.parametrize('out', ['x.obj', 'x', 'x.gltf'])
def test_出力がglbでなければ拒む(tmp_path, imgs, out):
    argv = base_argv(tmp_path, imgs)
    argv[argv.index('--out') + 1] = str(tmp_path / out)
    with pytest.raises(SystemExit):
        run_pipeline.parse_args(argv)


@pytest.mark.parametrize('bad', ['0', '-0.01'])
def test_ボクセル幅が0以下なら拒む(tmp_path, imgs, bad):
    with pytest.raises(SystemExit):
        run_pipeline.parse_args(base_argv(tmp_path, imgs) + ['--voxel', bad])


def test_正面が無ければ拒む(tmp_path, imgs):
    with pytest.raises(SystemExit):
        run_pipeline.parse_args(['--out', str(tmp_path / 'o.glb'),
                                 '--left', imgs['left']])


def test_絵が無ければ止まる(tmp_path):
    a = run_pipeline.parse_args(['--out', str(tmp_path / 'o.glb'),
                                 '--front', str(tmp_path / 'ない.png')])
    with pytest.raises(SystemExit) as e:
        run_pipeline.collect_images(a)
    assert '見つかりません' in str(e.value)


def test_workの既定は出力先のpipeline(tmp_path, imgs):
    a = run_pipeline.parse_args(['--out', str(tmp_path / 'o.glb'),
                                 '--front', imgs['front']])
    assert run_pipeline.work_dir(a) == os.path.join(str(tmp_path), 'pipeline')


def test_工程ごとに別の名前を使う(tmp_path):
    ps = run_pipeline.stage_paths(str(tmp_path))
    assert len(set(ps.values())) == 2
    assert all(v.endswith('.glb') for v in ps.values())


def test_上方向は形づくりの向きに決め打つ():
    # ★形づくりの出力は Z 上。ここで決めて配る（パーツからは測れない）
    assert run_pipeline.SHAPE_UP == 'z'


# ---- 前の工程の出力が無いとき ---------------------------------------------

def test_途中から始めて前の出力が無ければ案内して止まる(tmp_path):
    with pytest.raises(SystemExit) as e:
        run_pipeline.require(str(tmp_path / 'ない.glb'), '形', '形づくり')
    msg = str(e.value)
    assert '形が見つかりません' in msg
    assert '--from' in msg                      # どうすればいいか書く
    assert '形づくり' in msg


def test_あれば通す(tmp_path):
    p = tmp_path / 'ある.glb'
    p.write_text('x')
    assert run_pipeline.require(str(p), '形', '形づくり') == str(p)


def test_リトポロジー用のpythonが無ければ案内して止まる(tmp_path):
    with pytest.raises(SystemExit) as e:
        run_pipeline.bpy_python(str(tmp_path))
    msg = str(e.value)
    assert 'venv-bpy' in msg
    assert 'trellis2-windows.md' in msg         # 作り方に案内する


# ---- 通しの並びと渡す値 ---------------------------------------------------

class Recorder:
    def __init__(self, monkeypatch):
        self.calls = []

        def fake_shape(args, images, dst):
            self.calls.append(('shape', args.res, args.mode, args.seed,
                               tuple(sorted(images))))
            open(dst, 'w').write('x')
            return dst

        def fake_retopo(args, src, dst):
            self.calls.append(('retopo', src, args.voxel))
            open(dst, 'w').write('x')
            return dst

        def fake_parts(args, images, shape, work, out):
            self.calls.append(('parts', shape, args.texsize, args.margin,
                               tuple(sorted(images))))
            open(out, 'w').write('x')
            return out

        monkeypatch.setattr(run_pipeline, 'run_shape', fake_shape)
        monkeypatch.setattr(run_pipeline, 'run_retopo', fake_retopo)
        monkeypatch.setattr(run_pipeline, 'run_parts', fake_parts)


@pytest.fixture
def rec(monkeypatch):
    return Recorder(monkeypatch)


def test_通しの並び(tmp_path, imgs, rec):
    assert run_pipeline.main(base_argv(tmp_path, imgs)) == 0
    assert [c[0] for c in rec.calls] == ['shape', 'retopo', 'parts']


def test_リトポロジーは形づくりの出力を受け取る(tmp_path, imgs, rec):
    run_pipeline.main(base_argv(tmp_path, imgs))
    ps = run_pipeline.stage_paths(str(tmp_path / 'w'))
    assert [c[1] for c in rec.calls if c[0] == 'retopo'] == [ps['shape']]


def test_パーツづくりはリトポロジーの出力を受け取る(tmp_path, imgs, rec):
    # ★形づくりの生の出力を渡すと 700 万面を塗ろうとする
    run_pipeline.main(base_argv(tmp_path, imgs))
    ps = run_pipeline.stage_paths(str(tmp_path / 'w'))
    assert [c[1] for c in rec.calls if c[0] == 'parts'] == [ps['retopo']]


def test_絵は全工程へ4枚とも渡る(tmp_path, imgs, rec):
    run_pipeline.main(base_argv(tmp_path, imgs))
    for c in rec.calls:
        if c[0] in ('shape', 'parts'):
            assert c[-1] == tuple(sorted(run_pipeline.VIEWS))


def test_設定がそれぞれの工程へ届く(tmp_path, imgs, rec):
    run_pipeline.main(base_argv(tmp_path, imgs, res=1536, seed=7,
                                voxel=0.02, texsize=2048, margin=0.03))
    shape = [c for c in rec.calls if c[0] == 'shape'][0]
    assert (shape[1], shape[3]) == (1536, 7)
    assert [c[2] for c in rec.calls if c[0] == 'retopo'] == [0.02]
    parts = [c for c in rec.calls if c[0] == 'parts'][0]
    assert (parts[2], parts[3]) == (2048, 0.03)


def test_retopoから始めると形づくりを飛ばす(tmp_path, imgs, rec):
    ps = run_pipeline.stage_paths(str(tmp_path / 'w'))
    os.makedirs(str(tmp_path / 'w'), exist_ok=True)
    open(ps['shape'], 'w').write('x')
    run_pipeline.main(base_argv(tmp_path, imgs) + ['--from', 'retopo'])
    assert [c[0] for c in rec.calls] == ['retopo', 'parts']


def test_partsから始めると2つ飛ばす(tmp_path, imgs, rec):
    ps = run_pipeline.stage_paths(str(tmp_path / 'w'))
    os.makedirs(str(tmp_path / 'w'), exist_ok=True)
    open(ps['retopo'], 'w').write('x')
    run_pipeline.main(base_argv(tmp_path, imgs) + ['--from', 'parts'])
    assert [c[0] for c in rec.calls] == ['parts']


def test_途中から始めて前の出力が無ければ何もせず止まる(tmp_path, imgs, rec):
    with pytest.raises(SystemExit) as e:
        run_pipeline.main(base_argv(tmp_path, imgs) + ['--from', 'retopo'])
    assert '形が見つかりません' in str(e.value)
    assert rec.calls == []


def test_partsから始めてリトポロジーが無ければ止まる(tmp_path, imgs, rec):
    with pytest.raises(SystemExit) as e:
        run_pipeline.main(base_argv(tmp_path, imgs) + ['--from', 'parts'])
    assert 'リトポロジー済みの形' in str(e.value)
    assert rec.calls == []


# ---- 下の工程へ渡す引数（差し替えた先の中身） -----------------------------

def test_形づくりへ渡す引数(tmp_path, imgs, monkeypatch):
    import make_shape
    got = {}
    monkeypatch.setattr(make_shape, 'main', lambda argv: got.update(argv=argv))
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs, res=1536, seed=9))
    run_pipeline.run_shape(a, run_pipeline.collect_images(a), 'S.glb')
    argv = got['argv']
    assert argv[argv.index('--out') + 1] == 'S.glb'
    assert argv[argv.index('--res') + 1] == '1536'
    assert argv[argv.index('--seed') + 1] == '9'
    assert argv[argv.index('--mode') + 1] == 'multidiffusion'
    for v in run_pipeline.VIEWS:
        assert f'--{v}' in argv                 # 4枚とも渡す


def test_パーツづくりへ渡す引数(tmp_path, imgs, monkeypatch):
    import make_parts
    got = {}
    monkeypatch.setattr(make_parts, 'main', lambda argv: got.update(argv=argv))
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs, texsize=2048))
    run_pipeline.run_parts(a, run_pipeline.collect_images(a), 'R.glb',
                           str(tmp_path / 'w'), 'OUT.glb')
    argv = got['argv']
    assert argv[argv.index('--shape') + 1] == 'R.glb'
    assert argv[argv.index('--out') + 1] == 'OUT.glb'
    assert argv[argv.index('--texsize') + 1] == '2048'
    # ★上方向を必ず渡す。渡さないとパーツから測って外す
    assert argv[argv.index('--up') + 1] == run_pipeline.SHAPE_UP
    assert '--no-fixviews' not in argv


def test_視点の固定を外す指定は下へ届く(tmp_path, imgs, monkeypatch):
    import make_parts
    got = {}
    monkeypatch.setattr(make_parts, 'main', lambda argv: got.update(argv=argv))
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs) + ['--no-fixviews'])
    run_pipeline.run_parts(a, run_pipeline.collect_images(a), 'R.glb',
                           str(tmp_path / 'w'), 'OUT.glb')
    assert '--no-fixviews' in got['argv']


def test_塗り環境の指定は下へ届く(tmp_path, imgs, monkeypatch):
    import make_parts
    got = {}
    monkeypatch.setattr(make_parts, 'main', lambda argv: got.update(argv=argv))
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs) + ['--paint-root', 'X:/e'])
    run_pipeline.run_parts(a, run_pipeline.collect_images(a), 'R.glb',
                           str(tmp_path / 'w'), 'OUT.glb')
    assert got['argv'][got['argv'].index('--paint-root') + 1] == 'X:/e'


def test_リトポロジーは別プロセスで動かす(tmp_path, imgs, monkeypatch):
    # ★bpy は Python 3.11 用しか無いので、形づくりと同じ環境では動かせない
    import subprocess
    got = {}

    def fake_run(cmd, **kw):
        got.update(cmd=cmd, env=kw.get('env'))
        open(cmd[3], 'w').write('x')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(run_pipeline, 'bpy_python', lambda *a: 'BPY.exe')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs, voxel=0.02))
    src = tmp_path / 's.glb'
    src.write_text('x')
    run_pipeline.run_retopo(a, str(src), str(tmp_path / 'r.glb'))
    assert got['cmd'][0] == 'BPY.exe'
    assert got['cmd'][1].endswith('retopo_shrinkwrap.py')
    assert os.path.isabs(got['cmd'][2]) and os.path.isabs(got['cmd'][3])
    assert got['cmd'][4] == '0.02'
    assert got['env']['PYTHONUTF8'] == '1'


def retopo_env(tmp_path, imgs, monkeypatch, code, writes):
    """リトポロジーの別プロセスを差し替える。writes=True なら出力を書く。"""
    import subprocess

    def fake_run(cmd, **kw):
        if writes:
            open(cmd[3], 'w').write('できた')
        return subprocess.CompletedProcess(cmd, code)

    monkeypatch.setattr(run_pipeline, 'bpy_python', lambda *a: 'BPY.exe')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    a = run_pipeline.parse_args(base_argv(tmp_path, imgs))
    src = tmp_path / 's.glb'
    src.write_text('x')
    return a, str(src), str(tmp_path / 'r.glb')


def test_出力が書けなければ止まる(tmp_path, imgs, monkeypatch):
    a, src, dst = retopo_env(tmp_path, imgs, monkeypatch, code=2, writes=False)
    with pytest.raises(SystemExit) as e:
        run_pipeline.run_retopo(a, src, dst)
    assert '終了コード 2' in str(e.value)


def test_成功しても出力が無ければ止まる(tmp_path, imgs, monkeypatch):
    a, src, dst = retopo_env(tmp_path, imgs, monkeypatch, code=0, writes=False)
    with pytest.raises(SystemExit) as e:
        run_pipeline.run_retopo(a, src, dst)
    assert '新しくなっていません' in str(e.value)


def test_終了コードが0でなくても出力が書けていれば続ける(tmp_path, imgs, monkeypatch, capsys):
    # ★bpy はモジュールとして使うと、書き出したあと終了時に落ちることがある
    #   （Windows で実測。終了コード 3221225477 = ACCESS_VIOLATION）。
    #   ここで止めると、正しく書けた glb を捨ててしまう
    a, src, dst = retopo_env(tmp_path, imgs, monkeypatch, code=3221225477, writes=True)
    assert run_pipeline.run_retopo(a, src, dst) == dst
    out = capsys.readouterr().out
    assert '3221225477' in out                  # ★黙って飲み込まない
    assert '続けます' in out


def test_前回の出力が残っていても更新されなければ止まる(tmp_path, imgs, monkeypatch):
    # ★「ファイルがある」だけで通すと、前回の形をそのまま使ってしまう
    a, src, dst = retopo_env(tmp_path, imgs, monkeypatch, code=1, writes=False)
    open(dst, 'w').write('前回のもの')
    with pytest.raises(SystemExit) as e:
        run_pipeline.run_retopo(a, src, dst)
    assert '新しくなっていません' in str(e.value)


def test_更新されたら通す(tmp_path, imgs, monkeypatch):
    a, src, dst = retopo_env(tmp_path, imgs, monkeypatch, code=0, writes=True)
    open(dst, 'w').write('前回のもの')
    os.utime(dst, (0, 0))                       # 古い時刻にしておく
    assert run_pipeline.run_retopo(a, src, dst) == dst


def test_始める工程ごとに要るものが決まっている():
    # ★--from=parts なのに形まで要求すると、リトポロジー済みの形があっても始められない
    assert run_pipeline.NEEDS['shape'] is None
    assert run_pipeline.NEEDS['retopo'][0] == 'shape'
    assert run_pipeline.NEEDS['parts'][0] == 'retopo'
    assert set(run_pipeline.NEEDS) == set(run_pipeline.STEPS)


def test_partsから始めるとき形は要らない(tmp_path, imgs, rec):
    # ★リトポロジー済みの形だけあれば始められる
    ps = run_pipeline.stage_paths(str(tmp_path / 'w'))
    os.makedirs(str(tmp_path / 'w'), exist_ok=True)
    open(ps['retopo'], 'w').write('x')
    assert not os.path.exists(ps['shape'])
    run_pipeline.main(base_argv(tmp_path, imgs) + ['--from', 'parts'])
    assert [c[0] for c in rec.calls] == ['parts']
