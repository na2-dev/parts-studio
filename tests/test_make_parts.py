# tools/make_parts.py のテスト。GPU も torch も要らない。
# 重い工程（切る・ならす・塗る・投影・結合）は差し替えて、【並びと渡す値】を見る。
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import make_parts                                            # noqa: E402


@pytest.fixture
def imgs(tmp_path):
    made = {}
    for v in make_parts.VIEWS:
        p = tmp_path / f'{v}.png'
        p.write_bytes(b'\x89PNG\r\n\x1a\n')
        made[v] = str(p)
    return made


def base_argv(tmp_path, imgs, **over):
    shape = tmp_path / 'retopo.glb'
    shape.write_text('x')
    argv = ['--shape', str(shape), '--out', str(tmp_path / 'final.glb')]
    for v, p in imgs.items():
        argv += [f'--{v}', p]
    for k, val in over.items():
        argv += [f'--{k}', str(val)]
    return argv


# ---- パーツの定義（ここが仕様そのもの） -----------------------------------

def test_パーツは頭と体の2つ():
    assert [p['name'] for p in make_parts.PARTS] == ['head', 'body']


def test_ならすのは頭だけ():
    # ★体をならすと背中の鍵穴が消える（2026-08-30 実測）。
    #   間違えても見た目が「少し滑らか」になるだけで、情報が落ちたと気づけない
    smoothed = [p['name'] for p in make_parts.PARTS if p['smooth']]
    assert smoothed == ['head']


def test_視点の対応づけは既定で固定する():
    # ★この題材で確かめただけ。座標の規約から導いたものではない
    assert make_parts.FIXVIEWS is True


# ---- 引数 -----------------------------------------------------------------

@pytest.mark.parametrize('out', ['x.obj', 'x.gltf', 'x'])
def test_出力がglbでなければ拒む(tmp_path, imgs, out):
    argv = base_argv(tmp_path, imgs)
    argv[argv.index('--out') + 1] = str(tmp_path / out)      # ★--out だけ差し替える
    with pytest.raises(SystemExit):
        make_parts.parse_args(argv)


def test_出力がglbなら通る(tmp_path, imgs):
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    assert a.out.endswith('final.glb')


def test_正面が無ければ拒む(tmp_path, imgs):
    with pytest.raises(SystemExit):
        make_parts.parse_args(['--shape', 'a.glb', '--out', 'b.glb',
                               '--left', imgs['left']])


def test_ならしの強さは0から1(tmp_path, imgs):
    for bad in ('-0.1', '1.5'):
        with pytest.raises(SystemExit):
            make_parts.parse_args(base_argv(tmp_path, imgs) +
                                  ['--smooth-lambda', bad])


def test_ならしの回数は0以上(tmp_path, imgs):
    with pytest.raises(SystemExit):
        make_parts.parse_args(base_argv(tmp_path, imgs) + ['--smooth-iters', '-1'])


def test_既定値(tmp_path, imgs):
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    assert (a.texsize, a.margin, a.smooth_iters, a.smooth_lambda) == (4096, 0.01, 8, 0.5)


def test_絵が無ければ止まる(tmp_path, imgs):
    a = make_parts.parse_args(['--shape', 'a.glb', '--out', str(tmp_path / 'o.glb'),
                               '--front', str(tmp_path / 'ない.png')])
    with pytest.raises(SystemExit) as e:
        make_parts.collect_images(a)
    assert '見つかりません' in str(e.value)


def test_4枚渡せば4枚集まる(tmp_path, imgs):
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    assert set(make_parts.collect_images(a)) == set(imgs)


# ---- 途中のファイルの置き場所 ---------------------------------------------

def test_workの既定は出力先のparts(tmp_path, imgs):
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    assert make_parts.work_dir(a) == os.path.join(str(tmp_path), 'parts')


def test_workは指定できる(tmp_path, imgs):
    a = make_parts.parse_args(base_argv(tmp_path, imgs) + ['--work', str(tmp_path / 'w')])
    assert make_parts.work_dir(a) == str(tmp_path / 'w')


def test_工程ごとに別の名前を使う(tmp_path):
    # ★同じ名前を使い回すと、途中で落ちたときにどこまで進んだか分からなくなる
    ps = make_parts.part_paths(str(tmp_path), 'head')
    assert set(ps) == {'raw', 'shape', 'painted', 'final'}
    assert len(set(ps.values())) == 4
    assert all(v.endswith('.glb') for v in ps.values())
    assert all(os.path.basename(v).startswith('head_') for v in ps.values())


# ---- 通しの並びと渡す値 ---------------------------------------------------

class Recorder:
    """重い工程を差し替えて、呼ばれた順と引数を記録する。"""

    def __init__(self, tmp_path, monkeypatch):
        self.calls = []
        self.tmp = tmp_path

        def fake_split(shape, work, margin, up):
            self.calls.append(('split', shape, margin, up))
            paths = {p['name']: make_parts.part_paths(work, p['name'])
                     for p in make_parts.PARTS}
            for ps in paths.values():
                open(ps['raw'], 'w').write('x')
            return paths

        def fake_prepare(part, paths, args):
            self.calls.append(('prepare', part['name'], part['smooth'],
                               args.smooth_iters, args.smooth_lambda))
            open(paths['shape'], 'w').write('x')
            return paths['shape']

        def fake_paint(part, paths, args, up):
            self.calls.append(('paint', part['name'], args.texsize, up))
            open(paths['painted'], 'w').write('x')
            return paths['painted']

        def fake_project(part, paths, images, shape, up, fixviews=True):
            self.calls.append(('project', part['name'], up,
                               shape, tuple(sorted(images)), fixviews))
            open(paths['final'], 'w').write('x')
            return paths['final']

        # ★偽の glb を渡すので、実際に読む工程は差し替える
        monkeypatch.setattr(make_parts, 'detect_up', lambda shape: 'z')
        monkeypatch.setattr(make_parts, 'split', fake_split)
        monkeypatch.setattr(make_parts, 'prepare_shape', fake_prepare)
        monkeypatch.setattr(make_parts, 'paint', fake_paint)
        monkeypatch.setattr(make_parts, 'project', fake_project)

        import combine_parts
        self.combined = []

        def fake_combine(dst, parts, up='z'):
            self.combined.append((dst, list(parts), up))
            open(dst, 'w').write('x')
            return []

        monkeypatch.setattr(combine_parts, 'combine', fake_combine)


@pytest.fixture
def rec(tmp_path, monkeypatch):
    return Recorder(tmp_path, monkeypatch)


def test_通しの並び(tmp_path, imgs, rec):
    assert make_parts.main(base_argv(tmp_path, imgs)) == 0
    order = [c[0] for c in rec.calls]
    # ★切る → (形を作る) x2 → (塗る → 投影) x2
    assert order == ['split', 'prepare', 'prepare',
                     'paint', 'project', 'paint', 'project']


def test_塗る前に形を作る(tmp_path, imgs, rec):
    # ★ならす前に塗ると、彫られた細部を残したまま塗ってしまう
    make_parts.main(base_argv(tmp_path, imgs))
    order = [c[0] for c in rec.calls]
    assert order.index('prepare') < order.index('paint')


def test_投影は塗ったあと(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs))
    for name in ('head', 'body'):
        got = [i for i, c in enumerate(rec.calls) if len(c) > 1 and c[1] == name]
        kinds = [rec.calls[i][0] for i in got]
        assert kinds.index('paint') < kinds.index('project')


def test_投影には切る前の全身を渡す(tmp_path, imgs, rec):
    # ★これが無いとパーツ単体で正規化してしまい、1画素も貼れない（体 0% を実測）
    make_parts.main(base_argv(tmp_path, imgs))
    shape = os.path.abspath(str(tmp_path / 'retopo.glb'))
    for c in rec.calls:
        if c[0] == 'project':
            assert c[3] == shape


def test_投影には全身の絵を4枚とも渡す(tmp_path, imgs, rec):
    # ★絵は切らない。切ると全身の正規化と噛み合わない
    make_parts.main(base_argv(tmp_path, imgs))
    for c in rec.calls:
        if c[0] == 'project':
            assert c[4] == tuple(sorted(make_parts.VIEWS))


def test_全身の上方向を全パーツへ渡す(tmp_path, imgs, rec):
    # ★パーツからは測れない（背が低く一番長い軸が横になる）。
    #   ずれると絵とメッシュが対応せず、貼れる画素が激減する
    #   （2026-08-31 実測: 頭 9.46% / 体 6.18% → 直して 37.37% / 33.49%）
    make_parts.main(base_argv(tmp_path, imgs))
    got = {c[1]: c[2] for c in rec.calls if c[0] == 'project'}
    assert got == {'head': 'z', 'body': 'z'}


def test_上方向は指定できる(tmp_path, imgs, rec, monkeypatch):
    monkeypatch.setattr(make_parts, 'detect_up',
                        lambda shape: pytest.fail('指定したのに測っている'))
    make_parts.main(base_argv(tmp_path, imgs) + ['--up', 'y'])
    got = {c[1]: c[2] for c in rec.calls if c[0] == 'project'}
    assert got == {'head': 'y', 'body': 'y'}


def test_上方向はパーツではなく全身から測る(tmp_path, imgs, rec, monkeypatch):
    seen = []
    monkeypatch.setattr(make_parts, 'detect_up', lambda shape: seen.append(shape) or 'z')
    make_parts.main(base_argv(tmp_path, imgs))
    assert seen == [os.path.abspath(str(tmp_path / 'retopo.glb'))]


def test_知らない上方向は拒む(tmp_path, imgs):
    with pytest.raises(SystemExit):
        make_parts.parse_args(base_argv(tmp_path, imgs) + ['--up', 'x'])


def test_頭だけならす指示が渡る(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs))
    got = {c[1]: c[2] for c in rec.calls if c[0] == 'prepare'}
    assert got == {'head': True, 'body': False}


def test_texsizeがそのまま塗りへ渡る(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs, texsize=2048))
    for c in rec.calls:
        if c[0] == 'paint':
            assert c[2] == 2048


def test_結合には投影後のものを渡す(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs))
    dst, parts, _ = rec.combined[0]
    # ★出来上がるまで --out には置かない。作業用の名前で組み立てる
    assert os.path.basename(dst) == '_combined.glb'
    assert [os.path.basename(p) for p in parts] == ['head_final.glb', 'body_final.glb']


def test_投影を飛ばすと塗ったものを結合する(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs) + ['--skip-project'])
    assert not any(c[0] == 'project' for c in rec.calls)
    _, parts, _ = rec.combined[0]
    assert [os.path.basename(p) for p in parts] == ['head_painted.glb', 'body_painted.glb']


def test_形が無ければ何もせず止まる(tmp_path, imgs, rec):
    argv = base_argv(tmp_path, imgs)
    os.remove(str(tmp_path / 'retopo.glb'))
    with pytest.raises(SystemExit) as e:
        make_parts.main(argv)
    assert '形が見つかりません' in str(e.value)
    assert rec.calls == []


def test_切った結果が無ければ止まる(tmp_path, imgs, monkeypatch):
    # ★次の工程へ進む前に確かめる。進むと「塗る対象が無い」と別の所で落ちる
    import split_parts
    monkeypatch.setattr(split_parts, 'split', lambda *a, **k: None)
    with pytest.raises(SystemExit) as e:
        make_parts.split(str(tmp_path / 'a.glb'), str(tmp_path), 0.01, 'z')
    assert 'head' in str(e.value) or 'body' in str(e.value)


# ---- 上方向の判定（apply_reference_detail 側） -----------------------------

def test_一番長い軸がZなら上はZ():
    np = pytest.importorskip('numpy')
    import apply_reference_detail as ARD
    v = np.array([[0, 0, 0], [0.76, 0.43, 1.0]])          # 全身の実測に近い形
    assert ARD.detect_up(v) == 'z'


def test_一番長い軸が横なら上はYと判定してしまう():
    # ★これが「パーツからは測れない」の中身。実測（2026-08-31）で
    #   頭 X=0.488 Y=0.433 Z=0.470、体 X=0.758 Y=0.287 Z=0.536。
    #   どちらも Z 上なのに X が一番長く、Y 上と判定される
    np = pytest.importorskip('numpy')
    import apply_reference_detail as ARD
    assert ARD.detect_up(np.array([[0, 0, 0], [0.488, 0.433, 0.470]])) == 'y'
    assert ARD.detect_up(np.array([[0, 0, 0], [0.758, 0.287, 0.536]])) == 'y'


def test_上方向を指定すれば測らない(tmp_path, monkeypatch):
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import apply_reference_detail as ARD
    m = trimesh.creation.box(extents=(0.758, 0.287, 0.536))
    p = tmp_path / 'part.glb'
    m.export(str(p))
    monkeypatch.setattr(ARD, 'detect_up',
                        lambda v: pytest.fail('指定したのに測っている'))
    _, converted = ARD.load_mesh_as_yup(str(p), up='z')
    assert converted is True                               # Z上 -> Y上に変換した


def test_知らない上方向は拒む_projection(tmp_path):
    trimesh = pytest.importorskip('trimesh')
    import apply_reference_detail as ARD
    p = tmp_path / 'part.glb'
    trimesh.creation.box().export(str(p))
    with pytest.raises(SystemExit):
        ARD.load_mesh_as_yup(str(p), up='x')


# ---- 出来上がりの向き（glTF は Y が上と決まっている） ---------------------

def test_結合で上方向を渡す(tmp_path, imgs, rec, monkeypatch):
    # ★直さないと、出来た glb は標準のビューアで寝たまま表示される
    import combine_parts
    seen = []
    monkeypatch.setattr(combine_parts, 'combine',
                        lambda dst, parts, up='z': seen.append(up) or
                        open(dst, 'w').write('x'))
    make_parts.main(base_argv(tmp_path, imgs))
    assert seen == ['z']


def test_Z上はY上へ直す():
    np = pytest.importorskip('numpy')
    import combine_parts
    m = np.array(combine_parts.UP_TO_GLTF['z'], dtype=np.float64)
    # (x, y, z) -> (x, z, -y)
    got = m @ np.array([1.0, 2.0, 3.0, 1.0])
    assert list(got[:3]) == [1.0, 3.0, -2.0]


def test_Y上はそのまま():
    import combine_parts
    assert combine_parts.UP_TO_GLTF['y'] is None


def test_結合は知らない上方向を拒む(tmp_path):
    import combine_parts
    with pytest.raises(SystemExit):
        combine_parts.combine(str(tmp_path / 'o.glb'), ['a.glb'], up='x')


def test_結合はパーツが無ければ拒む(tmp_path):
    import combine_parts
    with pytest.raises(SystemExit):
        combine_parts.combine(str(tmp_path / 'o.glb'), [])


def test_結合は無いパーツを名指しして拒む(tmp_path):
    import combine_parts
    with pytest.raises(SystemExit) as e:
        combine_parts.combine(str(tmp_path / 'o.glb'), [str(tmp_path / 'ない.glb')])
    assert 'ない.glb' in str(e.value)


def glb_extents(path):
    """glb の【生の中身】から、各メッシュの大きさを返す。

    ★trimesh で読み直してはいけない。trimesh は自分が書いたものを
      そのまま読むだけなので、規約に合っているかを検証できない
      （実際にこれで一度、直っていないのに緑になった）。
    """
    import json
    import struct

    import numpy as np
    b = open(path, 'rb').read()
    assert b[:4] == b'glTF', 'glb ではない'
    off, js = 12, None
    while off < len(b):
        ln, kind = struct.unpack_from('<II', b, off)
        if kind == 0x4E4F534A:                       # 'JSON'
            js = json.loads(b[off + 8:off + 8 + ln].decode('utf-8'))
            break
        off += 8 + ln
    assert js is not None, 'JSON の塊が無い'
    # ★ノード変換が付いていないことも確かめる（付けても書き出しで落ちる）
    for nd in js.get('nodes', []):
        assert 'matrix' not in nd and 'rotation' not in nd, \
            'ノード変換に頼っている。頂点へ焼き込むこと'
    out = []
    for mesh in js['meshes']:
        for pr in mesh['primitives']:
            a = js['accessors'][pr['attributes']['POSITION']]
            out.append(np.array(a['max']) - np.array(a['min']))
    return out


def test_結合すると生のglbでYが一番長くなる(tmp_path):
    # ★これが glTF の決まり（+Y が上）。直さないと標準のビューアで寝て表示される
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import combine_parts
    src = tmp_path / 'part.glb'
    trimesh.creation.box(extents=(0.4, 0.3, 1.0)).export(str(src))   # Z が一番長い
    assert int(np.argmax(glb_extents(str(src))[0])) == 2             # 入力は Z
    dst = tmp_path / 'out.glb'
    combine_parts.combine(str(dst), [str(src)], up='z')
    ext = glb_extents(str(dst))[0]
    assert int(np.argmax(ext)) == 1, f'Y が一番長いはず: {ext}'
    assert ext == pytest.approx([0.4, 1.0, 0.3], abs=1e-5)           # (x,y,z)->(x,z,-y)


def test_up_yなら向きを変えない(tmp_path):
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import combine_parts
    src = tmp_path / 'part.glb'
    trimesh.creation.box(extents=(0.4, 0.3, 1.0)).export(str(src))
    dst = tmp_path / 'out.glb'
    combine_parts.combine(str(dst), [str(src)], up='y')
    assert glb_extents(str(dst))[0] == pytest.approx([0.4, 0.3, 1.0], abs=1e-5)


def test_パーツごとに別のメッシュのまま残る(tmp_path):
    # ★1つに結合するとテクスチャが混ざり、パーツごとに 2048 を使う狙いが消える
    trimesh = pytest.importorskip('trimesh')
    import combine_parts
    srcs = []
    for i, ext in enumerate(((0.4, 0.3, 1.0), (0.2, 0.2, 0.5))):
        p = tmp_path / f'p{i}.glb'
        trimesh.creation.box(extents=ext).export(str(p))
        srcs.append(str(p))
    dst = tmp_path / 'out.glb'
    combine_parts.combine(str(dst), srcs, up='z')
    assert len(glb_extents(str(dst))) == 2


# ---- 差し替えていた工程そのものを見る -------------------------------------
# ★Recorder は paint / project / combine を丸ごと差し替えるので、
#   その【中身】が壊れても気づけない。ここは1段下で検証する。

class FakeMesh:
    def __init__(self, n=3):
        import numpy as np
        self.vertices = np.zeros((n, 3))
        self.exported = None

    def export(self, path):
        self.exported = path
        open(path, 'w').write('x')


def test_投影は全身と上方向をそのまま下へ渡す(tmp_path, monkeypatch):
    # ★partof が抜けると1画素も貼れない。up が抜けるとパーツから測って外す
    import apply_reference_detail as ARD
    got = {}

    def fake(src, dst, images, partof=None, fixviews=False, up=None, **kw):
        got.update(src=src, dst=dst, partof=partof, fixviews=fixviews, up=up)
        open(dst, 'w').write('x')

    monkeypatch.setattr(ARD, 'project', fake)
    ps = make_parts.part_paths(str(tmp_path), 'head')
    make_parts.project(make_parts.PARTS[0], ps, {'front': 'f.png'}, 'FULL.glb', 'z')
    assert got['partof'] == 'FULL.glb'
    assert got['up'] == 'z'
    assert got['fixviews'] is True
    assert got['src'] == ps['painted'] and got['dst'] == ps['final']


def test_塗りへ渡す引数(tmp_path, imgs, monkeypatch):
    # ★texsize が固定値に化けると、黙って解像度が落ちる
    import make_texture
    got = {}
    monkeypatch.setattr(make_texture, 'main', lambda argv: got.update(argv=argv))
    a = make_parts.parse_args(base_argv(tmp_path, imgs, texsize=2048))
    ps = make_parts.part_paths(str(tmp_path), 'head')
    make_parts.paint(make_parts.PARTS[0], ps, a, 'z')
    argv = got['argv']
    assert argv[argv.index('--texsize') + 1] == '2048'
    assert argv[argv.index('--mesh') + 1] == ps['shape']       # ★ならした形を塗る
    assert argv[argv.index('--out') + 1] == ps['painted']
    assert '--paint-root' not in argv                          # 未指定なら渡さない


def test_塗り環境を指定したら渡す(tmp_path, imgs, monkeypatch):
    import make_texture
    got = {}
    monkeypatch.setattr(make_texture, 'main', lambda argv: got.update(argv=argv))
    a = make_parts.parse_args(base_argv(tmp_path, imgs) + ['--paint-root', 'X:/env'])
    make_parts.paint(make_parts.PARTS[0],
                     make_parts.part_paths(str(tmp_path), 'head'), a, 'z')
    assert got['argv'][got['argv'].index('--paint-root') + 1] == 'X:/env'


def test_ならしの既定は実測した値():
    # ★8回・0.5 で「フードの縁が滑らかになり目がはっきりする」ことを実測している。
    #   変えると静かに結果が変わる
    import smooth_part
    assert (smooth_part.ITERS, smooth_part.LAMBDA) == (8, 0.5)


def test_ならしの既定はmake_partsと揃っている(tmp_path, imgs):
    import smooth_part
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    assert (a.smooth_iters, a.smooth_lambda) == (smooth_part.ITERS, smooth_part.LAMBDA)


# ---- apply_reference_detail.project の中身 --------------------------------

def stub_loader(seen):
    def fake(path, up=None):
        seen.append((path, up))
        return FakeMesh(), False
    return fake


def test_全身にもパーツと同じ上方向を使う(tmp_path, monkeypatch):
    # ★ここがずれると、パーツと基準の座標系が食い違って貼れなくなる
    import apply_reference_detail as ARD
    seen = []
    monkeypatch.setattr(ARD, 'load_mesh_as_yup', stub_loader(seen))
    monkeypatch.setattr(ARD, 'apply_detail', lambda mesh, imgs, **kw: FakeMesh())
    ARD.project('PART.glb', str(tmp_path / 'o.glb'), {'front': object()},
                partof='FULL.glb', up='z')
    assert seen == [('PART.glb', 'z'), ('FULL.glb', 'z')]


def test_全身を渡すと正規化の基準と固定が入る(tmp_path, monkeypatch):
    import apply_reference_detail as ARD
    got = {}
    monkeypatch.setattr(ARD, 'load_mesh_as_yup', stub_loader([]))
    monkeypatch.setattr(ARD, 'apply_detail',
                        lambda mesh, imgs, **kw: got.update(kw) or FakeMesh())
    ARD.project('a.glb', str(tmp_path / 'o.glb'), {'front': object()},
                partof='FULL.glb', up='z')
    assert got['fixfit'] is True
    assert got['norm_ref'] is not None


def test_全身を渡さなければ正規化の基準を入れない(tmp_path, monkeypatch):
    import apply_reference_detail as ARD
    got = {}
    monkeypatch.setattr(ARD, 'load_mesh_as_yup', stub_loader([]))
    monkeypatch.setattr(ARD, 'apply_detail',
                        lambda mesh, imgs, **kw: got.update(kw) or FakeMesh())
    ARD.project('a.glb', str(tmp_path / 'o.glb'), {'front': object()})
    assert 'norm_ref' not in got and 'fixfit' not in got


@pytest.mark.parametrize('fixviews', [True, False])
def test_視点の割り当ては必ず元に戻す(tmp_path, monkeypatch, fixviews):
    # ★同じプロセスで頭→体と続けて呼ぶので、戻し忘れると次のパーツが壊れる
    import project_detail as PD
    import apply_reference_detail as ARD
    before = PD.assign_views
    monkeypatch.setattr(ARD, 'load_mesh_as_yup', stub_loader([]))
    monkeypatch.setattr(ARD, 'apply_detail', lambda mesh, imgs, **kw: FakeMesh())
    ARD.project('a.glb', str(tmp_path / 'o.glb'), {'front': object()},
                fixviews=fixviews)
    assert PD.assign_views is before


def test_途中で落ちても視点の割り当てを戻す(tmp_path, monkeypatch):
    import project_detail as PD
    import apply_reference_detail as ARD
    before = PD.assign_views

    def boom(mesh, imgs, **kw):
        raise RuntimeError('途中で落ちた')

    monkeypatch.setattr(ARD, 'load_mesh_as_yup', stub_loader([]))
    monkeypatch.setattr(ARD, 'apply_detail', boom)
    with pytest.raises(RuntimeError):
        ARD.project('a.glb', str(tmp_path / 'o.glb'), {'front': object()},
                    fixviews=True)
    assert PD.assign_views is before


def test_固定するときだけ差し替える(tmp_path, monkeypatch):
    import project_detail as PD
    import apply_reference_detail as ARD
    seen = []
    monkeypatch.setattr(ARD, 'load_mesh_as_yup', stub_loader([]))
    monkeypatch.setattr(ARD, 'apply_detail',
                        lambda mesh, imgs, **kw: seen.append(PD.assign_views) or FakeMesh())
    ARD.project('a.glb', str(tmp_path / 'o.glb'), {'front': object()}, fixviews=True)
    ARD.project('a.glb', str(tmp_path / 'o.glb'), {'front': object()}, fixviews=False)
    assert seen[0] is ARD._fixed_assign        # 固定した
    assert seen[1] is not ARD._fixed_assign    # 固定していない


def test_正面の絵が無ければ止まる(tmp_path, monkeypatch):
    import apply_reference_detail as ARD
    with pytest.raises(SystemExit) as e:
        ARD.project('a.glb', str(tmp_path / 'o.glb'), {'left': object()})
    assert '正面' in str(e.value)


def test_結合は1つに混ぜない(tmp_path):
    # ★混ぜるとテクスチャが1枚になり、パーツごとに 2048 を使う狙いが消える
    trimesh = pytest.importorskip('trimesh')
    import combine_parts
    srcs = []
    for i, ext in enumerate(((0.4, 0.3, 1.0), (0.2, 0.2, 0.5))):
        p = tmp_path / f'p{i}.glb'
        trimesh.creation.box(extents=ext).export(str(p))
        srcs.append(str(p))
    dst = tmp_path / 'out.glb'
    info = combine_parts.combine(str(dst), srcs, up='z')
    assert len(info) == 2
    assert len(glb_extents(str(dst))) == 2         # 生の glb でも2つ


# ---- レビューで「壊しても緑」だった箇所 -----------------------------------

def test_塗りへ上方向を渡す(tmp_path, imgs, monkeypatch):
    # ★渡さないと塗りだけ既定（z）で動く。Y上の形をもう一度倒して塗るので、
    #   形は往復して戻り【テクスチャだけが壊れる】
    import make_texture
    got = {}
    monkeypatch.setattr(make_texture, 'main', lambda argv: got.update(argv=argv))
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    make_parts.paint(make_parts.PARTS[0], make_parts.part_paths(str(tmp_path), 'head'),
                     a, 'y')
    assert got['argv'][got['argv'].index('--up') + 1] == 'y'


def test_切る工程へ上方向を渡す(tmp_path, monkeypatch):
    # ★渡さないと split_parts が「一番長い軸が上」で決め直す。腕を広げた題材で
    #   横幅が背丈を超えると、腕に沿って首を探す
    import split_parts
    got = {}
    monkeypatch.setattr(split_parts, 'split',
                        lambda src, h, b, margin, up: got.update(margin=margin, up=up) or
                        (open(h, 'w').write('x'), open(b, 'w').write('x')))
    make_parts.split('a.glb', str(tmp_path), 0.02, 'y')
    assert got == {'margin': 0.02, 'up': 'y'}


def test_通しでも上方向が4か所すべてへ届く(tmp_path, imgs, rec):
    # ★split / paint / project / combine の4か所。1つでも抜けると静かに壊れる
    import combine_parts
    seen = []
    make_parts.main(base_argv(tmp_path, imgs) + ['--up', 'y'])
    ups = {c[0]: c[-1] if c[0] == 'split' else None for c in rec.calls}
    assert [c[3] for c in rec.calls if c[0] == 'split'] == ['y']
    assert {c[3] for c in rec.calls if c[0] == 'paint'} == {'y'}
    assert {c[2] for c in rec.calls if c[0] == 'project'} == {'y'}
    assert rec.combined[0][2] == 'y'


def test_通しでmarginが切る工程へ届く(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs, margin=0.05))
    assert [c[2] for c in rec.calls if c[0] == 'split'] == [0.05]


def test_marginの範囲(tmp_path, imgs):
    for bad in ('0.6', '-0.6'):
        with pytest.raises(SystemExit):
            make_parts.parse_args(base_argv(tmp_path, imgs) + ['--margin', bad])
    assert make_parts.parse_args(base_argv(tmp_path, imgs) +
                                 ['--margin', '0.4']).margin == 0.4


def test_視点の固定は外せる(tmp_path, imgs, rec):
    # ★別の題材では固定の並びが合わないかもしれない
    make_parts.main(base_argv(tmp_path, imgs) + ['--no-fixviews'])
    assert {c[5] for c in rec.calls if c[0] == 'project'} == {False}


def test_既定では固定する(tmp_path, imgs, rec):
    make_parts.main(base_argv(tmp_path, imgs))
    assert {c[5] for c in rec.calls if c[0] == 'project'} == {True}


def test_ならすのは頭だけを実際に確かめる(tmp_path, imgs, monkeypatch):
    # ★PARTS の表ではなく【それを使うコード】を見る。条件が反転すると
    #   体がならされて背中の鍵穴が消え、頭はならされない
    import smooth_part
    smoothed = []
    monkeypatch.setattr(smooth_part, 'smooth',
                        lambda src, dst, i, l: smoothed.append(os.path.basename(src)) or
                        open(dst, 'w').write('x'))
    a = make_parts.parse_args(base_argv(tmp_path, imgs))
    for part in make_parts.PARTS:
        ps = make_parts.part_paths(str(tmp_path), part['name'])
        open(ps['raw'], 'w').write('x')
        make_parts.prepare_shape(part, ps, a)
    assert smoothed == ['head_raw.glb']
    # 体はそのまま写しただけ
    assert os.path.isfile(make_parts.part_paths(str(tmp_path), 'body')['shape'])


def test_ならし0回なら頭も写すだけ(tmp_path, imgs, monkeypatch):
    import smooth_part
    monkeypatch.setattr(smooth_part, 'smooth',
                        lambda *a, **k: pytest.fail('0回なのに呼んでいる'))
    a = make_parts.parse_args(base_argv(tmp_path, imgs) + ['--smooth-iters', '0'])
    ps = make_parts.part_paths(str(tmp_path), 'head')
    open(ps['raw'], 'w').write('x')
    make_parts.prepare_shape(make_parts.PARTS[0], ps, a)
    assert os.path.isfile(ps['shape'])


def test_切った先を取り違えない(tmp_path, monkeypatch):
    # ★頭と体を入れ替えると、体がならされて頭がならされない
    import split_parts
    got = {}
    monkeypatch.setattr(split_parts, 'split',
                        lambda src, h, b, margin, up: got.update(head=h, body=b) or
                        (open(h, 'w').write('x'), open(b, 'w').write('x')))
    make_parts.split('a.glb', str(tmp_path), 0.01, 'z')
    assert os.path.basename(got['head']) == 'head_raw.glb'
    assert os.path.basename(got['body']) == 'body_raw.glb'


def test_固定の並び():
    # ★FIXED の中身そのものを固定する。入れ替わると正面の顔を後頭部に貼る
    import apply_reference_detail as ARD
    assert ARD.FIXED == {'front': 'front', 'right': 'left',
                         'back': 'back', 'left': 'right'}


def test_固定の並びは戻り値の形も守る():
    # ★assign_views の契約は {向き: (絵のキー, 一致度)}
    import apply_reference_detail as ARD
    got = ARD._fixed_assign(None, {'front': 1, 'left': 1, 'right': 1, 'back': 1}, 0)
    assert set(got) == set(ARD.FIXED)
    for v, (k, score) in got.items():
        assert k == ARD.FIXED[v] and score == 1.0


def test_複数パーツが全部同じだけ回る(tmp_path):
    # ★1個目だけ回すと頭だけ立って体が寝る
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import combine_parts
    srcs = []
    for i in range(2):
        p = tmp_path / f'p{i}.glb'
        trimesh.creation.box(extents=(0.4, 0.3, 1.0)).export(str(p))
        srcs.append(str(p))
    dst = tmp_path / 'out.glb'
    combine_parts.combine(str(dst), srcs, up='z')
    for ext in glb_extents(str(dst)):
        assert ext == pytest.approx([0.4, 1.0, 0.3], abs=1e-5)


def test_出来上がるまで出力先に前回のものを残さない(tmp_path, imgs, rec, monkeypatch):
    # ★途中で落ちたとき、前回の成果物が「今回の結果」に見えてしまう
    import combine_parts
    out = tmp_path / 'final.glb'
    out.write_text('前回のもの')

    def boom(dst, parts, up='z'):
        raise RuntimeError('結合で落ちた')

    monkeypatch.setattr(combine_parts, 'combine', boom)
    with pytest.raises(RuntimeError):
        make_parts.main(base_argv(tmp_path, imgs))
    assert out.read_text() == '前回のもの'      # 触っていない


def test_成功したら置き換わる(tmp_path, imgs, rec):
    out = tmp_path / 'final.glb'
    out.write_text('前回のもの')
    make_parts.main(base_argv(tmp_path, imgs))
    assert out.read_text() != '前回のもの'


# ---- 首の切り口を動かさない -----------------------------------------------

def test_ならしても切り口は動かない(tmp_path):
    # ★体はならさないので、動かすと頭と体の切り口が合わなくなる。
    #   実測（2026-08-31）では z が +0.00803 動き、頭の下端が体の上端を上回った
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import smooth_part
    m = trimesh.creation.icosphere(subdivisions=3)
    v = np.asarray(m.vertices, float)
    keep = np.asarray(m.faces)[v[np.asarray(m.faces)].mean(axis=1)[:, 2] > -0.5]
    uniq, inv = np.unique(keep.reshape(-1), return_inverse=True)
    cut = trimesh.Trimesh(vertices=v[uniq], faces=inv.reshape(-1, 3), process=False)
    src, dst = tmp_path / 'a.glb', tmp_path / 'b.glb'
    cut.export(str(src))
    before = np.asarray(trimesh.load(str(src), force='mesh', process=False).vertices, float)
    info = smooth_part.smooth(str(src), str(dst), iters=8, lam=0.5)
    after = np.asarray(trimesh.load(str(dst), force='mesh', process=False).vertices, float)
    rim = smooth_part.boundary_vertices(cut)
    assert len(rim) > 0, '切り口が見つからない'
    assert info['pinned'] == len(rim)
    assert np.allclose(before[rim], after[rim]), '切り口が動いた'
    assert not np.allclose(before, after), '何もならしていない'


def test_固定しない指定もできる(tmp_path):
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import smooth_part
    m = trimesh.creation.box()
    src, dst = tmp_path / 'a.glb', tmp_path / 'b.glb'
    m.export(str(src))
    assert smooth_part.smooth(str(src), str(dst), pin_boundary=False)['pinned'] == 0


def test_閉じたメッシュには切り口が無い():
    trimesh = pytest.importorskip('trimesh')
    import smooth_part
    assert len(smooth_part.boundary_vertices(trimesh.creation.box())) == 0


# ---- split_parts の中身 ---------------------------------------------------

def make_figure():
    """首がくびれた人型もどきを作る（体の箱＋細い首＋頭の球）。Z 上。"""
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    body = trimesh.creation.box(extents=(0.4, 0.3, 0.5))
    body.apply_translation((0, 0, 0.25))
    neck = trimesh.creation.cylinder(radius=0.05, height=0.12, sections=32)
    neck.apply_translation((0, 0, 0.56))
    head = trimesh.creation.icosphere(subdivisions=3, radius=0.2)
    head.apply_translation((0, 0, 0.8))
    return trimesh.util.concatenate([body, neck, head])


def test_首はくびれた所に見つかる():
    np = pytest.importorskip('numpy')
    import split_parts
    v = np.asarray(make_figure().vertices, dtype=np.float64)
    neck, w = split_parts.find_neck(v)
    assert 0.50 < neck < 0.63, f'首が {neck:.3f} に出た'
    assert w < 0.2                       # くびれているので断面が小さい


def test_首が見つからなければ止まる():
    # ★黙って高さ45%を返すと、胴の真ん中で切った「頭」ができる
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import split_parts
    v = np.asarray(trimesh.creation.box().vertices, dtype=np.float64)   # 8頂点
    with pytest.raises(SystemExit) as e:
        split_parts.find_neck(v)
    assert '首が見つかりません' in str(e.value)


def test_切ると頭と体に分かれる(tmp_path):
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import split_parts
    src = tmp_path / 'a.glb'
    make_figure().export(str(src))
    h, b = tmp_path / 'h.glb', tmp_path / 'b.glb'
    split_parts.split(str(src), str(h), str(b), 0.01, 'z')
    hv = np.asarray(trimesh.load(str(h), force='mesh').vertices, float)
    bv = np.asarray(trimesh.load(str(b), force='mesh').vertices, float)
    assert hv[:, 2].mean() > bv[:, 2].mean(), '頭のほうが上にあるはず'
    assert len(hv) > 10 and len(bv) > 10


def test_上方向の指定に従う(tmp_path):
    # ★無視して測ると、腕を広げた題材で横方向に切ってしまう
    np = pytest.importorskip('numpy')
    trimesh = pytest.importorskip('trimesh')
    import split_parts
    src = tmp_path / 'a.glb'
    make_figure().export(str(src))
    got = {}
    for up in ('z', 'y'):
        h, b = tmp_path / f'h_{up}.glb', tmp_path / f'b_{up}.glb'
        try:
            split_parts.split(str(src), str(h), str(b), 0.01, up)
            got[up] = len(np.asarray(trimesh.load(str(h), force='mesh').vertices))
        except SystemExit:
            got[up] = 'エラー'
    # ★z と y で結果が変わる＝指定が効いている
    assert got['z'] != got['y'], f'上方向の指定が効いていない: {got}'


def test_切った結果がほぼ空なら止まる(tmp_path):
    # ★空のまま進むと 47 秒かけて空を塗る
    trimesh = pytest.importorskip('trimesh')
    import split_parts
    src = tmp_path / 'a.glb'
    make_figure().export(str(src))
    with pytest.raises(SystemExit) as e:
        split_parts.split(str(src), str(tmp_path / 'h.glb'),
                          str(tmp_path / 'b.glb'), 0.45, 'z')
    assert '面が少なすぎます' in str(e.value)


@pytest.mark.parametrize('bad', ['w', 'Z', '', 'up'])
def test_切る工程は知らない上方向を拒む(tmp_path, bad):
    # ★x は許す（横に寝た題材があり得る）。大文字や空文字は拒む
    import split_parts
    with pytest.raises(SystemExit):
        split_parts.split('a.glb', 'h.glb', 'b.glb', 0.01, bad)


def test_切る工程はおかしなmarginを拒む(tmp_path):
    import split_parts
    with pytest.raises(SystemExit):
        split_parts.split('a.glb', 'h.glb', 'b.glb', 0.9, 'z')
