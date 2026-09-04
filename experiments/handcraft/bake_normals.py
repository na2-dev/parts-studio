# ハイ→ロー ベイク（Blender・venv-bpy）。700万面の元メッシュの彫りを、
# リトポ後のメッシュに法線マップと AO として焼く。
# 使いかた: venv-bpy\Scripts\python.exe bake_normals.py 元.glb リトポ.glb 出力dir [解像度]
import os, sys
import bpy

hi_path, lo_path, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
size = int(sys.argv[4]) if len(sys.argv) > 4 else 2048
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=hi_path)
hi = max([o for o in bpy.context.scene.objects if o.type == 'MESH'], key=lambda o: len(o.data.polygons))
hi.name = 'hi'
before = set(bpy.context.scene.objects)
bpy.ops.import_scene.gltf(filepath=lo_path)
lo = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o not in before][0]
lo.name = 'lo'
print(f'元: 面 {len(hi.data.polygons):,} / リトポ: 面 {len(lo.data.polygons):,} / UV {"あり" if lo.data.uv_layers else "★無し"}', flush=True)
assert lo.data.uv_layers, 'リトポ側に UV が無い'

# ★面の向きを外向きに揃え、スムーズにする。trimesh 経由の glb は平面シェーディングで
#   入り、面の向きも保証されない。内向きだとケージが内側に出て光線が裏面に当たり、
#   法線マップの大部分が黄土色（Z=0）になった（実測）
import bmesh
for ob in (hi, lo):
    bm = bmesh.new(); bm.from_mesh(ob.data)
    # ★UV の継ぎ目で複製された頂点を結合してから向きを揃える。結合しないと島ごとに
    #   分離した殻になり、殻ごとに勝手な向きが選ばれて半分が裏向きになる（実測: 黄土色）。
    #   UV は面の角（loop）に付くので、結合しても継ぎ目は壊れない
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data); bm.free()
    for p in ob.data.polygons: p.use_smooth = True
    print(f'  {ob.name}: 頂点 {before:,} → {len(ob.data.vertices):,}（継ぎ目の複製を結合）', flush=True)
print('面の向きを外向きに揃えた / スムーズ', flush=True)

sc = bpy.context.scene
sc.render.engine = 'CYCLES'
prefs = bpy.context.preferences.addons['cycles'].preferences
try:
    prefs.compute_device_type = 'OPTIX'
    prefs.get_devices()
    for d in prefs.devices: d.use = True
    sc.cycles.device = 'GPU'
    print('Cycles: GPU (OptiX)', flush=True)
except Exception as e:
    sc.cycles.device = 'CPU'; print(f'Cycles: CPU ({e})', flush=True)

def target_image(name):
    img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    img.colorspace_settings.name = 'Non-Color'
    return img

mat = bpy.data.materials.new('bake'); mat.use_nodes = True
lo.data.materials.clear(); lo.data.materials.append(mat)
nodes = mat.node_tree.nodes
tex = nodes.new('ShaderNodeTexImage'); nodes.active = tex

bpy.ops.object.select_all(action='DESELECT')
hi.select_set(True); lo.select_set(True); bpy.context.view_layer.objects.active = lo
sc.render.bake.use_selected_to_active = True
sc.render.bake.cage_extrusion = 0.02
sc.render.bake.max_ray_distance = 0.06
sc.render.bake.margin = 8

# 法線（タンジェント空間）
tex.image = target_image('normal')
sc.cycles.samples = 4
sc.render.bake.normal_space = 'TANGENT'
bpy.ops.object.bake(type='NORMAL', use_selected_to_active=True)
tex.image.filepath_raw = os.path.join(out_dir, 'normal.png'); tex.image.file_format = 'PNG'; tex.image.save()
print('保存: normal.png', flush=True)

# AO
tex.image = target_image('ao')
sc.cycles.samples = 32
bpy.ops.object.bake(type='AO', use_selected_to_active=True)
tex.image.filepath_raw = os.path.join(out_dir, 'ao.png'); tex.image.save()
print('保存: ao.png', flush=True)
