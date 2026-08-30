# ボクセルリメッシュだけで、均一・多様体・四角主体のメッシュを作る。
import sys, bpy, bmesh
src, dst, voxel = sys.argv[1], sys.argv[2], float(sys.argv[3])
def nm(me):
    bm = bmesh.new(); bm.from_mesh(me)
    n = sum(1 for e in bm.edges if not e.is_manifold); bm.free(); return n
def parts(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data)
    seen=set(); c=0
    for f in bm.faces:
        if f.index in seen: continue
        c+=1; stack=[f]
        while stack:
            g=stack.pop()
            if g.index in seen: continue
            seen.add(g.index)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen: stack.append(h)
    bm.free(); return c
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obs=[o for o in bpy.context.scene.objects if o.type=='MESH']
ob=max(obs,key=lambda o:len(o.data.polygons))
bpy.ops.object.select_all(action='DESELECT'); ob.select_set(True)
bpy.context.view_layer.objects.active=ob
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.remove_doubles(threshold=1e-5); bpy.ops.mesh.delete_loose()
bpy.ops.mesh.select_all(action='SELECT'); bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode='OBJECT')
ob.data.remesh_voxel_size=voxel; ob.data.remesh_voxel_adaptivity=0.0
bpy.ops.object.voxel_remesh()
f=len(ob.data.polygons); q=sum(1 for p in ob.data.polygons if len(p.vertices)==4)
print(f'voxel={voxel}: 頂点 {len(ob.data.vertices):,} / 面 {f:,} / 四角 {q/max(f,1)*100:.0f}% / '
      f'おかしな辺 {nm(ob.data):,} / つながり {parts(ob)} 個', flush=True)
for p in ob.data.polygons: p.use_smooth=True
bpy.ops.export_scene.gltf(filepath=dst, export_format='GLB')
print('保存:', dst, flush=True)
