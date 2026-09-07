// Phone stand parameters
/* [Stand Dimensions] */
// Width of the phone (inner cradle width)
phone_width = 70;            // [60:1:90]
// Thickness allowance for phone depth
phone_thickness_allowance = 12; // [8:1:20]
// Overall stand depth
stand_depth = 90;            // [70:1:140]
// Overall stand height
stand_height = 80;           // [60:1:140]
// Tilt angle of the phone support surface
support_tilt_angle = 15;     // [5:1:40]
// Thickness of main walls
wall_thickness = 3;          // [3:0.5:8]
// Lip height at bottom front to catch phone
front_lip_height = 10;       // [5:1:20]
// Cable slot width at the rear
cable_slot_width = 12;       // [8:1:25]
// Cable slot height
cable_slot_height = 15;      // [10:1:25]
// Cable slot offset from base
cable_slot_bottom_offset = 10; // [5:1:30]
// Vent slot width
vent_slot_width = 6;         // [4:1:10]
// Vent slot height
vent_slot_height = 40;       // [25:1:60]
// Vent slot spacing (center-to-center)
vent_slot_spacing = 14;      // [10:1:20]
// Corner round radius
corner_radius = 4;           // [2:1:8]

/* [Appearance] */
stand_color = "LightSlateGray";

$fn = 64;

include <BOSL2/std.scad>;
include <BOSL2/rounding.scad>;

module phone_stand() {
    color(stand_color)
    rounded_phone_stand();
}

// Main stand with manually rounded outer vertical edges
module rounded_phone_stand() {
    // We will fillet vertical outer edges by placing cylinders at corners and hull with main bodies.
    union() {
        // Core geometry without rounding
        raw_stand();
        
        // Add vertical fillets at four outer base corners
        vertical_corner_fillets();
    }
}

// Raw stand geometry before corner rounding
module raw_stand() {
    union() {
        base_plate();
        back_support();
        front_lip();
        side_rails();
    }
}

module base_plate() {
    // Simple rectangular base
    translate([-stand_depth/2, -phone_width/2 - wall_thickness, 0])
    cube([stand_depth, phone_width + 2*wall_thickness, wall_thickness]);
}

module back_support() {
    // Back support is a vertical plate with ventilation slots and cable slot
    support_height = stand_height;
    support_thickness = wall_thickness;

    // Upright back wall anchored at rear of base
    translate([-stand_depth/2, -phone_width/2 - wall_thickness, wall_thickness])
    difference() {
        cube([wall_thickness, phone_width + 2*wall_thickness, support_height]);
        
        // Cable slot at rear center
        translate([0,
                   (phone_width + 2*wall_thickness - cable_slot_width)/2,
                   cable_slot_bottom_offset])
        cube([wall_thickness+0.1, cable_slot_width, cable_slot_height]);
        
        // Ventilation slots: three vertical slots spaced horizontally
        vent_total_width = (3*vent_slot_width) + (2*vent_slot_spacing);
        vent_start_y = -vent_total_width/2 + vent_slot_width/2 + wall_thickness;
        vent_start_z = stand_height/2 - vent_slot_height/2;
        
        for(i = [0:2]) {
            translate([0,
                       vent_start_y + i*(vent_slot_width + vent_slot_spacing),
                       vent_start_z])
            cube([wall_thickness+0.1, vent_slot_width, vent_slot_height]);
        }
    }

    // Tilted support surface that phone rests against
    support_plate_height = stand_height - front_lip_height;
    support_plate_width = phone_width;
    support_plate_length = stand_depth - 20; // leave space at front

    translate([-stand_depth/2 + wall_thickness,
               -support_plate_width/2,
               wall_thickness])
    rotate([support_tilt_angle, 0, 0])
    cube([support_plate_length, support_plate_width, support_thickness]);
}

module front_lip() {
    // Small lip at the front edge to stop phone sliding
    lip_thickness = wall_thickness;

    translate([stand_depth/2 - lip_thickness,
               -phone_width/2,
               wall_thickness])
    cube([lip_thickness, phone_width, front_lip_height]);
}

module side_rails() {
    // Two small rails along sides to help centering and retention
    rail_thickness = wall_thickness;
    rail_height = 15;
    rail_length = stand_depth/2; // front half

    // Left rail
    translate([-rail_length/2,
               -phone_width/2 - rail_thickness,
               wall_thickness])
    cube([rail_length, rail_thickness, rail_height]);

    // Right rail
    translate([-rail_length/2,
               phone_width/2,
               wall_thickness])
    cube([rail_length, rail_thickness, rail_height]);
}

// Simple vertical fillets at base corners
module vertical_corner_fillets() {
    // Back left corner
    translate([-stand_depth/2, -phone_width/2 - wall_thickness, 0])
    cylinder(h=stand_height + wall_thickness, r=corner_radius);
    
    // Back right corner
    translate([-stand_depth/2, phone_width/2 + wall_thickness, 0])
    cylinder(h=stand_height + wall_thickness, r=corner_radius);
    
    // Front left corner
    translate([stand_depth/2, -phone_width/2 - wall_thickness, 0])
    cylinder(h=stand_height + wall_thickness, r=corner_radius);
    
    // Front right corner
    translate([stand_depth/2, phone_width/2 + wall_thickness, 0])
    cylinder(h=stand_height + wall_thickness, r=corner_radius);
}

phone_stand();