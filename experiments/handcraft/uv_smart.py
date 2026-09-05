# 大きなメッシュ（40万三角形）の UV を Blender の Smart UV Project で切る（venv-bpy）。
# ★xatlas は 44 万三角形で 1 時間半以上終わらなかった（2026-09-05）。Smart UV Project は数十秒。
#   島は多くなるが、投影は「テクセルごとに同じ絵から色を拾う」ので島の継ぎ目は見えない。
#   埋めは穴ごとに縁の色で塗るので、穴が島で分断されても局所的に正しい色になる。
# 使いかた: venv-bpy\Scripts\python.exe uv_smart.py 入力.glb 出力.glb
import sys, math, bpy, bmesh
src, dst = sys.argv[1], sys.argv[2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
for o in obs:
    o.select_set(True)
bpy.context.view_layer.objects.active = obs[0]
if len(obs) > 1:
    bpy.ops.object.join()
ob = bpy.context.view_layer.objects.active
# glTF の読み書きで頂点が面ごとに分かれていることがある。結合して面のつながりを戻す
bm = bmesh.new(); bm.from_mesh(ob.data)
n0 = len(bm.verts)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
bm.to_mesh(ob.data); bm.free()
print(f'頂点結合: {n0:,} → {len(ob.data.vertices):,} / 面 {len(ob.data.polygons):,}', flush=True)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.001, area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.shade_smooth()
if not ob.data.materials:
    ob.data.materials.append(bpy.data.materials.new('m'))
bpy.ops.object.select_all(action='DESELECT'); ob.select_set(True)
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB', use_selection=True, export_texcoords=True, export_materials='EXPORT')
print('保存:', dst, flush=True)
