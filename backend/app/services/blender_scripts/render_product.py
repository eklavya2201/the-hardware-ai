"""Runs INSIDE Blender (`blender -b --python render_product.py -- in.json out.png`).

Imports the STL meshes Synqit exported (board / parts / case), gives each a
plain PBR material, lights a soft studio, frames a three-quarter view from
above (so the open-top case shows the board inside) and renders with Cycles
on the CPU. Deterministic: same geometry, same picture.
"""
import json
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
payload = json.load(open(argv[0], encoding="utf-8"))
out_png = argv[1]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

MATERIALS = {
    # solder-mask green, matte epoxy black for parts, light grey plastic case
    "board": ((0.02, 0.26, 0.12, 1.0), 0.55, 0.0),
    "parts": ((0.09, 0.09, 0.10, 1.0), 0.45, 0.0),
    "case": ((0.82, 0.82, 0.80, 1.0), 0.6, 0.0),
}


def material(name):
    color, rough, metal = MATERIALS[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if name == "case":
        bsdf.inputs["Alpha"].default_value = 0.55  # see the board through the walls
        mat.blend_method = "BLEND" if hasattr(mat, "blend_method") else mat.blend_method
    return mat


objs = []
for name, path in payload["meshes"].items():
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path)
    else:  # Blender < 4.0
        bpy.ops.import_mesh.stl(filepath=path)
    obj = bpy.context.selected_objects[0]
    obj.name = name
    obj.data.materials.append(material(name))
    bpy.ops.object.shade_smooth()
    objs.append(obj)

# --- frame everything: bounding box of all imported geometry -------------------
lo = Vector((1e9, 1e9, 1e9))
hi = Vector((-1e9, -1e9, -1e9))
for obj in objs:
    for corner in obj.bound_box:
        v = obj.matrix_world @ Vector(corner)
        lo = Vector((min(lo.x, v.x), min(lo.y, v.y), min(lo.z, v.z)))
        hi = Vector((max(hi.x, v.x), max(hi.y, v.y), max(hi.z, v.z)))
centre = (lo + hi) / 2
size = max((hi - lo).x, (hi - lo).y, (hi - lo).z, 10.0)

# ground plane for a contact shadow
bpy.ops.mesh.primitive_plane_add(size=size * 12, location=(centre.x, centre.y, lo.z - 0.01))
ground = bpy.context.object
gmat = bpy.data.materials.new("ground")
gmat.use_nodes = True
gmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.92, 0.92, 0.91, 1)
gmat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
ground.data.materials.append(gmat)

# soft studio: big area key from the front-left-above, fill from the right, weak sun for edges
def area(name, loc, energy, sz):
    bpy.ops.object.light_add(type="AREA", location=loc)
    light = bpy.context.object
    light.name = name
    light.data.energy = energy
    light.data.size = sz
    light.data.shape = "SQUARE"
    d = centre - Vector(loc)
    light.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return light

area("key", (centre.x - size * 1.2, centre.y - size * 1.4, centre.z + size * 2.0), size * size * 12, size * 2.0)
area("fill", (centre.x + size * 1.8, centre.y - size * 0.4, centre.z + size * 1.2), size * size * 4, size * 2.5)
bpy.ops.object.light_add(type="SUN", location=(centre.x, centre.y, centre.z + size * 4))
sun = bpy.context.object
sun.data.energy = 1.5
sun.rotation_euler = (math.radians(35), math.radians(-20), math.radians(30))

# camera: three-quarter view from above, distance from the bounding sphere
bpy.ops.object.camera_add()
cam = bpy.context.object
scene.camera = cam
cam.data.lens = 50
cam.data.sensor_width = 36
dist = size * 1.9
cam.location = centre + Vector((-0.75 * dist, -1.05 * dist, 0.85 * dist))
cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()

world = bpy.data.worlds.new("studio")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.86, 0.87, 0.88, 1.0)
bg.inputs["Strength"].default_value = 0.9

scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = int(payload.get("samples", 48))
scene.cycles.use_denoising = False
scene.render.resolution_x = int(payload.get("width", 1280))
scene.render.resolution_y = int(payload.get("height", 800))
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = out_png
scene.view_settings.view_transform = "Filmic" if "Filmic" in [i.identifier for i in bpy.types.ColorManagedViewSettings.bl_rna.properties["view_transform"].enum_items] else "Standard"
bpy.ops.render.render(write_still=True)
print("SYNQIT_RENDER_OK", out_png)
