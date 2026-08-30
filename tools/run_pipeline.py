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

# 進み具合の目安。★2026-08-31 の実測（RTX 4070 Ti SUPER 16GB）。
#   ここを実測とずらすと、待っている人が「固まった」と思って止めてしまう
EST = {'shape': '約110秒', 'retopo': '約15秒', 'parts': '約190〜250秒'}

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
    # ★下位の検査を前倒しする。ここで弾かないと、形づくり110秒と
    #   リトポロジー15秒を終えた【124秒後】に argparse が落とす
    if not 0.001 <= a.voxel <= 0.1:
        p.error(f'--voxel は 0.001〜0.1 にすること（実測があるのは 0.009）。'
                f'受け取った値: {a.voxel}')
    if a.res < 1024 or (a.res - 1024) % 128 != 0:
        p.error(f'--res は 1024 以上で、1024 + 128 の倍数にすること。'
                f'受け取った値: {a.res}')
    if not -0.5 < a.margin < 0.5:
        p.error(f'--margin は -0.5〜0.5 にすること。受け取った値: {a.margin}')
    if a.texsize < 1:
        p.error(f'--texsize は 1 以上にすること。受け取った値: {a.texsize}')
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


MANIFEST = 'manifest.json'


def fingerprint(images):
    """入力の絵の指紋。中身までは見ないが、取り違えはこれで十分に防げる。"""
    return {v: {'path': os.path.abspath(p),
                'size': os.path.getsize(p),
                'mtime': os.path.getmtime(p)} for v, p in sorted(images.items())}


def write_manifest(work, images):
    """この work が【どの絵から作られたか】を残す。"""
    import json
    with open(os.path.join(work, MANIFEST), 'w', encoding='utf-8') as f:
        json.dump({'images': fingerprint(images)}, f, ensure_ascii=False, indent=2)


def check_manifest(work, images):
    """--from で始めるとき、前に作ったものが同じ絵のものか確かめる。

    ★これが無いと、別の題材の中間ファイルを黙って使う。
      しかも --partof と視点の固定で、絵とメッシュが噛み合っていないことを
      見つける仕組みが両方とも切れているので、
      【前の題材の形に今の絵を貼った glb】が正常終了で出てくる。
    """
    import json
    path = os.path.join(work, MANIFEST)
    if not os.path.isfile(path):
        raise SystemExit(
            f'この場所で何を作ったのかの記録がありません: {path}\n'
            '--from で途中から始めるには、同じ --work で一度通しておく必要があります。'
            '（別の題材の中間ファイルを使ってしまわないための確認です）')
    with open(path, encoding='utf-8') as f:
        was = json.load(f).get('images', {})
    now = fingerprint(images)
    if was != now:
        diff = sorted(set(was) | set(now))
        lines = ['前に作ったときと絵が違います。'
                 '別の題材の中間ファイルを使うところでした。']
        for v in diff:
            a, b = was.get(v), now.get(v)
            if a != b:
                lines.append(f'  {v}: {a["path"] if a else "無し"}'
                             f' → {b["path"] if b else "無し"}')
        lines += ['', f'--from=shape で作り直すか、--work を分けてください（いま {work}）。']
        raise SystemExit('\n'.join(lines))


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


def check_mesh(path, what, least=1):
    """glb が読めて面があるか確かめる。壊れた出力を次の工程へ渡さない。"""
    import trimesh
    if os.path.getsize(path) == 0:
        raise SystemExit(f'{what}が空です: {path}')
    try:
        m = trimesh.load(path, force='mesh', process=False)
    except Exception as e:
        raise SystemExit(f'{what}が読めません: {path}\n  {type(e).__name__}: {e}')
    n = len(getattr(m, 'faces', ()))
    if n < least:
        raise SystemExit(f'{what}に面がありません（{n} 面）: {path}')
    return n


def bpy_python(repo_root=None):
    """リトポロジーを動かす Python を返す。"""
    path = os.path.join(repo_root or ROOT, BPY_PYTHON)
    if not os.path.isfile(path):
        raise SystemExit(
            f'リトポロジー用の Python が見つかりません: {path}\n'
            'bpy は Python 3.11 用しか無いので、形づくりとは別の環境が要ります。\n'
            '作り方は docs/setup/trellis2-windows.md の'
            '「リトポロジー用の環境（venv-bpy）」を参照してください。')
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


def free_vram():
    """形づくりが確保した VRAM を返す。

    ★形づくりはこのプロセスの中で走るので、抱えたままだと
      別プロセスの塗り（使用 13.41GB・確保 20.41GB の実測）と取り合う。
      16GB の機体では、--res を上げたときに塗りが OOM で落ちうる。
    """
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        print('  形づくりが使った VRAM を返しました', flush=True)


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
    # ★新しくなっただけでは足りない。終了コードを見ないと決めた以上、
    #   「書き終えてから落ちた」と「書いている途中で落ちた」を区別できない。
    #   読めて面があるところまで確かめる（子は dst へ直接書くので、
    #   途中で落ちると前回の正しい出力ごと壊れる）
    check_mesh(dst, 'リトポロジーの出力')
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
        check_manifest(work, images)             # ★別の題材のものを使わない
        require(paths[need[0]], need[1], need[2])
        check_mesh(paths[need[0]], need[1])
    else:
        write_manifest(work, images)

    # ★リトポロジー用の環境は【形づくりの前】に確かめる。
    #   あとで見ると、109 秒かけた形づくりを捨ててから足りないと分かる
    if 'retopo' in todo:
        bpy_python()

    if 'shape' in todo:
        print(f'=== 1) 形を作る（{EST["shape"]}）', flush=True)
        run_shape(args, images, paths['shape'])
        check_mesh(paths['shape'], '形')          # ★中断すると書きかけが残る
        free_vram()

    if 'retopo' in todo:
        print(f'=== 2) リトポロジー（{EST["retopo"]}）', flush=True)
        run_retopo(args, paths['shape'], paths['retopo'])

    print(f'=== 3) パーツづくり（{EST["parts"]}）', flush=True)
    run_parts(args, images, paths['retopo'], work, args.out)

    print(f'できました: {args.out}（合計 {time.time() - t0:.0f}秒）', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
