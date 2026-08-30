# tools/paintenv.py・tools/make_texture.py・tools/paint_backend.py のテスト。
# GPU も torch も要らない（paint_backend は torch を関数の中でしか import しない）。
import os
import pathlib
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import paintenv                                                  # noqa: E402
import make_texture                                              # noqa: E402
import paint_backend                                             # noqa: E402


def make_env(base, complete=True):
    """塗り環境らしいディレクトリを作る。complete=False なら venv を欠く。"""
    base.mkdir(parents=True, exist_ok=True)
    (base / paintenv.UPSTREAM).mkdir(exist_ok=True)
    ck = base / paintenv.CHECKPOINT
    ck.parent.mkdir(parents=True, exist_ok=True)
    ck.write_text('# dummy')
    if complete:
        py = base / 'venv-21' / 'Scripts'
        py.mkdir(parents=True, exist_ok=True)
        (py / 'python.exe').write_text('# dummy')
    return base


# ---- 探す順序 -------------------------------------------------------------

def test_明示した場所が最優先(tmp_path):
    assert paintenv.candidates(explicit='X:/明示', root=str(tmp_path))[0] == 'X:/明示'


def test_環境変数はリポジトリ直下より先(tmp_path, monkeypatch):
    monkeypatch.setenv(paintenv.ENV_VAR, 'X:/環境変数')
    got = paintenv.candidates(root=str(tmp_path))
    assert got.index('X:/環境変数') < got.index(os.path.join(str(tmp_path), 'paint'))


def test_明示指定は環境変数を上書きする(tmp_path, monkeypatch):
    # ★両方を見ると、有効な --paint-root を渡しても古い環境変数で止まる
    monkeypatch.setenv(paintenv.ENV_VAR, 'X:/環境変数')
    got = paintenv.candidates(explicit='X:/明示', root=str(tmp_path))
    assert got[0] == 'X:/明示'
    assert 'X:/環境変数' not in got


def test_明示指定があれば壊れた環境変数を無視する(tmp_path, monkeypatch):
    monkeypatch.setenv(paintenv.ENV_VAR, str(tmp_path / 'こわれている'))
    env = make_env(tmp_path / 'ちゃんとある')
    path, _, _ = paintenv.find(explicit=str(env), root=str(tmp_path))
    assert path == str(env)


def test_借り物は最後(tmp_path):
    assert paintenv.candidates(root=str(tmp_path))[-1] == paintenv.BORROWED


def test_環境変数が無ければ候補に入らない(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    assert paintenv.candidates(root=str(tmp_path)) == [
        os.path.join(str(tmp_path), 'paint'), paintenv.BORROWED]


# ---- 足りないものの検出 ---------------------------------------------------

def test_揃っていれば空(tmp_path):
    assert paintenv.missing_parts(str(make_env(tmp_path / 'p'))) == {}


def test_venvが無ければ検出する(tmp_path):
    lack = paintenv.missing_parts(str(make_env(tmp_path / 'p', complete=False)))
    assert list(lack) == ['Python 環境（venv-21）']


def test_何も無ければ3つとも検出する(tmp_path):
    assert set(paintenv.missing_parts(str(tmp_path))) == {n for n, _, _ in paintenv.REQUIRED}
    assert len(paintenv.REQUIRED) == 3


def test_上流がファイルなら使えないと判定する(tmp_path):
    # ★中断した clone が同名のファイルを残すことがある。exists だけだと通ってしまい、
    #   案内の無い生のエラーになる
    env = make_env(tmp_path / 'p')
    import shutil
    shutil.rmtree(env / paintenv.UPSTREAM)
    (env / paintenv.UPSTREAM).write_text('こわれた clone')
    assert list(paintenv.missing_parts(str(env))) == ['上流の Hunyuan3D-2.1']


def test_pythonがディレクトリなら使えないと判定する(tmp_path):
    # ★venv 作成に失敗すると python.exe が空ディレクトリで残ることがある
    env = make_env(tmp_path / 'p')
    (env / paintenv.PYTHON).unlink()
    (env / paintenv.PYTHON).mkdir()
    assert list(paintenv.missing_parts(str(env))) == ['Python 環境（venv-21）']


def test_高精細化の重みも必須(tmp_path):
    # ★2026-08-31 実測。無いと上流が Hunyuan3DPaintPipeline.__init__ の中で
    #   生の FileNotFoundError を投げる。上流の既定も cwd 相対で同じ場所を指す
    env = make_env(tmp_path / 'p')
    (env / paintenv.CHECKPOINT).unlink()
    assert list(paintenv.missing_parts(str(env))) == ['高精細化の重み']


# ---- find -----------------------------------------------------------------

def test_見つかれば場所とpythonを返す(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    env = make_env(tmp_path / 'paint')
    path, py, borrowed = paintenv.find(root=str(tmp_path))
    assert path == str(env)
    assert py == os.path.join(str(env), paintenv.PYTHON)
    assert borrowed is False


def test_借り物判定は大文字小文字を区別しない(tmp_path, monkeypatch):
    # ★Windows のパスは大文字小文字を区別しない。区別すると
    #   z:\... と書いただけで「借りています」が出ず、自前環境と誤認する
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    env = make_env(tmp_path / 'BorrowedEnv')
    monkeypatch.setattr(paintenv, 'BORROWED', str(env).upper())
    assert paintenv.is_borrowed(str(env)) is True


def test_別の場所は借り物ではない(tmp_path, monkeypatch):
    monkeypatch.setattr(paintenv, 'BORROWED', str(tmp_path / 'a'))
    assert paintenv.is_borrowed(str(tmp_path / 'b')) is False


def test_借り物かどうかを見分ける(tmp_path, monkeypatch):
    # ★借りていることを黙って隠さない。使う側が気づけるようにする
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    monkeypatch.setattr(paintenv, 'BORROWED', str(make_env(tmp_path / 'borrowed')))
    _, _, borrowed = paintenv.find(root=str(tmp_path / 'ない'))
    assert borrowed is True


def test_揃っていない候補は飛ばして次を見る(tmp_path, monkeypatch):
    # ★中途半端な paint/ があっても、そこで止まらずに借り物へ落ちる
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    make_env(tmp_path / 'paint', complete=False)
    ok = make_env(tmp_path / 'borrowed')
    monkeypatch.setattr(paintenv, 'BORROWED', str(ok))
    path, _, borrowed = paintenv.find(root=str(tmp_path))
    assert path == str(ok)
    assert borrowed is True


def test_明示した場所が使えないなら黙って他へ落ちない(tmp_path, monkeypatch):
    # ★ここが落ちると、指したつもりの無い環境で塗った結果が返る
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    monkeypatch.setattr(paintenv, 'BORROWED', str(make_env(tmp_path / 'borrowed')))
    with pytest.raises(SystemExit) as e:
        paintenv.find(explicit=str(tmp_path / 'ない'), root=str(tmp_path))
    msg = str(e.value)
    assert '--paint-root に指定された場所が使えません' in msg
    assert str(tmp_path / 'ない') in msg


def test_環境変数の場所が使えないなら黙って他へ落ちない(tmp_path, monkeypatch):
    monkeypatch.setenv(paintenv.ENV_VAR, str(tmp_path / 'ない'))
    monkeypatch.setattr(paintenv, 'BORROWED', str(make_env(tmp_path / 'borrowed')))
    with pytest.raises(SystemExit) as e:
        paintenv.find(root=str(tmp_path))
    assert f'環境変数 {paintenv.ENV_VAR} に指定された場所が使えません' in str(e.value)


def test_自動で探すpaintが中途半端でも借り物へ落ちてよい(tmp_path, monkeypatch):
    # ★「指定」ではないので止めない。上の2件との違いを固定する
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    make_env(tmp_path / 'paint', complete=False)
    monkeypatch.setattr(paintenv, 'BORROWED', str(make_env(tmp_path / 'borrowed')))
    _, _, borrowed = paintenv.find(root=str(tmp_path))
    assert borrowed is True


def test_無ければ足りないものを列挙して止まる(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    monkeypatch.setattr(paintenv, 'BORROWED', str(tmp_path / 'これも無い'))
    with pytest.raises(SystemExit) as e:
        paintenv.find(root=str(tmp_path / 'ない'))
    msg = str(e.value)
    assert paintenv.DOC in msg                    # 作り方に案内する
    assert paintenv.ENV_VAR in msg                # 既にある場合の指し方も案内する
    assert '無い: Python 環境（venv-21）' in msg   # 何が足りないかを名指しする
    assert '無い: 上流の Hunyuan3D-2.1' in msg
    assert '無い: 高精細化の重み' in msg


# ---- make_texture の引数 --------------------------------------------------

def test_texsizeの既定は4096():
    # ★下げると実際に出る絵が半分になる。既定は 4096（実際に出るのは 2048）
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    assert a.texsize == 4096


def test_塗りの既定値():
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    assert (a.rendersize, a.views, a.res) == (1024, 6, 512)


def test_必須の引数が欠けると拒む():
    with pytest.raises(SystemExit):
        make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png'])


@pytest.mark.parametrize('name', ['texsize', 'rendersize', 'views', 'res'])
def test_大きさが0以下なら拒む(name):
    with pytest.raises(SystemExit):
        make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png',
                                 '--out', 'o.glb', f'--{name}', '0'])


@pytest.mark.parametrize('out', ['out/p', 'out/p.obj', 'out/p.gltf', 'out/p.png'])
def test_出力先がglbでなければ塗りの前に拒む(out):
    # ★塗りの実体が stem から中間ファイル名を作るので、.obj だと
    #   出力先と中間ファイルが同名になり自分自身を上書きする
    with pytest.raises(SystemExit):
        make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', out])


def test_大文字のGLBも受ける():
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.GLB'])
    assert a.out == 'o.GLB'


def test_入力と出力が同じなら拒む():
    # ★入力の形が結果で上書きされて消える
    with pytest.raises(SystemExit):
        make_texture.parse_args(['--mesh', 'out/a.glb', '--front', 'f.png',
                                 '--out', 'out/a.glb'])


# ---- 別環境へ渡す環境変数 -------------------------------------------------

def test_子へPYTHONPATHを渡さない(monkeypatch):
    # ★親は 3.11、子は 3.12。形づくり側の道を継ぐと DLL load failed になる
    monkeypatch.setenv('PYTHONPATH', r'C:\work\parts-studio\TRELLIS.2')
    monkeypatch.setenv('PYTHONHOME', r'C:\python311')
    env = make_texture.child_env()
    assert 'PYTHONPATH' not in env
    assert 'PYTHONHOME' not in env


def test_子の文字コードを固定する(monkeypatch):
    monkeypatch.setenv('PARTS_STUDIO_なにか', 'のこる')
    env = make_texture.child_env()
    assert env['PYTHONUTF8'] == '1'
    assert env['PYTHONIOENCODING'] == 'utf-8'
    assert env['PYTHONNOUSERSITE'] == '1'
    assert env['PARTS_STUDIO_なにか'] == 'のこる'    # 他は落とさない


def test_渡すコマンドは全部絶対パス(tmp_path):
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    cmd = make_texture.build_command('py.exe', a, str(tmp_path))
    assert cmd[0] == 'py.exe'
    assert os.path.isabs(cmd[1]) and cmd[1].endswith('paint_backend.py')
    # ★塗り環境を作業ディレクトリにするので、相対パスだと壊れる
    assert all(os.path.isabs(c) for c in cmd[2:5])
    assert cmd[5] == f'--paint-root={os.path.abspath(str(tmp_path))}'


def test_paint_rootが相対でも絶対にして渡す():
    # ★ここが外れると、塗り環境を cwd にして起動するので <root>/paint と
    #   二重に潜って壊れる。tmp_path は元から絶対なので検出できない
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    cmd = make_texture.build_command('py.exe', a, 'paint')
    assert cmd[5] == f'--paint-root={os.path.abspath("paint")}'
    assert cmd[5] != '--paint-root=paint'
    assert cmd[6:] == ['--texsize=4096', '--rendersize=1024', '--views=6', '--res=512']


def test_塗りの実体は自分のリポジトリのものを使う():
    # ★借りるのは環境だけ。コードは parts-studio が持つ
    assert make_texture.BACKEND == os.path.join(ROOT, 'tools', 'paint_backend.py')
    assert os.path.isfile(make_texture.BACKEND)


@pytest.mark.parametrize('n', [4096, 2048, 1024])
def test_texsizeを変えたら渡す値も変わる(n, tmp_path):
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png',
                                 '--out', 'o.glb', '--texsize', str(n)])
    assert f'--texsize={n}' in make_texture.build_command('p', a, str(tmp_path))


def test_形が無ければ止まる(tmp_path):
    with pytest.raises(SystemExit) as e:
        make_texture.main(['--mesh', str(tmp_path / 'ない.glb'),
                           '--front', str(tmp_path / 'ない.png'),
                           '--out', str(tmp_path / 'o.glb')])
    assert '形が見つかりません' in str(e.value)


def test_正面の絵が無ければ止まる(tmp_path):
    mesh = tmp_path / 'a.glb'
    mesh.write_text('x')
    with pytest.raises(SystemExit) as e:
        make_texture.main(['--mesh', str(mesh),
                           '--front', str(tmp_path / 'ない.png'),
                           '--out', str(tmp_path / 'o.glb')])
    assert '正面の絵が見つかりません' in str(e.value)


# ---- paint_backend の穴埋め -----------------------------------------------

def test_穴埋めは塗れていない画素を近傍の色で埋める():
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    tex = np.zeros((4, 4, 3), np.uint8)
    tex[0, 0] = (10, 20, 30)
    mask = np.zeros((4, 4), np.uint8)
    mask[0, 0] = 1                                # 左上だけ塗れている
    out, m2 = paint_backend.inpaint_fallback(tex, mask)
    assert (out == (10, 20, 30)).all()            # 全部その色で埋まる
    assert (m2 == 1).all()


def test_穴埋めは近いほうの色を選ぶ():
    # ★「いちばん近い塗れている画素」であることを見る。どれか1色で埋めるのでは駄目
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    tex = np.zeros((1, 5, 3), np.uint8)
    tex[0, 0] = (10, 10, 10)
    tex[0, 4] = (200, 200, 200)
    mask = np.zeros((1, 5), np.uint8)
    mask[0, 0] = mask[0, 4] = 1
    out, _ = paint_backend.inpaint_fallback(tex, mask)
    assert tuple(out[0, 1]) == (10, 10, 10)       # 左に近い
    assert tuple(out[0, 3]) == (200, 200, 200)    # 右に近い


def test_穴埋めは全部塗れていれば何もしない():
    np = pytest.importorskip('numpy')
    tex = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    mask = np.ones((4, 4), np.uint8)
    out, m2 = paint_backend.inpaint_fallback(tex, mask)
    assert (out == tex).all()
    assert (m2 == 1).all()


def test_穴埋めは1画素も塗れていなければ諦める():
    # ★近傍が無いので埋めようがない。mask をそのまま返して呼び出し側に伝える
    np = pytest.importorskip('numpy')
    tex = np.zeros((4, 4, 3), np.uint8)
    mask = np.zeros((4, 4), np.uint8)
    out, m2 = paint_backend.inpaint_fallback(tex, mask)
    assert (out == tex).all()
    assert (m2 == 0).all()


def test_穴埋めは3チャンネルのmaskも受ける():
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    tex = np.zeros((4, 4, 3), np.uint8)
    tex[1, 1] = (7, 8, 9)
    mask = np.zeros((4, 4, 3), np.uint8)
    mask[1, 1] = 1
    out, _ = paint_backend.inpaint_fallback(tex, mask)
    assert (out == (7, 8, 9)).all()


# ---- paint_backend の差し込み ---------------------------------------------

class FakeMeshRender:
    pass


def test_差し込み先が無ければ黙って通さない(capsys):
    # ★見つからないまま進むと、最後の uv_inpaint で NameError になるまで気づけない
    assert paint_backend.install_inpaint_fallback() == []
    assert '差し込み先が見つかりません' in capsys.readouterr().out


def test_MeshRenderで終わるモジュールに差し込む(monkeypatch):
    mod = FakeMeshRender()
    monkeypatch.setitem(sys.modules, 'なにか.MeshRender', mod)
    assert paint_backend.install_inpaint_fallback() == ['なにか.MeshRender']
    assert mod.meshVerticeInpaint is paint_backend.inpaint_fallback


def test_既に持っているモジュールは上書きしない(monkeypatch):
    # ★上流がビルド済みなら本物を優先する
    mod = FakeMeshRender()
    mod.meshVerticeInpaint = 'ほんもの'
    monkeypatch.setitem(sys.modules, 'ほか.MeshRender', mod)
    assert paint_backend.install_inpaint_fallback() == []
    assert mod.meshVerticeInpaint == 'ほんもの'


def test_MeshRenderで終わらないモジュールには差し込まない(monkeypatch):
    mod = FakeMeshRender()
    monkeypatch.setitem(sys.modules, 'MeshRenderer', mod)
    assert paint_backend.install_inpaint_fallback() == []
    assert not hasattr(mod, 'meshVerticeInpaint')


# ---- paint_backend の引数 -------------------------------------------------

def test_backendはpaint_rootを必須にする():
    with pytest.raises(SystemExit):
        paint_backend.parse_args(['m.glb', 'f.png', 'o.glb'])


def test_backendの既定はmake_textureと揃っている():
    # ★二重定義なので、片方だけ変えると黙って解像度が落ちる
    b = paint_backend.parse_args(['m.glb', 'f.png', 'o.glb', '--paint-root', 'x'])
    t = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    assert (b.texsize, b.rendersize, b.views, b.res) == \
           (t.texsize, t.rendersize, t.views, t.res)


def test_上流が無い場所を指すと止まる(tmp_path):
    with pytest.raises(SystemExit) as e:
        paint_backend.setup_paths(str(tmp_path))
    assert paintenv.UPSTREAM in str(e.value)


def test_上流があればsys_pathに入る(tmp_path):
    root = tmp_path / 'p'
    (root / paintenv.UPSTREAM / 'hy3dpaint').mkdir(parents=True)
    before = list(sys.path)
    try:
        repo = paint_backend.setup_paths(str(root))
        assert repo == str(root / paintenv.UPSTREAM)
        assert repo in sys.path
        assert os.path.join(repo, 'hy3dpaint') in sys.path
    finally:
        sys.path[:] = before


# ---- 塗りの不変条件（レビューで「壊しても緑」だった箇所） -----------------

class FakeConf:
    """Hunyuan3DPaintConfig の代わり。設定された値を記録するだけ。"""

    def __init__(self, views, res):
        self.max_num_view, self.resolution = views, res


def test_texsizeがそのまま上流へ渡る():
    # ★半分にする補正などを入れると、器の値がずれて解像度が黙って落ちる
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png',
                                 '--out', 'o.glb', '--texsize', '4096',
                                 '--rendersize', '1024'])
    conf = paint_backend.configure(FakeConf(6, 512), a, 'REPO', 'ROOT')
    assert conf.texture_size == 4096
    assert conf.render_size == 1024


def test_上流の設定は渡された場所を基準にする():
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    conf = paint_backend.configure(FakeConf(6, 512), a, os.path.join('R', 'Hunyuan3D-2.1'), 'ROOT')
    assert conf.multiview_cfg_path.startswith(os.path.join('R', 'Hunyuan3D-2.1'))
    assert conf.custom_pipeline.startswith(os.path.join('R', 'Hunyuan3D-2.1'))
    # ★重みは repo ではなく root 直下の ckpt/（上流の既定の場所とは別）
    assert conf.realesrgan_ckpt_path == os.path.join('ROOT', 'ckpt', 'RealESRGAN_x4plus.pth')


class FakePipe:
    def __init__(self):
        self.kw = None

    def __call__(self, **kw):
        self.kw = kw
        return kw['output_mesh_path']


def test_use_remeshは必ずFalse(tmp_path):
    # ★True だと【渡した形が作り直される】。この工程で一番大事な不変条件で、
    #   壊れてもエラーは出ず、リトポロジー済みの形が静かに置き換わる
    pipe = FakePipe()
    paint_backend.run_paint(pipe, 'in.obj', str(tmp_path / 'f.png'), 'out.obj')
    assert pipe.kw['use_remesh'] is False
    assert pipe.kw['save_glb'] is True
    assert pipe.kw['mesh_path'] == 'in.obj'
    assert pipe.kw['output_mesh_path'] == 'out.obj'
    assert os.path.isabs(pipe.kw['image_path'])


def test_金属とざらつきの入る色が入れ替わらない():
    # ★glTF は G=ざらつき / B=金属。入れ替えると、つやのある所が金属になる。
    #   見た目は「なんか変」でしか出ないので気づけない
    np = pytest.importorskip('numpy')
    Image = pytest.importorskip('PIL.Image', reason='Pillow が要る')
    from PIL import Image
    met = Image.new('L', (4, 4), 200)          # 金属 = 200
    rgh = Image.new('L', (4, 4), 50)           # ざらつき = 50
    mr = paint_backend.metallic_roughness(met, rgh)
    assert mr.shape == (4, 4, 3)
    assert (mr[..., 0] == 0).all()             # R は使わない
    assert (mr[..., 1] == 50).all()            # G = ざらつき
    assert (mr[..., 2] == 200).all()           # B = 金属


def test_大きさが違えばざらつきに合わせる():
    pytest.importorskip('numpy')
    from PIL import Image
    mr = paint_backend.metallic_roughness(Image.new('L', (2, 2), 9),
                                          Image.new('L', (8, 8), 7))
    assert mr.shape == (8, 8, 3)


# ---- 前回の中間ファイルを消す（critical だった箇所） ----------------------

def test_前回の中間ファイルを消す(tmp_path):
    # ★これが無いと【2回目の実行で前回の形が出る】。
    #   古い .glb が残っていると「作り直す必要が無い」と誤判定し、
    #   古い形へ新しい絵を貼って、エラーも出さずに完成品として返す
    stem = str(tmp_path / 'painted')
    for suffix in paint_backend.ARTIFACTS + (paint_backend.WORK_SUFFIX,):
        pathlib.Path(stem + suffix).write_text('前回のもの')
    removed = paint_backend.clear_stale(stem)
    assert len(removed) == len(paint_backend.ARTIFACTS) + 1
    for suffix in paint_backend.ARTIFACTS + (paint_backend.WORK_SUFFIX,):
        assert not os.path.exists(stem + suffix)


def test_消す対象に出力そのものが入っている():
    # ★.glb を消さないと critical が再発する
    assert '.glb' in paint_backend.ARTIFACTS


def test_無ければ何もしない(tmp_path):
    assert paint_backend.clear_stale(str(tmp_path / 'ない')) == []


def test_関係ないファイルは消さない(tmp_path):
    stem = str(tmp_path / 'painted')
    keep = tmp_path / 'painted_notes.txt'
    keep.write_text('のこす')
    other = tmp_path / 'ほか.glb'
    other.write_text('のこす')
    pathlib.Path(stem + '.glb').write_text('けす')
    paint_backend.clear_stale(stem)
    assert keep.exists() and other.exists()


# ---- 呼び出し側が失敗を握りつぶさない -------------------------------------

def test_塗りが失敗したら止まる(tmp_path, monkeypatch):
    # ★returncode を見ないと、失敗しても「保存」と表示して 0 で終わる
    import subprocess
    mesh, front = tmp_path / 'a.glb', tmp_path / 'f.png'
    mesh.write_text('x')
    front.write_text('x')
    env = make_env(tmp_path / 'paint')
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, 3))
    with pytest.raises(SystemExit) as e:
        make_texture.main(['--mesh', str(mesh), '--front', str(front),
                           '--out', str(tmp_path / 'o.glb'),
                           '--paint-root', str(env)])
    assert '終了コード 3' in str(e.value)


def test_成功しても出力が無ければ止まる(tmp_path, monkeypatch):
    # ★別プロセスなので、0 で終わったのに何も書いていないことがありうる
    import subprocess
    mesh, front = tmp_path / 'a.glb', tmp_path / 'f.png'
    mesh.write_text('x')
    front.write_text('x')
    env = make_env(tmp_path / 'paint')
    monkeypatch.setattr(subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, 0))
    with pytest.raises(SystemExit) as e:
        make_texture.main(['--mesh', str(mesh), '--front', str(front),
                           '--out', str(tmp_path / 'o.glb'),
                           '--paint-root', str(env)])
    assert '出力がありません' in str(e.value)


def test_出力があれば成功で返す(tmp_path, monkeypatch):
    import subprocess
    mesh, front = tmp_path / 'a.glb', tmp_path / 'f.png'
    mesh.write_text('x')
    front.write_text('x')
    out = tmp_path / 'o.glb'
    env = make_env(tmp_path / 'paint')

    def fake_run(*a, **k):
        out.write_text('できた')
        return subprocess.CompletedProcess(a, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert make_texture.main(['--mesh', str(mesh), '--front', str(front),
                              '--out', str(out), '--paint-root', str(env)]) == 0


# ---- main の中にあって検証できなかった箇所を切り出したもの ----------------

def test_重みが無ければモデルを載せる前に止まる(tmp_path):
    # ★通さないと、上流が Hunyuan3DPaintPipeline.__init__ の中で
    #   生の FileNotFoundError を投げる
    with pytest.raises(SystemExit) as e:
        paint_backend.check_ckpt(str(tmp_path))
    assert 'RealESRGAN_x4plus.pth' in str(e.value)
    assert 'paint-environment.md' in str(e.value)


def test_重みがあれば場所を返す(tmp_path):
    ck = tmp_path / 'ckpt' / 'RealESRGAN_x4plus.pth'
    ck.parent.mkdir()
    ck.write_text('x')
    assert paint_backend.check_ckpt(str(tmp_path)) == str(ck)


class FakeUpstreamMeshRender:
    pass


def test_上流を読んだら必ず穴埋めを差し込む(monkeypatch):
    # ★呼び忘れると 60 秒走り切ったあとに NameError で落ちる。
    #   import と差し込みを1つの関数に閉じ込めて、離れないようにしている
    import types
    fake = types.ModuleType('textureGenPipeline')
    fake.Hunyuan3DPaintConfig = 'CONF'
    fake.Hunyuan3DPaintPipeline = 'PIPE'
    render = FakeUpstreamMeshRender()
    monkeypatch.setitem(sys.modules, 'textureGenPipeline', fake)
    monkeypatch.setitem(sys.modules, 'DifferentiableRenderer.MeshRender', render)
    conf, pipe = paint_backend.import_upstream()
    assert (conf, pipe) == ('CONF', 'PIPE')
    assert render.meshVerticeInpaint is paint_backend.inpaint_fallback


def test_出力先を整えるときに前回の中間ファイルを消す(tmp_path):
    # ★これが呼ばれないと【2回目の実行で前回の形が出る】
    d = tmp_path / 'out'
    d.mkdir()
    (d / 'painted.glb').write_text('前回のもの')
    (d / 'painted.jpg').write_text('前回のもの')
    out, stem = paint_backend.prepare_output(str(d / 'painted.glb'))
    assert out == str(d / 'painted.glb')
    assert stem == str(d / 'painted')
    assert not (d / 'painted.glb').exists()
    assert not (d / 'painted.jpg').exists()


def test_出力先のディレクトリが無ければ作る(tmp_path):
    out, stem = paint_backend.prepare_output(str(tmp_path / 'なかった' / 'p.glb'))
    assert os.path.isdir(os.path.dirname(out))


def test_PBRのマップが無ければ成功にしない(tmp_path):
    # ★これを入れるのが ADR-0008 の要点。飛ばした出力は目的を果たしていない
    stem = str(tmp_path / 'p')
    with pytest.raises(SystemExit) as e:
        paint_backend.require_pbr_maps(stem)
    assert 'p_metallic.jpg' in str(e.value)
    assert 'p_roughness.jpg' in str(e.value)


def test_片方だけ無くても成功にしない(tmp_path):
    stem = str(tmp_path / 'p')
    pathlib.Path(stem + '_metallic.jpg').write_text('x')
    with pytest.raises(SystemExit) as e:
        paint_backend.require_pbr_maps(stem)
    assert 'p_roughness.jpg' in str(e.value)
    assert 'p_metallic.jpg' not in str(e.value)


def test_両方あれば場所を返す(tmp_path):
    stem = str(tmp_path / 'p')
    for suffix in ('_metallic.jpg', '_roughness.jpg'):
        pathlib.Path(stem + suffix).write_text('x')
    assert paint_backend.require_pbr_maps(stem) == (stem + '_metallic.jpg',
                                                    stem + '_roughness.jpg')


# ---- 切り出した確認が「実際に呼ばれている」ことを見る ---------------------

def test_mainは重みの確認をtorchより先に行う(tmp_path):
    # ★main は torch を import するのでフルには走らせられないが、
    #   重みの確認はその前にあるので、ここまでは検証できる。
    #   呼び忘れると、モデルを載せたあとに生の FileNotFoundError になる
    (tmp_path / paintenv.UPSTREAM).mkdir()
    with pytest.raises(SystemExit) as e:
        paint_backend.main(['m.glb', 'f.png', 'o.glb', '--paint-root', str(tmp_path)])
    assert 'RealESRGAN_x4plus.pth' in str(e.value)


def test_attach_pbrはglbを読む前にマップを確認する(tmp_path):
    # ★確認が後ろだと、trimesh が先に落ちて「マップが無い」と分からない
    pytest.importorskip('trimesh')
    with pytest.raises(SystemExit) as e:
        paint_backend.attach_pbr(str(tmp_path / '存在しない.glb'), str(tmp_path / 'p'))
    assert '金属・ざらつきのマップが見つかりません' in str(e.value)
