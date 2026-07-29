#!/usr/bin/env python3
"""
替换参考镜像 F1 中 system/bin + system/lib64 为 CI 编译产物。
参考镜像的 F0/page0/DTB 完全不动。
"""
import struct, subprocess, os, shutil, sys, argparse

PAGE_SIZE = 4096
ES = 108
PART_SZ = 67108864

def align(v, a):
    return (v + a - 1) // a * a

def rd(path):
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
        if eo + 12 > len(d): break
        sz, off, typ = struct.unpack_from('<III', d, eo)
        name = d[eo+12:eo+44].rstrip(b'\x00').decode()
        frags.append({
            'sz': sz, 'off': off, 'typ': typ, 'name': name,
            'payload': d[ro+off:ro+off+sz] if sz > 0 and ro+off+sz <= len(d) else b'',
        })
    return {'raw': d, 'p0': d[:PAGE_SIZE], 'hs': hs, 'ds': ds, 'rs': rs,
            'ro': ro, 'dtbo': dtbo, 'tblo': tblo, 'ec': ec, 'es': es, 'frags': frags}

def uz(data, dest):
    if os.path.exists(dest): shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin', '--favor-decSpeed'],
                       input=data, capture_output=True, timeout=30)
    if r.returncode != 0: return None
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, timeout=30, cwd=dest)
    return r.stdout

def pk(src, tmp):
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, timeout=10, cwd=src)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'],
                       input=r.stdout, capture_output=True, timeout=60, cwd=src)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', tmp],
                   input=p.stdout, capture_output=True, timeout=60)
    return open(tmp, 'rb').read()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ci', required=True)
    ap.add_argument('--template', default='/home/lakitu/下载/vendor_boot-dash-OS3.0.305.WPLCNXM-lakitu-0.img')
    ap.add_argument('--output', default='/tmp/dash-UNTESTED-vendor_boot.img')
    args = ap.parse_args()

    tpl = rd(args.template)   # 参考模板
    ci = rd(args.ci)           # CI

    print(f"模板: F0={len(tpl['frags'][0]['payload'])/1024/1024:.2f} F1={len(tpl['frags'][1]['payload'])/1024/1024:.2f}")
    print(f"CI:   F1={len(ci['frags'][1]['payload'])/1024/1024:.2f}")

    # 提取 CI 和模板的 F1
    ci_dir = '/tmp/ci_dir'
    uz(ci['frags'][1]['payload'], ci_dir)
    tp_dir = '/tmp/tp_dir'
    uz(tpl['frags'][1]['payload'], tp_dir)

    # 统计模板 F1 中 system/bin + system/lib64
    tp_bins = set()
    tp_libs = set()
    for root, dirs, files in os.walk(f'{tp_dir}/system'):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), tp_dir)
            if rel.startswith('system/bin/'):
                tp_bins.add(rel)
            elif rel.startswith('system/lib64/'):
                tp_libs.add(rel)

    print(f"模板 F1: {len(tp_bins)} bins, {len(tp_libs)} libs")

    # 新建 F1 目录：用模板为骨架，替换其中的 bin/lib64 为 CI 的
    f1_dir = '/tmp/f1_dir'
    if os.path.exists(f1_dir): shutil.rmtree(f1_dir)
    # 复制模板的完整 F1
    shutil.copytree(tp_dir, f1_dir, symlinks=True)

    # 删模板的 system/bin 和 system/lib64
    for sub in ['system/bin', 'system/lib64']:
        p = os.path.join(f1_dir, sub)
        if os.path.exists(p): shutil.rmtree(p)

    # 复制 CI 的 system/bin 和 system/lib64
    for sub in ['system/bin', 'system/lib64']:
        src = os.path.join(ci_dir, sub)
        dst = os.path.join(f1_dir, sub)
        if os.path.exists(src):
            shutil.copytree(src, dst, symlinks=True)

    # 删核心组件
    for f in ['init', 'linker64', 'adbd', 'fastbootd', 'toybox']:
        p = os.path.join(f1_dir, 'system/bin', f)
        if os.path.exists(p): os.remove(p)

    # 删 CI 中多余的文件：只保留模板 F1 中有名的 bin + lib64
    # 但 lib64 例外——保持 CI 所有库以满足传递依赖
    for root, dirs, files in os.walk(f1_dir):
        for f in files:
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, f1_dir)
            if rel.startswith('system/bin/') and rel not in tp_bins:
                os.remove(fp)
            elif rel.startswith('system/lib64/') and rel not in tp_libs:
                # 保留 CI 的额外库以满足传递依赖
                pass  # 保留 CI 所有 lib

    # 补模板中有但 CI 中没有的 bin（从模板补回）
    ci_names = set()
    for root, dirs, files in os.walk(f1_dir):
        for f in files:
            ci_names.add(os.path.relpath(os.path.join(root, f), f1_dir))

    for rel in tp_bins:
        if rel not in ci_names:
            src = os.path.join(tp_dir, rel)
            dst = os.path.join(f1_dir, rel)
            if os.path.lexists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.islink(src):
                    os.symlink(os.readlink(src), dst)
                else:
                    shutil.copy2(src, dst, follow_symlinks=False)

    # 统计
    final_bins = set()
    final_libs = set()
    for root, dirs, files in os.walk(f1_dir):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), f1_dir)
            if rel.startswith('system/bin/'): final_bins.add(rel)
            elif rel.startswith('system/lib64/'): final_libs.add(rel)
    print(f"最终 F1: {len(final_bins)} bins, {len(final_libs)} libs")

    # pack F1
    f1_new = pk(f1_dir, '/tmp/f1_new.lz4')
    print(f"F1: {len(f1_new)/1024/1024:.2f} MB LZ4")

    # F0 用模板原样
    f0 = tpl['frags'][0]['payload']
    rsz = len(f0) + len(f1_new)
    print(f"合计: {rsz/1024/1024:.2f} MB")

    # 构建
    print("\n构建 vendor_boot ...")
    img = bytearray(PART_SZ)
    img[:PAGE_SIZE] = tpl['p0'][:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)

    ro = align(tpl['hs'], PAGE_SIZE)
    img[ro:ro + len(f0)] = f0
    img[ro + len(f0):ro + rsz] = f1_new

    dtbo = align(ro + rsz, PAGE_SIZE)
    d_start = None
    for o in range(tpl['dtbo'], min(tpl['dtbo'] + 256, len(tpl['raw']))):
        if tpl['raw'][o:o+4] == b'\xd0\x0d\xfe\xed':
            d_start = o - 64; break
    if d_start is not None and d_start >= tpl['dtbo']:
        img[dtbo:dtbo + tpl['ds']] = tpl['raw'][d_start:d_start + tpl['ds']]
    else:
        print("ERROR: DTB not found", file=sys.stderr); sys.exit(1)

    tblo = align(dtbo + align(tpl['ds'], PAGE_SIZE), PAGE_SIZE)
    for i, (data, off) in enumerate([(f0, 0), (f1_new, len(f0))]):
        eo = tblo + i * tpl['es']
        raw = bytearray(tpl['es'])
        struct.pack_into('<I', raw, 0, len(data))
        struct.pack_into('<I', raw, 4, off)
        struct.pack_into('<I', raw, 8, i + 1)
        raw[12:44] = b'\x00' * 32
        img[eo:eo + tpl['es']] = bytes(raw)

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PART_SZ
    print(f"输出: {args.output}")
    print(f"64 MB (F0:{len(f0)/1024/1024:.2f}+F1:{len(f1_new)/1024/1024:.2f})")

    for d in ['/tmp/ci_dir', '/tmp/tp_dir', '/tmp/f1_dir']:
        if os.path.exists(d): shutil.rmtree(d)
    for f in ['/tmp/f1_new.lz4']:
        if os.path.exists(f): os.remove(f)

if __name__ == '__main__':
    main()
