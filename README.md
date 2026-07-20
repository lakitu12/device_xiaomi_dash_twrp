### TWRP device tree for Redmi Turbo 5 Max (dash)

=========================================

The Redmi Turbo 5 Max (codenamed _"dash"_) is a smartphone from Xiaomi,
powered by the Mediatek Dimensity 9500s (MT6991Z) platform. It ships with
Android 16 (HyperOS 3) and targets the TWRP-16.0 (API 36) recovery tree.

This tree is adapted from the Redmi K80 Ultra (_"dali"_) `Twrp_A15_A16` device
tree (same MT6991 SoC family), with the following device-specific changes:

- `prebuilt/dtb` — replaced with the **dash** official DTB (not dali's)
- `recovery/root/lib/modules/*.ko` — replaced with the **272** kernel modules
  extracted from the dash official recovery (not dali's)
- Device name, model (`2602BRT18C`), and product strings rewritten dali -> dash
- `PRODUCT_SHIPPING_API_LEVEL` kept at **35** (the `Twrp_A15_A16` design is
  intended to be A15/A16-universal; the recovery is built against the TWRP-16.0
  / API 36 source tree to match the device's Android 16 userspace)

## Device specifications

Basic   | Spec Sheet
-------:|:-------------------------
CPU     | Mediatek Dimensity 9500s (MT6991Z)
Chipset | Mediatek Dimensity 9500s
GPU     | Mali-G925-Immortalis MC12
Memory  | 12/16 GB RAM (LPDDR5X)
Shipped Android Version | 16 (HyperOS 3)
Storage | 256/512/1024 GB (UFS 4.1)
Battery | Li-Po, non-removable
Display | 120 Hz

## Building TWRP (recovery / vendorboot)

This tree is meant to be built with the TWRP-Test `twrp-16.0` manifest
(android-16.0.0_r1, API 36):

```bash
repo init -u https://github.com/TWRP-Test/platform_manifest_twrp_aosp -b twrp-16.0
repo sync -j$(nproc --all) --force-sync
git clone https://github.com/lakitu12/device_xiaomi_dash_twrp -b main device/xiaomi/dash
source build/envsetup.sh
export ALLOW_MISSING_DEPENDENCIES=true
lunch twrp_dash-eng
make recoveryimage -j$(nproc --all)
```

## Flashing

> **WARNING:** `fastboot boot` is disabled on this device. The only recovery
> path is `fastboot flash vendor_boot`. Keep a copy of the stock
> `vendor_boot.img` to revert.

```bash
# flash the rebuilt vendor_boot (must be exactly 64 MB / 67108864 bytes)
fastboot flash vendor_boot twrp_vendor_boot.img
# to revert:
fastboot flash vendor_boot stock_vendor_boot.img
```

## Notes / known limitations

- The recovery ramdisk must be repacked into the stock `vendor_boot` v4 image
  (exactly 64 MB). Use a vendor_boot v4 repacker and keep the stock DTB/boot
  config intact — only the recovery ramdisk fragment is replaced.
- 272 dash-specific kernel modules are bundled; if a module fails to load on
  your exact firmware, compare against the modules shipped in your device's
  official recovery.
