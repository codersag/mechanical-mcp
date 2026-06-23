"""
Basic Static Structural workflow via MCP tools.

This script shows the equivalent Python calls that the MCP tools make.
In practice, Claude calls these tools automatically from natural language prompts.
"""

# 1. Connect to a running Mechanical instance
# Claude prompt: "Connect to Mechanical on port 10000"
# connect_to_mechanical(port=10000)

# 2. Assign material to a body by its exact name in the Mechanical tree
# Claude prompt: "Assign Structural Steel to Part 1"
# assign_material(body_name="Part 1", material_name="Structural Steel")

# 3. Generate mesh
# Claude prompt: "Generate mesh with 50mm element size"
# set_mesh_element_size(element_size_mm=50)
# generate_mesh()

# 4. Add boundary conditions
# Claude prompt: "Add fixed support to named selection Base"
# add_fixed_support(named_selection="Base")

# Claude prompt: "Add a force of 10000 N in the -Y direction to named selection Top"
# add_force(named_selection="Top", fy_n=-10000)

# 5. Solve
# Claude prompt: "Solve the analysis"
# solve_analysis()

# 6. Get results
# Claude prompt: "Get the maximum equivalent stress"
# add_equivalent_stress()

# Claude prompt: "Get the total deformation"
# add_total_deformation()

# 7. Generate report
# Claude prompt: "Generate a report at C:/results/report.docx"
# generate_report(output_path="C:/results/report.docx", fmt="docx")
