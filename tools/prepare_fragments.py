#!/usr/bin/env python3
"""
准备 vendor_boot fragment 的 LZ4 压缩包和 manifest。

输出三个文件：
  <prefix>.f0.lz4       — 改造后的 F0 fragment (stock + twrp16 + setenv)
  <prefix>.f1.lz4       — 改造后的 F1 fragment (validated F1 + staging overlay + RC)
  <prefix>.manifest.json — stock_aware_repack_tool 的 --replacement-manifest

用法：
  python3 tools/prepare_fragments.py \
    --stock stock.img \
    --ci ci.img \
    --validated validated.img \
    --staging out/target/product/dash/recovery/root \
    --dtroot /path/to/device/tree \
    --prefix /tmp/fragments

然后用 stock_aware_repack_tool 重打包：
  repack/stock_aware_repack_tool \
    --template stock.img \
    --vbmeta-owner vbmeta.img \
    --replacement-manifest /tmp/fragments.manifest.json \
    --output final.img \
    --report report.json \
    --budget-output budget.json
"""

import struct, subprocess, os, shutil, sys, argparse, tempfile, json

PAGE_SIZE = 4096

TWRP16_LIBS = [
    'libbase.so', 'libbootloader_message.so', 'libc++.so', 'libcutils.so',
    'libfs_mgr.so', 'liblog.so', 'liblp.so', 'libprotobuf-cpp-lite.so', 'libutils.so',
]

DT_OVERLAY_RCS = ['init.recovery.mt6991.rc', 'init.recovery.project.rc']

STAGING_OVERLAY_FILES = [
    'system/bin/recovery',
    'system/etc/recovery.fstab',
    'twres/languages/en.xml',
]


def align(v, a):
    return (v + a - 1) // a * a

def parse(path):
    d = open(path, 'rb').read()
    hs = struct.unpack_from('<I', d, 2096)[0]
    ds = struct.unpack_from('<I', d, 2100)[0]
    rs = struct.unpack_from('<I', d, 24)[0]
    ro = align(hs, PAGE_SIZE)
    dtbo = align(ro + rs, PAGE_SIZE)
    tblo = align(dtbo + align(ds, PAGE_SIZE), PAGE_SIZE)
    ec = struct.unpack_from('<I', d, 2116)[0]
    es = struct.unpack_from('<I', d, 2120)[0]
    frags = []
    for i in range(ec):
        eo = tblo + i * es
        sz, off, typ = struct.unpack_from('<III', d, eo)
        frags.append(d[ro + off:ro + off + sz])
    return frags

def pack_cpio_lz4(src_dir):
    """打包目录为 LZ4 legacy 格式的 CPIO fragment。"""
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, cwd=src_dir)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, cwd=src_dir)
    result = subprocess.run(['lz4', '-l', '-9', '--force', '-', '-'],
                            input=p.stdout, capture_output=True)
    return result.stdout

def decompress_fragment(frag_data, dest_dir):
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=frag_data, capture_output=True)
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, cwd=dest_dir)


def main():
    ap = argparse.ArgumentParser(description='Prepare F0+F1 LZ4 fragments and manifest for stock_aware_repack_tool')
    ap.add_argument('--stock', required=True, help='原厂 vendor_boot.img')
    ap.add_argument('--ci', required=True, help='CI 构建产物 vendor_boot.img（提取 twrp16 库）')
    ap.add_argument('--validated', required=True, help='已验证可工作的 vendor_boot.img（提取 F1）')
    ap.add_argument('--staging', default=None, help='可选：m recovery vendorbootimage 的 staging 目录')
    ap.add_argument('--dtroot', default=None, help='设备树根目录（默认脚本上级目录）')
    ap.add_argument('--prefix', default='/tmp/fragments', help='输出文件前缀')
    args = ap.parse_args()

    dtroot = args.dtroot
    if dtroot is None:
        dtroot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dt_rc_dir = os.path.join(dtroot, 'recovery/root')

    for p in [args.stock, args.ci, args.validated]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    tmpdir = tempfile.mkdtemp(prefix='frag_')
    print(f"临时目录: {tmpdir}")

    try:
        stk_frags = parse(args.stock)
        ci_frags = parse(args.ci)
        val_frags = parse(args.validated)

        # === F0: stock + twrp16 + setenv - recovery - res ===
        print("[F0] 改造 stock vendor ramdisk ...")
        f0d = os.path.join(tmpdir, '_f0')
        decompress_fragment(stk_frags[0], f0d)

        for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
            if os.path.exists(p):
                (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
        print("  - recovery binary + res/")

        ci_f1d = os.path.join(tmpdir, '_ci_f1')
        decompress_fragment(ci_frags[1], ci_f1d)

        td = f'{f0d}/system/lib64/twrp16'
        os.makedirs(td, exist_ok=True)
        copied = 0
        for lib in TWRP16_LIBS:
            s = os.path.join(ci_f1d, 'system/lib64', lib)
            if os.path.exists(s):
                shutil.copy2(s, f'{td}/{lib}', follow_symlinks=False)
                copied += 1
        print(f"  + twrp16/ ({copied} libs)")

        irc = f'{f0d}/system/etc/init/hw/init.rc'
        if os.path.exists(irc):
            c = open(irc).read()
            old = '    seclabel u:r:recovery:s0\n    user root\n'
            setenv_line = '    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64\n'
            if old in c and setenv_line.strip() not in c:
                c = c.replace(old, old + setenv_line)
            open(irc, 'w').write(c)
            print("  + setenv in init.rc")

        f0_lz4 = pack_cpio_lz4(f0d)
        print(f"  F0: {len(f0_lz4)/1024/1024:.2f} MB")

        # === F1: validated F1 + staging overlay + device tree RC ===
        print(f"\n[F1] 使用 validated runtime tree ...")
        val_f1d = os.path.join(tmpdir, '_val_f1')
        decompress_fragment(val_frags[1], val_f1d)

        f1d = os.path.join(tmpdir, '_new_f1')
        if os.path.exists(f1d): shutil.rmtree(f1d)
        shutil.copytree(val_f1d, f1d, symlinks=True)
        print(f"  F1: copied from validated tree")

        if args.staging:
            print(f"  [staging] 从 {args.staging} 覆盖审计文件 ...")
            for rel in STAGING_OVERLAY_FILES:
                src = os.path.join(args.staging, rel)
                if os.path.exists(src):
                    dst = os.path.join(f1d, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst, follow_symlinks=False)
                    print(f"    + {rel}")
                else:
                    print(f"    ! {rel} not in staging (skip)")

        for rc_name in DT_OVERLAY_RCS:
            dt_path = os.path.join(dt_rc_dir, rc_name)
            if os.path.exists(dt_path):
                shutil.copy2(dt_path, os.path.join(f1d, rc_name))
                print(f"  + overlay {rc_name}")

        f1_lz4 = pack_cpio_lz4(f1d)
        print(f"  F1: {len(f1_lz4)/1024/1024:.2f} MB")

        # === 输出文件 ===
        f0_path = f'{args.prefix}.f0.lz4'
        f1_path = f'{args.prefix}.f1.lz4'
        manifest_path = f'{args.prefix}.manifest.json'

        for p in [f0_path, f1_path]:
            if os.path.exists(p):
                os.remove(p)

        with open(f0_path, 'wb') as f:
            f.write(f0_lz4)
        with open(f1_path, 'wb') as f:
            f.write(f1_lz4)

        manifest = {
            "replacements": [
                {"name": "", "type": 1, "path": os.path.abspath(f0_path)},
                {"name": "recovery", "type": 2, "path": os.path.abspath(f1_path)},
            ]
        }
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        print(f"\n输出:")
        print(f"  F0 fragment: {f0_path} ({len(f0_lz4)/1024/1024:.2f} MB)")
        print(f"  F1 fragment: {f1_path} ({len(f1_lz4)/1024/1024:.2f} MB)")
        print(f"  Manifest:    {manifest_path}")
        print(f"\n用 stock_aware_repack_tool 重打包:")
        print(f"  repack/stock_aware_repack_tool \\")
        print(f"    --template <stock_vendor_boot.img> \\")
        print(f"    --vbmeta-owner <stock_vbmeta.img> \\")
        print(f"    --replacement-manifest {manifest_path} \\")
        print(f"    --output <final.img> \\")
        print(f"    --report report.json \\")
        print(f"    --budget-output budget.json")
        print("完成!")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == '__main__':
    main()
