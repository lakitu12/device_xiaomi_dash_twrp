#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包 — 严格按 README 流程

三步：
  1. 改 F0: 提取原厂 F0 → 删 recovery + res → 加 twrp16/ + setenv
  2. 改 F1: 提取 CI F1 → 删 init/linker64/adbd/fastbootd/toybox → 删同名库
  3. 合并: 以原厂为模板，替换 F0/F1，更新 DTB/表位置
"""
import struct, subprocess, os, shutil, sys, argparse

PAGE_SIZE = 4096
ES = 108
PART_SZ = 67108864

def align(v, a):
    return (v + a - 1) // a * a

def rd(path):
    """同 README 的解析。"""
    d = open(path, 'rb').read()
    hs, ds = struct.unpack_from('<II', d, 2096)
    _, ec, es, _ = struct.unpack_from('<IIII', d, 2112)
    rs = struct.unpack_from('<I', d, 24)[0]
    ro = align(hs, PAGE_SIZE)
    dtbo = align(ro + rs, PAGE_SIZE)
    tblo = align(dtbo + align(ds, PAGE_SIZE), PAGE_SIZE)  # README 公式
    frags = []
    for i in range(ec):
        eo = tblo + i * es
        if eo + 12 > len(d):
            break
        sz, off, typ = struct.unpack_from('<III', d, eo)
        name = d[eo + 12:eo + 44].rstrip(b'\x00').decode()
        frags.append({
            'sz': sz, 'off': off, 'typ': typ, 'name': name,
            'payload': d[ro + off:ro + off + sz] if sz > 0 and ro + off + sz <= len(d) else b'',
            'raw_entry': d[eo:eo + es],  # 保留原始条目数据
        })
    return {'raw': d, 'p0': d[:PAGE_SIZE], 'hs': hs, 'ds': ds, 'rs': rs,
            'ro': ro, 'dtbo': dtbo, 'tblo': tblo, 'ec': ec, 'es': es, 'frags': frags}

def uz(data, dest):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin', '--favor-decSpeed'],
                       input=data, capture_output=True, timeout=30)
    if r.returncode != 0:
        return None
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
    ap.add_argument('--stock', required=True)
    ap.add_argument('--ci', required=True)
    ap.add_argument('--output', default='/tmp/dash-UNTESTED-vendor_boot.img')
    args = ap.parse_args()

    sk = rd(args.stock)
    ci = rd(args.ci)
    print(f"原厂: {len(sk['frags'])} frags, CI: {len(ci['frags'])} frags")
    for i in range(len(sk['frags'])):
        print(f"  原厂 F{i}: sz={sk['frags'][i]['sz']} typ={sk['frags'][i]['typ']} name='{sk['frags'][i]['name']}'")

    # ====== 1. 改 F0 ======
    print("\n[1] 改造 F0 ...")
    d0 = '/tmp/f0'
    uz(sk['frags'][0]['payload'], d0)
    for f in [f'{d0}/system/bin/recovery', f'{d0}/res']:
        if os.path.exists(f):
            (os.remove if os.path.isfile(f) else shutil.rmtree)(f)
            print(f"  删 {os.path.basename(f)}")
    ci1 = '/tmp/ci1'
    uz(ci['frags'][1]['payload'], ci1)
    tdir = f'{d0}/system/lib64/twrp16'
    os.makedirs(tdir, exist_ok=True)
    for lib in ['libbase.so', 'libbootloader_message.so', 'libc++.so',
                'libcutils.so', 'libfs_mgr.so', 'liblog.so', 'liblp.so',
                'libprotobuf-cpp-lite.so', 'libutils.so']:
        s = f'{ci1}/system/lib64/{lib}'
        if os.path.exists(s):
            shutil.copy2(s, f'{tdir}/{lib}', follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(tdir))} libs)")

    irc = f'{d0}/system/etc/init/hw/init.rc'
    with open(irc) as f:
        c = f.read()
    tag = ('service recovery /system/bin/recovery\n'
           '    socket recovery stream 422 system system\n'
           '    seclabel u:r:recovery:s0\n    user root')
    if tag in c:
        c = c.replace(tag, tag + '\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
        with open(irc, 'w') as f:
            f.write(c)
        print("  + setenv")
    f0_new = pk(d0, '/tmp/f0.lz4')
    print(f"  F0: {len(f0_new)/1024/1024:.2f} MB")

    # ====== 2. 改 F1 ======
    print("\n[2] 裁剪 F1 ...")
    d1 = '/tmp/f1'
    uz(ci['frags'][1]['payload'], d1)
    for f in ['system/bin/init', 'system/bin/linker64', 'system/bin/adbd',
              'system/bin/fastbootd', 'system/bin/toybox']:
        p = f'{d1}/{f}'
        if os.path.exists(p):
            os.remove(p)
            print(f"  删 {os.path.basename(f)}")

    f1ld = f'{d1}/system/lib64'
    f0ld = f'{d0}/system/lib64'
    if os.path.isdir(f0ld) and os.path.isdir(f1ld):
        f0set = {l for l in os.listdir(f0ld)
                 if os.path.isfile(f'{f0ld}/{l}') and not os.path.islink(f'{f0ld}/{l}')}
        dc = ds = kc = ks = 0
        for lib in list(os.listdir(f1ld)):
            if lib == 'twrp16':
                continue
            fp = f'{f1ld}/{lib}'
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            if lib in f0set:
                sz = os.path.getsize(fp)
                os.remove(fp)
                dc += 1; ds += sz
            else:
                kc += 1; ks += os.path.getsize(fp)
        print(f"  lib64: keep={kc} ({ks/1024/1024:.2f} MB) del={dc} ({ds/1024/1024:.2f} MB)")

    f1_new = pk(d1, '/tmp/f1.lz4')
    print(f"  F1: {len(f1_new)/1024/1024:.2f} MB")
    print(f"  合计: {(len(f0_new)+len(f1_new))/1024/1024:.2f} MB")

    # ====== 3. 构建 ======
    print("\n[3] 构建 vendor_boot ...")
    rsz = len(f0_new) + len(f1_new)
    ro = align(sk['hs'], PAGE_SIZE)
    dtbo = align(ro + rsz, PAGE_SIZE)     # DTB 新位置
    tblo = align(dtbo + align(sk['ds'], PAGE_SIZE), PAGE_SIZE)  # 表新位置

    # DTB: 从原厂复制完整区域（含 64B MTK 表头）
    stk_raw = sk['raw']
    # 找到 DTB 的开始（64B 表头 + FDT）
    d_start = None
    for o in range(sk['dtbo'], min(sk['dtbo'] + 256, len(stk_raw))):
        if stk_raw[o:o+4] == b'\xd0\x0d\xfe\xed':
            d_start = o - 64
            break
    if d_start is not None and d_start >= sk['dtbo']:
        dtb = stk_raw[d_start:d_start + sk['ds']]
    else:
        print("FATAL: DTB not found", file=sys.stderr); sys.exit(1)

    img = bytearray(PART_SZ)
    img[:PAGE_SIZE] = sk['p0'][:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)
    img[ro:ro + len(f0_new)] = f0_new
    img[ro + len(f0_new):ro + rsz] = f1_new
    img[dtbo:dtbo + sk['ds']] = dtb

    # fragment 表: 用零填充 name（参考和 CI 都用空 name）
    for i, (data, off) in enumerate([(f0_new, 0), (f1_new, len(f0_new))]):
        eo = tblo + i * sk['es']
        raw = bytearray(sk['es'])
        struct.pack_into('<I', raw, 0, len(data))
        struct.pack_into('<I', raw, 4, off)
        struct.pack_into('<I', raw, 8, i + 1)
        # name 置零（参考镜像和 CI 均为空）
        raw[12:44] = b'\x00' * 32
        img[eo:eo + sk['es']] = bytes(raw)

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PART_SZ

    print(f"  输出: {args.output}")
    print(f"  64 MB (F0:{len(f0_new)/1024/1024:.2f}+F1:{len(f1_new)/1024/1024:.2f} DTB@{dtbo} 表@{tblo})")

    for d in ['/tmp/f0', '/tmp/ci1', '/tmp/f1']:
        if os.path.exists(d):
            shutil.rmtree(d)
    for f in ['/tmp/f0.lz4', '/tmp/f1.lz4']:
        if os.path.exists(f):
            os.remove(f)

if __name__ == '__main__':
    main()
