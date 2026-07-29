#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包工具 — for Redmi Turbo 5 Max (dash)

用法:
    python3 tools/repack_vendor_boot.py \\
        --stock /path/to/stock_vendor_boot.img \\
        --ci    /path/to/ci_vendor_boot.img \\
        [--output /path/to/output.img]

流程:
  1. 从 stock F0 取 vendor ramdisk → 删 recovery binary + res/
     → 加 twrp16/（9 个 API36 库，从 CI 提取）
     → init.rc service recovery 内加 setenv LD_LIBRARY_PATH
  2. 从 stock F1 取 stock recovery → 取 first_stage_ramdisk、vintf、fastboot 服务
  3. 从 CI F1 取 TWRP recovery + 独有 lib（不在 F0 中的）+ twres/
  4. 合并 F1：stock F1 基础设施 + CI TWRP 内容
  5. 构建 MTK 双 fragment vendor_boot v4 → 64MB 镜像
"""
import struct, subprocess, os, shutil, sys, argparse

PARTITION_SIZE = 67108864
PAGE_SIZE = 4096
ENTRY_SIZE = 108
HS_OFF = 2096
DTB_OFF = 2100
TBL_OFF = 2112


def align(v, a):
    return (v + a - 1) // a * a


def get_frags(img_path):
    """解析 MTK vendor_boot v4 结构。"""
    with open(img_path, 'rb') as f:
        d = f.read()
    hs = struct.unpack_from('<I', d, HS_OFF)[0]
    ds = struct.unpack_from('<I', d, DTB_OFF)[0]
    rs = struct.unpack_from('<I', d, 24)[0]
    ec = struct.unpack_from('<I', d, TBL_OFF + 4)[0]
    es = struct.unpack_from('<I', d, TBL_OFF + 8)[0]
    ro = align(hs, PAGE_SIZE)
    dtbo = align(ro + rs, PAGE_SIZE)
    tblo = align(dtbo + align(ds, PAGE_SIZE), PAGE_SIZE)
    frags = []
    for i in range(ec):
        eo = tblo + i * es
        sz = struct.unpack_from('<I', d, eo)[0]
        off = struct.unpack_from('<I', d, eo + 4)[0]
        typ = struct.unpack_from('<I', d, eo + 8)[0]
        name = d[eo + 12:eo + 44].rstrip(b'\x00').decode()
        payload = d[ro + off:ro + off + sz]
        frags.append({'size': sz, 'offset': off, 'type': typ, 'name': name, 'payload': payload})
    dtb_data = b''
    for o in range(dtbo, min(dtbo + 8192, len(d))):
        if d[o:o + 4] == b'\xd0\x0d\xfe\xed':
            dtb_sz = struct.unpack_from('>I', d, o + 4)[0]
            dtb_data = d[o:o + dtb_sz]
            break
    return {
        'frags': frags, 'dtb': dtb_data, 'page0': d[:PAGE_SIZE],
        'hs': hs, 'ds': ds, 'ro': ro,
    }


def unpack_lz4(data, dest):
    """解压 LZ4 cpio 到目录。"""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=data,
                       capture_output=True, timeout=30)
    if r.returncode != 0:
        return None
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True,
                   timeout=30, cwd=dest)
    return r.stdout


def pack_cpio(src):
    """目录 → cpio。"""
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=10, cwd=src)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, timeout=60, cwd=src)
    return p.stdout


def pack_lz4(src, tmp):
    """目录 → cpio → LZ4 -l -9，返回 LZ4 bytes。"""
    cpio = pack_cpio(src)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', tmp],
                   input=cpio, capture_output=True, timeout=60)
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

    # ---- F0: 改造 stock vendor ramdisk ----
    print("\n[F0] 改造 stock vendor ramdisk ...")
    f0d = '/tmp/rf0'
    unpack_lz4(stk['frags'][0]['payload'], f0d)

    # 删 recovery binary + res/
    for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
            print(f"  removed {os.path.basename(p)}")

    # 加 twrp16/（从 CI F1 提取）
    ci1d = '/tmp/rci1'
    unpack_lz4(ci['frags'][1]['payload'], ci1d)
    twrp16 = ['libbase.so', 'libbootloader_message.so', 'libc++.so', 'libcutils.so',
              'libfs_mgr.so', 'liblog.so', 'liblp.so', 'libprotobuf-cpp-lite.so', 'libutils.so']
    tdir = f'{f0d}/system/lib64/twrp16'
    os.makedirs(tdir, exist_ok=True)
    for lib in twrp16:
        s = f'{ci1d}/system/lib64/{lib}'
        if os.path.exists(s):
            shutil.copy2(s, f'{tdir}/{lib}', follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(tdir))} libs)")

    # init.rc setenv
    irc = f'{f0d}/system/etc/init/hw/init.rc'
    if os.path.exists(irc):
        with open(irc) as f:
            c = f.read()
        tag = 'service recovery /system/bin/recovery\n' \
              '    socket recovery stream 422 system system\n' \
              '    seclabel u:r:recovery:s0\n    user root'
        if tag in c:
            c = c.replace(tag, tag + '\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
            with open(irc, 'w') as f:
                f.write(c)
            print("  + setenv in init.rc")

    f0 = pack_lz4(f0d, '/tmp/rf0.lz4')
    print(f"  F0: {len(f0)/1024/1024:.2f} MB LZ4")

    # ---- F1: CI F1 base + stock 基础设施 ----
    print("\n[F1] 构建 recovery ramdisk ...")
    f1d = '/tmp/rf1'

    # 基础：CI F1（TWRP 核心）
    unpack_lz4(ci['frags'][1]['payload'], f1d)
    print(f"  base: CI F1 ({len(ci['frags'][1]['payload'])/1024/1024:.2f} MB LZ4)")

    # 删 CI F1 中 system/lib64/ 下 F0 已有的库（同名不论版本）
    # 删除 F1 中 F0 也有的同名库以节省空间
    # 但保留 NDK/HAL 后端库（android.hardware.*.so, *-ndk.so, libbinder_ndk.so）
    # 因为这些是 API 版本特定的，F0 的 API 35 版与 API 36 二进制不兼容
    f0_lib64 = f'{f0d}/system/lib64'
    f1_lib64 = f'{f1d}/system/lib64'
    deleted_ct = 0
    deleted_sz = 0
    kept_ct = 0
    kept_sz = 0
    def _is_api_specific(lib):
        return (lib.startswith('android.hardware.')
                or lib.endswith('-ndk.so')
                or lib == 'libbinder_ndk.so')
    if os.path.isdir(f0_lib64) and os.path.isdir(f1_lib64):
        f0_map = {}
        for lib in os.listdir(f0_lib64):
            fp = os.path.join(f0_lib64, lib)
            if os.path.isfile(fp) and not os.path.islink(fp):
                f0_map[lib] = os.path.getsize(fp)
        for lib in list(os.listdir(f1_lib64)):
            if lib == 'twrp16':
                continue
            fp = os.path.join(f1_lib64, lib)
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            if lib in f0_map and not _is_api_specific(lib):
                sz = os.path.getsize(fp)
                os.remove(fp)
                deleted_ct += 1
                deleted_sz += sz
            else:
                sz = os.path.getsize(fp)
                kept_ct += 1
                kept_sz += sz
        print(f"  system/lib64/: kept {kept_ct} unique ({kept_sz/1024/1024:.2f} MB), "
              f"deleted {deleted_ct} (from F0, {deleted_sz/1024/1024:.2f} MB)")

    # 在 F1 init.rc 中添加 setenv LD_LIBRARY_PATH
    # dmesg 显示 /init.recovery.service.rc 先被解析，它的 service recovery 才是生效的
    # /system/etc/init/hw/init.rc 的重复定义被 ignore，所以两个都要加
    for irc_rel in ['init.recovery.service.rc', 'system/etc/init/hw/init.rc']:
        irc = f'{f1d}/{irc_rel}'
        if os.path.exists(irc):
            with open(irc) as f:
                c = f.read()
            tag = 'service recovery /system/bin/recovery\n' \
                  '    socket recovery stream 422 system system\n' \
                  '    seclabel u:r:recovery:s0\n    user root'
            if tag in c:
                c = c.replace(tag, tag + '\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
                with open(irc, 'w') as f:
                    f.write(c)
                print(f"  + setenv in F1 {irc_rel}")

    # 删 CI F1 system/bin/ 中非必要的工具（REF 只保留 26 个关键工具）
    bin_keep = {'recovery', 'dmctl', 'e2fsck', 'minadbd', 'sgdisk', 'awk', 'bc',
                'resize2fs', 'pigz', 'unpigz', 'tune2fs', 'ziptool', 'fsck.fat',
                'bu', 'mkfs.fat', 'bash', 'sh', 'touch_report_debug',
                'sload_f2fs', 'mkfs.f2fs', 'fsck.f2fs', 'sgdisk', 'parted',
                'mke2fs', 'e2fsdroid', 'blkid'}
    f1_bin = f'{f1d}/system/bin'
    if os.path.isdir(f1_bin):
        bin_del = 0
        bin_del_sz = 0
        for f in list(os.listdir(f1_bin)):
            fp = os.path.join(f1_bin, f)
            if os.path.isfile(fp) and f not in bin_keep and not os.path.islink(fp):
                sz = os.path.getsize(fp)
                os.remove(fp)
                bin_del += 1
                bin_del_sz += sz
        print(f"  system/bin/: deleted {bin_del} tools ({bin_del_sz/1024/1024:.2f} MB), "
              f"kept critical tools")

    # 叠 stock F1 基础设施：first_stage_ramdisk、vintf manifest、odm、fastboot 服务
    if len(stk['frags']) > 1 and stk['frags'][1]['size'] > 0:
        stk1d = '/tmp/rstk1'
        unpack_lz4(stk['frags'][1]['payload'], stk1d)
        # 只复制 CI F1 没有的关键基础设施文件
        infra_dirs = [
            'first_stage_ramdisk', 'lib/modules',
            'odm/firmware', 'odm/lib64',
            'system/etc/vintf/manifest',
            'vendor/etc/vintf/manifest',
            'system/lib64/stock-vendor-hal',
            'system/bin/hw',
            'system/etc/init',
        ]
        for dir_rel in infra_dirs:
            src_d = os.path.join(stk1d, dir_rel)
            if not os.path.exists(src_d):
                continue
            for root, dirs, files in os.walk(src_d):
                # Skip files that already exist in CI F1
                rel = os.path.relpath(root, stk1d)
                for f in files:
                    src_f = os.path.join(root, f)
                    dst_f = os.path.join(f1d, rel, f)
                    if os.path.exists(dst_f) or os.path.lexists(dst_f):
                        continue
                    # Ensure parent chain exists; remove any symlink in the
                    # way that CI F1 uses as a mount-point placeholder
                    parent = os.path.dirname(dst_f)
                    if not os.path.isdir(parent):
                        if os.path.lexists(parent):
                            os.unlink(parent)
                        os.makedirs(parent, exist_ok=True)
                    if os.path.islink(src_f):
                        os.symlink(os.readlink(src_f), dst_f)
                    else:
                        shutil.copy2(src_f, dst_f, follow_symlinks=False)

        # 补 stock 的 system/bin/ 工具（CI 没有的）
        for f in ['touch_report_debug']:
            src = f'{stk1d}/system/bin/{f}'
            dst = f'{f1d}/system/bin/{f}'
            if os.path.exists(src) and not os.path.lexists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)

        # 补 recovery 二进制 NEEDED 但 CI F1 缺失的库（如 libresetprop.so）
        f1_lib64 = f'{f1d}/system/lib64'
        needed = subprocess.run(
            ['aarch64-linux-gnu-readelf', '-d', f'{f1d}/system/bin/recovery'],
            capture_output=True, timeout=15, text=True)
        missing_libs = []
        for line in needed.stdout.split('\n'):
            if 'NEEDED' not in line:
                continue
            lib = line.split('[')[1].split(']')[0]
            lib_path = f'{f1_lib64}/{lib}'
            if not os.path.exists(lib_path):
                # Try stock F1
                stk_lib = f'{stk1d}/system/lib64/{lib}'
                if os.path.exists(stk_lib):
                    os.makedirs(f1_lib64, exist_ok=True)
                    shutil.copy2(stk_lib, lib_path, follow_symlinks=False)
                    missing_libs.append(lib)
                    print(f"  + missing lib from stock F1: {lib}")
                else:
                    print(f"  WARNING: {lib} not found in stock F1 either!")

        shutil.rmtree(stk1d)
        print(f"  + stock infrastructure (first_stage_ramdisk, modules, odm, vintf)")

    # 补 init、sbin/sh 等 symlink（从 F0）
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

    # ---- 构建 vendor_boot ----
    print("\n构建 vendor_boot ...")
    rsz = len(f0) + len(f1)
    rs = PAGE_SIZE
    re = rs + rsz
    ds = align(re, PAGE_SIZE)
    de = ds + len(stk['dtb'])
    ts = 2 * ENTRY_SIZE
    to = align(de, PAGE_SIZE)

    img = bytearray(PARTITION_SIZE)
    img[:PAGE_SIZE] = stk['page0'][:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)
    struct.pack_into('<I', img, DTB_OFF, len(stk['dtb']))
    img[rs:rs + len(f0)] = f0
    img[rs + len(f0):rs + rsz] = f1
    if stk['dtb']:
        img[ds:de] = stk['dtb']

    def entry(sz, off, typ, name):
        e = bytearray(ENTRY_SIZE)
        struct.pack_into('<I', e, 0, sz)
        struct.pack_into('<I', e, 4, off)
        struct.pack_into('<I', e, 8, typ)
        nb = name.encode()[:31] + b'\x00'
        e[12:12 + len(nb)] = nb
        return e

    img[to:to + ENTRY_SIZE] = entry(len(f0), 0, 1, '')
    img[to + ENTRY_SIZE:to + 2 * ENTRY_SIZE] = entry(len(f1), len(f0), 2, '')
    struct.pack_into('<I', img, TBL_OFF, ts)
    struct.pack_into('<I', img, TBL_OFF + 4, 2)
    struct.pack_into('<I', img, TBL_OFF + 8, ENTRY_SIZE)

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PARTITION_SIZE

    print(f"\n输出: {args.output}")
    print(f"大小: {PARTITION_SIZE/1024/1024:.0f} MB")
    print(f"  F0: {len(f0)/1024/1024:.2f} MB (vendor, twrp16/, setenv)")
    print(f"  F1: {len(f1)/1024/1024:.2f} MB (TWRP)")
    print("完成!")

    # Cleanup
    for d in ['/tmp/rf0', '/tmp/rci1', '/tmp/rf1']:
        if os.path.exists(d):
            shutil.rmtree(d)
    for f in ['/tmp/rf0.lz4', '/tmp/rf1.lz4']:
        if os.path.exists(f):
            os.remove(f)


if __name__ == '__main__':
    main()
