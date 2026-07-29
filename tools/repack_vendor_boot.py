#!/usr/bin/env python3
"""
Replace recovery fragment in a vendor_boot v4 image for dash (Redmi Turbo 5 Max).

Usage:
    python3 tools/repack_vendor_boot.py \\
        --template /path/to/dash-twrp16-v1.0.0-rc1-vendor_boot.img \\
        --fragment /path/to/recovery-only-fragment.lz4 \\
        --output /tmp/dash-FINAL.img

The template image (rc1) has a custom vendor ramdisk with system/lib64/twrp16/
library directory that API-36 recovery needs. The script replaces only fragment 1
(recovery ramdisk), preserving vendor ramdisk, DTB, and AVB footer.
"""
import struct, sys, argparse

PARTITION_SIZE = 67108864  # 64 MB


def align(value, alignment):
    return (value + alignment - 1) // alignment * alignment


def parse_vendor_boot(image):
    if image[:8] != b"VNDRBOOT":
        raise ValueError("not a vendor_boot image")
    version, page_size, _, _, ramdisk_size = struct.unpack_from("<IIIII", image, 8)
    header_size, dtb_size = struct.unpack_from("<II", image, 2096)
    table_size, entry_count, entry_size, bootconfig_size = struct.unpack_from("<IIII", image, 2112)

    ramdisk_offset = align(header_size, page_size)
    ramdisk_end = ramdisk_offset + ramdisk_size
    dtb_offset = align(ramdisk_end, page_size)
    dtb_end = dtb_offset + dtb_size
    table_offset = align(dtb_end, page_size)

    entries = []
    for index in range(entry_count):
        eo = table_offset + index * entry_size
        sz, off, frag_type = struct.unpack_from("<III", image, eo)
        name = image[eo + 12:eo + 44].split(b"\0", 1)[0].decode()
        payload = image[ramdisk_offset + off:ramdisk_offset + off + sz]
        entries.append({
            "index": index, "size": sz, "offset": off,
            "type": frag_type, "name": name,
            "payload": payload, "raw_entry": image[eo:eo + entry_size],
        })

    return {
        "version": version,
        "page_size": page_size,
        "header_size": header_size,
        "ramdisk_size": ramdisk_size,
        "dtb_size": dtb_size,
        "table_size": table_size,
        "table_offset": table_offset,
        "entry_count": entry_count,
        "entry_size": entry_size,
        "bootconfig_size": bootconfig_size,
        "entries": entries,
        "avb_footer": image[-4096:] if image[-8:-4] == b"AVBf" else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Repack vendor_boot v4 with new recovery fragment")
    parser.add_argument("--template", required=True, help="rc1 template vendor_boot image")
    parser.add_argument("--fragment", required=True, help="new recovery fragment (LZ4 cpio)")
    parser.add_argument("--output", default="/tmp/dash-FINAL.img", help="output image path")
    args = parser.parse_args()

    # Load template
    data = open(args.template, "rb").read()
    c = parse_vendor_boot(data)

    print(f"Template: {args.template}")
    print(f"Entries: {c['entry_count']}")
    for e in c['entries']:
        print(f"  [{e['index']}] type={e['type']} name='{e['name']}' size={e['size']}")

    # Load new fragment
    new_fragment = open(args.fragment, "rb").read()
    print(f"\nNew fragment: {len(new_fragment)} bytes")

    # Build full 64MB image
    result = bytearray(PARTITION_SIZE)

    # Copy header, update ramdisk_size
    result[:c['header_size']] = data[:c['header_size']]
    new_ramdisk_size = c['entries'][0]['size'] + len(new_fragment)
    struct.pack_into("<I", result, 24, new_ramdisk_size)

    page = c['page_size']
    new_ramdisk_offset = align(c['header_size'], page)
    new_dtb_offset = align(new_ramdisk_offset + new_ramdisk_size, page)
    new_dtb_end = new_dtb_offset + c['dtb_size']
    new_table_offset = align(new_dtb_end, page)
    new_table_end = new_table_offset + c['entry_count'] * c['entry_size']
    new_bootconfig_offset = align(new_table_end, page)
    new_bootconfig_end = new_bootconfig_offset + c['bootconfig_size']

    # Fragment 0 (vendor ramdisk) — unchanged
    result[new_ramdisk_offset:new_ramdisk_offset + c['entries'][0]['size']] = c['entries'][0]['payload']

    # Fragment 1 (recovery ramdisk) — replaced
    frag1_offset = new_ramdisk_offset + c['entries'][0]['size']
    result[frag1_offset:frag1_offset + len(new_fragment)] = new_fragment

    # DTB — unchanged
    result[new_dtb_offset:new_dtb_end] = data[c['dtb_offset']:c['dtb_end']]

    # Entry table — preserve original type/name, update size/offset
    for i, e in enumerate(c['entries']):
        eo = new_table_offset + i * c['entry_size']
        result[eo:eo + c['entry_size']] = e['raw_entry']
        if i == 0:
            struct.pack_into("<I", result, eo, e['size'])
            struct.pack_into("<I", result, eo + 4, 0)
        elif i == 1:
            struct.pack_into("<I", result, eo, len(new_fragment))
            struct.pack_into("<I", result, eo + 4, c['entries'][0]['size'])

    # Bootconfig
    if c['bootconfig_size'] > 0:
        result[new_bootconfig_end - c['bootconfig_size']:new_bootconfig_end] = \
            data[c['bootconfig_end'] - c['bootconfig_size']:c['bootconfig_end']]

    # AVB footer
    if c['avb_footer']:
        result[-4096:] = c['avb_footer']

    # Verify
    print(f"\nOutput: {args.output}")
    print(f"Size: {len(result)} bytes ({len(result) / 1024 / 1024:.1f} MB)")
    assert len(result) == PARTITION_SIZE
    v = struct.unpack_from("<I", result, 24)[0]
    assert v == new_ramdisk_size, f"ramdisk_size mismatch: {v} != {new_ramdisk_size}"
    print(f"Fragment 0: {c['entries'][0]['size']}")
    print(f"Fragment 1: {len(new_fragment)}")
    print(f"DTB: {c['dtb_size']}")
    print(f"AVB footer: {'present' if c['avb_footer'] else 'missing'}")

    with open(args.output, "wb") as f:
        f.write(bytes(result))
    print(f"\nWritten: {args.output}")


if __name__ == "__main__":
    main()
