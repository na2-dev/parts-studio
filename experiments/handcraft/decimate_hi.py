# 生成直後の形（700万面）を、細部を保ったまま三角形数を減らす（Blender・venv-bpy）。
# ★リトポ（ボクセル → QuadriFlow 28k）は帽子・フードの縁・髪・目玉・指をすべて壊していた
#   （2026-09-05 素の形の比較で確定）。ギザギザ・手の板状・顔の目の穴の正体はこれ。
#   quadric decimation なら境界と細部を保ったまま 1/15〜1/20 に減らせる。
# 使いかた: venv-bpy\Scripts\python.exe decimate_hi.py 入力.glb 出力.glb [目標三角形数=400000]
import sys, bpy
src, dst = sys.argv[1], sys.argv[2]
target = int(sys.argv[3]) if len(sys.argv) > 3 else 400000
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
for o in obs:
    o.select_set(True)
bpy.context.view_layer.objects.active = obs[0]
if len(obs) > 1:
    bpy.ops.object.join()
ob = bpy.context.view_layer.objects.active
n0 = len(ob.data.polygons)
# ★生成直後の形は面の裏表が揃っていない（外向き 46%・巻き不一致、2026-09-05 実測）。
#   そのまま削減すると法線が半分裏向きになり、投影の「面がカメラを向いているか」判定が壊れて
#   81% が埋めになった。以前はボクセルリメッシュが偶然これを直していた。
#   頂点を結合して面のつながりを戻し、面の向きを外向きに揃えてから削減する
import bmesh
bm = bmesh.new(); bm.from_mesh(ob.data)
nv0 = len(bm.verts)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.to_mesh(ob.data); bm.free()
print(f'頂点結合 {nv0:,} → {len(ob.data.vertices):,}、面の向きを揃えた', flush=True)
mod = ob.modifiers.new('dec', 'DECIMATE')
mod.decimate_type = 'COLLAPSE'
mod.ratio = min(1.0, target / max(1, n0))
mod.use_collapse_triangulate = True
bpy.ops.object.modifier_apply(modifier='dec')
# 滑らかな陰影で書き出す（平面ごとの陰影だとファセットが見える）
bpy.ops.object.shade_smooth()
if not ob.data.materials:
    ob.data.materials.append(bpy.data.materials.new('m'))
print(f'decimate: {n0:,} → {len(ob.data.polygons):,} 面', flush=True)
bpy.ops.object.select_all(action='DESELECT'); ob.select_set(True)
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB', use_selection=True, export_materials='EXPORT')
print('保存:', dst, flush=True)
