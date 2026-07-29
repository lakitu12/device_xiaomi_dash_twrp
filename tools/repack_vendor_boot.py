#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包工具 — for Redmi Turbo 5 Max (dash)

用法:
    python3 tools/repack_vendor_boot.py \\
        --stock    /path/to/stock_vendor_boot.img \\
        --ci       /path/to/ci_vendor_boot.img \\
        --ref      /path/to/reference_vendor_boot.img \\
        [--output  /path/to/output.img]

流程:
  1. 从 stock F0 取 vendor ramdisk → 删 recovery binary + res/
     → 加 twrp16/（9 个 API36 库，从 CI F1 提取）
     → init.rc service recovery 内加 setenv LD_LIBRARY_PATH
  2. 从 CI F1 取 TWRP recovery → 按 ref_f1_whitelist.txt 白名单过滤
     → 白名单有但 CI 缺的文件从 REF F1/F0 补入
  3. 构建 MTK 双 fragment vendor_boot v4 → 64MB 镜像
"""
import struct, subprocess, os, shutil, sys, argparse

PARTITION_SIZE = 67108864
PAGE_SIZE = 4096
ENTRY_SIZE = 108
HS_OFF = 2096
DTB_OFF = 2100
TBL_OFF = 2112
WHITELIST = os.path.join(os.path.dirname(__file__), 'ref_f1_whitelist.txt')


def align(v, a):
    return (v + a - 1) // a * a


def get_frags(img_path):
    """解析 MTK vendor_boot v4 双 fragment 结构。"""
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
    avb = d[-4096:] if d[-8:-4] == b'AVBf' else None
    return {
        'hs': hs, 'ds': ds, 'ro': ro, 'dtbo': dtbo, 'tblo': tblo,
        'frags': frags, 'dtb': dtb_data, 'avb': avb, 'page0': d[:PAGE_SIZE],
    }


def unpack(img, dest):
    """解压 LZ4 cpio 到目录。"""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=img,
                       capture_output=True, timeout=30)
    if r.returncode != 0:
        return None
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True,
                   timeout=30, cwd=dest)
    return r.stdout


def pack_cpio(src):
    """目录打包为 cpio。"""
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=10, cwd=src)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, timeout=60, cwd=src)
    return p.stdout


def pack_lz4(src, out):
    """目录 → cpio → LZ4 -l -9。"""
    cpio = pack_cpio(src)
    if cpio is None:
        return None
    subprocess.run(['lz4', '-l', '-9', '--force', '-', out],
                   input=cpio, capture_output=True, timeout=60)
    return open(out, 'rb').read()


def build_image(f0, f1, dtb, page0, out):
    """构建 64MB MTK vendor_boot v4。"""
    rsz = len(f0) + len(f1)
    rs = PAGE_SIZE
    re = rs + rsz
    ds = align(re, PAGE_SIZE)
    de = ds + len(dtb)
    ts = 2 * ENTRY_SIZE
    to = align(de, PAGE_SIZE)
    img = bytearray(PARTITION_SIZE)
    img[:PAGE_SIZE] = page0[:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)
    struct.pack_into('<I', img, DTB_OFF, len(dtb))
    img[rs:rs + len(f0)] = f0
    img[rs + len(f0):rs + rsz] = f1
    if dtb:
        img[ds:de] = dtb

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
    with open(out, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(out) == PARTITION_SIZE


def load_whitelist(path):
    if not os.path.exists(path):
        print(f"ERROR: whitelist not found: {path}", file=sys.stderr)
        return None
    with open(path) as f:
        return set(l.strip() for l in f if l.strip())


def main():
    ap = argparse.ArgumentParser(description='TWRP vendor_boot repack for dash')
    ap.add_argument('--stock', required=True)
    ap.add_argument('--ci', required=True)
    ap.add_argument('--ref', required=True)
    ap.add_argument('--output', default='/tmp/dash-UNTESTED-vendor_boot.img')
    ap.add_argument('--whitelist', default=WHITELIST)
    args = ap.parse_args()

    for p in [args.stock, args.ci, args.ref]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    ref = get_frags(args.ref)
    stk = get_frags(args.stock)
    ci = get_frags(args.ci)
    print(f"Stock: {len(stk['frags'])} frags, CI: {len(ci['frags'])} frags")

    # ---- F0 ----
    print("\n[F0] Modifying stock vendor ramdisk ...")
    f0_dir = '/tmp/rf0'
    unpack(stk['frags'][0]['payload'], f0_dir)
    for p in [f'{f0_dir}/system/bin/recovery', f'{f0_dir}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)

    ci_f1_dir = '/tmp/rci1'
    unpack(ci['frags'][1]['payload'], ci_f1_dir)
    twrp16 = ['libbase.so', 'libbootloader_message.so', 'libc++.so', 'libcutils.so',
              'libfs_mgr.so', 'liblog.so', 'liblp.so', 'libprotobuf-cpp-lite.so', 'libutils.so']
    tdir = f'{f0_dir}/system/lib64/twrp16'
    os.makedirs(tdir, exist_ok=True)
    for lib in twrp16:
        s = f'{ci_f1_dir}/system/lib64/{lib}'
        if os.path.exists(s):
            shutil.copy2(s, f'{tdir}/{lib}', follow_symlinks=False)

    irc = f'{f0_dir}/system/etc/init/hw/init.rc'
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

    f0 = pack_lz4(f0_dir, '/tmp/rf0.lz4')
    print(f"  F0: {len(f0)/1024/1024:.2f} MB LZ4")

    # ---- F1 ----
    print("\n[F1] Filtering CI recovery by REF whitelist ...")
    wl = load_whitelist(args.whitelist)
    if wl is None:
        sys.exit(1)

    f1_dir = '/tmp/rf1'
    unpack(ci['frags'][1]['payload'], f1_dir)

    for root, dirs, files in os.walk(f1_dir, topdown=False):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.relpath(fp, f1_dir) not in wl:
                os.remove(fp)
        for d in dirs:
            dp = os.path.join(root, d)
            r = os.path.relpath(dp, f1_dir) + '/'
            if r not in wl:
                try:
                    shutil.rmtree(dp)
                except OSError:
                    pass
    for item in list(os.listdir(f1_dir)):
        fp = os.path.join(f1_dir, item)
        r = item + '/' if os.path.isdir(fp) and not os.path.islink(fp) else item
        if r not in wl:
            try:
                (shutil.rmtree if os.path.isdir(fp) and not os.path.islink(fp) else os.remove)(fp)
            except OSError:
                pass

    missing = [r for r in wl if not r.endswith('/')
               and not os.path.exists(os.path.join(f1_dir, r))]
    if missing:
        print(f"  Missing {len(missing)} items from CI, filling from REF ...")
        rf1_dir = '/tmp/rrf1'
        rf0_dir = '/tmp/rrf0'
        unpack(ref['frags'][1]['payload'], rf1_dir)
        unpack(ref['frags'][0]['payload'], rf0_dir)
        for item in missing:
            for sd, lb in [(rf1_dir, 'REF F1'), (rf0_dir, 'REF F0')]:
                src = os.path.join(sd, item)
                dst = os.path.join(f1_dir, item)
                if os.path.lexists(src):
                    dd = os.path.dirname(dst)
                    if os.path.islink(dd):
                        os.unlink(dd)
                    if not os.path.exists(dd):
                        os.makedirs(dd, exist_ok=True)
                    if os.path.lexists(dst):
                        os.unlink(dst)
                    if os.path.islink(src):
                        os.symlink(os.readlink(src), dst)
                    else:
                        shutil.copy2(src, dst, follow_symlinks=False)
                    break
        shutil.rmtree(rf1_dir)
        shutil.rmtree(rf0_dir)

    f1 = pack_lz4(f1_dir, '/tmp/rf1.lz4')
    print(f"  F1: {len(f1)/1024/1024:.2f} MB LZ4")
    print(f"  Total: {(len(f0)+len(f1))/1024/1024:.2f} MB")

    # ---- Build ----
    print("\nBuilding vendor_boot ...")
    build_image(f0, f1, stk['dtb'], stk['page0'], args.output)
    print(f"\nOutput: {args.output}")
    print(f"Size: {os.path.getsize(args.output)/1024/1024:.2f} MB")
    print("Done!")

    for d in ['/tmp/rf0', '/tmp/rci1', '/tmp/rf1', '/tmp/rrf1', '/tmp/rrf0',
              '/tmp/rf0.lz4', '/tmp/rf1.lz4']:
        if os.path.exists(d):
            (shutil.rmtree if os.path.isdir(d) else os.remove)(d)


if __name__ == '__main__':
    main()
