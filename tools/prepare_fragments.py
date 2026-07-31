#!/usr/bin/env python3
"""
准备 F0+F1 碎片清单，供 stock_aware_repack_tool --replacement-manifest 使用。

用法：
  python3 prepare_fragments.py \
    --stock /tmp/stock_vendor_boot.img \
    --ci workspace/out/target/product/dash/vendor_boot.img \
    --validated /tmp/validated_vendor_boot.img \
    --staging workspace/out/target/product/dash/recovery/root \
    --dtroot device/xiaomi/dash \
    --prefix /tmp/fragments
"""
import argparse, json, os, subprocess, sys, shutil, struct, hashlib

PAGE_SIZE = 4096
PART_SZ = 67108864

def align(v, a):
    return (v + a - 1) // a * a

def parse_vendor_boot(path):
    """解析 vendor_boot v4 镜像，返回字段和碎片列表"""
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
        name = d[eo+12:eo+44].rstrip(b'\x00').decode('utf-8', errors='ignore')
        payload = d[ro+off:ro+off+sz] if sz > 0 and ro+off+sz <= len(d) else b''
        frags.append({
            'idx': i + 1,
            'sz': sz, 'off': off, 'typ': typ, 'name': name,
            'payload': payload,
        })
    return {
        'raw': d, 'p0': d[:PAGE_SIZE], 'hs': hs, 'ds': ds, 'rs': rs,
        'ro': ro, 'dtbo': dtbo, 'tblo': tblo, 'ec': ec, 'es': es, 'frags': frags,
    }

def lz4_decompress(data, dest):
    """解压 LZ4 到目录"""
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin', '--favor-decSpeed'],
                       input=data, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"lz4 decompress failed: {r.stderr.decode()}")
    r2 = subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, timeout=60, cwd=dest)
    if r2.returncode != 0:
        raise RuntimeError(f"cpio extract failed: {r2.stderr.decode()}")

def lz4_compress(src_dir, out_path):
    """压缩目录为 LZ4 cpio"""
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=30, cwd=src_dir)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, timeout=120, cwd=src_dir)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', out_path],
                   input=p.stdout, capture_output=True, timeout=120)
    return open(out_path, 'rb').read()

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stock', required=True, help='原厂 vendor_boot.img')
    ap.add_argument('--ci', required=True, help='CI 编译的 vendor_boot.img')
    ap.add_argument('--validated', required=True, help='已验证可工作的 vendor_boot.img')
    ap.add_argument('--staging', required=True, help='staging 目录 (recovery/root)')
    ap.add_argument('--dtroot', required=True, help='device tree 根目录')
    ap.add_argument('--prefix', required=True, help='输出前缀 (如 /tmp/fragments)')
    args = ap.parse_args()

    print(f"Stock:  {args.stock}")
    print(f"CI:     {args.ci}")
    print(f"Valid:  {args.validated}")
    print(f"Staging: {args.staging}")
    print(f"Output:  {args.prefix}.lz4 / .manifest.json")

    # 解析三个镜像
    stock = parse_vendor_boot(args.stock)
    ci = parse_vendor_boot(args.ci)
    validated = parse_vendor_boot(args.validated)

    print(f"\nStock:  F0={len(stock['frags'][0]['payload'])/1024/1024:.2f}MB F1={len(stock['frags'][1]['payload'])/1024/1024:.2f}MB")
    print(f"CI:     F0={len(ci['frags'][0]['payload'])/1024/1024:.2f}MB F1={len(ci['frags'][1]['payload'])/1024/1024:.2f}MB")
    print(f"Valid:  F0={len(validated['frags'][0]['payload'])/1024/1024:.2f}MB F1={len(validated['frags'][1]['payload'])/1024/1024:.2f}MB")

    # F0: stock 的 F0 (page0 + dtb + vendor_ramdisk 碎片 0)
    f0_lz4 = stock['frags'][0]['payload']
    f0_path = args.prefix + '.F0.lz4'
    open(f0_path, 'wb').write(f0_lz4)
    f0_sha256 = sha256_file(f0_path)
    print(f"F0: {len(f0_lz4)/1024/1024:.2f} MB, sha256={f0_sha256[:16]}...")

    # F1: validated 的 F1 (已验证可工作的 recovery ramdisk)
    f1_lz4 = validated['frags'][1]['payload']
    f1_path = args.prefix + '.F1.lz4'
    open(f1_path, 'wb').write(f1_lz4)
    f1_sha256 = sha256_file(f1_path)
    print(f"F1: {len(f1_lz4)/1024/1024:.2f} MB, sha256={f1_sha256[:16]}...")

    # 验证 staging 目录存在
    if not os.path.isdir(args.staging):
        print(f"ERROR: staging dir not found: {args.staging}", file=sys.stderr)
        sys.exit(1)

    # 生成 manifest.json 供 stock_aware_repack_tool --replacement-manifest 使用
    manifest = {
        'template': args.stock,
        'vbmeta_owner': None,  # 工作流中单独提供
        'replacements': [
            {
                'target_name': 'recovery',
                'target_type': 2,
                'replacement': f1_path,
                'sha256': f1_sha256,
                'original_size': len(f1_lz4),
            },
        ],
        'metadata': {
            'f0_sha256': f0_sha256,
            'f1_sha256': f1_sha256,
            'staging': args.staging,
            'dtroot': args.dtroot,
        }
    }
    manifest_path = args.prefix + '.manifest.json'
    json.dump(manifest, open(manifest_path, 'w'), indent=2)
    print(f"\nManifest written: {manifest_path}")
    print("Done.")

if __name__ == '__main__':
    main()
