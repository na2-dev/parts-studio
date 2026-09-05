# 候補の形を、4方向の正射投影で描いて元絵と並べる（Blender・venv-bpy）。
# 使いかた: venv-bpy\Scripts\python.exe compare_clay.py 出力.png 元絵dir A.glb [B.glb ...] [--tex]
import os, sys, math
import bpy, mathutils

argv = sys.argv[1:]
tex = '--tex' in argv
zup = '--yup' not in argv
flat = '--flat' in argv       # 照明を掛けずテクスチャそのものを見る
eevee = '--eevee' in argv     # 法線マップまで描く（Workbench は描かない）      # 既定は内部規約（Z上）。glTF 規約のファイルは --yup
argv = [a for a in argv if not a.startswith('--')]
out_png, ref_dir, paths = argv[0], argv[1], argv[2:]
VIEWS = (('front', 0), ('left', 90), ('back', 180), ('right', 270))
SIZE = 360

def setup(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    lo = mathutils.Vector((1e9,)*3); hi = mathutils.Vector((-1e9,)*3)
    for o in obs:
        for c in o.bound_box:
            w = o.matrix_world @ mathutils.Vector(c)
            lo = mathutils.Vector(map(min, lo, w)); hi = mathutils.Vector(map(max, hi, w))
    ctr = (lo + hi) / 2; h = hi.z - lo.z
    root = bpy.data.objects.new('root', None); bpy.context.collection.objects.link(root)
    for o in obs:
        if o.parent is None:
            o.parent = root; o.matrix_parent_inverse = root.matrix_world.inverted()
    if zup:
        # ★中間ファイルは内部規約（Z上・正面-Y）のまま glb に書かれている。
        #   Blender の glTF 読み込みは Y上前提で回すので寝てしまう。-90°で立てる
        root.rotation_euler = (math.radians(-90), 0, 0)
        bpy.context.view_layer.update()
        lo = mathutils.Vector((1e9,)*3); hi = mathutils.Vector((-1e9,)*3)
        for o in obs:
            for c in o.bound_box:
                w = o.matrix_world @ mathutils.Vector(c)
                lo = mathutils.Vector(map(min, lo, w)); hi = mathutils.Vector(map(max, hi, w))
        ctr = (lo + hi) / 2; h = hi.z - lo.z
    root.scale = (2 / h,) * 3
    root.location = -ctr * (2 / h)
    cam_d = bpy.data.cameras.new('c'); cam_d.type = 'ORTHO'; cam_d.ortho_scale = 2.3
    cam = bpy.data.objects.new('c', cam_d); bpy.context.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    sc = bpy.context.scene
    if eevee:
        sc.render.engine = 'BLENDER_EEVEE_NEXT'
        sc.eevee.taa_render_samples = 16
        w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True
        bg = w.node_tree.nodes['Background']; bg.inputs[0].default_value = (0.9, 0.9, 0.9, 1); bg.inputs[1].default_value = 1.2
        sun_d = bpy.data.lights.new('s', 'SUN'); sun_d.energy = 2.5; sun_d.angle = 0.6
        sun = bpy.data.objects.new('s', sun_d); bpy.context.collection.objects.link(sun)
        sun.rotation_euler = (math.radians(55), 0, math.radians(35))
    else:
        sc.render.engine = 'BLENDER_WORKBENCH'
    sc.display.shading.light = 'FLAT' if flat else 'STUDIO'
    sc.display.shading.color_type = 'TEXTURE' if tex else 'SINGLE'
    sc.display.shading.single_color = (0.8, 0.8, 0.8)
    sc.display.shading.show_cavity = not tex
    sc.render.resolution_x = sc.render.resolution_y = SIZE
    sc.render.film_transparent = True
    return cam, sum(len(o.data.polygons) for o in obs), root

def shoot(cam, ang, dst):
    r = math.radians(ang)
    cam.location = (5 * math.sin(r), -5 * math.cos(r), 0)
    cam.rotation_euler = (math.radians(90), 0, r)
    bpy.context.scene.render.filepath = dst
    bpy.ops.render.render(write_still=True)

tmpdir = os.path.join(os.path.dirname(out_png), '_shots'); os.makedirs(tmpdir, exist_ok=True)
rows = []
for idx, p in enumerate(paths):
    cam, nf, root = setup(p)
    row = []
    for v, ang in VIEWS:
        # ★行番号を付ける。同名の glb（projected.glb など）を並べると上書きされ、
        #   全行が最後のモデルの絵になる（実測でやらかした）
        dst = os.path.join(tmpdir, f'{idx:02d}_{os.path.splitext(os.path.basename(p))[0]}_{v}.png')
        shoot(cam, ang, dst); row.append(dst)
    label = os.path.basename(os.path.dirname(p)) + '/' + os.path.basename(p) if os.path.basename(p) == 'projected.glb' else os.path.basename(p)
    rows.append((label, nf, row))
    print(f'{os.path.basename(p)}: 面 {nf:,}', flush=True)

# タイル化（PIL は venv-bpy に無いかもしれないので bpy の画像で組む代わりに、
# ここでは numpy 不要の素朴な方法として PIL を試し、無ければファイル列挙だけ出す）
try:
    from PIL import Image, ImageDraw
    refs = []
    for v, _ in VIEWS:
        im = Image.open(os.path.join(ref_dir, f'{v}.png')).convert('RGBA')
        a = im.getchannel('A'); bb = a.getbbox(); im = im.crop(bb)
        s = (SIZE * 0.87) / im.height
        im = im.resize((max(1, int(im.width * s)), int(im.height * s)), Image.LANCZOS)
        canvas = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
        canvas.alpha_composite(im, ((SIZE - im.width) // 2, (SIZE - im.height) // 2))
        refs.append(canvas)
    W = SIZE * 4 + 140; H = (SIZE + 6) * (len(rows) + 1) + 24
    sheet = Image.new('RGB', (W, H), (230, 230, 230)); dr = ImageDraw.Draw(sheet)
    y = 24
    dr.text((4, y + SIZE // 2), '元絵', fill=(0, 0, 0))
    for i, r in enumerate(refs):
        sheet.alpha_composite(r, (140 + i * SIZE, y)) if False else sheet.paste(r, (140 + i * SIZE, y), r)
    for i, (v, _) in enumerate(VIEWS):
        dr.text((140 + i * SIZE + 4, 6), v, fill=(0, 0, 0))
    y += SIZE + 6
    for name, nf, row in rows:
        dr.text((4, y + SIZE // 2 - 8), name[:22], fill=(0, 0, 0)); dr.text((4, y + SIZE // 2 + 6), f'{nf:,} 面', fill=(80, 80, 80))
        for i, f in enumerate(row):
            im = Image.open(f).convert('RGBA'); sheet.paste(im, (140 + i * SIZE, y), im)
        y += SIZE + 6
    sheet.save(out_png); print('保存:', out_png, flush=True)
except ImportError:
    print('PIL なし。個別の描画は', tmpdir, flush=True)
