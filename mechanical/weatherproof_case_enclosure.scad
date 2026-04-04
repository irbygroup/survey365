/*
  Survey365 - Parametric two-part gasketed enclosure

  Target internal dimensions (default):
    - Height: 4 in  (101.6 mm)
    - Width:  6 in  (152.4 mm)
    - Length: 8 in  (203.2 mm)

  Features:
    - Two screw-together halves (base + lid)
    - Gasket groove around perimeter
    - Slightly thinner lid top for through-fittings
    - Corner fasteners with captive nut pockets in base

  Units: millimeters
*/

$fn = 72;

// ---------- Helpers ----------
inch = 25.4;

// ---------- Primary size requirements (inside dimensions) ----------
inside_h = 4 * inch;   // 101.6 mm
inside_w = 6 * inch;   // 152.4 mm
inside_l = 8 * inch;   // 203.2 mm

// ---------- General wall and split ----------
wall = 4.0;
floor_thk = 4.0;
lid_top_thk = 2.4;     // intentionally thinner top face for through-fittings
lip_depth = 10.0;      // interlocking overlap depth at split
clearance = 0.35;      // assembly clearance between lip/tongue walls

// ---------- Hardware ----------
screw_d = 4.3;         // M4 clearance
screw_head_d = 8.2;    // basic counterbore for socket head
screw_head_h = 3.0;
nut_flat = 7.0;        // M4 hex nut across flats (nominal)
nut_pocket_h = 3.4;
corner_inset = 12.0;   // from inside corner to fastener center

// ---------- Gasket ----------
gasket_cs = 2.5;       // gasket cross section diameter (O-ring cord)
gasket_groove_depth = 1.5;
gasket_groove_margin = 4.5;

// ---------- Derived dimensions ----------
outer_w = inside_w + 2 * wall;
outer_l = inside_l + 2 * wall;

base_inside_h = inside_h * 0.62;
lid_inside_h  = inside_h - base_inside_h;

base_outer_h = floor_thk + base_inside_h + lip_depth;
lid_outer_h  = lid_top_thk + lid_inside_h + lip_depth;

// Fastener XY positions in local body coordinates
function corner_xy(sign_x, sign_y) = [
  sign_x * (outer_w/2 - wall - corner_inset),
  sign_y * (outer_l/2 - wall - corner_inset)
];

module rounded_rect_prism(w, l, h, r=4) {
  linear_extrude(height=h)
    offset(r=r)
      offset(delta=-r)
        square([w, l], center=true);
}

module screw_hole_stack(total_h) {
  // Through-hole from lid top through base bosses
  cylinder(h=total_h, d=screw_d, center=false);
}

module base_half() {
  difference() {
    union() {
      // Main shell
      difference() {
        rounded_rect_prism(outer_w, outer_l, base_outer_h, r=6);
        translate([0, 0, floor_thk])
          rounded_rect_prism(inside_w, inside_l, base_inside_h + lip_depth + 2, r=4);
      }

      // Perimeter tongue/lip that nests into lid
      translate([0, 0, floor_thk + base_inside_h])
        difference() {
          rounded_rect_prism(inside_w + 2*(wall - clearance), inside_l + 2*(wall - clearance), lip_depth, r=3);
          translate([0, 0, -0.1])
            rounded_rect_prism(inside_w - 2*clearance, inside_l - 2*clearance, lip_depth + 0.2, r=2);
        }

      // Bosses for screw + nut pockets
      for (sx = [-1, 1], sy = [-1, 1]) {
        p = corner_xy(sx, sy);
        translate([p[0], p[1], 0])
          cylinder(h=base_outer_h, d=10.5, center=false);
      }
    }

    // Vertical screw holes and nut traps
    for (sx = [-1, 1], sy = [-1, 1]) {
      p = corner_xy(sx, sy);
      translate([p[0], p[1], 0]) {
        screw_hole_stack(base_outer_h + 1);

        // hex nut trap near top of base section
        translate([0, 0, base_outer_h - lip_depth - nut_pocket_h - 1.2])
          cylinder(h=nut_pocket_h, d=nut_flat / cos(30), $fn=6);
      }
    }
  }
}

module lid_half() {
  difference() {
    union() {
      // Main lid shell
      difference() {
        rounded_rect_prism(outer_w, outer_l, lid_outer_h, r=6);

        // Internal cavity
        translate([0, 0, lid_top_thk])
          rounded_rect_prism(inside_w, inside_l, lid_inside_h + lip_depth + 2, r=4);
      }

      // Extra material around fastener zones
      for (sx = [-1, 1], sy = [-1, 1]) {
        p = corner_xy(sx, sy);
        translate([p[0], p[1], 0])
          cylinder(h=lid_outer_h, d=10.5, center=false);
      }
    }

    // Recess pocket to receive base tongue
    translate([0, 0, lid_top_thk + lid_inside_h])
      rounded_rect_prism(inside_w + 2*(wall + clearance), inside_l + 2*(wall + clearance), lip_depth + 0.4, r=3);

    // Gasket groove (in lid, around perimeter)
    translate([0, 0, lid_top_thk + lid_inside_h + lip_depth - gasket_groove_depth])
      difference() {
        rounded_rect_prism(inside_w + 2*(wall - gasket_groove_margin), inside_l + 2*(wall - gasket_groove_margin), gasket_groove_depth + 0.2, r=2.5);
        translate([0, 0, -0.1])
          rounded_rect_prism(inside_w + 2*(wall - gasket_groove_margin - gasket_cs), inside_l + 2*(wall - gasket_groove_margin - gasket_cs), gasket_groove_depth + 0.4, r=1.5);
      }

    // Through screws from top, with counterbore
    for (sx = [-1, 1], sy = [-1, 1]) {
      p = corner_xy(sx, sy);
      translate([p[0], p[1], 0]) {
        screw_hole_stack(lid_outer_h + 1);
        cylinder(h=screw_head_h, d=screw_head_d);
      }
    }
  }
}

// ---------- Build mode ----------
// set to "assembled", "exploded", "base_only", "lid_only"
mode = "exploded";

if (mode == "assembled") {
  base_half();
  translate([0, 0, base_outer_h - lip_depth])
    lid_half();
} else if (mode == "exploded") {
  base_half();
  translate([outer_w + 20, 0, 0])
    lid_half();
} else if (mode == "base_only") {
  base_half();
} else if (mode == "lid_only") {
  lid_half();
}
