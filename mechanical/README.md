# 3D Printable Gasketed Electronics Enclosure

This folder contains a parametric OpenSCAD model for a two-part enclosure matching your requested **internal** size:

- **Height:** 4 in (101.6 mm)
- **Width:** 6 in (152.4 mm)
- **Length:** 8 in (203.2 mm)

File: `weatherproof_case_enclosure.scad`

## Included design features

- Two halves (`base_half` + `lid_half`) that screw together.
- Perimeter overlap lip to improve splash resistance.
- Gasket groove in lid.
- Slightly thinner lid top (`lid_top_thk = 2.4 mm`) for through-fittings.
- Four corner fastener stacks sized for M4 clearance + nut traps.

## Notes before printing

- This is **weather/splash resistant oriented**, not guaranteed submersible.
- For stronger waterproofing, use:
  - TPU/silicone cord gasket matching `gasket_cs` parameter.
  - Stainless hardware + threadlocker.
  - Post-process sealing at cable penetrations (IP glands or bulkhead fittings).
- If you use resin or high-shrink material, tune `clearance` and gasket groove dimensions.

## Export to STEP (for SolidWorks import)

OpenSCAD itself exports STL, not STEP. A common path is:

1. Open `weatherproof_case_enclosure.scad` in OpenSCAD and set `mode` to `base_only`.
2. Export STL.
3. Repeat for `lid_only`.
4. Import each STL into FreeCAD and convert/export to STEP (`.step`).
5. Open STEP files in SolidWorks.

If you want, I can also provide a direct CADQuery/FreeCAD script version that generates STEP natively in one step when that toolchain is available.
