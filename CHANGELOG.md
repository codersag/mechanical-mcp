# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2026-06-23

### Added
- **Connection management**: `connect_to_mechanical`, `disconnect_from_mechanical`, `check_mechanical_connection`
- **Model info**: `get_model_info`, `list_materials`, `assign_material`
- **Mesh**: `set_mesh_element_size`, `generate_mesh`, `get_mesh_statistics`
- **Boundary conditions**: `add_fixed_support`, `add_frictionless_support`, `add_displacement`, `add_remote_displacement`, `add_force`, `add_remote_force`, `add_pressure`, `add_moment`, `add_standard_gravity`
- **Solve**: `solve_analysis`, `get_solve_status`
- **Results**: `add_equivalent_stress`, `add_principal_stress`, `add_stress_tool`, `add_total_deformation`, `add_total_deformation_all_modes`, `add_directional_deformation`, `get_modal_frequencies`, `add_reaction_force`
- **Geometry**: `suppress_bodies`, `list_point_masses`
- **Point mass conversion** (no ACT extensions required): `convert_part_to_point_mass`, `convert_prefix_to_point_mass`
- **Named selections**: `list_named_selections`, `delete_named_selection`
- **Reports**: `generate_report` (DOCX and TXT)
- **Scripting**: `run_mechanical_script`
