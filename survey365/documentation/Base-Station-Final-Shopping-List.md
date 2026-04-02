# Base Station — Final Shopping List
## Starting From Zero — Everything You Need

---

## ORDER 1: ArduSimple (ardusimple.com)
*Ships from Spain via DHL Express, 2-4 days to US*

| # | Item | SKU | EUR | ~USD |
|---|------|-----|-----|------|
| 1 | **simpleRTK2B Budget (ZED-F9P)** | AS-RTK2B-F9P-L1L2-NH-03 | €172 | $186 |
| 2 | **Calibrated Survey Tripleband + L-band Antenna (IP67)** | AS-ANT3B-CAL-01 | €199 | $215 |
| 3 | USB to USB-C cable (F9P to Pi) | (accessory on product page) | €7 | $8 |
| | **ArduSimple Total** | | **€378** | **~$409** |

**Why these choices:**
- F9P board: mature ecosystem, strong Linux support, and direct serial control
- Tripleband antenna: NGS-calibrated, 5/8" thread screws directly onto survey tripod, IP67 weatherproof, 1.5m cable with TNC-to-SMA (plugs straight into the F9P). Future-proofed for L5/E6 if you ever upgrade to a UM980 board or rover. This is the last antenna you'll buy.
- The antenna cable SMA male plugs directly into the F9P board SMA female. No adapters needed.

---

## ORDER 2: Amazon
*All US-stocked, Prime eligible*

### Raspberry Pi + Storage

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 4 | **Raspberry Pi 4 Model B (2GB RAM)** | $45 | The sweet spot — 4 USB ports, low power draw, handles Survey365 with ease. Buy from CanaKit, Vilros, or official Pi reseller. |
| 5 | **Samsung EVO Select 64GB microSD** | $10 | For Pi OS + Survey365. 64GB gives weeks of raw GNSS data logging before rotation. |
| 6 | **Raspberry Pi 4 aluminum heatsink set** | $3 | Passive cooling. Essential for a sealed case in Alabama summer heat. Get the set that covers CPU + RAM + USB controller. |
| 7 | **Raspberry Pi 4 USB-C power supply** | $8 | For bench setup/testing at home only. In the field you'll run off the power bank. |

### Cellular Connectivity

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 8 | **Waveshare SIM7600G-H 4G Dongle** | $45 | Industrial-grade LTE modem. Plugs into Pi USB. Global band support. Linux/Pi compatible out of the box via RNDIS. Nano SIM slot. |
| 9 | **SUPERBAT 4G LTE 9dBi Magnetic Mount Antenna (SMA male, 3m RG58 cable)** | $16 | High-gain omni LTE antenna. SMA male screws directly onto the Waveshare dongle's SMA female port (via bulkhead pass-through). 3m cable routes from case to tripod leg. |
| 10 | **T-Mobile Connect or Mint Mobile prepaid SIM (Nano)** | $10-15/mo | T-Mobile Connect $10/mo for 1GB (RTCM3 uses ~1.5MB/hour — plenty). Activate online. No contract. |

### Long-Range WiFi (for no-cell sites + skid steer later)

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 11 | **Alfa AWUS036ACH AC1200 USB WiFi Adapter** | $30 | Dual-band. Supports AP (hotspot) mode on Raspberry Pi OS. Two RP-SMA antenna connectors for external antennas. Well-documented Linux driver support. |
| 12 | **9dBi 2.4GHz RP-SMA Omnidirectional Antenna (x2)** | $15 | Replace the Alfa's stock antennas. 9dBi omni gives ~400-600m range. The Alfa has two antenna ports (MIMO) so get a pair. Mount vertically on the tripod. |

### Power

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 13 | **Anker 523 Power Bank (10,000mAh, USB-C PD)** | $22 | Powers Pi 4 + F9P + 4G dongle + WiFi adapter for 5-7 hours. Compact. USB-C PD output won't trigger low-voltage warnings on the Pi 4. |
| 14 | **USB-C to USB-C cable, 1ft** | $7 | Power bank to Pi. Short = clean routing inside the case. |

### Survey Tripod

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 15 | **AdirPro Aluminum Survey Tripod, 5/8" x 11 flat head** | $90 | Proper survey tripod. Adjustable height ~3.5-5.5ft. The flat head with 5/8" thread directly accepts the ArduSimple Calibrated Survey Antenna. Stable enough for all-day use. Look for "flat head" specifically — dome head tripods won't mate with the antenna's female thread. |

### Enclosure + Mounting

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 16 | **Pelican 1200 Case (with foam)** | $42 | Interior 9.2" x 7.2" x 4.1". IP67 waterproof. Fits everything — Pi, F9P, 4G dongle, WiFi adapter, power bank, and all cables. Pluck foam for custom layout. Pre-molded walls are easy to drill for bulkhead connectors. Tough enough for daily jobsite abuse. |
| 17 | **SMA Female Bulkhead Panel Mount Connectors (5-pack)** | $8 | Drill holes in case wall, mount with nut + O-ring. Three needed: GNSS antenna, LTE antenna, WiFi antenna. Two spares. |
| 18 | **SMA Male to SMA Male Jumper Cables, 6" (3-pack)** | $8 | Inside the case — connects each bulkhead to its device (F9P board, Waveshare dongle, Alfa adapter). |
| 19 | **PG7/PG9 Waterproof Cable Gland Assortment** | $8 | For passing the USB-C charging cable through the case wall. One PG9 gland for the charging port. Extras useful for any future cable pass-throughs. |
| 20 | **M2.5 Brass Standoff Kit (male-female assortment)** | $8 | For mounting the Pi 4 to a base plate inside the case. Standard Pi 4 mounting holes are M2.5. |
| 21 | **Adhesive Zip-Tie Mounts (50-pack)** | $5 | Stick to the base plate for cable management and securing the F9P board, dongle, and Alfa adapter. |
| 22 | **1" Velcro Straps (6-pack)** | $5 | For securing the power bank inside the case. |
| 23 | **3mm Acrylic Sheet (8"x10" or similar)** | $5 | Cut to fit the Pelican 1200 interior as a mounting plate. Everything mounts to this plate, plate drops into the case. Alternatively use thin plywood or aluminum sheet. |
| 24 | **Small Tube of Silicone Sealant (clear)** | $5 | Seal around the SMA bulkheads and cable gland for extra weather protection. |

### Field Accessories

| # | Item | ~USD | Notes |
|---|------|------|-------|
| 25 | **PK Nails (box of 50)** | $15 | Drive into asphalt or dirt to mark your base station position at each job site for reoccupation. Essential for OPUS workflow — you need to set up on the exact same point next visit. |
| 26 | **Zip Ties (assorted, 100-pack)** | $5 | For mounting antennas to tripod legs, cable management, field repairs. You probably already have these but listing for completeness. |
| 27 | **Small Hammer** | $0 | For the PK nails. You own one. |

| | **Amazon Total** | **~$405-410 + $10-15/mo cell** | |

---

## ORDER 3: Free Software + Services (download/register)

| # | Item | Cost | URL |
|---|------|------|-----|
| 28 | **Survey365** (native base station software) | Free | github.com/irbygroup/rtk-surveying |
| 29 | **Emlid Caster** (cloud NTRIP relay) | Free | caster.emlid.com |
| 30 | **ALDOT CORS** (Alabama free RTK network) | Free | aldotcors.dot.state.al.us |
| 31 | **NGS OPUS** (precise base positioning) | Free | opus.ngs.noaa.gov |
| 32 | **Emlid Flow** (survey app for Reach RX) | Free / $240/yr Survey plan | App Store / Google Play |
| 33 | **balenaEtcher** (SD card flasher) | Free | etcher.balena.io |

---

## GRAND TOTAL

| Category | Cost |
|----------|------|
| ArduSimple — F9P board + tripleband antenna + cable | $409 |
| Raspberry Pi 4 + SD card + heatsink + power supply | $66 |
| Waveshare 4G dongle + LTE antenna + SIM | $71 + $10-15/mo |
| Alfa WiFi adapter + 9dBi antennas (x2) | $45 |
| Power bank + cable | $29 |
| Survey tripod (5/8" flat head) | $90 |
| Pelican 1200 case | $42 |
| Bulkheads, glands, standoffs, mounting hardware | $52 |
| PK nails + zip ties | $20 |
| Software + services | $0 |
| | |
| **TOTAL ONE-TIME COST** | **~$824** |
| **MONTHLY RECURRING** | **$10-15 (cell data)** |

---

## WHAT YOU GET FOR $824

- Centimeter-accurate RTK base station, portable to any job site
- Corrections via cell (Emlid Caster) for your Emlid Reach RX surveying
- Corrections via WiFi (local NTRIP caster) when no cell service — 400-600m range
- Future-proofed tripleband antenna ready for UM980 upgrade
- RINEX logging for OPUS submissions (absolute accuracy for setback-critical work)
- XBee socket ready for LoRa radio when you add the skid steer
- Professional weatherproof field kit that sets up in 5 minutes
- Works with Emlid Flow, SW Maps, FieldGenius, OpenGrade, or any NTRIP-capable app
- Zero subscription fees (ALDOT CORS and Emlid Caster are both free)

---

## WHAT YOU'LL ADD LATER

| Phase | Items | Approx Cost |
|-------|-------|-------------|
| **Skid steer grading** | Second simpleRTK2B Budget + ANN-MB-00 antenna + Windows tablet + USB cable | ~$450 |
| **LoRa radio link** | ArduSimple LR radio pair (XBee format, one for base, one for machine) | ~$130 |
| **DIY survey rover** | Third simpleRTK2B (or RTK3B UM980) + survey antenna + BT+BLE Bridge + survey pole | ~$500-600 |
| **Permanent shop base** | Pi Zero 2W + F9P + antenna + PoE + mount | ~$300 |

All future additions plug into the same base station you're building now. No rework, no replacement — just expansion.

---

## ASSEMBLY DIAGRAM

```
                GNSS TRIPLEBAND ANTENNA
                (screwed onto tripod 5/8" head)
                        │
                        │ 1.5m TNC-to-SMA cable
                        │
    ┌───────────────────▼─── SMA BULKHEAD #1 ──────────┐
    │  PELICAN 1200                                     │
    │  (mounted on tripod tray or hanging from leg)     │
    │                                                   │
    │  ┌────────────────────────────────────────┐       │
    │  │  MOUNTING PLATE (acrylic/plywood)      │       │
    │  │                                        │       │
    │  │  ┌──────────┐      ┌───────────────┐   │       │
    │  │  │ Pi 4 2GB │      │ Anker 523     │   │       │
    │  │  │ + heatsink│ USB-C│ power bank    │   │       │
    │  │  │          │◄─────│               │   │       │
    │  │  └──┬──┬──┬─┘      └───────────────┘   │       │
    │  │     │  │  │                             │       │
    │  │     │  │  └──► Alfa WiFi ──────────────────► SMA BULKHEAD #2
    │  │     │  │       adapter                  │    ↓
    │  │     │  │                                │  9dBi WiFi omni
    │  │     │  └────► Waveshare ───────────────────► SMA BULKHEAD #3
    │  │     │         4G dongle                 │    ↓
    │  │     │                                   │  9dBi LTE antenna
    │  │     └── SMA jumper ──► F9P board        │
    │  │                        (connected to    │
    │  │                         BULKHEAD #1)    │
    │  └────────────────────────────────────────┘│
    │                                            │
    │  ═══ PG9 CABLE GLAND ═════════════════════─┼──► USB-C for charging
    └────────────────────────────────────────────┘

    TRIPOD LEGS:
    ├── 9dBi WiFi omni antenna (zip-tied vertical)
    ├── 9dBi LTE antenna (zip-tied vertical)
    └── Pelican case (sitting on tray or strapped to leg)
```

---

## FIRST BOOT CHECKLIST

1. □ Flash Raspberry Pi OS to SD card with balenaEtcher
2. □ Mount Pi 4 on standoffs on mounting plate
3. □ Attach heatsink to Pi
4. □ Connect F9P to Pi via USB-C cable
5. □ Connect Waveshare dongle to Pi USB (insert SIM first)
6. □ Connect Alfa adapter to Pi USB
7. □ Wire SMA jumpers from bulkheads to each device
8. □ Boot Pi, connect to its WiFi hotspot from phone
9. □ Install and start Survey365 on the Pi
10. □ Verify satellite reception in the Survey365 status view
11. □ Add outbound caster credentials in Survey365 NTRIP profiles
12. □ Enable the local caster in Survey365 config if needed
13. □ Configure WiFi hotspot on the Alfa adapter
14. □ Register for ALDOT CORS (aldotcors.dot.state.al.us)
15. □ Add NTRIP profiles in Emlid Flow:
    - "Pi Base - Caster" → caster.emlid.com
    - "Pi Base - Local" → 192.168.x.x:2101
    - "ALDOT CORS" → aldotcors credentials
16. □ Take outside, verify RTK fix on Reach RX
17. □ Drive a PK nail at your first base position
18. □ Go build some houses
