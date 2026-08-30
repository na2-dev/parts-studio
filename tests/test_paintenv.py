# tools/paintenv.py・tools/make_texture.py・tools/paint_backend.py のテスト。
# GPU も torch も要らない（paint_backend は torch を関数の中でしか import しない）。
import os
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


def test_何も無ければ両方とも検出する(tmp_path):
    assert set(paintenv.missing_parts(str(tmp_path))) == set(paintenv.REQUIRED)


def test_高精細化の重みは必須にしない(tmp_path):
    # ★無くても上流の既定で塗れる。必須にすると置き忘れで塗りごと止まる
    env = make_env(tmp_path / 'p')
    assert not (env / paintenv.CHECKPOINT).exists()
    assert paintenv.missing_parts(str(env)) == {}


# ---- find -----------------------------------------------------------------

def test_見つかれば場所とpythonを返す(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    env = make_env(tmp_path / 'paint')
    path, py, borrowed = paintenv.find(root=str(tmp_path))
    assert path == str(env)
    assert py == os.path.join(str(env), paintenv.PYTHON)
    assert borrowed is False


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


def test_出力先に拡張子が無いと塗りの前に拒む():
    with pytest.raises(SystemExit):
        make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'out/p'])


def test_渡すコマンドは全部絶対パス(tmp_path):
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    cmd = make_texture.build_command('py.exe', a, str(tmp_path))
    assert cmd[0] == 'py.exe'
    assert os.path.isabs(cmd[1]) and cmd[1].endswith('paint_backend.py')
    # ★塗り環境を作業ディレクトリにするので、相対パスだと壊れる
    assert all(os.path.isabs(c) for c in cmd[2:5])
    assert cmd[5] == f'--paint-root={os.path.abspath(str(tmp_path))}'
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
