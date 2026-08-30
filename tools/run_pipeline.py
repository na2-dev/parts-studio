# 4枚の絵から完成した glb までを1コマンドで通す。
#
#   1. 形を全身で作る    TRELLIS.2 + 多視点条件付け（ADR-0003 / ADR-0005）
#   2. リトポロジー      Blender ボクセル化 → 元表面へスナップ（ADR-0007）
#   3〜7. パーツづくり   切る → ならす → 塗る → 元絵を投影 → 結合（ADR-0008）
#
# ★3つの Python 環境をまたぐ
#   venv     3.11  形づくり（torch 2.7.0+cu128）とこのスクリプト
#   venv-bpy       リトポロジー（bpy は 3.11 用しか無い）
#   venv-21  3.12  色塗り（torch 2.6.0+cu126）… make_texture が中で呼ぶ
#   同居できないので、リトポロジーは【別プロセスとして呼ぶ】。
#
# ★上方向はここで決めて、下の工程へ配る
#   形づくりの出力は Z 上。パーツからは測れないので、測るのは全身のうちに1回だけ。
#   出来上がりは結合のところで glTF の Y 上に直る。
#
# ★途中から始められる
#   形づくりに3分、塗りに1分半かかる。--from で工程を選べる。
#
# 使いかた:
#   python tools\run_pipeline.py --front=testimg\front.png --left=... --right=... ^
#       --back=... --out=out\model.glb [--work=out\pipeline] [--from=shape|retopo|parts]
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

VIEWS = ('front', 'left', 'right', 'back')
STEPS = ('shape', 'retopo', 'parts')

# リトポロジーは bpy が要るので別環境で動かす
BPY_PYTHON = os.path.join('venv-bpy', 'Scripts', 'python.exe')
RETOPO = os.path.join(HERE, 'retopo_shrinkwrap.py')

# ★形づくりの出力は Z 上（TRELLIS.2 の向きをそのまま書き出している）。
#   ここで決めて配る。パーツからは測れない（背が低く一番長い軸が横になる）
SHAPE_UP = 'z'


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description='4枚の絵から完成した glb までを1コマンドで通す',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    for v in VIEWS:
        p.add_argument(f'--{v}', required=(v == 'front'), help=f'{v} の絵（透過PNG）')
    p.add_argument('--out', required=True, help='出来上がりの glb')
    p.add_argument('--work', default=None,
                   help='途中のファイルを置く場所。既定は --out と同じ場所の pipeline/')
    p.add_argument('--from', dest='start', choices=STEPS, default='shape',
                   help='どの工程から始めるか。★前の工程の出力が --work に'
                        '残っている必要がある')
    p.add_argument('--res', type=int, default=1024, help='形の解像度')
    p.add_argument('--mode', default='multidiffusion', help='4枚の渡し方')
    p.add_argument('--seed', type=int, default=1234, help='乱数の種')
    p.add_argument('--voxel', type=float, default=0.009, help='リトポロジーのボクセル幅')
    p.add_argument('--texsize', type=int, default=4096,
                   help='テクスチャの器。★実際に出る絵はこの半分')
    p.add_argument('--margin', type=float, default=0.01, help='首から上へ足す余白')
    p.add_argument('--paint-root', default=None, help='塗り環境の場所')
    p.add_argument('--repo', default=None, help='TRELLIS.2 の場所')
    p.add_argument('--no-fixviews', action='store_true',
                   help='視点の対応づけを固定しない（別の題材で一度は確かめること）')
    a = p.parse_args(argv)
    if os.path.splitext(a.out)[1].lower() != '.glb':
        p.error(f'--out は .glb にすること。受け取った値: {a.out}')
    if a.voxel <= 0:
        p.error(f'--voxel は 0 より大きくすること。受け取った値: {a.voxel}')
    return a


def collect_images(args):
    """渡された絵を {向き: パス} で返す。正面は必須。"""
    got = {}
    for v in VIEWS:
        path = getattr(args, v)
        if path:
            if not os.path.isfile(path):
                raise SystemExit(f'絵が見つかりません: {path}')
            got[v] = path
    if 'front' not in got:
        raise SystemExit('正面の絵（--front）は必須です')
    return got


def work_dir(args):
    if args.work:
        return os.path.abspath(args.work)
    return os.path.join(os.path.dirname(os.path.abspath(args.out)), 'pipeline')


def stage_paths(work):
    """工程ごとの出力。★同じ名前を使い回さない（どこまで進んだか分かるように）。"""
    return {'shape': os.path.join(work, 'shape.glb'),
            'retopo': os.path.join(work, 'retopo.glb')}


# 始める工程ごとに、前もって要るもの（工程の出力 / 説明 / 作る工程の名前）
NEEDS = {
    'shape': None,                                   # 絵だけあればよい
    'retopo': ('shape', '形', '形づくり'),
    'parts': ('retopo', 'リトポロジー済みの形', 'リトポロジー'),
}


def steps_from(start):
    """start 以降の工程を返す。"""
    return STEPS[STEPS.index(start):]


def require(path, what, produced_by):
    """前の工程の出力があるか確かめる。"""
    if not os.path.isfile(path):
        raise SystemExit(
            f'{what}が見つかりません: {path}\n'
            f'--from を使うなら、先に {produced_by} を通しておく必要があります。')
    return path


def bpy_python(repo_root=None):
    """リトポロジーを動かす Python を返す。"""
    path = os.path.join(repo_root or ROOT, BPY_PYTHON)
    if not os.path.isfile(path):
        raise SystemExit(
            f'リトポロジー用の Python が見つかりません: {path}\n'
            'bpy は Python 3.11 用しか無いので、形づくりとは別の環境が要ります。'
            '作り方は docs/setup/trellis2-windows.md を参照してください。')
    return path


def run_shape(args, images, dst):
    """1) 4枚から形を作る。"""
    import make_shape
    argv = ['--out', dst, '--res', str(args.res), '--mode', args.mode,
            '--seed', str(args.seed)]
    for v, path in images.items():
        argv += [f'--{v}', path]
    if args.repo:
        argv += ['--repo', args.repo]
    make_shape.main(argv)
    return dst


def stamp(path):
    """ファイルの更新時刻。無ければ None。"""
    return os.path.getmtime(path) if os.path.isfile(path) else None


def run_retopo(args, src, dst):
    """2) リトポロジー。★bpy が要るので別プロセス。

    ★終了コードだけでは成否を判定できない。
      bpy をモジュールとして使うと、書き出しを終えたあと
      インタプリタの終了時に落ちることがある（Windows で実測。
      終了コード 3221225477 = 0xC0000005 ACCESS_VIOLATION）。
      それでも glb は正しく書けている。出力が【新しくなったか】で判定する。
    """
    dst = os.path.abspath(dst)
    before = stamp(dst)
    cmd = [bpy_python(), RETOPO, os.path.abspath(src), dst, str(args.voxel)]
    print(f'リトポロジー: ボクセル幅 {args.voxel}（別環境で動かします）', flush=True)
    r = subprocess.run(cmd, cwd=ROOT,
                       env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1'))
    after = stamp(dst)
    if after is None or after == before:
        raise SystemExit(
            f'リトポロジーに失敗しました（終了コード {r.returncode}）。'
            f'出力が新しくなっていません: {dst}')
    if r.returncode != 0:
        # ★黙って飲み込まない。出力はあるが、異常終了したことは伝える
        print(f'※ リトポロジーは出力を書きましたが、終了コードが {r.returncode} '
              f'でした（bpy が終了時に落ちる既知の挙動）。出力を使って続けます。',
              flush=True)
    return dst


def run_parts(args, images, shape, work, out):
    """3〜7) 切って塗って投影して結合する。"""
    import make_parts
    argv = ['--shape', shape, '--out', out, '--work', os.path.join(work, 'parts'),
            '--texsize', str(args.texsize), '--margin', str(args.margin),
            '--up', SHAPE_UP]
    for v, path in images.items():
        argv += [f'--{v}', path]
    if args.paint_root:
        argv += ['--paint-root', args.paint_root]
    if args.no_fixviews:
        argv += ['--no-fixviews']
    make_parts.main(argv)
    return out


def main(argv=None):
    args = parse_args(argv)
    images = collect_images(args)
    work = work_dir(args)
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    paths = stage_paths(work)
    todo = steps_from(args.start)

    print(f'通し: 絵 {len(images)}枚（{"・".join(images)}） / '
          f'途中のファイル {work} / {todo[0]} から', flush=True)
    t0 = time.time()

    # ★要るのは「始める工程が食べるもの」だけ。
    #   --from=parts のときに形（shape.glb）まで要求すると、
    #   リトポロジー済みの形があるのに始められない
    need = NEEDS[args.start]
    if need:
        require(paths[need[0]], need[1], need[2])

    if 'shape' in todo:
        print('=== 1) 形を作る（約3分）', flush=True)
        run_shape(args, images, paths['shape'])

    if 'retopo' in todo:
        print('=== 2) リトポロジー（約30秒）', flush=True)
        run_retopo(args, paths['shape'], paths['retopo'])

    print('=== 3) パーツづくり（約2分30秒）', flush=True)
    run_parts(args, images, paths['retopo'], work, args.out)

    print(f'できました: {args.out}（合計 {time.time() - t0:.0f}秒）', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
