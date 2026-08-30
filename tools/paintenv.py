# 塗り工程の環境（Hunyuan3D-Paint 2.1）を見つける（ADR-0008）。
#
# ★なぜ別環境なのか
#   形づくりは Python 3.11 + torch 2.7.0+cu128、塗りは Python 3.12 + torch 2.6.0+cu126
#   で、1つの venv に同居できない。だから塗りは【別プロセスとして呼ぶ】。
#
# ★parts-studio が持つのはコードと手順、環境の場所は指定できるようにする
#   塗りのコードは tools/paint_backend.py（parts-studio のもの）。
#   環境（venv-21・Hunyuan3D-2.1・ckpt）は 5.8GB あるのでリポジトリには入れず、
#   次の順で探す。
#     1. --paint-root で明示
#     2. 環境変数 PARTS_STUDIO_PAINT_ROOT
#     3. リポジトリ直下の paint/（docs/setup/paint-environment.md の手順で作った場合）
#     4. Z:\work\3d-studio（3d-studio のものを借りる。当面の既定）
#   借りていることは実行時に必ず表示する。作り方は docs/setup/paint-environment.md。
import os

ENV_VAR = 'PARTS_STUDIO_PAINT_ROOT'
BORROWED = r'Z:\work\3d-studio'
DOC = 'docs/setup/paint-environment.md'
PYTHON = os.path.join('venv-21', 'Scripts', 'python.exe')
UPSTREAM = 'Hunyuan3D-2.1'
CHECKPOINT = os.path.join('ckpt', 'RealESRGAN_x4plus.pth')

# ★CHECKPOINT はここに入れない。無くても上流の既定で塗れる（絵が少しぼやけるだけ）。
#   必須にすると、重みを置き忘れただけで塗り自体が止まる
REQUIRED = {
    'Python 環境（venv-21）': PYTHON,
    '上流の Hunyuan3D-2.1': UPSTREAM,
}


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidates(explicit=None, root=None):
    """探す順に候補を返す。"""
    if root is None:
        root = repo_root()
    out = []
    if explicit:
        out.append(explicit)
    if os.environ.get(ENV_VAR):
        out.append(os.environ[ENV_VAR])
    out.append(os.path.join(root, 'paint'))
    out.append(BORROWED)
    return out


def missing_parts(path):
    """path に足りないものを {説明: 相対パス} で返す。空なら使える。"""
    return {name: rel for name, rel in REQUIRED.items()
            if not os.path.exists(os.path.join(path, rel))}


def find(explicit=None, root=None):
    """使える塗り環境を返す。無ければ SystemExit。

    返り値: (場所, venv-21 の python, 借り物かどうか)
    """
    # ★人が指定した場所が使えないときは、他を探さずにそこで止める。
    #   黙って別の環境へ落ちると、指したつもりの無い環境で塗った結果が返り、
    #   なぜ設定が効かないのか分からなくなる。
    #   自動で探す paint/ と借り物は「指定」ではないので、落ちてよい
    for label, path in (('--paint-root', explicit),
                        (f'環境変数 {ENV_VAR}', os.environ.get(ENV_VAR))):
        if not path:
            continue
        lack = missing_parts(path)
        if lack:
            lines = [f'{label} に指定された場所が使えません: {path}']
            lines += [f'    無い: {name}（{rel}）' for name, rel in lack.items()]
            lines += ['', f'作り方は {DOC} を参照してください。']
            raise SystemExit('\n'.join(lines))

    tried = []
    for path in candidates(explicit, root):
        lack = missing_parts(path)
        if not lack:
            return (path, os.path.join(path, PYTHON),
                    os.path.abspath(path) == os.path.abspath(BORROWED))
        tried.append((path, lack))

    lines = ['塗り環境が見つかりません。探した場所:']
    for path, lack in tried:
        lines.append(f'  {path}')
        for name, rel in lack.items():
            lines.append(f'      無い: {name}（{rel}）')
    lines += ['',
              f'作り方は {DOC} を参照してください。',
              f'既にどこかにあるなら --paint-root か {ENV_VAR} で指してください。']
    raise SystemExit('\n'.join(lines))
