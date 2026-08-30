# 面の並べ直し（リメッシュ）：デコボコな面の並びを、きれいな四角の網目に作り直す。
#
# ★なぜ必要か（2026-08-16 実測）
#   Tripoのモデル … 18,368面できれい
#   うちの出力    … 60,000面あるのにガタガタ
#   AIが作った形は「等高線から削り出した」ような面の並びなので、面数を増やしても
#   なめらかになりません。並べ直すと【少ない面で、よりきれい】になります。
#   おまけに、面が少ないほど1面あたりに使える色の絵が広くなるので、
#   色塗りも細かくなります。
#
# 3d-studio (experiments/remesh_blender.py) からそのまま移植した。実測にもとづく注意書きも
# 当時のまま残してある。ADR-0007 の「UV展開の前にリトポロジーを入れる」の実体。
#
# 使いかた: venv-bpy\Scripts\python.exe experiments\remesh_blender.py 入力.glb 出力.glb [目標の三角形数]
#           venv-bpy\Scripts\python.exe experiments\remesh_blender.py 入力.glb 出力.glb 20000
import sys

import bpy

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
src, dst = argv[0], argv[1]
target_tris = int(argv[2]) if len(argv) > 2 else 20000

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)

meshes = [o for o in bpy.data.objects if o.type == 'MESH']
if not meshes:
    raise RuntimeError('メッシュが見つかりません')
# いちばん頂点数の多いものを本体とみなす（小さなゴミが混ざることがある）
ob = max(meshes, key=lambda o: len(o.data.vertices))
before_v, before_f = len(ob.data.vertices), len(ob.data.polygons)

bpy.ops.object.select_all(action='DESELECT')
ob.select_set(True)
bpy.context.view_layer.objects.active = ob

# ---- 並べ直しの前に、メッシュを修復する ------------------------------------
# ★2026-08-17 に判明した落とし穴
#   Blenderの並べ直しは「きちんと閉じた面のつながり」しか受け付けません。
#   形づくりAIの出力には【非多様体の辺】（1本の辺に3枚以上の面がくっついている等）が
#   混ざっていて、実測で165本ありました。そのままだと**エラーも出さずに何もせず**
#   返してくるので、「並べ直したつもり」で気づけません。
#   先に掃除しておくと通ります。
import bmesh


def count_nonmanifold(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    n = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return n


before_nm = count_nonmanifold(ob.data)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=1e-5)      # 重なった頂点をくっつける
bpy.ops.mesh.delete_loose()                      # ぶらさがりのゴミを消す
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.normals_make_consistent(inside=False)   # 面の裏表をそろえる
bpy.ops.object.mode_set(mode='OBJECT')
after_nm = count_nonmanifold(ob.data)
print(f'修復: おかしな辺 {before_nm} → {after_nm} 本', flush=True)

# ★2026-08-17：ここで「穴を埋める（fill_holes）」を使ってはいけません。
#   一度入れたところ、**顔の口や目のあたりまで塞がれ、面の裏表が壊れました**。
#   実際の画面：顔に大きな穴が空き、髪が裂け、黒い抜けが全身に出ました。
#   埋めなくても、あとの工程は問題なく通ります。
#   （dissolve_degenerate も、細かい飾り＝フリルや髪の先を潰すので使いません）

# ---- 面を並べ直す ----------------------------------------------------------
# 四角の網目に作り直す。target_faces は「四角の数」なので、三角形の目標の半分。
target_quads = max(500, target_tris // 2)
method = ''
try:
    bpy.ops.object.quadriflow_remesh(
        mode='FACES',
        target_faces=target_quads,
        use_mesh_symmetry=True,     # 左右対称に整える（キャラ向き）
        use_preserve_sharp=False,
        use_preserve_boundary=False,
        smooth_normals=True,
    )
    method = '四角の網目に作り直し'
except Exception as e:
    print(f'（四角の網目づくりは使えませんでした: {e}）', flush=True)

# ★2026-08-17 に分かったこと（大事）
#   Blenderの「四角の網目に作り直す」は、**エラーも出さずに何もせずに終わる**ことが
#   あります（戻り値は成功と出ます）。形づくりAIの出力は、表面に小さなトンネルが
#   たくさん空いた形（実測で16個）になっていて、この作り直しが解けないためです。
#   ・色塗りのあとのメッシュ … 59,999 → 7,964 面（成功）
#   ・色塗りのまえのメッシュ … 59,999 → 59,999 面（何も起きない）
#   そこで、効かなかったときは【確実に効く減らし方】に切り替えます。
#   見た目のなめらかさは、別で入れた「なめらかぬり」で確保できています。
if len(ob.data.polygons) > target_quads * 4:
    print(f'四角の網目づくりが効きませんでした（{before_f} → {len(ob.data.polygons)}）。'
          f'確実に効く方法に切り替えます', flush=True)
    mod = ob.modifiers.new('reduce', 'DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = min(1.0, target_tris / max(before_f, 1))
    mod.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=mod.name)
    method = '面数を減らして均す'

after_v, after_f = len(ob.data.vertices), len(ob.data.polygons)
print(f'使った方法: {method}', flush=True)
if after_f < 50:
    # まれに ほぼ空のメッシュになることがある。そのときは失敗扱い。
    raise RuntimeError(f'並べ直しの結果が壊れています（面 {after_f}）')

# ★2026-08-17 に見つけた落とし穴
#   面が多すぎる（190万面など）メッシュを渡すと、Blenderは
#   【エラーも出さずに何もせず】返してきます。そのまま進むと
#   「並べ直したつもりで、実は何も変わっていない」ことに気づけません。
#   面数がほとんど減っていなければ、失敗として扱います。
if after_f > target_tris * 2:
    raise RuntimeError(
        f'並べ直しが効いていません（面 {before_f} → {after_f}、目標 {target_tris}）')

for p in ob.data.polygons:
    p.use_smooth = True

print(f'並べ直し: 頂点 {before_v} → {after_v} ／ 面 {before_f} → {after_f}'
      f'（目標 四角 {target_quads} ＝ 三角形 {target_tris} 相当）', flush=True)

bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB')
print('保存:', dst, flush=True)
