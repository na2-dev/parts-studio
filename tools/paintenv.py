# 塗り工程の環境（Hunyuan3D-Paint 2.1）を見つける（ADR-0008）。
#
# ★なぜ別環境なのか
#   形づくりは Python 3.11 + torch 2.7.0+cu128、塗りは Python 3.12 +
#   Hunyuan3D-2.1 の依存で、1つの venv に同居できない。
#   だから塗りは【別プロセスとして呼ぶ】。呼ぶ側は torch を import しない。
#
# ★いまは 3d-studio の環境を借りている
#   parts-studio 単体では塗れない。場所は次の順で決める。
#     1. --paint-root で明示
#     2. 環境変数 PARTS_STUDIO_PAINT_ROOT
#     3. リポジトリ直下の paint/（自前で作った場合）
#     4. Z:\work\3d-studio（3d-studio を借りる。既定）
#   借りている事実と作り直す手順は docs/setup/paint-environment.md。
import os

ENV_VAR = 'PARTS_STUDIO_PAINT_ROOT'
BORROWED = r'Z:\work\3d-studio'
SCRIPT = 'paint21_pipeline.py'
PYTHON = os.path.join('venv-21', 'Scripts', 'python.exe')
CHECKPOINT = os.path.join('ckpt', 'RealESRGAN_x4plus.pth')


def candidates(explicit=None, repo_root=None):
    """探す順に候補を返す。"""
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = []
    if explicit:
        out.append(explicit)
    if os.environ.get(ENV_VAR):
        out.append(os.environ[ENV_VAR])
    out.append(os.path.join(repo_root, 'paint'))
    out.append(BORROWED)
    return out


def missing_parts(root):
    """root に足りないものを返す。空なら使える。"""
    need = {
        '塗りのスクリプト': SCRIPT,
        'Python 環境（venv-21）': PYTHON,
        'Hunyuan3D-2.1': 'Hunyuan3D-2.1',
    }
    return {name: rel for name, rel in need.items()
            if not os.path.exists(os.path.join(root, rel))}


def find(explicit=None, repo_root=None):
    """使える塗り環境を返す。無ければ SystemExit。

    返り値: (root, python, script, borrowed)
    """
    tried = []
    for root in candidates(explicit, repo_root):
        lack = missing_parts(root)
        if not lack:
            return (root,
                    os.path.join(root, PYTHON),
                    os.path.join(root, SCRIPT),
                    os.path.abspath(root) == os.path.abspath(BORROWED))
        tried.append((root, lack))

    lines = ['塗り環境が見つかりません。探した場所:']
    for root, lack in tried:
        lines.append(f'  {root}')
        for name, rel in lack.items():
            lines.append(f'      無い: {name}（{rel}）')
    lines.append('')
    lines.append('作り方は docs/setup/paint-environment.md を参照してください。')
    lines.append(f'既にどこかにあるなら --paint-root か {ENV_VAR} で指してください。')
    raise SystemExit('\n'.join(lines))
