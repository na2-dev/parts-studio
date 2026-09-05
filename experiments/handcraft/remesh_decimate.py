# 生成直後の形を「細かいボクセルリメッシュ → 削減」で 1 つの水密な殻にする（Blender・venv-bpy）。
# ★経緯（2026-09-05）:
#   ・ボクセル 0.009 → QuadriFlow 28k は帽子の縁・髪・目玉・指を壊した（ギザギザ・板状の手・目の穴）
#   ・生成メッシュを直接削減すると 6.4 万個の破片（非多様体・巻き不一致）が残り、
#     xatlas が破片ごとに余白を付けて UV 面積が 20% に落ち、8 割の三角形が 1 テクセル未満になった
#   → ボクセルを 3 倍細かく（0.003）掛ければ 1 つの殻・向きも揃う・細部も残る。あとは削減だけ
# 使いかた: venv-bpy\Scripts\python.exe remesh_decimate.py 入力.glb 出力.glb [ボクセル=0.003] [目標三角形数=300000]
import sys, bpy
src, dst = sys.argv[1], sys.argv[2]
voxel = float(sys.argv[3]) if len(sys.argv) > 3 else 0.003
target = int(sys.argv[4]) if len(sys.argv) > 4 else 300000
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
ob.data.remesh_voxel_size = voxel
ob.data.remesh_voxel_adaptivity = 0.0
ob.data.use_remesh_fix_poles = False
ob.data.use_remesh_preserve_volume = True
bpy.ops.object.voxel_remesh()
n1 = len(ob.data.polygons)
mod = ob.modifiers.new('dec', 'DECIMATE')
mod.decimate_type = 'COLLAPSE'; mod.ratio = min(1.0, target / max(1, n1 * 2)); mod.use_collapse_triangulate = True
bpy.ops.object.modifier_apply(modifier='dec')
bpy.ops.object.shade_smooth()
if not ob.data.materials:
    ob.data.materials.append(bpy.data.materials.new('m'))
print(f'remesh {voxel}: {n0:,} → {n1:,} 面（四角）→ 削減 {len(ob.data.polygons):,} 面', flush=True)
bpy.ops.object.select_all(action='DESELECT'); ob.select_set(True)
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB', use_selection=True, export_materials='EXPORT')
print('保存:', dst, flush=True)
