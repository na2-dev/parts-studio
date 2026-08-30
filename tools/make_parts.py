# 形をパーツに分けて、それぞれ塗って、元の絵を投影して、1つにまとめる（ADR-0008）。
#
# 手順3〜7を1コマンドにしたもの。形づくり（手順1）とリトポロジー（手順2）は別。
#
#   3. メッシュを首で切る            断面の広がりが最小の高さを自動検出
#   4. 頭だけ表面をならす            ★体はならさない
#   5. 頭と体をそれぞれ塗る          Hunyuan3D-Paint 2.1（別環境）
#   6. それぞれに元の絵を投影        ★--partof 相当（全身の正規化を渡す）
#   7. 結合                          材質はパーツごとに分けたまま。
#                                    ★ここで glTF の向き（Y上）に直す
#
# ★なぜパーツに分けるのか（2026-08-30 実測）
#   全身を1枚の 2048 アトラスに詰めると顔に回るテクセルが足りない。
#   頭を独立させれば、頭だけで 2048 を使い切れる。
#
# ★ならしは頭だけ
#   体をならすと背中の鍵穴が消える。ここは間違えると静かに情報が落ちる。
#
# ★投影には【切る前の全身】を渡す
#   渡さないとパーツ単体で正規化してしまい、1 画素も貼れない（体 0% を実測）。
#   絵も切らずに全身のものを渡すこと。
#
# 使いかた:
#   python tools\make_parts.py --shape=out\retopo.glb --out=out\final.glb ^
#       --front=testimg\front.png --left=... --right=... --back=... ^
#       [--work=out\parts] [--paint-root=...] [--keep-work]
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VIEWS = ('front', 'left', 'right', 'back')

# パーツの定義。★ここが仕様そのもの。
#   smooth: 表面をならすか。体をならすと背中の鍵穴が消えるので頭だけ。
PARTS = (
    {'name': 'head', 'label': '頭', 'smooth': True},
    {'name': 'body', 'label': '体', 'smooth': False},
)

# ★視点の対応づけは【全パーツで固定する】（2026-08-31）。
#   front→front / right→left / back→back / left→right という並びは
#   座標の規約から決まるもので、題材によらない。
#   シルエットで測って決める自動割り当ては、それを毎回引き直しているだけで、
#   外すことがある（頭で正面を後ろに割り当てた実測がある）。
#   上方向を直したあと体で測り直したら、自動でも【この並びと完全に一致】した。
#   規約が正なので、毎回引き直さずに使う。
FIXVIEWS = True


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description='形をパーツに分けて塗り、元の絵を投影して1つにまとめる',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--shape', required=True,
                   help='リトポロジー済みの形の glb（切る前の全身）')
    p.add_argument('--out', required=True, help='出力する glb')
    for v in VIEWS:
        p.add_argument(f'--{v}', required=(v == 'front'),
                       help=f'{v} の絵（★切らずに全身のものを渡すこと）')
    p.add_argument('--work', default=None,
                   help='途中のファイルを置く場所。既定は --out と同じ場所の parts/')
    p.add_argument('--paint-root', default=None, help='塗り環境の場所')
    p.add_argument('--texsize', type=int, default=4096,
                   help='テクスチャの器。★実際に出る絵はこの半分')
    p.add_argument('--margin', type=float, default=0.01,
                   help='首から上へどれだけ余分に頭へ含めるか（全体の高さに対する割合）')
    p.add_argument('--smooth-iters', type=int, default=8, help='ならしの回数')
    p.add_argument('--smooth-lambda', type=float, default=0.5, help='ならしの強さ')
    p.add_argument('--keep-work', action='store_true',
                   help='途中のファイルを残す（既定でも消さないが、明示用）')
    p.add_argument('--up', choices=['y', 'z'], default=None,
                   help='全身の上方向。既定は全身から測る。'
                        '★パーツからは測れない（背が低く一番長い軸が横になる）')
    p.add_argument('--skip-project', action='store_true',
                   help='元の絵の投影を飛ばす（塗りまでの確認用）')
    a = p.parse_args(argv)
    if os.path.splitext(a.out)[1].lower() != '.glb':
        p.error(f'--out は .glb にすること。受け取った値: {a.out}')
    if a.smooth_iters < 0:
        p.error('--smooth-iters は 0 以上にすること')
    if not 0.0 <= a.smooth_lambda <= 1.0:
        p.error('--smooth-lambda は 0〜1 にすること')
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
    """途中のファイルを置く場所を決める。"""
    if args.work:
        return os.path.abspath(args.work)
    return os.path.join(os.path.dirname(os.path.abspath(args.out)), 'parts')


def part_paths(work, name):
    """1つのパーツが通る道のりを返す。工程ごとに別名にして上書きしない。"""
    return {
        'raw': os.path.join(work, f'{name}_raw.glb'),        # 切っただけ
        'shape': os.path.join(work, f'{name}_shape.glb'),    # ならし後（塗る対象）
        'painted': os.path.join(work, f'{name}_painted.glb'),
        'final': os.path.join(work, f'{name}_final.glb'),    # 投影後
    }


def split(shape, work, margin):
    """首で切って、頭と体の glb を作る。"""
    import split_parts
    paths = {p['name']: part_paths(work, p['name']) for p in PARTS}
    split_parts.split(shape, paths['head']['raw'], paths['body']['raw'], margin)
    for name, ps in paths.items():
        if not os.path.isfile(ps['raw']):
            raise SystemExit(f'{name} が作られませんでした: {ps["raw"]}')
    return paths


def prepare_shape(part, paths, args):
    """塗る前の形を作る。頭はならし、体はそのまま。"""
    src, dst = paths['raw'], paths['shape']
    if part['smooth'] and args.smooth_iters > 0:
        import smooth_part
        print(f'  {part["label"]}: 表面をならす', flush=True)
        smooth_part.smooth(src, dst, args.smooth_iters, args.smooth_lambda)
    else:
        # ★体はならさない。ならすと背中の鍵穴が消える
        import shutil
        print(f'  {part["label"]}: ならさない（彫られた細部を残す）', flush=True)
        shutil.copyfile(src, dst)
    return dst


def paint(part, paths, args):
    """パーツを塗る。塗り環境は別プロセスで動く。"""
    import make_texture
    argv = ['--mesh', paths['shape'], '--front', args.front,
            '--out', paths['painted'], '--texsize', str(args.texsize)]
    if args.paint_root:
        argv += ['--paint-root', args.paint_root]
    make_texture.main(argv)
    return paths['painted']


def detect_up(shape):
    """全身の上方向を測る。★パーツはこれを引き継ぐ。

    パーツ単体では当たらない。実測（2026-08-31）で頭も体も一番長い軸が X になり、
    Z 上なのに「Y 上」と判定された。全身とパーツで上方向がずれると、
    絵とメッシュが対応せず貼れる画素が激減する（頭 9.46% / 体 6.18%）。
    """
    import numpy as np
    import trimesh
    import apply_reference_detail
    v = np.asarray(trimesh.load(shape, force='mesh').vertices, dtype=np.float64)
    up = apply_reference_detail.detect_up(v)
    print(f'  全身の上方向: {up.upper()}（パーツもこれに揃える）', flush=True)
    return up


def project(part, paths, images, shape, up):
    """元の絵の細部を貼り直す。★切る前の全身と、その上方向を渡す。"""
    import apply_reference_detail
    apply_reference_detail.project(
        paths['painted'], paths['final'], images,
        partof=shape,                      # ★これが無いと1画素も貼れない
        up=up,                             # ★切り出し元の上方向を引き継ぐ
        fixviews=FIXVIEWS)
    return paths['final']


def main(argv=None):
    args = parse_args(argv)
    if not os.path.isfile(args.shape):
        raise SystemExit(f'形が見つかりません: {args.shape}')
    images = collect_images(args)
    shape = os.path.abspath(args.shape)
    work = work_dir(args)
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    print(f'パーツづくり: {os.path.basename(shape)} / 絵 {len(images)}枚 / '
          f'途中のファイル {work}', flush=True)
    t0 = time.time()

    print('1) 首で切る', flush=True)
    up = args.up or detect_up(shape)
    paths = split(shape, work, args.margin)

    print('2) 塗る前の形を作る', flush=True)
    for part in PARTS:
        prepare_shape(part, paths[part['name']], args)

    made = []
    for part in PARTS:
        ps = paths[part['name']]
        print(f'3) {part["label"]}を塗る', flush=True)
        paint(part, ps, args)
        if args.skip_project:
            made.append(ps['painted'])
            continue
        print(f'4) {part["label"]}に元の絵を投影する', flush=True)
        project(part, ps, images, shape, up)
        made.append(ps['final'])

    print('5) 結合する', flush=True)
    import combine_parts
    combine_parts.combine(args.out, made, up)     # ★ここで glTF の向きに直す
    print(f'できました: {args.out}（{time.time() - t0:.0f}秒）', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
