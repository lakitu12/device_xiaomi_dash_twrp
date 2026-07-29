#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包 — 直接修改原厂双 fragment 镜像

用法:
    python3 tools/repack_vendor_boot.py \\
        --stock /path/to/stock_vendor_boot.img \\
        --ci    /path/to/ci_vendor_boot.img \\
        [--output /path/to/output.img]

流程:
  1. 从原厂 F0 删 recovery binary + res/ → 加 twrp16/ → setenv
  2. 从 CI F1 删 init/linker64/adbd/fastbootd/toybox → 删同名库
  3. 写回原厂镜像（page0/DTB 保持不变）
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

def read_stock(img_path):
    """解析原厂 MTK 双 fragment vendor_boot"""
    d = open(img_path, 'rb').read()
    page0 = d[:PAGE_SIZE]
    hs = struct.unpack_from('<I', page0, HS_OFF)[0]
    ds = struct.unpack_from('<I', page0, DTB_OFF)[0]
    rs = struct.unpack_from('<I', page0, 24)[0]
    ro = align(hs, PAGE_SIZE)
    dtbo = align(ro + rs, PAGE_SIZE)
    ec = struct.unpack_from('<I', page0, TBL_OFF + 4)[0]
    es = struct.unpack_from('<I', page0, TBL_OFF + 8)[0]
    tblo = align(dtbo + ds, PAGE_SIZE)

    frags = []
    for i in range(ec):
        eo = tblo + i * es
        sz = struct.unpack_from('<I', d, eo)[0]
        off = struct.unpack_from('<I', d, eo + 4)[0]
        typ = struct.unpack_from('<I', d, eo + 8)[0]
        name = d[eo + 12:eo + 44].rstrip(b'\x00').decode()
        if sz > 0 and ro + off + sz <= len(d):
            payload = d[ro + off:ro + off + sz]
        else:
            payload = b''
        frags.append({'size': sz, 'offset': off, 'type': typ, 'name': name, 'payload': payload})

    return {
        'header': page0, 'stk': d, 'frags': frags,
        'hs': hs, 'ds': ds, 'rs': rs, 'ro': ro,
        'dtbo': dtbo, 'tblo': tblo, 'ec': ec, 'es': es,
    }

def unpack_lz4(data, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin', '--favor-decSpeed'],
                       input=data, capture_output=True, timeout=30)
    if r.returncode != 0:
        return None
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, timeout=30, cwd=dest)
    return r.stdout

def pack_lz4(src, tmp):
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=10, cwd=src)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, timeout=60, cwd=src)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', tmp],
                   input=p.stdout, capture_output=True, timeout=60)
    return open(tmp, 'rb').read()

def main():
    ap = argparse.ArgumentParser(description='TWRP vendor_boot repack for dash')
    ap.add_argument('--stock', required=True, help='原厂 vendor_boot.img')
    ap.add_argument('--ci', required=True, help='CI 构建产物 vendor_boot.img')
    ap.add_argument('--output', default='/tmp/dash-UNTESTED-vendor_boot.img')
    args = ap.parse_args()
    for p in [args.stock, args.ci]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr); sys.exit(1)

    print("读取原厂镜像...")
    stk = read_stock(args.stock)
    print(f"  F0: {len(stk['frags'][0]['payload'])/1024/1024:.2f} MB LZ4")
    print(f"  F1: {len(stk['frags'][1]['payload'])/1024/1024:.2f} MB LZ4")

    # 读取 CI
    ci = read_stock(args.ci)
    ci_f1 = ci['frags'][1]['payload']
    print(f"CI F1: {len(ci_f1)/1024/1024:.2f} MB LZ4")

    # ======== 改造 F0 ========
    print("\n[F0] 改造 stock vendor ramdisk ...")
    f0d = '/tmp/rf0'
    unpack_lz4(stk['frags'][0]['payload'], f0d)

    for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
            print(f"  removed {os.path.basename(p)}")

    # twrp16/
    ci1d = '/tmp/rci1'
    unpack_lz4(ci_f1, ci1d)
    twrp16_libs = ['libbase.so', 'libbootloader_message.so', 'libc++.so', 'libcutils.so',
                   'libfs_mgr.so', 'liblog.so', 'liblp.so', 'libprotobuf-cpp-lite.so', 'libutils.so']
    tdir = f'{f0d}/system/lib64/twrp16'
    os.makedirs(tdir, exist_ok=True)
    for lib in twrp16_libs:
        s = f'{ci1d}/system/lib64/{lib}'
        if os.path.exists(s):
            shutil.copy2(s, f'{tdir}/{lib}', follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(tdir))} libs)")

    # setenv
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

    f0_new = pack_lz4(f0d, '/tmp/rf0.lz4')
    print(f"  F0 new: {len(f0_new)/1024/1024:.2f} MB LZ4")

    # ======== 裁剪 CI F1 ========
    print("\n[F1] 裁剪 CI recovery ...")
    f1d = '/tmp/rf1'
    unpack_lz4(ci_f1, f1d)

    # 删核心组件
    for rel in ['system/bin/init', 'system/bin/linker64', 'system/bin/adbd',
                'system/bin/fastbootd', 'system/bin/toybox']:
        p = os.path.join(f1d, rel)
        if os.path.exists(p):
            os.remove(p)
            print(f"  removed {os.path.basename(rel)}")

    # 删同名库，但保护 API 版本特定的库（NDK/HAL 后端 + libbinder/libbinder_ndk）
    f0_lib64 = f'{f0d}/system/lib64'
    f1_lib64 = f'{f1d}/system/lib64'
    del_ct = 0; del_sz = 0; keep_ct = 0; keep_sz = 0
    f0_libs = set()
    def _protected_lib(lib):
        return (lib.startswith('android.hardware.')
                or lib.endswith('-ndk.so')
                or lib in ('libbinder_ndk.so', 'libbinder.so'))
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
            if lib in f0_libs and not _protected_lib(lib):
                sz = os.path.getsize(fp)
                os.remove(fp)
                del_ct += 1; del_sz += sz
            else:
                keep_ct += 1; keep_sz += os.path.getsize(fp)
        print(f"  system/lib64/: kept {keep_ct} ({keep_sz/1024/1024:.2f} MB), "
              f"deleted {del_ct} (from F0, {del_sz/1024/1024:.2f} MB)")

    # setenv 在 F1 的 init.rc
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

    # 补 init/sh 等 symlink
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

    # 补 stock F1 基础设施（参考镜像包含这些）
    # 原厂可能是单 ramdisk 格式或双 fragment。从有效的地方提取
    stk_infra_src = '/tmp/rstk_infra'
    if len(stk['frags']) > 1 and stk['frags'][1]['size'] > 0:
        unpack_lz4(stk['frags'][1]['payload'], stk_infra_src)
    elif stk['rs'] > 0:
        # 单 ramdisk 格式：整个 ramdisk 包含基础设施
        unpack_lz4(stk['stk'][stk['ro']:stk['ro'] + stk['rs']], stk_infra_src)
    
    if os.path.exists(stk_infra_src):
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
            src_d = os.path.join(stk_infra_src, dir_rel)
            if not os.path.exists(src_d):
                continue
            for root, dirs, files in os.walk(src_d):
                rel = os.path.relpath(root, stk_infra_src)
                for f in files:
                    src_f = os.path.join(root, f)
                    dst_f = os.path.join(f1d, rel, f)
                    if os.path.lexists(dst_f):
                        continue
                    parent = os.path.dirname(dst_f)
                    if not os.path.isdir(parent):
                        if os.path.lexists(parent):
                            os.unlink(parent)
                        os.makedirs(parent, exist_ok=True)
                    if os.path.islink(src_f):
                        os.symlink(os.readlink(src_f), dst_f)
                    else:
                        shutil.copy2(src_f, dst_f, follow_symlinks=False)

        # 补 recovery 二进制 NEEDED 但 F1 缺失且 F0/twrp16 也不提供的库
        needed = subprocess.run(
            ['aarch64-linux-gnu-readelf', '-d', f'{f1d}/system/bin/recovery'],
            capture_output=True, timeout=15, text=True)
        for line in needed.stdout.split('\n'):
            if 'NEEDED' not in line:
                continue
            lib = line.split('[')[1].split(']')[0]
            lib_path = os.path.join(f1d, 'system/lib64', lib)
            if os.path.exists(lib_path):
                continue  # 已在 F1 中
            # 跳过在 twrp16/ 中的（可通过 LD_LIBRARY_PATH 找到）
            twrp16_path = os.path.join(f1d, 'system/lib64/twrp16', lib)
            if os.path.exists(twrp16_path):
                continue
            # 跳过在 F0 中的（可通过 LD_LIBRARY_PATH fallback 找到）
            if lib in f0_libs:
                continue
            stk_lib = os.path.join(stk_infra_src, 'system/lib64', lib)
            if os.path.exists(stk_lib):
                os.makedirs(os.path.dirname(lib_path), exist_ok=True)
                shutil.copy2(stk_lib, lib_path, follow_symlinks=False)
                print(f"  + missing lib from stock: {lib}")

        shutil.rmtree(stk_infra_src)

    f1_new = pack_lz4(f1d, '/tmp/rf1.lz4')
    print(f"  F1 new: {len(f1_new)/1024/1024:.2f} MB LZ4")
    total = len(f0_new) + len(f1_new)
    print(f"  Total: {total/1024/1024:.2f} MB / 64 MB")

    if total + align(len(stk['frags'][0]['payload'])+len(stk['frags'][1]['payload']), PAGE_SIZE) > PARTITION_SIZE:
        print("  WARNING: may exceed partition!")

    # ======== 构建 ========
    print("\n构建 vendor_boot ...")
    img = bytearray(partition_size := PARTITION_SIZE)
    img[:PAGE_SIZE] = stk['header'][:PAGE_SIZE]
    
    # 新 ramdisk 数据
    rsz = len(f0_new) + len(f1_new)
    ro_new = align(stk['hs'], PAGE_SIZE)
    img[ro_new:ro_new + len(f0_new)] = f0_new
    img[ro_new + len(f0_new):ro_new + rsz] = f1_new
    
    # DTB 紧跟新 ramdisk 之后（page 对齐）
    dtbo_new = align(ro_new + rsz, PAGE_SIZE)
    # DTB 数据从原厂复制（64B MTK 表头 + FDT）
    stk_raw = open(args.stock, 'rb').read()
    # 在原厂中找到 DTB magic 位置
    dtb_start = None
    for o in range(stk['dtbo'], min(stk['dtbo'] + 256, len(stk_raw))):
        if stk_raw[o:o+4] == b'\xd0\x0d\xfe\xed':
            dtb_start = o - 64  # 含 64B 表头
            break
    if dtb_start is not None and dtb_start >= stk['dtbo']:
        dtb_data = stk_raw[dtb_start:dtb_start + stk['ds']]
        img[dtbo_new:dtbo_new + stk['ds']] = dtb_data
    else:
        print("  WARNING: DTB not found!")
        dtbo_new = stk['dtbo']  # fallback

    # fragment 表紧跟 DTB 之后（page 对齐）
    tblo_new = align(dtbo_new + stk['ds'], PAGE_SIZE)
    for i, frag_data, frag_off in [
        (0, f0_new, 0),
        (1, f1_new, len(f0_new)),
    ]:
        eo = tblo_new + i * stk['es']
        struct.pack_into('<I', img, eo, len(frag_data))
        struct.pack_into('<I', img, eo + 4, frag_off)
        struct.pack_into('<I', img, eo + 8, i + 1)  # type=1,2
        # name 留空（全零）

    # 更新 header
    struct.pack_into('<I', img, 24, rsz)

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PARTITION_SIZE

    print(f"\n输出: {args.output}")
    print(f"  64 MB (F0: {len(f0_new)/1024/1024:.2f} + F1: {len(f1_new)/1024/1024:.2f} MB LZ4)")

    # Cleanup
    for d in ['/tmp/rf0', '/tmp/rci1', '/tmp/rf1']:
        if os.path.exists(d): shutil.rmtree(d)
    for f in ['/tmp/rf0.lz4', '/tmp/rf1.lz4']:
        if os.path.exists(f): os.remove(f)

if __name__ == '__main__':
    main()
