# ボクセルで作り直したあと、元の表面へ貼り付け直す（シュリンクラップ）。
#
# ★なぜ貼り付け直しが要るのか（2026-08-30 実測）
#   ボクセルリメッシュ（幅0.009）は表面を最大0.0045ずらす。一方、色のボクセル場は
#   幅 1/1024 ≒ 0.00098。4〜5ボクセル分ずれた位置を引きに行くので、疎なボクセル場を
#   外して【アトラスがほぼ真っ黒】になった。
#   リトポロジーの標準どおり、新しい面を元の表面へスナップしてから焼く。
#
# 使いかた:
#   venv-bpy\Scripts\python.exe tools\retopo_shrinkwrap.py 入力.glb 出力.glb [ボクセル幅]
import sys
import bpy, bmesh

src = sys.argv[1]; dst = sys.argv[2]
voxel = float(sys.argv[3]) if len(sys.argv) > 3 else 0.009

def nm(me):
    bm = bmesh.new(); bm.from_mesh(me)
    n = sum(1 for e in bm.edges if not e.is_manifold); bm.free(); return n

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
orig = max(obs, key=lambda o: len(o.data.polygons))
print(f'入力: 頂点 {len(orig.data.vertices):,} / 面 {len(orig.data.polygons):,}', flush=True)

# 元の形を貼り付け先として取っておく
target = orig.copy(); target.data = orig.data.copy()
bpy.context.collection.objects.link(target)
target.name = 'shrink_target'

bpy.ops.object.select_all(action='DESELECT')
orig.select_set(True); bpy.context.view_layer.objects.active = orig
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=1e-5)
bpy.ops.mesh.delete_loose()
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode='OBJECT')

orig.data.remesh_voxel_size = voxel
orig.data.remesh_voxel_adaptivity = 0.0
bpy.ops.object.voxel_remesh()
f = len(orig.data.polygons)
q = sum(1 for p in orig.data.polygons if len(p.vertices) == 4)
print(f'ボクセル化(幅 {voxel}): 面 {f:,} / 四角 {q/max(f,1)*100:.0f}% / おかしな辺 {nm(orig.data):,}', flush=True)

# ---- 元の表面へ貼り付け直す ----
mod = orig.modifiers.new('snap', 'SHRINKWRAP')
mod.target = target
mod.wrap_method = 'NEAREST_SURFACEPOINT'
mod.offset = 0.0
bpy.ops.object.modifier_apply(modifier=mod.name)
print(f'貼り付け直し: 完了 / おかしな辺 {nm(orig.data):,}', flush=True)

bpy.data.objects.remove(target, do_unlink=True)
for p in orig.data.polygons:
    p.use_smooth = True
bpy.ops.object.select_all(action='DESELECT')
orig.select_set(True); bpy.context.view_layer.objects.active = orig
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB', use_selection=True)
print('保存:', dst, flush=True)
