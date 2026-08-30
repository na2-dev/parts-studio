# 形に色を塗る（ADR-0008）。Hunyuan3D-Paint 2.1 を別環境で呼ぶ。
#
# ★このスクリプトは torch を import しない。
#   塗りの実体（tools/paint_backend.py）は Python 3.12 の別 venv で動くので、
#   ここは薄いラッパに徹する。通しのパイプラインが1つの環境から
#   全工程を回せるようにするため。
#
# ★texsize の罠
#   texture_size の【半分】が実際に出る絵の大きさになる（4096 → 2048）。
#   既定を下げると気づかないうちに解像度が落ちる。既定のまま使うこと。
#
# 使いかた:
#   python tools\make_texture.py --mesh=out\shape.glb --front=testimg\front.png ^
#       --out=out\painted.glb [--paint-root=...] [--texsize=4096]
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paintenv                                                  # noqa: E402

BACKEND = os.path.join(HERE, 'paint_backend.py')


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description='形に色を塗る（Hunyuan3D-Paint 2.1 を別環境で呼ぶ）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--mesh', required=True, help='塗る対象の形の glb')
    p.add_argument('--front', required=True, help='正面の絵（切り抜き済みの透過PNG）')
    p.add_argument('--out', required=True, help='出力する glb')
    p.add_argument('--paint-root', default=None,
                   help=f'塗り環境の場所。未指定なら {paintenv.ENV_VAR} → '
                        'リポジトリ直下の paint/ → 借り物 の順で探す')
    p.add_argument('--texsize', type=int, default=4096,
                   help='テクスチャの器。★実際に出る絵はこの半分（4096 → 2048）。'
                        '下げると解像度が落ちるので既定のまま使うこと')
    p.add_argument('--rendersize', type=int, default=1024, help='描画の大きさ')
    p.add_argument('--views', type=int, default=6, help='生成する向きの数')
    p.add_argument('--res', type=int, default=512,
                   help='多視点で生成する絵の大きさ。★上げると実用外に遅い')
    a = p.parse_args(argv)
    for name in ('texsize', 'rendersize', 'views', 'res'):
        if getattr(a, name) < 1:
            p.error(f'--{name} は 1 以上にすること。受け取った値: {getattr(a, name)}')
    # ★--out の妥当性は塗りの【前】に見る。1分かけた後に落とさないため。
    #   .glb 限定にするのは、塗りの実体が stem（拡張子を外したもの）から
    #   .obj / .jpg などの中間ファイル名を作るため。--out=x.obj だと
    #   出力先と中間ファイルが同じ名前になり、自分自身を上書きする
    if os.path.splitext(a.out)[1].lower() != '.glb':
        p.error(f'--out は .glb にすること（例 out\\painted.glb）。'
                f'受け取った値: {a.out}')
    if os.path.abspath(a.mesh) == os.path.abspath(a.out):
        p.error(f'--mesh と --out が同じです。入力の形が消えます: {a.out}')
    return a


def child_env():
    """別環境へ渡す環境変数。

    ★PYTHONPATH と PYTHONHOME は落とす。ここは Python 3.11 で、
      子は 3.12。親の PYTHONPATH に形づくり側の道（TRELLIS.2 など）が
      入っていると、3.12 が 3.11 向けの拡張モジュールを拾って
      「DLL load failed」になる。分けるために別プロセスにしている以上、
      環境変数も分けきる。
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONNOUSERSITE='1')
    return env


def build_command(python, args, paint_root):
    """別環境へ渡すコマンドを組む。

    ★パスはすべて絶対にする。塗りの実体は塗り環境を作業ディレクトリに
      して動かすため、相対だと壊れる。
    """
    return [python, BACKEND,
            os.path.abspath(args.mesh),
            os.path.abspath(args.front),
            os.path.abspath(args.out),
            f'--paint-root={os.path.abspath(paint_root)}',
            f'--texsize={args.texsize}',
            f'--rendersize={args.rendersize}',
            f'--views={args.views}',
            f'--res={args.res}']


def main(argv=None):
    args = parse_args(argv)
    for label, path in (('形', args.mesh), ('正面の絵', args.front)):
        if not os.path.isfile(path):
            raise SystemExit(f'{label}が見つかりません: {path}')

    root, python, borrowed = paintenv.find(args.paint_root)
    if borrowed:
        print(f'※ 塗り環境を借りています: {root}', flush=True)
        print(f'   parts-studio 単体で動かすには {paintenv.DOC}', flush=True)
    else:
        print(f'塗り環境: {root}', flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    print(f'塗り: {os.path.basename(args.mesh)} / テクスチャの器 {args.texsize}'
          f'（実際に出る絵は {args.texsize // 2}）', flush=True)
    # ★cwd は塗り環境。上流が相対パスで同梱のものを読む場面があるため
    r = subprocess.run(build_command(python, args, root), cwd=root, env=child_env())
    if r.returncode != 0:
        raise SystemExit(f'塗りに失敗しました（終了コード {r.returncode}）')
    if not os.path.isfile(args.out):
        raise SystemExit(f'塗りは終わりましたが出力がありません: {args.out}')
    print(f'保存: {args.out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
