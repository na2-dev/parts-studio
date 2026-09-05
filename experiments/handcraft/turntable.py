# 斜めから見る（Blender・venv-bpy）。
# ★4方向の正射投影だけで判定すると、投影に使った角度なので必ず一致して見える。
#   ビューアで人が回す角度（斜め上・斜め下・透視投影）で描いて初めて
#   「4方向のどこからも見えていない面」の埋め色の斑・手の色違い・目玉の崩れが出る（2026-09-05 実測）。
# 使いかた: venv-bpy\Scripts\python.exe turntable.py 出力.png A.glb [B.glb ...] [--yup] [--flat]
#   既定は内部規約（Z上・正面-Y）。glTF 規約（Y上）のファイルは --yup。
#   列: 方位 0,45,...,315（8）× 行: 仰角 +60・+30（見下ろし）・-15・-45（見上げ）。モデルごとに4行。
import os, sys, math
import bpy, mathutils

argv = sys.argv[1:]
zup = '--yup' not in argv
flat = '--flat' in argv
argv = [a for a in argv if not a.startswith('--')]
out_png, paths = argv[0], argv[1:]
# 環境変数で絞れる: TT_AZ='45,315' TT_EL='30,-45' TT_SIZE=1000 TT_DIST=2.4（寄りで細部を見る）
AZ = [int(a) for a in os.environ.get('TT_AZ', '0,45,90,135,180,225,270,315').split(',')]
EL = tuple(int(e) for e in os.environ.get('TT_EL', '60,30,-15,-45').split(','))
SIZE = int(os.environ.get('TT_SIZE', 600))
DIST = float(os.environ.get('TT_DIST', 3.2))


def bounds(obs):
    lo = mathutils.Vector((1e9,) * 3); hi = mathutils.Vector((-1e9,) * 3)
    for o in obs:
        for c in o.bound_box:
            w = o.matrix_world @ mathutils.Vector(c)
            lo = mathutils.Vector(map(min, lo, w)); hi = mathutils.Vector(map(max, hi, w))
    return lo, hi


def setup(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=path)
    obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    root = bpy.data.objects.new('root', None); bpy.context.collection.objects.link(root)
    for o in obs:
        if o.parent is None:
            o.parent = root; o.matrix_parent_inverse = root.matrix_world.inverted()
    if zup:
        root.rotation_euler = (math.radians(-90), 0, 0)
    bpy.context.view_layer.update()
    lo, hi = bounds(obs)
    ctr = (lo + hi) / 2; h = hi.z - lo.z
    root.scale = (2 / h,) * 3
    root.location = -ctr * (2 / h)
    cam_d = bpy.data.cameras.new('c'); cam_d.type = 'PERSP'; cam_d.lens = 50
    cam = bpy.data.objects.new('c', cam_d); bpy.context.collection.objects.link(cam)
    sc = bpy.context.scene; sc.camera = cam
    if os.environ.get('TT_CLAY') == '1':
        # 素の形だけを見る（テクスチャ無し・単色・凹凸強調）。ギザギザが形か継ぎ目かを切り分ける
        sc.render.engine = 'BLENDER_WORKBENCH'
        sc.display.shading.light = 'STUDIO'
        sc.display.shading.color_type = 'SINGLE'
        sc.display.shading.single_color = (0.8, 0.8, 0.8)
        sc.display.shading.show_cavity = True
    elif flat:
        sc.render.engine = 'BLENDER_WORKBENCH'
        sc.display.shading.light = 'FLAT'
        sc.display.shading.color_type = 'TEXTURE'
    else:
        # ★ビューアに近い照明: 白い環境光 + 弱い太陽。model-viewer の既定に寄せる
        sc.render.engine = 'BLENDER_EEVEE_NEXT'
        sc.eevee.taa_render_samples = 16
        w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True
        bg = w.node_tree.nodes['Background']
        bg.inputs[0].default_value = (0.9, 0.9, 0.9, 1); bg.inputs[1].default_value = 1.0
        sun_d = bpy.data.lights.new('s', 'SUN'); sun_d.energy = 2.0; sun_d.angle = 0.8
        sun = bpy.data.objects.new('s', sun_d); bpy.context.collection.objects.link(sun)
        sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    sc.render.resolution_x = sc.render.resolution_y = SIZE
    sc.render.film_transparent = True
    return cam


def shoot(cam, az, el, dst):
    a = math.radians(az); e = math.radians(el)
    pos = mathutils.Vector((DIST * math.cos(e) * math.sin(a), -DIST * math.cos(e) * math.cos(a), DIST * math.sin(e)))
    cam.location = pos
    cam.rotation_euler = (-pos).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.render.filepath = dst
    bpy.ops.render.render(write_still=True)


tmpdir = os.path.join(os.path.dirname(out_png), '_turn'); os.makedirs(tmpdir, exist_ok=True)
rows = []
for idx, p in enumerate(paths):
    cam = setup(p)
    name = os.path.splitext(os.path.basename(p))[0]
    if name in ('projected', 'model'):
        name = os.path.basename(os.path.dirname(p)) + '_' + name
    for el in EL:
        row = []
        for az in AZ:
            dst = os.path.join(tmpdir, f'{idx:02d}_{name}_e{el}_a{az}.png')
            shoot(cam, az, el, dst)
            row.append(dst)
        rows.append((f'{name} el{el:+d}', row))

from PIL import Image, ImageDraw
W = SIZE * len(AZ); H = SIZE * len(rows) + 24 * len(rows)
sheet = Image.new('RGB', (W, H), (40, 40, 40))
d = ImageDraw.Draw(sheet)
y = 0
for label, row in rows:
    d.text((6, y + 4), label, fill=(255, 255, 255))
    y += 24
    for i, f in enumerate(row):
        im = Image.open(f).convert('RGBA')
        bgim = Image.new('RGBA', im.size, (40, 40, 40, 255))
        sheet.paste(Image.alpha_composite(bgim, im).convert('RGB'), (i * SIZE, y))
    y += SIZE
sheet.save(out_png)
print('wrote', out_png, sheet.size)
