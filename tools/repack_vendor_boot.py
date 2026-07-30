#!/usr/bin/env python3
"""
TWRP vendor_boot 重打包工具 — for Redmi Turbo 5 Max (dash)

流程：
  1. F0 = stock vendor ramdisk + twrp16/ (9 API36 兼容库) + setenv + charger 去 critical
  2. F1 = CI 构建产物 F1，按内嵌白名单保留必要文件 + F0 去重
  3. DTB = stock 原厂

参数：
  --stock  原厂 vendor_boot.img（必需）
  --ci     CI 构建产物 vendor_boot.img（必需）
  --output 输出路径
"""

import struct, subprocess, os, shutil, sys, argparse

PARTITION_SIZE = 67108864
PAGE_SIZE = 4096

# F1 文件白名单：从经过验证的正常工作镜像（lakitu-1）提取，
# 确保 F1 只包含 recovery 运行必需的文件。
F1_WHITELIST = {
    'default.prop', 'first_stage_ramdisk/fstab.emmc', 'init',
    'init.recovery.mt6991.rc', 'init.recovery.project.rc',
    'lib/modules/modules.load.recovery',
    'lib/modules/nt38771_touch_dash.ko', 'lib/modules/xiaomi_touch_dash.ko',
    'odm/firmware/film_model.tflite', 'odm/firmware/novatek_nt38771_p10u_fw_tm.bin',
    'odm/firmware/p10u_nova_tm_thp_config.ini', 'odm/firmware/smooth.tflite',
    'odm/firmware/water_check.tflite',
    'odm/lib64/libtensorflowlite_touch_c.so', 'odm/lib64/libtouchreport.so',
    'odm/lib64/libtouchreport_alg.so', 'odm/lib64/libtouchreport_hal.so',
    'odm/lib64/libtouchreport_sensor.so',
    'odm_property_contexts', 'plat_property_contexts', 'product_property_contexts',
    'prop.default', 'sbin/sh', 'sepolicy',
    'system/bin/awk', 'system/bin/bc', 'system/bin/bu', 'system/bin/dmctl',
    'system/bin/dmuserd', 'system/bin/dump_image', 'system/bin/e2fsck',
    'system/bin/erase_image', 'system/bin/fatlabel', 'system/bin/flash_image',
    'system/bin/fsck.fat',
    'system/bin/hw/android.hardware.fastboot-service.example_recovery',
    'system/bin/me.twrp.twrpapp.apk', 'system/bin/minadbd',
    'system/bin/mkfs.fat', 'system/bin/pigz', 'system/bin/privapp-permissions-twrpapp.xml',
    'system/bin/recovery', 'system/bin/resize2fs', 'system/bin/sgdisk',
    'system/bin/simg2img', 'system/bin/touch_report_debug', 'system/bin/tune2fs',
    'system/bin/twrp', 'system/bin/unpigz', 'system/bin/vold_prepare_subdirs',
    'system/bin/ziptool',
    'system/etc/init/android.hardware.fastboot-service.example_recovery.rc',
    'system/etc/ld.config.txt', 'system/etc/mkshrc',
    'system/etc/recovery.fstab', 'system/etc/ueventd.rc',
    'system/etc/vintf/manifest/android.hardware.fastboot-service.example.xml',
    'system/etc/vintf/manifest/android.hardware.gatekeeper-service.mitee.xml',
    'system/etc/vintf/manifest/android.hardware.secure_element-eSE1.xml',
    'system/etc/vintf/manifest/android.hardware.security.keymint-service.mitee.xml',
    'system/etc/vintf/manifest/android.hardware.security.secureclock-service.mitee.xml',
    'system/etc/vintf/manifest/android.hardware.security.sharedsecret-service.mitee.xml',
    'system/etc/vintf/manifest/android.hardware.weaver-service.nxp.xml',
    'system/lib64/android.frameworks.stats@1.0.so',
    'system/lib64/android.hardware.authsecret@1.0.so',
    'system/lib64/android.hardware.boot-V1-ndk.so',
    'system/lib64/android.hardware.boot@1.0.so',
    'system/lib64/android.hardware.boot@1.1.so',
    'system/lib64/android.hardware.boot@1.2.so',
    'system/lib64/android.hardware.fastboot-V1-ndk.so',
    'system/lib64/android.hardware.fastboot@1.0.so',
    'system/lib64/android.hardware.fastboot@1.1.so',
    'system/lib64/android.hardware.gatekeeper-V1-ndk.so',
    'system/lib64/android.hardware.health-V2-ndk.so',
    'system/lib64/android.hardware.health-V3-ndk.so',
    'system/lib64/android.hardware.health-translate-ndk.so',
    'system/lib64/android.hardware.health@1.0.so',
    'system/lib64/android.hardware.health@2.0.so',
    'system/lib64/android.hardware.health@2.1.so',
    'system/lib64/android.hardware.oemlock@1.0.so',
    'system/lib64/android.hardware.security.keymint-V1-ndk.so',
    'system/lib64/android.hardware.security.keymint-V3-ndk.so',
    'system/lib64/android.hardware.security.secureclock-V1-ndk.so',
    'system/lib64/android.hardware.weaver-V2-ndk.so',
    'system/lib64/android.hidl.token@1.0.so',
    'system/lib64/android.security.maintenance-ndk.so',
    'system/lib64/android.system.keystore2-V1-ndk.so',
    'system/lib64/android.system.keystore2-V4-ndk.so',
    'system/lib64/hw/android.hardware.health@2.0-impl-2.1.so',
    'system/lib64/lib_android_keymaster_keymint_utils.so',
    'system/lib64/libadb_crypto.so', 'system/lib64/libadb_protos.so',
    'system/lib64/libadb_sysdeps.so', 'system/lib64/libadb_tls_connection.so',
    'system/lib64/libadbconnection_server.so', 'system/lib64/libadbd.so',
    'system/lib64/libadbd_services.so', 'system/lib64/libandroid_runtime_lazy.so',
    'system/lib64/libandroidicu.so', 'system/lib64/libaosprecovery.so',
    'system/lib64/libapexsupport.so', 'system/lib64/libapp_processes_protos_lite.so',
    'system/lib64/libbinder.so', 'system/lib64/libblkid.so', 'system/lib64/libbmlutils.so',
    'system/lib64/libboot_control_client.so', 'system/lib64/libc.so',
    'system/lib64/libchrome.so', 'system/lib64/libcrecovery.so',
    'system/lib64/libcutils_sockets.so', 'system/lib64/libdl_android.so',
    'system/lib64/libevent.so', 'system/lib64/libext2_profile.so',
    'system/lib64/libf2fs_sparseblock.so', 'system/lib64/libflashutils.so',
    'system/lib64/libfscrypt.so', 'system/lib64/libfscrypttwrp.so',
    'system/lib64/libft2.so', 'system/lib64/libfuse-lite.so',
    'system/lib64/libfusesideload.so', 'system/lib64/libgatekeeper_aidl.so',
    'system/lib64/libgpt_twrp.so', 'system/lib64/libhardware.so',
    'system/lib64/libhidlbase.so', 'system/lib64/libhidltransport.so',
    'system/lib64/libicui18n.so', 'system/lib64/libicuuc.so',
    'system/lib64/libkeymaster_messages.so', 'system/lib64/libkeymint_support.so',
    'system/lib64/libminuitwrp.so', 'system/lib64/libmmcutils.so',
    'system/lib64/libmtdutils.so', 'system/lib64/libnetd_client.so',
    'system/lib64/libresetprop.so', 'system/lib64/libstdc++.so',
    'system/lib64/libsync.so', 'system/lib64/libtar.so',
    'system/lib64/libtwadbbu.so', 'system/lib64/libtwrpdigest.so',
    'system/lib64/libtwrpmtp-ffs.so', 'system/lib64/libusbhost.so',
    'system/lib64/libutil-linux.so', 'system/lib64/libvndksupport.so',
    'system/lib64/libzstd.so',
    'system/lib64/stock-vendor-hal/android.hardware.secure_element-V1-ndk.so',
    'system/lib64/stock-vendor-hal/ese_weaver.nxp.so',
    'system/lib64/stock-vendor-hal/libbinder.so',
    'system_ext_property_contexts',
    'twres/fonts/DroidSansMono.ttf', 'twres/fonts/NotoSansCJKsc-Regular.ttf',
    'twres/fonts/RobotoCondensed-Regular.ttf',
    'twres/images/back.png', 'twres/images/backspace.png',
    'twres/images/checkbox_false.png', 'twres/images/checkbox_true.png',
    'twres/images/console.png', 'twres/images/cursor.png', 'twres/images/enter.png',
    'twres/images/fab_selectfolder.png', 'twres/images/file.png',
    'twres/images/folder.png', 'twres/images/handle.png', 'twres/images/home.png',
    'twres/images/indeterminate001.png', 'twres/images/indeterminate002.png',
    'twres/images/indeterminate003.png', 'twres/images/indeterminate004.png',
    'twres/images/indeterminate005.png', 'twres/images/indeterminate006.png',
    'twres/images/indeterminate007.png', 'twres/images/indeterminate008.png',
    'twres/images/indeterminate009.png', 'twres/images/indeterminate010.png',
    'twres/images/indeterminate011.png', 'twres/images/indeterminate012.png',
    'twres/images/kb_arrow_down.png', 'twres/images/kb_arrow_left.png',
    'twres/images/kb_arrow_right.png', 'twres/images/kb_arrow_up.png',
    'twres/images/kb_hide.png', 'twres/images/kb_show.png',
    'twres/images/logo.png', 'twres/images/main_button.png',
    'twres/images/main_button_half_height.png',
    'twres/images/main_button_half_height_full_width.png',
    'twres/images/progress_empty.png', 'twres/images/progress_fill.png',
    'twres/images/radio_false.png', 'twres/images/radio_true.png',
    'twres/images/shift.png', 'twres/images/shift_fill.png',
    'twres/images/slider.png', 'twres/images/slider_touch.png',
    'twres/images/slider_used.png', 'twres/images/sort_asc.png',
    'twres/images/sort_desc.png', 'twres/images/sort_empty.png',
    'twres/images/space.png', 'twres/images/splashlogo.png',
    'twres/images/splashteamwin.png', 'twres/images/tab_3.png',
    'twres/images/tab_4.png', 'twres/images/tab_display.png',
    'twres/images/tab_general.png', 'twres/images/tab_language.png',
    'twres/images/tab_timezone.png', 'twres/images/tab_vibration.png',
    'twres/images/unlock_icon.png',
    'twres/languages/cz.xml', 'twres/languages/de.xml', 'twres/languages/el.xml',
    'twres/languages/en.xml', 'twres/languages/es.xml', 'twres/languages/fr.xml',
    'twres/languages/hu.xml', 'twres/languages/id.xml', 'twres/languages/it.xml',
    'twres/languages/ja.xml', 'twres/languages/lk.xml', 'twres/languages/nl.xml',
    'twres/languages/pl.xml', 'twres/languages/pt_BR.xml', 'twres/languages/pt_PT.xml',
    'twres/languages/ru.xml', 'twres/languages/sk.xml', 'twres/languages/sl.xml',
    'twres/languages/sv.xml', 'twres/languages/tr.xml', 'twres/languages/uk.xml',
    'twres/languages/zh_CN.xml', 'twres/languages/zh_TW.xml',
    'twres/portrait.xml', 'twres/splash.xml', 'twres/ui.xml',
    'vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.mitee.xml',
    'vendor/etc/vintf/manifest/android.hardware.security.keymint-service.mitee.xml',
    'vendor/etc/vintf/manifest/android.hardware.security.secureclock-service.mitee.xml',
    'vendor/etc/vintf/manifest/android.hardware.security.sharedsecret-service.mitee.xml',
    'vendor/etc/vintf/manifest/android.hardware.weaver-service.nxp.xml',
    'vendor_file_contexts', 'vendor_property_contexts', 'vendor_service_contexts',
}

# CI 构建多余的 keystore2 生态库（recovery 不需要）
KS_REMOVE = {
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
    'android.hardware.vibrator@1.0.so','android.hardware.vibrator@1.1.so','android.hardware.vibrator@1.2.so',
    'android.security.aaid_aidl-cpp.so','android.security.apc-ndk.so',
    'android.security.authorization-ndk.so','android.security.compat-ndk.so',
    'android.system.keystore2-V3-ndk.so','android.system.keystore2-V5-ndk.so',
    'android.system.suspend-V1-ndk.so','android.system.suspend@1.0.so',
    'android.system.wifi.keystore@1.0.so',
    'android.hardware.health.storage-V1-ndk.so','android.hardware.health.storage@1.0.so',
}

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

def pack_cpio(src_dir):
    r = subprocess.run(['find', '.', '-print0'], capture_output=True, cwd=src_dir)
    p = subprocess.run(['cpio', '-o', '-H', 'newc', '--null'], input=r.stdout, capture_output=True, cwd=src_dir)
    result = subprocess.run(['lz4', '-l', '-9', '--force', '-', '-'], input=p.stdout, capture_output=True)
    return result.stdout

def main():
    ap = argparse.ArgumentParser(description='TWRP vendor_boot repack for dash')
    ap.add_argument('--stock', required=True, help='原厂 vendor_boot.img')
    ap.add_argument('--ci', required=True, help='CI 构建产物 vendor_boot.img')
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

    for p in [f'{f0d}/system/bin/recovery', f'{f0d}/res']:
        if os.path.exists(p):
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)

    # 从 CI F1 提取 twrp16/ 9 个兼容库
    ci_f1d = '/tmp/_ci_f1'
    if os.path.exists(ci_f1d): shutil.rmtree(ci_f1d)
    os.makedirs(ci_f1d)
    r = subprocess.run(['lz4', '-d', '-c', '/dev/stdin'], input=ci_frags[1], capture_output=True)
    subprocess.run(['cpio', '-idm'], input=r.stdout, capture_output=True, cwd=ci_f1d)

    twrp16_libs = ['libbase.so','libbootloader_message.so','libc++.so','libcutils.so',
                   'libfs_mgr.so','liblog.so','liblp.so','libprotobuf-cpp-lite.so','libutils.so']
    twrp_src = ci_f1d
    if not all(os.path.exists(os.path.join(twrp_src, 'system/lib64', lib)) for lib in twrp16_libs):
        for alt_path in ['/tmp/vendor_boot.img']:
            if not os.path.exists(alt_path): continue
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
            if all(os.path.exists(os.path.join(alt_td, 'system/lib64', lib)) for lib in twrp16_libs):
                twrp_src = alt_td
                break

    td = f'{f0d}/system/lib64/twrp16'
    os.makedirs(td, exist_ok=True)
    for lib in twrp16_libs:
        s = os.path.join(twrp_src, 'system/lib64', lib)
        if os.path.exists(s):
            shutil.copy2(s, f'{td}/{lib}', follow_symlinks=False)
    print(f"  + twrp16/ ({len(os.listdir(td))} libs)")

    # init.rc: setenv + charger de-critical
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
    f0 = subprocess.run(['lz4', '-l', '-9', '--force', '-', '-'], input=p.stdout, capture_output=True).stdout
    print(f"  F0: {len(f0)/1024/1024:.2f} MB")

    # === F1: 按白名单重建 ===
    print(f"\n[F1] 按白名单重建 F1 ...")

    # 收集 CI F1 文件索引
    ci_files = set()
    for root, dirs, fnames in os.walk(ci_f1d):
        for f in fnames:
            ci_files.add(os.path.relpath(os.path.join(root, f), ci_f1d))

    # 重建 F1：只复制白名单中的文件
    f1d = '/tmp/_new_f1'
    if os.path.exists(f1d): shutil.rmtree(f1d)
    os.makedirs(f1d)

    from_ci = 0
    for rel in sorted(F1_WHITELIST):
        dst = os.path.join(f1d, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        src = os.path.join(ci_f1d, rel)
        if os.path.exists(src) or os.path.islink(src):
            if os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
            from_ci += 1
        else:
            print(f"  WARNING: {rel} not found in CI F1, skipping")

    print(f"  F1: {from_ci}/{len(F1_WHITELIST)} files from CI")

    # 打包 F1
    f1 = pack_cpio(f1d)
    print(f"  F1 LZ4: {len(f1)/1024/1024:.2f} MB")

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
    print(f"  F0: {len(f0)/1024/1024:.2f} MB")
    print(f"  F1: {len(f1)/1024/1024:.2f} MB")
    print("完成!")

    for d in ['/tmp/_f0', '/tmp/_ci_f1', '/tmp/_new_f1', '/tmp/_ci_f1_alt']:
        if os.path.exists(d):
            try:
                os.remove(d) if os.path.isfile(d) else shutil.rmtree(d)
            except:
                pass

if __name__ == '__main__':
    main()
