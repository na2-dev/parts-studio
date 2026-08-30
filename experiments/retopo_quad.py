# 多様体化してから四角の網目に作り直す（ADR-0007 のリトポロジー工程）。
#
# ★なぜ2段なのか（2026-08-30 実測）
#   3d-studio の remesh_blender.py をそのまま当てたところ、QuadriFlow が
#   「多様体でなく、面法線の向きも揃っていない」と言って拒否した。
#   掃除しても おかしな辺が 720本 残る（元は 30,365本）。生成AIの出力は
#   表面に小さなトンネルが空いた形になっているため。
#   そこで先にボクセルで作り直して多様体にしてから、四角化する。
#
#   細部はここで一度失われるが、色はボクセル場から焼き直すので問題なく、
#   形の細部は法線マップで戻す方針（retopo_bake_blender.py 相当）。
#
# 使いかた:
#   venv-bpy\Scripts\python.exe tools\retopo_quad.py 入力.glb 出力.glb [目標三角形数] [ボクセル幅]
import sys
import bpy

src, dst = sys.argv[1], sys.argv[2]
target_tris = int(sys.argv[3]) if len(sys.argv) > 3 else 20000
voxel = float(sys.argv[4]) if len(sys.argv) > 4 else 0.006

def nonmanifold(me):
    import bmesh
    bm = bmesh.new(); bm.from_mesh(me)
    n = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free(); return n

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
if not obs:
    raise RuntimeError('メッシュが見つかりません')
ob = max(obs, key=lambda o: len(o.data.polygons))
bpy.ops.object.select_all(action='DESELECT')
ob.select_set(True)
bpy.context.view_layer.objects.active = ob
before_v, before_f = len(ob.data.vertices), len(ob.data.polygons)
print(f'入力: 頂点 {before_v:,} / 面 {before_f:,} / おかしな辺 {nonmanifold(ob.data):,}本', flush=True)

# ---- 掃除 -----------------------------------------------------------------
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=1e-5)
bpy.ops.mesh.delete_loose()
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode='OBJECT')

# ---- 1段目: ボクセルで作り直して多様体にする ------------------------------
ob.data.remesh_voxel_size = voxel
ob.data.remesh_voxel_adaptivity = 0.0
bpy.ops.object.voxel_remesh()
vox_f = len(ob.data.polygons)
nm = nonmanifold(ob.data)
print(f'ボクセル化(幅 {voxel}): 面 {vox_f:,} / おかしな辺 {nm:,}本', flush=True)
if nm > 0:
    print('  ※まだ多様体ではありません。四角化は失敗する見込み', flush=True)

# ---- 2段目: 四角の網目に作り直す ------------------------------------------
target_quads = max(500, target_tris // 2)
method = ''
try:
    bpy.ops.object.quadriflow_remesh(
        mode='FACES', target_faces=target_quads,
        use_mesh_symmetry=True, use_preserve_sharp=False,
        use_preserve_boundary=False, smooth_normals=True)
    method = '四角の網目'
except Exception as e:
    print(f'（四角化に失敗: {e}）', flush=True)

after_f = len(ob.data.polygons)
if after_f > target_quads * 4:
    print(f'四角化が効きませんでした（{vox_f:,} → {after_f:,}）。面数を減らす方法に切り替えます', flush=True)
    mod = ob.modifiers.new('reduce', 'DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = min(1.0, target_tris / max(vox_f, 1))
    mod.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=mod.name)
    method = '面数を減らして均す'
    after_f = len(ob.data.polygons)

quads = sum(1 for p in ob.data.polygons if len(p.vertices) == 4)
print(f'使った方法: {method}', flush=True)
print(f'結果: 頂点 {len(ob.data.vertices):,} / 面 {after_f:,}'
      f'（うち四角 {quads:,} = {quads/max(after_f,1)*100:.0f}%）'
      f' / おかしな辺 {nonmanifold(ob.data):,}本', flush=True)
if after_f < 50:
    raise RuntimeError(f'並べ直しの結果が壊れています（面 {after_f}）')

for p in ob.data.polygons:
    p.use_smooth = True
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB')
print('保存:', dst, flush=True)
