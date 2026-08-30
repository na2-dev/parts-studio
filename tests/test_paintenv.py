# tools/paintenv.py と tools/make_texture.py のテスト。GPU も torch も要らない。
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import paintenv                                                  # noqa: E402
import make_texture                                              # noqa: E402


def make_env(base, complete=True):
    """塗り環境らしいディレクトリを作る。complete=False なら venv を欠く。"""
    base.mkdir(parents=True, exist_ok=True)
    (base / paintenv.SCRIPT).write_text('# dummy')
    (base / 'Hunyuan3D-2.1').mkdir(exist_ok=True)
    if complete:
        py = base / 'venv-21' / 'Scripts'
        py.mkdir(parents=True, exist_ok=True)
        (py / 'python.exe').write_text('# dummy')
    return base


# ---- 探す順序 -------------------------------------------------------------

def test_明示した場所が最優先(tmp_path):
    got = paintenv.candidates(explicit='X:/明示', repo_root=str(tmp_path))
    assert got[0] == 'X:/明示'


def test_環境変数はリポジトリ直下より先(tmp_path, monkeypatch):
    monkeypatch.setenv(paintenv.ENV_VAR, 'X:/環境変数')
    got = paintenv.candidates(repo_root=str(tmp_path))
    assert got[0] == 'X:/環境変数'
    assert got.index('X:/環境変数') < got.index(os.path.join(str(tmp_path), 'paint'))


def test_借り物は最後(tmp_path):
    got = paintenv.candidates(repo_root=str(tmp_path))
    assert got[-1] == paintenv.BORROWED


def test_環境変数が無ければ候補に入らない(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    got = paintenv.candidates(repo_root=str(tmp_path))
    assert got == [os.path.join(str(tmp_path), 'paint'), paintenv.BORROWED]


# ---- 足りないものの検出 ---------------------------------------------------

def test_揃っていれば空(tmp_path):
    assert paintenv.missing_parts(str(make_env(tmp_path / 'p'))) == {}


def test_venvが無ければ検出する(tmp_path):
    lack = paintenv.missing_parts(str(make_env(tmp_path / 'p', complete=False)))
    assert 'Python 環境（venv-21）' in lack
    assert len(lack) == 1


def test_何も無ければ3つとも検出する(tmp_path):
    assert len(paintenv.missing_parts(str(tmp_path))) == 3


# ---- find -----------------------------------------------------------------

def test_見つかれば場所と実行ファイルを返す(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    env = make_env(tmp_path / 'paint')
    root, py, script, borrowed = paintenv.find(repo_root=str(tmp_path))
    assert root == str(env)
    assert py.endswith('python.exe')
    assert script.endswith(paintenv.SCRIPT)
    assert borrowed is False


def test_借り物かどうかを見分ける(tmp_path, monkeypatch):
    # ★借りていることを黙って隠さない。使う側が気づけるようにする
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    monkeypatch.setattr(paintenv, 'BORROWED', str(make_env(tmp_path / 'borrowed')))
    root, py, script, borrowed = paintenv.find(repo_root=str(tmp_path / 'ない'))
    assert borrowed is True


def test_揃っていない候補は飛ばして次を見る(tmp_path, monkeypatch):
    # ★中途半端な paint/ があっても、そこで止まらずに借り物へ落ちる
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    make_env(tmp_path / 'paint', complete=False)
    ok = make_env(tmp_path / 'borrowed')
    monkeypatch.setattr(paintenv, 'BORROWED', str(ok))
    root, _, _, borrowed = paintenv.find(repo_root=str(tmp_path))
    assert root == str(ok)
    assert borrowed is True


def test_無ければ足りないものを列挙して止まる(tmp_path, monkeypatch):
    monkeypatch.delenv(paintenv.ENV_VAR, raising=False)
    monkeypatch.setattr(paintenv, 'BORROWED', str(tmp_path / 'これも無い'))
    with pytest.raises(SystemExit) as e:
        paintenv.find(repo_root=str(tmp_path / 'ない'))
    msg = str(e.value)
    assert 'paint-environment.md' in msg          # 直し方に案内する
    assert paintenv.ENV_VAR in msg                # 既にある場合の指し方も案内する
    assert '無い:' in msg                          # 何が足りないかを出す


# ---- make_texture の引数 --------------------------------------------------

def test_texsizeの既定は4096():
    # ★下げると実際に出る絵が半分になる。3d-studio が実測して注意書きを残した罠
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    assert a.texsize == 4096


def test_resの既定は512():
    # ★768 は VRAM を使い切って 15分以上戻らなかった（実測）
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    assert a.res == 512


def test_必須の引数が欠けると拒む():
    with pytest.raises(SystemExit):
        make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png'])


def test_渡すコマンドが絶対パスになる():
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb'])
    cmd = make_texture.build_command('py.exe', 's.py', a)
    assert cmd[0] == 'py.exe' and cmd[1] == 's.py'
    # ★別ディレクトリで実行するので相対パスだと壊れる
    assert all(os.path.isabs(c) for c in cmd[2:5])
    assert cmd[5:] == ['--texsize=4096', '--rendersize=1024', '--views=6', '--res=512']


def test_texsizeを変えたら渡す値も変わる():
    a = make_texture.parse_args(['--mesh', 'm.glb', '--front', 'f.png', '--out', 'o.glb',
                                 '--texsize', '2048'])
    assert '--texsize=2048' in make_texture.build_command('p', 's', a)


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
