#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包 — 严格按 README

用法:
    python3 tools/repack_vendor_boot.py \\
        --stock /path/to/stock_vendor_boot.img \\
        --ci    /path/to/ci_vendor_boot.img \\
        [--output /path/to/output.img]

流程:
  1. 提取原厂 F0 → 删 recovery + res → 加 twrp16/ → setenv
  2. 提取 CI F1 → 删 init/linker64/adbd/fastbootd/toybox → 删同名库
  3. 按 MTK v4 双 fragment 重打包
"""
import struct, subprocess, os, shutil, sys, argparse

PAGE_SIZE = 4096
ENTRY_SIZE = 108
PARTITION_SIZE = 67108864

def align(v, a):
    return (v + a - 1) // a * a

def read_vendor_boot(path):
    """读取 vendor_boot，按 README 的公式计算 tblo。"""
    d = open(path, 'rb').read()
    hs, ds = struct.unpack_from('<II', d, 2096)
    _, ec, es, _ = struct.unpack_from('<IIII', d, 2112)
    rs = struct.unpack_from('<I', d, 24)[0]
    ro = align(hs, PAGE_SIZE)
    dtbo = align(ro + rs, PAGE_SIZE)
    tblo = align(dtbo + align(ds, PAGE_SIZE), PAGE_SIZE)

    frags = []
    for i in range(ec):
        eo = tblo + i * es
        if eo + 12 > len(d):
            break
        sz, off, typ = struct.unpack_from('<III', d, eo)
        name = d[eo + 12:eo + 44].rstrip(b'\x00').decode()
        if sz > 0 and ro + off + sz <= len(d):
            payload = d[ro + off:ro + off + sz]
        else:
            payload = b''
        frags.append({'sz': sz, 'off': off, 'typ': typ, 'name': name, 'payload': payload})

    return {
        'raw': d, 'page0': d[:PAGE_SIZE],
        'hs': hs, 'ds': ds, 'rs': rs,
        'ro': ro, 'dtbo': dtbo, 'tblo': tblo,
        'ec': ec, 'es': es, 'frags': frags,
    }

def unpack(data, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin', '--favor-decSpeed'],
                       input=data, capture_output=True, timeout=30)
    if r.returncode != 0:
        return None
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, timeout=30, cwd=dest)
    return r.stdout

def pack(src, tmp):
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=10, cwd=src)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, timeout=60, cwd=src)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', tmp],
                   input=p.stdout, capture_output=True, timeout=60)
    return open(tmp, 'rb').read()

def main():
    ap = argparse.ArgumentParser(description='TWRP vendor_boot repack for dash')
    ap.add_argument('--stock', required=True)
    ap.add_argument('--ci', required=True)
    ap.add_argument('--output', default='/tmp/dash-UNTESTED-vendor_boot.img')
    args = ap.parse_args()
    for p in [args.stock, args.ci]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    stk = read_vendor_boot(args.stock)
    ci = read_vendor_boot(args.ci)
    print(f"Stock: {len(stk['frags'])} frags, CI: {len(ci['frags'])} frags")

    # ======== 1. 改造 F0 ========
    print("\n[1] 改造 stock vendor ramdisk ...")
    f0d = '/tmp/rf0'
    unpack(stk['frags'][0]['payload'], f0d)

    for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
            print(f"  removed {os.path.basename(p)}")

    ci1d = '/tmp/rci1'
    unpack(ci['frags'][1]['payload'], ci1d)
    tardir = f'{f0d}/system/lib64/twrp16'
    os.makedirs(tardir, exist_ok=True)
    for lib in ['libbase.so', 'libbootloader_message.so', 'libc++.so',
                'libcutils.so', 'libfs_mgr.so', 'liblog.so', 'liblp.so',
                'libprotobuf-cpp-lite.so', 'libutils.so']:
        s = os.path.join(ci1d, 'system/lib64', lib)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(tardir, lib), follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(tardir))} libs)")

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

    f0_new = pack(f0d, '/tmp/rf0.lz4')
    print(f"  F0: {len(f0_new)/1024/1024:.2f} MB LZ4")

    # ======== 2. 裁剪 CI F1 ========
    print("\n[2] 裁剪 CI recovery ...")
    f1d = '/tmp/rf1'
    unpack(ci['frags'][1]['payload'], f1d)

    for f in ['system/bin/init', 'system/bin/linker64', 'system/bin/adbd',
              'system/bin/fastbootd', 'system/bin/toybox']:
        p = os.path.join(f1d, f)
        if os.path.exists(p):
            os.remove(p)
            print(f"  removed {os.path.basename(f)}")

    # 删同名库
    f1ld = f'{f1d}/system/lib64'
    f0ld = f'{f0d}/system/lib64'
    if os.path.isdir(f0ld) and os.path.isdir(f1ld):
        f0_has = {l for l in os.listdir(f0ld)
                  if os.path.isfile(os.path.join(f0ld, l)) and not os.path.islink(os.path.join(f0ld, l))}
        dc = 0; ds = 0; kc = 0; ks = 0
        for lib in list(os.listdir(f1ld)):
            if lib == 'twrp16':
                continue
            fp = os.path.join(f1ld, lib)
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            if lib in f0_has:
                sz = os.path.getsize(fp)
                os.remove(fp)
                dc += 1; ds += sz
            else:
                kc += 1; ks += os.path.getsize(fp)
        print(f"  lib64: kept {kc} ({ks/1024/1024:.2f} MB), deleted {dc} ({ds/1024/1024:.2f} MB)")

    # 补 CI 缺失的库（libresetprop.so 等）：从原厂 F1 或参考提取
    stk1d = '/tmp/rstk1'
    if stk['frags'][1]['payload']:
        unpack(stk['frags'][1]['payload'], stk1d)
        for lib in ['libresetprop.so']:
            src = os.path.join(stk1d, 'system/lib64', lib)
            dst = os.path.join(f1ld, lib)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst, follow_symlinks=False)
                print(f"  + missing lib from stock F1: {lib}")
            elif not os.path.exists(src) and not os.path.exists(dst):
                # CI 和原厂都没有，从参考提取的预置文件复制
                fallback = os.path.expanduser(f'/home/lakitu/dash_twrp_local/prebuilt/{lib}')
                if os.path.exists(fallback):
                    shutil.copy2(fallback, dst, follow_symlinks=False)
                    print(f"  + missing lib from prebuilt: {lib}")
        shutil.rmtree(stk1d)

    f1_new = pack(f1d, '/tmp/rf1.lz4')
    print(f"  F1: {len(f1_new)/1024/1024:.2f} MB LZ4")
    print(f"  Total: {(len(f0_new)+len(f1_new))/1024/1024:.2f} MB")

    # ======== 3. 构建 vendor_boot ========
    print("\n[3] 构建 vendor_boot ...")
    rsz = len(f0_new) + len(f1_new)
    ro = align(stk['hs'], PAGE_SIZE)
    dtbo = align(ro + rsz, PAGE_SIZE)
    tblo = align(dtbo + align(stk['ds'], PAGE_SIZE), PAGE_SIZE)

    # DTB 从原厂复制（含 64B MTK 表头）
    stk_raw = open(args.stock, 'rb').read()
    dtb_start = None
    for o in range(stk['dtbo'], min(stk['dtbo'] + 256, len(stk_raw))):
        if stk_raw[o:o+4] == b'\xd0\x0d\xfe\xed':
            dtb_start = o - 64
            break
    if dtb_start is not None and dtb_start >= stk['dtbo']:
        dtb_data = stk_raw[dtb_start:dtb_start + stk['ds']]
    else:
        print("ERROR: DTB not found in stock", file=sys.stderr)
        sys.exit(1)

    img = bytearray(PARTITION_SIZE)
    img[:PAGE_SIZE] = stk['page0'][:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)
    img[ro:ro + len(f0_new)] = f0_new
    img[ro + len(f0_new):ro + rsz] = f1_new
    img[dtbo:dtbo + stk['ds']] = dtb_data

    # fragment 表
    for i, (data, off) in enumerate([(f0_new, 0), (f1_new, len(f0_new))]):
        eo = tblo + i * stk['es']
        struct.pack_into('<I', img, eo, len(data))
        struct.pack_into('<I', img, eo + 4, off)
        struct.pack_into('<I', img, eo + 8, i + 1)
        img[eo + 12:eo + 44] = b'\x00' * 32

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PARTITION_SIZE

    print(f"  输出: {args.output}")
    print(f"  64 MB (F0: {len(f0_new)/1024/1024:.2f} + F1: {len(f1_new)/1024/1024:.2f} MB LZ4)")

    for d in ['/tmp/rf0', '/tmp/rci1', '/tmp/rf1']:
        if os.path.exists(d): shutil.rmtree(d)
    for f in ['/tmp/rf0.lz4', '/tmp/rf1.lz4']:
        if os.path.exists(f): os.remove(f)

if __name__ == '__main__':
    main()
