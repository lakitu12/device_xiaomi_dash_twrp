#!/bin/bash
set -euo pipefail

# Local vendor_boot repack script for dash (MT6991Z)
# Usage: ./local_repack_vendor_boot.sh [STOCK_VENDOR_BOOT] [CI_VENDOR_BOOT] [VALIDATED_VENDOR_BOOT] [OUTPUT]
# If args not provided, uses defaults from GitHub releases

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEVICE_TREE="$(dirname "$SCRIPT_DIR")"

STOCK_IMG="${1:-/tmp/stock_vendor_boot.img}"
CI_IMG="${2:-/tmp/ci_vendor_boot.img}"
VALIDATED_IMG="${3:-/tmp/validated_vendor_boot.img}"
OUTPUT_IMG="${4:-/tmp/repacked_vendor_boot.img}"
VBMETA_IMG="${5:-/tmp/stock_vbmeta.img}"

# Download defaults if not provided
download_if_missing() {
    local url="$1"
    local dest="$2"
    if [ ! -f "$dest" ]; then
        echo "Downloading $dest..."
        curl -sfL "$url" -o "$dest" || { echo "Failed to download $url"; exit 1; }
    fi
}

echo "=== Local Vendor_Boot Repack for dash ==="
echo "Stock:      $STOCK_IMG"
echo "CI:         $CI_IMG"
echo "Validated:  $VALIDATED_IMG"
echo "VBMeta:     $VBMETA_IMG"
echo "Output:     $OUTPUT_IMG"
echo

# Download default assets from GitHub if missing
download_if_missing "https://github.com/lakitu12/device_xiaomi_dash_twrp/releases/download/stock-vendor_boot/vendor_boot.img" "$STOCK_IMG"
download_if_missing "https://github.com/lakitu12/device_xiaomi_dash_twrp/releases/download/stock-vendor_boot/vbmeta.img" "$VBMETA_IMG"
download_if_missing "https://github.com/lakitu12/TWRP-Recovery-Builder-2024/releases/download/validated/vendor_boot-dash-OS3.0.305.WPLCNXM-lakitu-1.img" "$VALIDATED_IMG"

# CI image - try local build output first, then fallback
if [ ! -f "$CI_IMG" ]; then
    LOCAL_CI="$DEVICE_TREE/../source-twrp16/out/target/product/dash/vendor_boot.img"
    if [ -f "$LOCAL_CI" ]; then
        CI_IMG="$LOCAL_CI"
        echo "Using local CI build: $CI_IMG"
    else
        echo "CI vendor_boot.img not found at $CI_IMG"
        echo "Build it first: cd source-twrp16 && m recovery vendorbootimage"
        exit 1
    fi
fi

# Check all inputs exist
for f in "$STOCK_IMG" "$CI_IMG" "$VALIDATED_IMG" "$VBMETA_IMG"; do
    [ -f "$f" ] || { echo "Missing: $f"; exit 1; }
done

# Staging directory from local build
STAGING_DIR="$DEVICE_TREE/../source-twrp16/out/target/product/dash/recovery/root"
if [ ! -d "$STAGING_DIR" ]; then
    echo "Warning: Staging dir not found at $STAGING_DIR"
    STAGING_DIR=""
fi

# Run prepare_fragments
echo "=== Preparing F0+F1 fragments ==="
PREFIX="/tmp/fragments_$(date +%s)"
python3 "$DEVICE_TREE/tools/prepare_fragments.py" \
    --stock "$STOCK_IMG" \
    --ci "$CI_IMG" \
    --validated "$VALIDATED_IMG" \
    --staging "$STAGING_DIR" \
    --dtroot "$DEVICE_TREE" \
    --prefix "$PREFIX"

# Run stock_aware_repack_tool (needs AOSP build for avbtool)
echo "=== Running stock_aware_repack_tool ==="
AVBTOOL="$DEVICE_TREE/../source-twrp16/out/host/linux-x86/bin/avbtool"
if [ ! -f "$AVBTOOL" ]; then
    AVBTOOL=$(which avbtool 2>/dev/null || echo "")
fi
if [ -z "$AVBTOOL" ]; then
    echo "ERROR: avbtool not found. Build AOSP first or install android-tools-avbtool"
    exit 1
fi

REPACK_TOOL="$DEVICE_TREE/../source-twrp16/repack/stock_aware_repack_tool"
if [ ! -f "$REPACK_TOOL" ]; then
    echo "Building stock_aware_repack_tool..."
    (cd "$DEVICE_TREE/../source-twrp16" && m stock_aware_repack_tool)
    REPACK_TOOL="$DEVICE_TREE/../source-twrp16/out/host/linux-x86/bin/stock_aware_repack_tool"
fi

export PATH="$(dirname "$AVBTOOL"):$PATH"
"$REPACK_TOOL" \
    --template "$STOCK_IMG" \
    --vbmeta-owner "$VBMETA_IMG" \
    --replacement-manifest "$PREFIX.manifest.json" \
    --output "$OUTPUT_IMG" \
    --report "$PREFIX.repack_report.json" \
    --budget-output "$PREFIX.budget.json"

echo
echo "=== Success! ==="
echo "Output: $OUTPUT_IMG"
echo "Size:   $(du -h "$OUTPUT_IMG" | cut -f1)"
echo
echo "To flash:"
echo "  adb reboot bootloader"
echo "  fastboot flash vendor_boot $OUTPUT_IMG"
echo "  fastboot reboot"