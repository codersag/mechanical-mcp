"""
Equipment-to-Point-Mass workflow via MCP tools.

Converts a group of equipment bodies (organized as a Part in the Mechanical tree)
into a combined Point Mass, then automatically creates a Named Selection of the
structural attachment faces below the equipment and scopes the Point Mass to it.

No ACT extensions required — mass, CG, and moments of inertia are computed
from first principles using the parallel axis theorem.

Prerequisites:
- Equipment bodies grouped into a Part in the Mechanical tree (e.g. "Equipment1")
- Structural bodies (skid/slab) present and unsuppressed

Claude prompts that trigger this workflow:
  "Convert part Equipment1 to point mass"
  "Convert part Chiller_Unit to a point mass"
"""

# Equivalent MCP tool call:
# convert_part_to_point_mass(part_name="Equipment1")

# What the tool does internally:
# 1. Finds all unsuppressed bodies in the Part "Equipment1"
# 2. Records their bounding box (before suppression)
# 3. Creates a PointMass object via geometry.AddPointMass()
# 4. Iterates through each solid body: accumulates mass, CG (weighted average),
#    and moments of inertia using the parallel axis theorem
# 5. Suppresses each processed body
# 6. Sets PM name, mass, CG coordinates, and inertia tensors on the object
# 7. Computes pinball from volume-equivalent sphere: r = (3V/4π)^(1/3)
# 8. Finds the structural face closest to the equipment's bottom boundary
#    with a normal pointing toward the CG (the attachment surface)
# 9. Collects all coplanar faces on that attachment plane within the equipment footprint
# 10. Creates a Named Selection "NearestFace_Equipment1" from those faces
# 11. Sets pm.Location = named_selection (scopes the PM)
# 12. Resets pm.PinballRegion = Quantity("0 [mm]")  →  "All" (no pinball limit)

# Expected output:
# {
#   "cg_mm": [382.5, 851.4, 613.2],
#   "pinball": "0 [mm] (All)",
#   "bodies_matched": 28,
#   "attachment_faces": 4,
#   "named_selection": "NearestFace_Equipment1"
# }
