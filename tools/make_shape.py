# 4枚の絵から「形」を作る（ADR-0003 / ADR-0005）。
#
# ★このスクリプトの立ち位置
#   通しのパイプラインの1工程目。テクスチャは作らない（[ADR-0008](../docs/adr/0008-texture-by-hunyuan3d-paint.md)
#   で Hunyuan3D-Paint に任せると決めたため）。ここが出すのは頂点と面だけ。
#
# ★前提
#   Windows 機の venv（Python 3.11・torch 2.7.0+cu128）で動かす。
#   環境の作り方は docs/setup/trellis2-windows.md。
#   ATTN_BACKEND は未設定なら xformers を自動で入れる。
#   ★sdpa は選ばないこと。dense 側は受け付けるが sparse 側が受け付けず、
#     既定の flash_attn のまま残って import に失敗する。
#
# ★4枚の渡し方
#   TRELLIS.2 の run() は絵を1枚しか受け取らない。tools/mvcond.py で
#   サンプラーを包んで4枚を配る。方式は --mode で選ぶ（既定は multidiffusion）。
#
# 使いかた:
#   venv\Scripts\python.exe tools\make_shape.py ^
#       --front=front.png --left=left.png --right=right.png --back=back.png ^
#       --out=out\shape.glb [--res=1024] [--mode=multidiffusion] [--seed=1234]
import argparse
import os
import sys
import time

VIEWS = ('front', 'left', 'right', 'back')
DEFAULT_MODE = 'multidiffusion'


def _modes():
    """方式の一覧は mvcond を正とする（二重定義を避ける）。

    ★argparse を作る時点では TRELLIS.2 の sys.path が通っていないが、
      mvcond は torch を import しないので単体で読める。
    """
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import mvcond
    return mvcond.MODES


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description='4枚の絵から形を作る（テクスチャは作らない）',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    for v in VIEWS:
        p.add_argument(f'--{v}', required=(v == 'front'),
                       help=f'{v} の絵（背景ぬき済みの透過PNG）')
    p.add_argument('--out', required=True, help='出力する形の glb')
    p.add_argument('--res', type=int, default=1024,
                   help='形の解像度。1024 以上で、1024 + 128 の倍数のみ'
                        '（1024 / 1152 / 1280 / 1408 / 1536 …）。'
                        '実測があるのは 1024 と 1536（1536 は細部が出るが遅い）')
    p.add_argument('--mode', choices=_modes(), default=DEFAULT_MODE,
                   help='4枚の渡し方')
    p.add_argument('--seed', type=int, default=1234, help='乱数の種')
    p.add_argument('--repo', default=None,
                   help='TRELLIS.2 の場所。既定はこのファイルの2つ上の TRELLIS.2')
    p.add_argument('--max-tokens', type=int, default=49152,
                   help='形の潜在に使うトークンの上限。超えると解像度が 128 ずつ'
                        '下がる。★--res 1024 のときは何を指定しても効かない'
                        '（上流が 1024 で必ず打ち切るため）')
    a = p.parse_args(argv)
    # ★上流の打ち切り条件は `num_tokens < max_num_tokens or hr_resolution == 1024`
    #   で、下げ幅は 128 固定（trellis2_image_to_3d.py:335-339）。
    #   1024 に着地しない値を渡すと 1024 を跨いで下がり続け、
    #   トークン上限を小さくすると戻ってこなくなる。ここで弾いておく。
    if a.res < 1024 or (a.res - 1024) % 128 != 0:
        p.error('--res は 1024 以上で、1024 + 128 の倍数にすること'
                f'（1024 / 1152 / 1280 / 1408 / 1536 …）。受け取った値: {a.res}')
    if a.max_tokens < 1:
        p.error(f'--max-tokens は 1 以上にすること。受け取った値: {a.max_tokens}')
    # ★--out の妥当性は生成の【前】に見る。3分かけた後に落とさないため
    if not os.path.splitext(a.out)[1]:
        p.error(f'--out には拡張子を付けること（例 out\\shape.glb）。受け取った値: {a.out}')
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
        raise SystemExit('正面の絵（--front）は必須です。空文字は受け付けません')
    return got


def resolve_repo(explicit):
    """TRELLIS.2 の場所を決める。"""
    if explicit:
        repo = explicit
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        repo = os.path.join(os.path.dirname(here), 'TRELLIS.2')
    if not os.path.isdir(repo):
        raise SystemExit(
            f'TRELLIS.2 が見つかりません: {repo}\n'
            '  docs/setup/trellis2-windows.md の手順1で clone してください')
    return repo


def main(argv=None):
    args = parse_args(argv)
    images = collect_images(args)
    repo = resolve_repo(args.repo)

    # ★sparse 側は sdpa を受け付けない。ここで既定を入れておく
    os.environ.setdefault('ATTN_BACKEND', 'xformers')
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
    sys.path.insert(0, repo)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    import numpy as np
    import torch
    import trimesh
    from PIL import Image
    import mvcond
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    import patches                                   # 上流の非互換を埋める

    patches.apply()

    print(f'形づくり: 絵 {len(images)}枚（{"・".join(images)}） / '
          f'解像度 {args.res} / 方式 {args.mode} / 種 {args.seed}', flush=True)

    t0 = time.time()
    pipe = Trellis2ImageTo3DPipeline.from_pretrained('microsoft/TRELLIS.2-4B')
    pipe.cuda()
    print(f'  読み込み {time.time() - t0:.0f}s', flush=True)

    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    with torch.no_grad():
        # ★4枚は必ず VIEWS の順で渡す。mvcond が View 0 を正面とみなすため
        pil = [Image.open(images[v]) for v in VIEWS if v in images]
        imgs = [pipe.preprocess_image(im) for im in pil]
        torch.manual_seed(args.seed)
        n = len(imgs)

        # ★prepare_cond と inject は必ず対で使う。
        #   prepare_cond を飛ばすと neg_cond の batch が View 数のまま残り、
        #   CFG が x_t（batch 1）と噛み合わずに落ちる。
        c512 = mvcond.prepare_cond(pipe.get_cond(imgs, 512), args.mode)
        c1024 = mvcond.prepare_cond(pipe.get_cond(imgs, 1024), args.mode)

        with mvcond.inject(pipe.sparse_structure_sampler, n, args.mode):
            coords = pipe.sample_sparse_structure(c512, 32, 1, {})
        with mvcond.inject(pipe.shape_slat_sampler, n, args.mode):
            shape_slat, res = pipe.sample_shape_slat_cascade(
                c512, c1024,
                pipe.models['shape_slat_flow_model_512'],
                pipe.models['shape_slat_flow_model_1024'],
                512, args.res, coords, {}, args.max_tokens)
        torch.cuda.empty_cache()
        # ★テクスチャ側（sample_tex_slat / decode_tex_slat）は呼ばない。
        #   上流の decode_latent は tex_slat を coords/attrs にしか入れず
        #   （trellis2_image_to_3d.py:470-484）、頂点と面は decode_shape_slat が
        #   返すメッシュそのものなので、形だけ要るなら丸ごと不要。
        #   fill_holes は decode_latent:474 が呼んでいるぶんを自前で補う
        mesh, _ = pipe.decode_shape_slat(shape_slat, res)
        mesh = mesh[0]
        mesh.fill_holes()

    dt = time.time() - t0
    v = mesh.vertices.detach().cpu().numpy()
    f = mesh.faces.detach().cpu().numpy()
    if res != args.res:
        print(f'  ※トークンの上限で解像度が {args.res} → {res} に下がりました', flush=True)
    print(f'  生成 {dt:.0f}s / 解像度 {res} / 頂点 {len(v):,} / 面 {len(f):,} / '
          f'VRAM ピーク {torch.cuda.max_memory_allocated() / 1e9:.2f}GB', flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    trimesh.Trimesh(vertices=v, faces=f, process=False).export(args.out)
    print(f'保存: {args.out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
