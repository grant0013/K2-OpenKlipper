# Pack body.npz (orange, filament 3) + green.npz (green, filament 1) into one
# two-part 3MF object (Bambu/Orca project layout) that OrcaSlicer opens with
# the body on filament 3 and the stem and leaf on filament 1.
import json, os, shutil, sys, uuid, zipfile
import numpy as np

CX, CY = 128.0, 128.0          # where the pumpkin sits on the plate
OUT = sys.argv[1] if len(sys.argv) > 1 else "pumpkin.3mf"


def mesh_xml(obj_id, V, F):
    v = "\n".join('     <vertex x="%.4f" y="%.4f" z="%.4f"/>' % tuple(p) for p in V)
    t = "\n".join('     <triangle v1="%d" v2="%d" v3="%d"/>' % tuple(f) for f in F)
    return ('  <object id="%d" p:UUID="%s" type="model">\n   <mesh>\n    <vertices>\n%s\n'
            '    </vertices>\n    <triangles>\n%s\n    </triangles>\n   </mesh>\n  </object>'
            % (obj_id, uuid.uuid4(), v, t))


NS = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
      'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
      'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
      'requiredextensions="p"')

parts = [("Pumpkin body", np.load("body.npz"), 3), ("Stem and leaf", np.load("green.npz"), 1)]
objects = "\n".join(mesh_xml(i + 1, d["V"], d["F"]) for i, (n, d, e) in enumerate(parts))
objects_model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" %s>\n'
                 ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n%s\n </resources>\n'
                 ' <build/>\n</model>\n' % (NS, objects))
comps = "\n".join('    <component p:path="/3D/Objects/object_1.model" objectid="%d" p:UUID="%s" '
                  'transform="1 0 0 0 1 0 0 0 1 0 0 0" />' % (i + 1, uuid.uuid4())
                  for i in range(len(parts)))
main_model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" %s>\n'
              ' <metadata name="Application">BambuStudio-02.08.02.61</metadata>\n'
              ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n'
              ' <metadata name="Title">Two-colour pumpkin (Hark Tech K2 CFS test)</metadata>\n'
              ' <resources>\n  <object id="%d" p:UUID="%s" type="model">\n   <components>\n%s\n'
              '   </components>\n  </object>\n </resources>\n <build p:UUID="%s">\n'
              '  <item objectid="%d" p:UUID="%s" transform="1 0 0 0 1 0 0 0 1 %.3f %.3f 0" printable="1" />\n'
              ' </build>\n</model>\n'
              % (NS, len(parts) + 1, uuid.uuid4(), comps, uuid.uuid4(), len(parts) + 1, uuid.uuid4(), CX, CY))
part_xml = "\n".join(
    '    <part id="%d" subtype="normal_part">\n'
    '      <metadata key="name" value="%s"/>\n'
    '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
    '      <metadata key="extruder" value="%d"/>\n'
    '      <mesh_stat face_count="%d" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
    '    </part>' % (i + 1, n, e, len(d["F"])) for i, (n, d, e) in enumerate(parts))
oid = len(parts) + 1
model_settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="%d">\n'
                  '    <metadata key="name" value="Pumpkin"/>\n    <metadata key="extruder" value="3"/>\n'
                  '%s\n  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n'
                  '    <metadata key="plater_name" value=""/>\n    <metadata key="locked" value="false"/>\n'
                  '    <model_instance>\n      <metadata key="object_id" value="%d"/>\n'
                  '      <metadata key="instance_id" value="0"/>\n    </model_instance>\n  </plate>\n'
                  '  <assemble>\n   <assemble_item object_id="%d" instance_id="0" '
                  'transform="1 0 0 0 1 0 0 0 1 %.3f %.3f 0" offset="0 0 0" />\n  </assemble>\n</config>\n'
                  % (oid, part_xml, oid, oid, CX, CY))
# Filament order = CFS slots A-D. Flush volumes (mm3, from-row to-column)
# feed Orca's per-change flush; orange over green needs the most.
colours = ["#43A047", "#26EEEE", "#FB8C00", "#FFFFFF"]
flush = [[0, 150, 260, 150],
         [150, 0, 150, 150],
         [140, 150, 0, 150],
         [150, 150, 150, 0]]
project = {"filament_map": ["1"] * 4, "filament_colour": colours,
           "flush_volumes_matrix": [str(v) for row in flush for v in row],
           "flush_volumes_vector": ["140"] * 8, "flush_multiplier": ["1"]}

# Standard 3MF packaging files
CONTENT_TYPES = '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n <Default Extension="png" ContentType="image/png"/>\n <Default Extension="gcode" ContentType="text/x.gcode"/>\n</Types>'
RELS = '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />\n</Relationships>\n'
MODEL_RELS = '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n <Relationship Target="/3D/Objects/object_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n</Relationships>'
tmp = "build"
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp + "/3D/Objects"); os.makedirs(tmp + "/3D/_rels"); os.makedirs(tmp + "/Metadata"); os.makedirs(tmp + "/_rels")
open(tmp + "/[Content_Types].xml", "w").write(CONTENT_TYPES)
open(tmp + "/_rels/.rels", "w").write(RELS)
open(tmp + "/3D/_rels/3dmodel.model.rels", "w").write(MODEL_RELS)
open(tmp + "/3D/3dmodel.model", "w").write(main_model)
open(tmp + "/3D/Objects/object_1.model", "w").write(objects_model)
open(tmp + "/Metadata/model_settings.config", "w").write(model_settings)
json.dump(project, open(tmp + "/Metadata/project_settings.config", "w"), indent=4)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(tmp):
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, tmp))
print("wrote", OUT, os.path.getsize(OUT), "bytes")
