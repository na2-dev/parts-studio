# 色塗りの実体（ADR-0008）。★venv-21（Python 3.12）の中で動く。
#
# 直接呼ばずに tools/make_texture.py から呼ぶこと。make_texture は形づくりと同じ
# 環境（Python 3.11）で動き、このファイルを別プロセスとして起動する。
#
# ★上流をそのままでは動かせないので2つ補う。上流のファイルは書き換えない
#   1. basicsr が torchvision.transforms.functional_tensor を import するが、
#      torchvision 0.17 で消えている（この環境は 0.21）。橋渡しを作る。
#   2. テクスチャの穴埋め meshVerticeInpaint（C++拡張）が Windows 向けに
#      ビルドされておらず、上流にフォールバックも無い。Python で書いて差し込む。
#
# ★use_remesh=False は必須
#   既定の True は【渡した形を作り直してから塗る】。リトポロジー済みの形も、
#   頭と体に切り分けた形も置き換わってしまう。
#
# ★texture_size の【半分】が実際に出る絵の大きさ（実測 4096→2048、2048→1024）
#   下げても VRAM はほとんど変わらない（確保 20.4GB と 19.2GB）ので既定は 4096。
#
# ★2.1 の色塗りは金属とざらつきを別々の .jpg で出し、同時に書き出す .glb には
#   入れない。glTF は1枚にまとめる決まり（G=ざらつき / B=金属）なので attach_pbr で入れ直す。
import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')


def fix_torchvision():
    """basicsr が見に行く古い置き場所へ橋渡しする。

    Hunyuan3D-2.1 同梱の torchvision_fix.py と同じ考えかた。
    """
    try:
        import torchvision.transforms.functional_tensor              # noqa: F401
        return False
    except ImportError:
        pass
    import types

    import torchvision.transforms.functional as F
    mod = types.ModuleType('torchvision.transforms.functional_tensor')
    mod.rgb_to_grayscale = F.rgb_to_grayscale
    sys.modules['torchvision.transforms.functional_tensor'] = mod
    return True


def inpaint_fallback(texture, mask, *a, **kw):
    """テクスチャの塗り残しを、いちばん近い塗れている画素の色で埋める。

    上流の meshVerticeInpaint（C++拡張）の代わり。埋めるのは UV の島のすき間
    なので近傍の色で足りる。
    """
    import numpy as np
    tex = np.asarray(texture)
    m = np.asarray(mask)
    if m.ndim == 3:
        m = m[..., 0]
    filled = m > 0
    if filled.all() or not filled.any():
        return tex, (np.ones_like(m) if filled.any() else m)
    from scipy import ndimage
    _, (iy, ix) = ndimage.distance_transform_edt(
        ~filled, return_distances=True, return_indices=True)
    print(f'  穴埋め: {int((~filled).sum()):,} 画素', flush=True)
    return tex[iy, ix], np.ones_like(m)


def install_inpaint_fallback():
    """読み込み済みの MeshRender へ代替を差し込む。上流ファイルは触らない。"""
    done = []
    for name, mod in list(sys.modules.items()):
        if name.endswith('MeshRender') and getattr(mod, 'meshVerticeInpaint', None) is None:
            setattr(mod, 'meshVerticeInpaint', inpaint_fallback)
            done.append(name)
    if done:
        print(f'穴埋めの代替を差し込みました: {", ".join(done)}', flush=True)
    else:
        # ★黙って通さない。上流がビルド済みなら不要だが、名前が変わった場合も
        #   ここに落ちる。最後の uv_inpaint で NameError になるまで気づけない
        print('※ 穴埋めの差し込み先が見つかりませんでした。'
              '上流に meshVerticeInpaint があるか、モジュール名が変わっています',
              flush=True)
    return done


def attach_pbr(glb_path, stem):
    """金属・ざらつきのマップを glb に入れる。

    ★glTF は金属とざらつきを1枚にまとめる決まり（G=ざらつき / B=金属）。
      別々の2枚のままでは読み込み側が解釈できない。
    ★Factor はマップに掛かる係数なので、0 のままだとマップが打ち消される。
      金属だけは 0 にする（実測でこのマップは平均 6.8／最大 33（/255）で
      金属の画素が1つも無く、それでも金属が入っていると暗く沈むため）。
    """
    import numpy as np
    import trimesh
    from PIL import Image
    from trimesh.visual.material import PBRMaterial

    mesh = trimesh.load(glb_path, force='mesh')
    mat = getattr(mesh.visual, 'material', None)
    if not isinstance(mat, PBRMaterial):
        try:
            mat = mat.to_pbr()
            mesh.visual.material = mat
        except Exception:
            print('  材質を PBR にできませんでした。マップは付けません', flush=True)
            return

    # 色の絵は、書き出された .jpg のほうが大きいことがあるので置き換える
    alb = f'{stem}.jpg'
    if os.path.exists(alb):
        im = Image.open(alb).convert('RGB')
        cur = getattr(mat, 'baseColorTexture', None)
        if cur is None or im.size[0] > cur.size[0]:
            mat.baseColorTexture = im
            print(f'  色の絵を {im.size[0]}px に差し替えました'
                  f'（glb の中は {cur.size[0] if cur else "なし"}px でした）', flush=True)

    met, rgh = f'{stem}_metallic.jpg', f'{stem}_roughness.jpg'
    if not (os.path.exists(met) and os.path.exists(rgh)):
        print('  金属・ざらつきのマップが見つかりません', flush=True)
        return
    m_im = Image.open(met).convert('L')
    r_im = Image.open(rgh).convert('L')
    if m_im.size != r_im.size:
        m_im = m_im.resize(r_im.size, Image.LANCZOS)
    w, h = r_im.size
    mr = np.zeros((h, w, 3), np.uint8)
    mr[..., 1] = np.asarray(r_im)          # G = ざらつき
    mr[..., 2] = np.asarray(m_im)          # B = 金属
    mat.metallicRoughnessTexture = Image.fromarray(mr)
    mat.metallicFactor = 0.0
    mat.roughnessFactor = 1.0
    mesh.export(glb_path)
    print(f'  金属・ざらつきのマップを入れました（{w}px / '
          f'金属の平均 {np.asarray(m_im).mean():.0f}・'
          f'ざらつきの平均 {np.asarray(r_im).mean():.0f}）', flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='形に色を塗る（venv-21 の中で動く）')
    p.add_argument('mesh', help='塗る対象の形の glb')
    p.add_argument('front', help='正面の絵')
    p.add_argument('out', help='出力する glb')
    p.add_argument('--paint-root', required=True,
                   help='Hunyuan3D-2.1 と ckpt がある場所')
    p.add_argument('--texsize', type=int, default=4096)
    p.add_argument('--rendersize', type=int, default=1024)
    p.add_argument('--views', type=int, default=6)
    p.add_argument('--res', type=int, default=512)
    return p.parse_args(argv)


def setup_paths(root):
    """上流を import できるようにして、リポジトリの場所を返す。"""
    repo = os.path.join(root, 'Hunyuan3D-2.1')
    if not os.path.isdir(repo):
        raise SystemExit(f'Hunyuan3D-2.1 が見つかりません: {repo}')
    for p in (repo, os.path.join(repo, 'hy3dpaint')):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    return repo


def main(argv=None):
    args = parse_args(argv)
    root = os.path.abspath(args.paint_root)
    if fix_torchvision():
        print('basicsr 用の回避を適用しました', flush=True)
    repo = setup_paths(root)

    import torch
    import trimesh
    from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline

    dev = torch.cuda.get_device_properties(0)
    print(f'GPU: {dev.name} / {dev.total_memory / 1024 ** 3:.2f}GB / '
          f'sm_{dev.major}{dev.minor}', flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    m = trimesh.load(args.mesh, force='mesh')
    stem = os.path.splitext(os.path.abspath(args.out))[0]
    obj_in = stem + '_in.obj'
    trimesh.Trimesh(vertices=m.vertices, faces=m.faces).export(obj_in)

    conf = Hunyuan3DPaintConfig(args.views, args.res)
    conf.multiview_cfg_path = os.path.join(repo, 'hy3dpaint/cfgs/hunyuan-paint-pbr.yaml')
    conf.custom_pipeline = os.path.join(repo, 'hy3dpaint/hunyuanpaintpbr')
    ckpt = os.path.join(root, 'ckpt', 'RealESRGAN_x4plus.pth')
    if os.path.exists(ckpt):
        conf.realesrgan_ckpt_path = ckpt
    else:
        # ★止めない。上流の既定を使って続く（絵は少しぼやける）
        print(f'※ 高精細化の重みがありません（{ckpt}）。上流の既定を使います', flush=True)
    conf.texture_size = args.texsize
    conf.render_size = args.rendersize
    print(f'色塗り(2.1): 面 {len(m.faces):,} / テクスチャの器 {conf.texture_size}'
          f'（実際に出る絵は {conf.texture_size // 2}） / 描画 {conf.render_size} / '
          f'工程の絵 {conf.resolution}', flush=True)

    torch.cuda.reset_peak_memory_stats()
    t = time.time()
    pipe = Hunyuan3DPaintPipeline(conf)
    install_inpaint_fallback()
    # ★use_remesh=False は必須（渡した形を守る）
    out_obj = pipe(mesh_path=obj_in, image_path=os.path.abspath(args.front),
                   output_mesh_path=stem + '.obj',
                   use_remesh=False, save_glb=True)
    print(f'色塗り(2.1) 完了 {time.time() - t:.1f}秒 / GPUメモリ最大 '
          f'使用 {torch.cuda.max_memory_allocated() / 1024 ** 3:.2f}GB・'
          f'確保 {torch.cuda.max_memory_reserved() / 1024 ** 3:.2f}GB', flush=True)

    glb = stem + '.glb'
    if not os.path.exists(glb):
        got = out_obj if isinstance(out_obj, str) and os.path.exists(out_obj) else None
        if got is None:
            raise SystemExit('色塗り(2.1) の出力が見つかりません')
        trimesh.load(got, force='mesh').export(glb)
    if os.path.abspath(glb) != os.path.abspath(args.out):
        os.replace(glb, args.out)
    attach_pbr(args.out, stem)
    print(f'保存: {args.out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
