#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包工具 — for Redmi Turbo 5 Max (dash)

流程（已验证）：
  1. F0 = stock vendor ramdisk + twrp16/ (9 API36 兼容库) + setenv + charger 修复
  2. F1 = CI 构建产物的 F1（必须包含完整设备支持——ODM、触控、FBE HAL 等）
  3. DTB = stock 原厂

注意：CI 构建的 device tree 必须包含以下文件（否则 FBE/触控不可用）：
  - system/lib64/libbinder.so, libc.so, libhidlbase.so (API 36 版)
  - system/lib64/android.hardware.gatekeeper-V1-ndk.so 等 FBE HAL 库
  - system/lib64/android.hardware.health-*.so (V2, V3 版)
  - vendor/etc/vintf/manifest/*.xml (gatekeeper, keymint, weaver HAL)
  - lib/modules/*.ko (触控驱动)
  - system/bin/touch_report_debug
"""
import struct, subprocess, os, shutil, sys, argparse

PARTITION_SIZE = 67108864
PAGE_SIZE = 4096

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
    dtb = d[dtbo:dtbo + ds]
    return d[:PAGE_SIZE], ds, frags, dtb

def main():
    ap = argparse.ArgumentParser(description='TWRP vendor_boot repack for dash')
    ap.add_argument('--stock', required=True, help='原厂 vendor_boot.img')
    ap.add_argument('--ci', required=True, help='CI 构建产物 vendor_boot.img（需完整 F1）')
    ap.add_argument('--output', default='/tmp/dash-vendor_boot.img')
    args = ap.parse_args()

    for p in [args.stock, args.ci]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)

    stk_page0, stk_ds, stk_frags, stk_dtb = parse(args.stock)
    ci_page0, ci_ds, ci_frags, ci_dtb = parse(args.ci)

    # === F0: stock + twrp16 + setenv + charger fix ===
    print("[F0] 改造 stock vendor ramdisk ...")
    f0d = '/tmp/_f0'
    if os.path.exists(f0d): shutil.rmtree(f0d)
    os.makedirs(f0d)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=stk_frags[0], capture_output=True)
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, cwd=f0d)

    # 删 stock recovery + res
    for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)

    # 从 CI F1 提取 twrp16/ 9 个库（公共 CI 有完整 182 库，lakitu-1 没有这些系统库）
    # 优先从 --ci 的 F1 提取，如果缺失则尝试从公共 CI 提取
    ci_f1d = '/tmp/_ci_f1'
    if os.path.exists(ci_f1d): shutil.rmtree(ci_f1d)
    os.makedirs(ci_f1d)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=ci_frags[1], capture_output=True)
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, cwd=ci_f1d)

    twrp16_src = ci_f1d
    # 检查 libbase.so 是否在 CI F1 中
    if not os.path.exists(os.path.join(ci_f1d, 'system/lib64/libbase.so')):
        # 尝试从其他 CI 构建提取（公共 CI）
        for alt_path in ['/tmp/vendor_boot.img']:
            if os.path.exists(alt_path):
                print(f"  twrp16 libs not in --ci F1, trying {alt_path}...")
                alt_d = open(alt_path, 'rb').read()
                alt_hs = struct.unpack_from('<I', alt_d, 2096)[0]
                alt_rs = struct.unpack_from('<I', alt_d, 24)[0]
                alt_ro = align(alt_hs, PAGE_SIZE)
                alt_tblo = align(align(alt_ro + alt_rs, PAGE_SIZE) + align(struct.unpack_from('<I', alt_d, 2100)[0], PAGE_SIZE), PAGE_SIZE)
                alt_ec = struct.unpack_from('<I', alt_d, 2116)[0]
                alt_es = struct.unpack_from('<I', alt_d, 2120)[0]
                alt_eo = alt_tblo + 1 * alt_es
                alt_sz, alt_off, alt_typ = struct.unpack_from('<III', alt_d, alt_eo)
                alt_r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=alt_d[alt_ro + alt_off:alt_ro + alt_off + alt_sz], capture_output=True)
                alt_td = '/tmp/_ci_f1_alt'
                if os.path.exists(alt_td): shutil.rmtree(alt_td)
                os.makedirs(alt_td)
                subprocess.run(['cpio', '-idm'], input=alt_r.stdout, capture_output=True, cwd=alt_td)
                if os.path.exists(os.path.join(alt_td, 'system/lib64/libbase.so')):
                    twrp16_src = alt_td
                    print(f"  found twrp16 libs in {alt_path}")
                    break

    twrp16 = ['libbase.so','libbootloader_message.so','libc++.so','libcutils.so',
              'libfs_mgr.so','liblog.so','liblp.so','libprotobuf-cpp-lite.so','libutils.so']
    td = f'{f0d}/system/lib64/twrp16'
    os.makedirs(td, exist_ok=True)
    for lib in twrp16:
        s = os.path.join(twrp16_src, 'system/lib64', lib)
        if os.path.exists(s):
            shutil.copy2(s, f'{td}/{lib}', follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(td))} libs)")

    # init.rc: setenv + charger 去 critical
    irc = f'{f0d}/system/etc/init/hw/init.rc'
    if os.path.exists(irc):
        c = open(irc).read()
        tag = ('service recovery /system/bin/recovery\n'
               '    socket recovery stream 422 system system\n'
               '    seclabel u:r:recovery:s0\n    user root')
        if tag in c and 'setenv' not in c:
            c = c.replace(tag, tag + '\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
        c = c.replace('\nservice charger /system/bin/charger\n    critical\n',
                      '\nservice charger /system/bin/charger\n')
        open(irc, 'w').write(c)
        print("  + setenv + charger fix in init.rc")

    # 打包 F0
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, cwd=f0d)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'], input=r.stdout, capture_output=True, cwd=f0d)
    subprocess.run(['lz4', '-l', '-9', '--force', '-', '/tmp/_f0.lz4'], input=p.stdout, capture_output=True)
    f0 = open('/tmp/_f0.lz4', 'rb').read()
    print(f"  F0: {len(f0)/1024/1024:.2f} MB")

    # === F1: 使用 CI F1，按需裁剪 ===
    # 如果 CI F1 是自包含模式（>100 个库），做后处理：
    #   1. 去重 F0 已有的系统库（F0 + twrp16/ 提供）
    #   2. 删除 CI 多余的 keystore2 生态库
    # 如果 CI F1 已经是正确模式（约 84 库），直接使用
    f1 = ci_frags[1]
    
    # 检查 CI F1 的库数
    ci_f1_libs = 0
    if os.path.isdir(f'{ci_f1d}/system/lib64'):
        ci_f1_libs = len([f for f in os.listdir(f'{ci_f1d}/system/lib64') if f.endswith('.so')])
    
    if ci_f1_libs > 100:
        print(f"\n[F1] CI 是自包含模式 ({ci_f1_libs} 库)，开始裁剪 ...")
        # 解压 CI F1 到工作目录
        f1d = '/tmp/_f1_trim'
        if os.path.exists(f1d): shutil.rmtree(f1d)
        os.makedirs(f1d)
        r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=ci_frags[1], capture_output=True)
        subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, cwd=f1d)
        
        # 收集 F0 的库列表
        f0_libs = set()
        if os.path.isdir(f'{f0d}/system/lib64'):
            for f in os.listdir(f'{f0d}/system/lib64'):
                if os.path.isfile(os.path.join(f'{f0d}/system/lib64', f)):
                    f0_libs.add(f)
        
        # 删除 F0 已提供的系统库
        if os.path.isdir(f'{f1d}/system/lib64'):
            dedup = 0
            for f in list(os.listdir(f'{f1d}/system/lib64')):
                if f == 'twrp16': continue
                if f in f0_libs:
                    os.remove(os.path.join(f'{f1d}/system/lib64', f))
                    dedup += 1
            print(f"  去重: 删除 {dedup} 个 F0 已有系统库")
        
        # 删除 CI 多余的 keystore2 生态库
        ks_remove = [
            'libkeystore2_aaid.so','libkeystore2_apc_compat.so','libkeystore2_crypto.so',
            'libkeystore-attestation-application-id.so','libkm_compat.so','libkm_compat_service.so',
            'libservices.so','server_configurable_flags.so',
            'libcppbor.so','libcppbor_external.so','libcppcose_rkp.so',
            'libnos_datagram.so','libnos_transport.so',
            'libperfetto_c.so','libxml2.so','libincfs.so',
            'libaconfig_storage_read_api_cc.so','libutilscallstack.so',
            'libhardware_legacy.so','libhwbinder.so',
            'libpuresoftkeymasterdevice.so','libsoftkeymasterdevice.so',
            'libsoft_attestation_cert.so',
            'libkeymaster4_1support.so','libkeymaster4support.so','libkeymaster_portable.so',
            'android.frameworks.stats-V1-ndk.so',
            'android.hardware.confirmationui-V1-ndk.so','android.hardware.confirmationui@1.0.so',
            'android.hardware.security.rkp-V3-ndk.so','android.hardware.security.sharedsecret-V1-ndk.so',
            'android.hardware.vibrator-V1-cpp.so','android.hardware.vibrator-V1-ndk.so',
            'android.hardware.vibrator-V2-cpp.so','android.hardware.vibrator-V2-ndk.so',
            'android.hardware.vibrator@1.0.so','android.hardware.vibrator@1.1.so',
            'android.hardware.vibrator@1.2.so',
            'android.security.aaid_aidl-cpp.so','android.security.apc-ndk.so',
            'android.security.authorization-ndk.so','android.security.compat-ndk.so',
            'android.system.keystore2-V3-ndk.so','android.system.keystore2-V5-ndk.so',
            'android.system.suspend-V1-ndk.so','android.system.suspend@1.0.so',
            'android.system.wifi.keystore@1.0.so',
            'android.hardware.health.storage-V1-ndk.so','android.hardware.health.storage@1.0.so',
        ]
        if os.path.isdir(f'{f1d}/system/lib64'):
            ks_del = 0
            for f in list(os.listdir(f'{f1d}/system/lib64')):
                if f in ks_remove:
                    os.remove(os.path.join(f'{f1d}/system/lib64', f))
                    ks_del += 1
            print(f"  清理: 删除 {ks_del} 个 CI 多余生态库")
        
        # 重新打包 F1
        r = subprocess.run(['find', '.', '-print0'], capture_output=True, cwd=f1d)
        p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'], input=r.stdout, capture_output=True, cwd=f1d)
        subprocess.run(['lz4', '-l', '-9', '--force', '-', '/tmp/_f1_trimmed.lz4'], input=p.stdout, capture_output=True)
        f1 = open('/tmp/_f1_trimmed.lz4', 'rb').read()
        print(f"  裁剪后 F1: {len(f1)/1024/1024:.2f} MB")
    else:
        print(f"\n[F1] CI F1 已是正确模式 ({ci_f1_libs} 库)，直接使用")
    
    print(f"  F1: {len(f1)/1024/1024:.2f} MB")

    # === 构建 vendor_boot ===
    print("\n构建 vendor_boot ...")
    rsz = len(f0) + len(f1)
    if rsz + 2 * PAGE_SIZE > PARTITION_SIZE:
        print(f"ERROR: payload too large ({rsz/1024/1024:.2f} MB)", file=sys.stderr)
        sys.exit(1)

    rs = PAGE_SIZE
    dtb_off = align(rs + rsz, PAGE_SIZE)
    tblo = align(dtb_off + stk_ds, PAGE_SIZE)

    img = bytearray(PARTITION_SIZE)
    img[:PAGE_SIZE] = stk_page0[:PAGE_SIZE]
    struct.pack_into('<I', img, 24, rsz)
    struct.pack_into('<I', img, 2100, stk_ds)
    img[rs:rs + len(f0)] = f0
    img[rs + len(f0):rs + rsz] = f1
    img[dtb_off:dtb_off + len(stk_dtb)] = stk_dtb[:stk_ds]

    def entry(sz, off, typ, name=''):
        e = bytearray(108)
        struct.pack_into('<I', e, 0, sz)
        struct.pack_into('<I', e, 4, off)
        struct.pack_into('<I', e, 8, typ)
        e[12:12 + len(name.encode()[:31])] = name.encode()[:31]
        return e

    img[tblo:tblo + 108] = entry(len(f0), 0, 1, '')
    img[tblo + 108:tblo + 216] = entry(len(f1), len(f0), 2, '')
    struct.pack_into('<I', img, 2112, 216)
    struct.pack_into('<I', img, 2112 + 4, 2)
    struct.pack_into('<I', img, 2112 + 8, 108)

    with open(args.output, 'wb') as f:
        f.write(bytes(img))
    assert os.path.getsize(args.output) == PARTITION_SIZE

    print(f"\n输出: {args.output}")
    print(f"大小: {PARTITION_SIZE/1024/1024:.0f} MB")
    print(f"  F0: {len(f0)/1024/1024:.2f} MB (stock + twrp16/ + setenv)")
    print(f"  F1: {len(f1)/1024/1024:.2f} MB (CI build)")
    print("完成!")

    # 清理
    for d in ['/tmp/_f0', '/tmp/_ci_f1']:
        if os.path.exists(d):
            try:
                os.remove(d) if os.path.isfile(d) else shutil.rmtree(d)
            except:
                pass

if __name__ == '__main__':
    main()
