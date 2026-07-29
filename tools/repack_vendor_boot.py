#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包 — 按 README 流程
支持标准 AOSP vendor_boot（单 ramdisk）和 MTK 双 fragment 格式。

  --stock  原厂 vendor_boot.img
  --ci     CI 构建产物 vendor_boot.img  
"""
import struct, subprocess, os, shutil, sys, argparse

PAGE_SIZE = 4096
ENTRY_SIZE = 108
PARTITION_SIZE = 67108864
HS_OFF = 2096
DTB_OFF = 2100
TBL_OFF = 2112

def align(v, a):
    return (v + a - 1) // a * a

def get_frags(img_path):
    """解析 vendor_boot。fragment 表在 DTB 之后的数据区（不在 header 内）。"""
    d = open(img_path, 'rb').read()
    page0 = d[:PAGE_SIZE]
    hs = struct.unpack_from('<I', page0, HS_OFF)[0]   # 2128
    ds = struct.unpack_from('<I', page0, DTB_OFF)[0]  # 535963
    rs = struct.unpack_from('<I', page0, 24)[0]        # ramdisk_size
    ro = align(hs, PAGE_SIZE)                           # 4096
    dtbo = align(ro + rs, PAGE_SIZE)

    # fragment 表在数据区（DTB 之后 page 对齐处），不在 header 内
    # header 的 tbl_off=216 实际是 table size（ec*es）
    ec = struct.unpack_from('<I', page0, TBL_OFF + 4)[0]
    es = struct.unpack_from('<I', page0, TBL_OFF + 8)[0]
    tblo = align(dtbo + ds, PAGE_SIZE)

    frags = []
    has_valid_entries = False
    for i in range(ec):
        eo = tblo + i * es
        if eo + 44 > len(d):
            break
        sz = struct.unpack_from('<I', d, eo)[0]
        off = struct.unpack_from('<I', d, eo + 4)[0]
        typ = struct.unpack_from('<I', d, eo + 8)[0]
        name = d[eo + 12:eo + 44].rstrip(b'\x00').decode()
        if sz > 0 and ro + off + sz <= len(d):
            has_valid_entries = True
            payload = d[ro + off:ro + off + sz]
        else:
            payload = b''
        frags.append({'size': sz, 'offset': off, 'type': typ, 'name': name, 'payload': payload})

    # 标准格式（数据区无有效 entry）：整个 ramdisk 作为 F0
    if not has_valid_entries and rs > 0:
        payload = d[ro:ro + rs]
        frags = [{'size': rs, 'offset': 0, 'type': 1, 'name': '', 'payload': payload}]
        print(f"  -> standard format, single ramdisk ({len(payload)/1024/1024:.2f} MB)")

    # DTB
    dtb_data = b''
    for o in range(dtbo, min(dtbo + 16384, len(d))):
        if d[o:o + 4] == b'\xd0\x0d\xfe\xed':
            dtb_sz = struct.unpack_from('>I', d, o + 4)[0]
            dtb_data = d[o:o + dtb_sz]
            break
    if not dtb_data and ds > 0:
        dtb_data = d[dtbo:dtbo + ds]

    return {
        'frags': frags, 'dtb': dtb_data, 'page0': page0,
        'hs': hs, 'ds': ds, 'ro': ro, 'rs': rs,
    }

def unpack_lz4_or_raw(data, dest):
    """解压（可能未压缩的）cpio 到目录。"""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    # 先尝试 LZ4 解压
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin', '--favor-decSpeed'],
                       input=data, capture_output=True, timeout=30)
    if r.returncode == 0 and len(r.stdout) > 0:
        cpio_data = r.stdout
    else:
        # 不是 LZ4，尝试直接作为 cpio
        cpio_data = data
    subprocess.run(['cpio', '-idm'], input=cpio_data, capture_output=True, timeout=30, cwd=dest)
    return cpio_data

def pack_lz4(src, tmp):
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=10, cwd=src)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'], input=r.stdout, capture_output=True, timeout=60, cwd=src)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', tmp], input=p.stdout, capture_output=True, timeout=60)
    return open(tmp, 'rb').read()

def main():
    ap = argparse.ArgumentParser(description='TWRP vendor_boot repack for dash')
    ap.add_argument('--stock', required=True, help='原厂 vendor_boot.img')
    ap.add_argument('--ci', required=True, help='CI 构建产物 vendor_boot.img')
    ap.add_argument('--output', default='/tmp/dash-UNTESTED-vendor_boot.img')
    args = ap.parse_args()
    for p in [args.stock, args.ci]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    stk = get_frags(args.stock)
    ci = get_frags(args.ci)
    print(f"Stock: {len(stk['frags'])} frags, CI: {len(ci['frags'])} frags")

    # ======== F0：改造 stock vendor ramdisk ========
    print("\n[F0] 改造 stock vendor ramdisk ...")
    f0d = '/tmp/rf0'
    unpack_lz4_or_raw(stk['frags'][0]['payload'], f0d)

    # 删 recovery binary + res/
    for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
            print(f"  removed {os.path.basename(p)}")

    # 加 twrp16/（从 CI F1 提取）
    ci1d = '/tmp/rci1'
    ci_f1_payload = ci['frags'][1]['payload'] if len(ci['frags']) > 1 else ci['frags'][0]['payload']
    unpack_lz4_or_raw(ci_f1_payload, ci1d)
    twrp16_libs = ['libbase.so', 'libbootloader_message.so', 'libc++.so', 'libcutils.so',
                   'libfs_mgr.so', 'liblog.so', 'liblp.so', 'libprotobuf-cpp-lite.so', 'libutils.so']
    tdir = f'{f0d}/system/lib64/twrp16'
    os.makedirs(tdir, exist_ok=True)
    for lib in twrp16_libs:
        s = f'{ci1d}/system/lib64/{lib}'
        if os.path.exists(s):
            shutil.copy2(s, f'{tdir}/{lib}', follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(tdir))} libs)")

    # init.rc setenv
    irc = f'{f0d}/system/etc/init/hw/init.rc'
    if os.path.exists(irc):
        with open(irc) as f:
            c = f.read()
        tag = ('service recovery /system/bin/recovery\n'
               '    socket recovery stream 422 system system\n'
               '    seclabel u:r:recovery:s0\n    user root')
        if tag in c:
            c = c.replace(tag, tag + '\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
            with open(irc, 'w') as f:
                f.write(c)
            print("  + setenv in init.rc")

    f0 = pack_lz4(f0d, '/tmp/rf0.lz4')
    print(f"  F0: {len(f0)/1024/1024:.2f} MB LZ4")

    # ======== F1：裁剪 CI recovery ========
    print("\n[F1] 裁剪 CI recovery ...")
    f1d = '/tmp/rf1'
    f1_payload = ci['frags'][1]['payload'] if len(ci['frags']) > 1 else ci['frags'][0]['payload']
    unpack_lz4_or_raw(f1_payload, f1d)
    print(f"  base: CI F1 ({len(f1_payload)/1024/1024:.2f} MB LZ4)")

    # 删 vendor 已提供的核心组件
    for rel in ['system/bin/init', 'system/bin/linker64', 'system/bin/adbd',
                'system/bin/fastbootd', 'system/bin/toybox']:
        p = os.path.join(f1d, rel)
        if os.path.exists(p):
            os.remove(p)
            print(f"  removed {os.path.basename(rel)}")

    # 删 CI F1 中与 F0 同名的库
    f0_lib64 = f'{f0d}/system/lib64'
    f1_lib64 = f'{f1d}/system/lib64'
    del_ct = 0; del_sz = 0; keep_ct = 0; keep_sz = 0
    if os.path.isdir(f0_lib64) and os.path.isdir(f1_lib64):
        f0_libs = {lib for lib in os.listdir(f0_lib64)
                   if os.path.isfile(os.path.join(f0_lib64, lib))
                   and not os.path.islink(os.path.join(f0_lib64, lib))}
        for lib in list(os.listdir(f1_lib64)):
            if lib == 'twrp16':
                continue
            fp = os.path.join(f1_lib64, lib)
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            if lib in f0_libs:
                sz = os.path.getsize(fp)
                os.remove(fp)
                del_ct += 1
                del_sz += sz
            else:
                keep_ct += 1
                keep_sz += os.path.getsize(fp)
        print(f"  system/lib64/: kept {keep_ct} ({keep_sz/1024/1024:.2f} MB), "
              f"deleted {del_ct} (from F0, {del_sz/1024/1024:.2f} MB)")

    # setenv 在 F1 的 init.recovery.service.rc 上
    for irc_rel in ['init.recovery.service.rc', 'system/etc/init/hw/init.rc']:
        irc = f'{f1d}/{irc_rel}'
        if os.path.exists(irc):
            with open(irc) as f:
                c = f.read()
            tag = ('service recovery /system/bin/recovery\n'
                   '    socket recovery stream 422 system system\n'
                   '    seclabel u:r:recovery:s0\n    user root')
            if tag in c:
                c = c.replace(tag, tag + '\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
                with open(irc, 'w') as f:
                    f.write(c)
                print(f"  + setenv in F1 {irc_rel}")

    # 补 init 等 symlink
    for item in ['init', 'sbin/sh', 'default.prop']:
        dst = f'{f1d}/{item}'
        src = f'{f0d}/{item}'
        if not os.path.lexists(dst) and os.path.lexists(src):
            dd = os.path.dirname(dst)
            os.makedirs(dd, exist_ok=True)
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)

    f1 = pack_lz4(f1d, '/tmp/rf1.lz4')
    print(f"  F1: {len(f1)/1024/1024:.2f} MB LZ4")
    print(f"  Total: {(len(f0)+len(f1))/1024/1024:.2f} MB")

    if len(f0) + len(f1) + align(len(stk['dtb']), PAGE_SIZE) + 2*ENTRY_SIZE > PARTITION_SIZE:
        print("  ERROR: Total exceeds 64MB partition!", file=sys.stderr)
        sys.exit(1)

    # ======== 构建 vendor_boot ========
    print("\n构建 vendor_boot ...")
    rsz = len(f0) + len(f1)
    rs = PAGE_SIZE
    ds = align(rs + rsz, PAGE_SIZE)
    de = ds + len(stk['dtb'])

    img = bytearray(PARTITION_SIZE)
    img[:PAGE_SIZE] = stk['page0'][:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)
    struct.pack_into('<I', img, DTB_OFF, stk['ds'])

    img[rs:rs + len(f0)] = f0
    img[rs + len(f0):rs + rsz] = f1

    # DTB 区域：64 字节 MTK 表头 + FDT 数据
    # 表头从原厂 page0 之后/ramdisk 之后复制
    if stk['ds'] > 0:
        # 从原厂提取完整的 DTB 区域（64 字节表头 + FDT 数据）
        # 表头固定 64 字节，数据紧接其后
        dtb_header = stk['dtb']  # This may be just the FDT (535899 bytes without header)
        # Better: read directly from stock
        raw_stk = open(args.stock, 'rb').read()
        stk_dtbo = align(align(stk['hs'], PAGE_SIZE) + stk['rs'], PAGE_SIZE)
        # Find actual DTB start (with the 64-byte header)
        dtb_region_start = None
        for o in range(stk_dtbo, min(stk_dtbo + 256, len(raw_stk))):
            if raw_stk[o:o+4] == b'\xd0\x0d\xfe\xed':
                dtb_region_start = o - 64  # 64-byte header starts 64 bytes before DTB magic
                break
        if dtb_region_start is not None and dtb_region_start >= stk_dtbo:
            dtb_region = raw_stk[dtb_region_start:dtb_region_start + stk['ds']]
            img[ds:ds + stk['ds']] = dtb_region
            print(f"  DTB: {stk['ds']}-byte region (64B header + FDT) at {ds}")
        else:
            # Fallback: just write 64-byte header from stock page0 area + FDT
            print(f"  WARNING: could not find DTB region in stock")

    # fragment 表在 header（page0）内，从 tbl_off 开始
    # fragment 表在 DTB 之后 page 对齐的数据区（不在 header 内）
    # header 的 tbl_off=216 实际是 table size（ec × es）
    tbl_off_file = align(ds + stk['ds'], PAGE_SIZE)
    e0 = bytearray(ENTRY_SIZE)
    struct.pack_into('<I', e0, 0, len(f0))
    struct.pack_into('<I', e0, 4, 0)
    struct.pack_into('<I', e0, 8, 1)
    e0[12:44] = b'\x00' * 32
    img[tbl_off_file:tbl_off_file + ENTRY_SIZE] = e0

    e1 = bytearray(ENTRY_SIZE)
    struct.pack_into('<I', e1, 0, len(f1))
    struct.pack_into('<I', e1, 4, len(f0))
    struct.pack_into('<I', e1, 8, 2)
    e1[12:44] = b'\x00' * 32
    img[tbl_off_file + ENTRY_SIZE:tbl_off_file + 2 * ENTRY_SIZE] = e1

    # header 字段保持原样（tbl_off=216 是 table size，不是偏移）

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PARTITION_SIZE

    print(f"\n输出: {args.output}")
    print(f"  64 MB (F0: {len(f0)/1024/1024:.2f} + F1: {len(f1)/1024/1024:.2f} MB LZ4)")
    print("完成!")

    for d in ['/tmp/rf0', '/tmp/rci1', '/tmp/rf1']:
        if os.path.exists(d): shutil.rmtree(d)
    for f in ['/tmp/rf0.lz4', '/tmp/rf1.lz4']:
        if os.path.exists(f): os.remove(f)

if __name__ == '__main__':
    main()
