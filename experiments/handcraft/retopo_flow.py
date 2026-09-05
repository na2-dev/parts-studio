# 流れのあるリトポロジー（Blender・venv-bpy）。
#   1. ボクセルリメッシュで manifold にする（QuadriFlow は non-manifold を拒む）
#   2. QuadriFlow で【流れのある】四角に張り替える（目標面数を指定）
#   3. Smart UV Project で UV を切る
#   4. 元の表面へシュリンクラップ（ボクセル化で 0.0045 ずれるため）
# 使いかた: venv-bpy\Scripts\python.exe retopo_flow.py 入力.glb 出力.glb [目標面数] [ボクセル幅]
import sys, math
import bpy, bmesh

src, dst = sys.argv[1], sys.argv[2]
target = int(sys.argv[3]) if len(sys.argv) > 3 else 30000
voxel = float(sys.argv[4]) if len(sys.argv) > 4 else 0.009

def nonmanifold(me):
    bm = bmesh.new(); bm.from_mesh(me)
    n = sum(1 for e in bm.edges if not e.is_manifold); bm.free(); return n

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
orig = max(obs, key=lambda o: len(o.data.polygons))
print(f'入力: 頂点 {len(orig.data.vertices):,} / 面 {len(orig.data.polygons):,} / '
      f'おかしな辺 {nonmanifold(orig.data):,}', flush=True)

target_obj = orig.copy(); target_obj.data = orig.data.copy()
bpy.context.collection.objects.link(target_obj); target_obj.name = 'shrink_target'

bpy.ops.object.select_all(action='DESELECT')
orig.select_set(True); bpy.context.view_layer.objects.active = orig

# 1. ボクセル
orig.data.remesh_voxel_size = voxel
orig.data.remesh_voxel_adaptivity = 0.0
orig.data.use_remesh_fix_poles = True
bpy.ops.object.voxel_remesh()
print(f'ボクセル化(幅 {voxel}): 面 {len(orig.data.polygons):,} / おかしな辺 {nonmanifold(orig.data):,}', flush=True)

# 2. QuadriFlow
try:
    bpy.ops.object.quadriflow_remesh(target_faces=target, use_preserve_sharp=False,
                                     use_preserve_boundary=False, use_mesh_symmetry=False,
                                     seed=0, mode='FACES')
    quads = sum(1 for p in orig.data.polygons if len(p.vertices) == 4)
    print(f'QuadriFlow(目標 {target:,}): 面 {len(orig.data.polygons):,} / 四角 {quads/len(orig.data.polygons):.0%} / '
          f'おかしな辺 {nonmanifold(orig.data):,}', flush=True)
except Exception as e:
    print(f'QuadriFlow 失敗: {e} → ボクセルのまま進める', flush=True)

# 3. UV
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.003,
                         area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
bpy.ops.object.mode_set(mode='OBJECT')
print(f'UV: Smart Project 済み（島の余白 0.003）', flush=True)

# 4. スナップ
mod = orig.modifiers.new('snap', 'SHRINKWRAP')
mod.target = target_obj; mod.wrap_method = 'NEAREST_SURFACEPOINT'; mod.offset = 0.0
bpy.ops.object.modifier_apply(modifier=mod.name)
print(f'貼り付け直し: 完了 / おかしな辺 {nonmanifold(orig.data):,}', flush=True)

bpy.data.objects.remove(target_obj, do_unlink=True)
for p in orig.data.polygons: p.use_smooth = True
# ★材質を1つ付ける。無いと glTF に UV が入っても trimesh が捨てる（ColorVisuals になる）
mat = bpy.data.materials.new('base'); mat.use_nodes = True
orig.data.materials.clear(); orig.data.materials.append(mat)
bpy.ops.object.select_all(action='DESELECT')
orig.select_set(True); bpy.context.view_layer.objects.active = orig
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB', use_selection=True, export_texcoords=True, export_materials='EXPORT')
print('保存:', dst, flush=True)
