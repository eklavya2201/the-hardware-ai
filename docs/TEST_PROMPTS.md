# Test prompts for Synqit CAD

Paste one into the box at http://localhost:3000/cad. Times are with GPT-5.1 on Azure.

## 1. Smoke test (30 to 60 s, expect 8 to 12 sliders)

```
A wall-mount holder for a 65 x 120 mm phone: 3 mm walls, 15 mm deep pocket, open top, two countersunk M4 screw holes on the back plate, and a 12 mm slot at the bottom for the charging cable.
```

## 2. Electronics enclosure (closest to what Synqit does)

```
A two-part snap-fit enclosure for a 58 x 42 mm PCB, 1.6 mm thick, with 4 mounting holes on a 50 x 34 mm pattern for M2.5 screws. Inside clearance 12 mm above the board, 2 mm walls, 3 mm standoffs. Cut a USB-C opening 9 x 3.5 mm centred on one short wall, 2 mm above the standoffs, and four 2 mm ventilation slots on each long wall. Show the lid beside the base.
```

Check: is the USB-C cutout on the right wall, do standoffs match the hole
pattern, does the lid mate with the base in the TOP and FRONT views.

## 3. Follow-up edits (send after prompt 2, one at a time)

```
Add a 5 mm diameter hole for a status LED on the lid, 8 mm from the USB-C edge.
```

```
Change the wall thickness to 2.5 mm and add a 1 mm chamfer on all outer edges of the lid.
```

```
Make the four corner screw bosses 6 mm diameter and add a 45 degree fillet where each meets the floor.
```

## 4. Stress test (one of the upstream project's own benchmarks; GPT-5.1 will probably need several rounds)

```
A herringbone planetary gear stage, module 1.5: 18-tooth sun, three 18-tooth planets, 54-tooth internal ring, and a carrier plate linking the planet axles. Colour the sun, planets, ring and carrier differently.
```

## What to look at

- The chat shows each build attempt. More than one means the screenshot check
  rejected the first version, which is the loop doing its job.
- Drag a slider on the right: the model recompiles in the browser, no AI call.
- The cube in the corner snaps to TOP / FRONT / LEFT; compare with the prompt.
- STL button downloads the print file; the dropdown next to it gives SCAD and DXF.
