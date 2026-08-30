# 形に色を塗る（ADR-0008）。Hunyuan3D-Paint 2.1 を別環境で呼ぶ。
#
# ★このスクリプトは torch を import しない。
#   塗りは Python 3.12 の別 venv で動くので、ここは薄いラッパに徹する。
#   呼ぶ側（通しのパイプライン）が1つの環境から全工程を回せるようにするため。
#
# ★texsize の罠（3d-studio が実測して注意書きを残していた）
#   texture_size の【半分】が実際に出る絵の大きさになる（4096 → 2048）。
#   既定を下げると気づかないうちに解像度が落ちる。既定のまま使うこと。
#
# 使いかた:
#   python tools\make_texture.py --mesh=out\shape.glb --front=front.png ^
#       --out=out\painted.glb [--paint-root=...] [--texsize=4096]
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paintenv                                                  # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description='形に色を塗る（Hunyuan3D-Paint 2.1 を別環境で呼ぶ）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--mesh', required=True, help='塗る対象の形の glb')
    p.add_argument('--front', required=True, help='正面の絵（切り抜き済みの透過PNG）')
    p.add_argument('--out', required=True, help='出力する glb')
    p.add_argument('--paint-root', default=None,
                   help=f'塗り環境の場所。未指定なら {paintenv.ENV_VAR} → '
                        f'リポジトリ直下の paint/ → 借り物 の順で探す')
    p.add_argument('--texsize', type=int, default=4096,
                   help='テクスチャの器。★実際に出る絵はこの半分（4096 → 2048）。'
                        '下げると解像度が落ちるので既定のまま使うこと')
    p.add_argument('--rendersize', type=int, default=1024, help='描画の大きさ')
    p.add_argument('--views', type=int, default=6, help='生成する向きの数')
    p.add_argument('--res', type=int, default=512,
                   help='多視点で生成する絵の大きさ。★上げると実用外に遅い'
                        '（512 で 56秒、768 は VRAM を使い切って 15分以上戻らなかった）')
    return p.parse_args(argv)


def build_command(python, script, args):
    """別環境へ渡すコマンドを組む。"""
    return [python, script,
            os.path.abspath(args.mesh),
            os.path.abspath(args.front),
            os.path.abspath(args.out),
            f'--texsize={args.texsize}',
            f'--rendersize={args.rendersize}',
            f'--views={args.views}',
            f'--res={args.res}']


def main(argv=None):
    args = parse_args(argv)
    for label, path in (('形', args.mesh), ('正面の絵', args.front)):
        if not os.path.isfile(path):
            raise SystemExit(f'{label}が見つかりません: {path}')

    root, python, script, borrowed = paintenv.find(args.paint_root)
    if borrowed:
        print(f'※ 塗り環境を借りています: {root}', flush=True)
        print('   parts-studio 単体で動かすには docs/setup/paint-environment.md',
              flush=True)
    else:
        print(f'塗り環境: {root}', flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    cmd = build_command(python, script, args)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    print(f'塗り: {os.path.basename(args.mesh)} / テクスチャの器 {args.texsize}'
          f'（実際に出る絵は {args.texsize // 2}）', flush=True)
    r = subprocess.run(cmd, cwd=root, env=env)
    if r.returncode != 0:
        raise SystemExit(f'塗りに失敗しました（終了コード {r.returncode}）')
    if not os.path.isfile(args.out):
        raise SystemExit(f'塗りは終わりましたが出力がありません: {args.out}')
    print(f'保存: {args.out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
