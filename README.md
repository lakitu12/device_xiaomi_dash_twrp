# Unofficial TWRP for Xiaomi dash (Redmi Turbo 5 Max)

> 本 fork 由 AI 辅助维护。使用前请结合源码审查，自行承担风险。

dash 的 recovery 内嵌在 `vendor_boot` 分区中（VAB 架构），Android 构建输出并非最终可直接刷入的镜像。需用 rc1 模板重打包后刷写。

适配机型：**Redmi Turbo 5 Max** (`dash`, MT6991Z / Dimensity 9500s, 中国大陆版)。不保证兼容 Poco X8 Pro Max。

---

## 本地构建

### 同步源码

```bash
mkdir source-twrp16 && cd source-twrp16
repo init -u https://github.com/TWRP-Test/platform_manifest_twrp_aosp -b twrp-16.0
mkdir -p .repo/local_manifests
curl -L -o .repo/local_manifests/dash.xml \
    https://raw.githubusercontent.com/lakitu12/device_xiaomi_dash_twrp/twrp-16.0/local_manifest.xml
repo sync -j4 --force-sync
```

### 编译

```bash
source build/envsetup.sh
lunch twrp_dash-bp2a-eng
SOONG_GOMEMLIMIT=8GiB SOONG_GOGC=20 m recovery vendorbootimage -j4
```

编译产物：
- `out/target/product/dash/recovery/root/` — recovery ramdisk 文件系统
- `out/target/product/dash/obj/PACKAGING/vendor_boot_intermediates/vendor_ramdisk.cpio.gz` — vendor ramdisk（仅含 TWRP 文件，不含内核模块，不可直接刷入）

### 提取 recovery fragment

```bash
cd out/target/product/dash/obj/PACKAGING/vendor_boot_intermediates/
# vendor ramdisk 是 gzip 压缩的 cpio，解压后提取 recovery 相关内容
# 或者从 recovery/root/ 手动打包
cd out/target/product/dash/recovery/root
find . | cpio -o -H newc > /tmp/twrp.cpio
lz4 -l -12 --favor-decSpeed /tmp/twrp.cpio /tmp/twrp-fragment.lz4
```

---

## 重打包

流程：原厂 `vendor_boot.img` → 改造 vendor ramdisk → 裁剪 CI recovery + 加 CJK → 重打包。

### 0. 准备材料

| 材料 | 说明 | 来源 |
|------|------|------|
| 原厂 `vendor_boot.img` | 设备原始 vendor_boot 分区备份 | `adb pull /dev/block/by-name/vendor_boot` |
| CI 构建产物 | CI 输出的 `vendor_boot.img`（fragment 0 为空，fragment 1 为自包含 recovery） | GitHub Actions |

### 1. 改造 vendor ramdisk

从原厂 vendor ramdisk 出发，只做 4 处修改：

```bash
# 提取原厂 vendor ramdisk（fragment 0）
python3 << 'EOF'
import struct
def align(v,a): return (v+a-1)//a*a
img=open('stock_vendor_boot.img','rb').read()
hs,ds=struct.unpack_from('<II',img,2096)
_,ec,es,_=struct.unpack_from('<IIII',img,2112)
rs=struct.unpack_from('<I',img,24)[0]
ro=align(hs,4096); dtbo=align(ro+rs,4096); tblo=align(dtbo+align(ds,4096),4096)
eo=tblo+0*es; sz,off,_=struct.unpack_from('<III',img,eo)
open('stock-vendor-frag.lz4','wb').write(img[ro+off:ro+off+sz])
EOF

# 解压
lz4 -d stock-vendor-frag.lz4 /dev/stdout | cpio -id

# 修改 1：删除原厂 recovery binary（TWRP 接管）
rm -f system/bin/recovery

# 修改 2：删除 res/（MIUI 恢复 UI，TWRP 不需要）
rm -rf res/

# 修改 3：添加 twrp16/ 兼容库（从 CI 构建的 recovery 中提取 API 36 版本）
mkdir -p system/lib64/twrp16
# 需要 9 个库（参见下方兼容层原理章节）

# 修改 4：init.rc 在 service recovery 内部添加 setenv
# service recovery /system/bin/recovery
#     socket recovery stream 422 system system
#     seclabel u:r:recovery:s0
#     user root
#     setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64

# 重新压缩
find . | cpio -o -H newc > /tmp/vendor-mod.cpio
lz4 -l -9 /tmp/vendor-mod.cpio /tmp/vendor-mod.lz4
```

### 2. 提取并裁剪 CI recovery

```bash
# 从 CI 产物提取 fragment 1
python3 << 'EOF'
import struct
def align(v,a): return (v+a-1)//a*a
ci=open('ci_vendor_boot.img','rb').read()
hs,ds=struct.unpack_from('<II',ci,2096)
_,ec,es,_=struct.unpack_from('<IIII',ci,2112)
rs=struct.unpack_from('<I',ci,24)[0]
ro=align(hs,4096); dtbo=align(ro+rs,4096); tblo=align(dtbo+align(ds,4096),4096)
eo=tblo+1*es; sz,off,_=struct.unpack_from('<III',ci,eo)
open('ci-recovery.lz4','wb').write(ci[ro+off:ro+off+sz])
EOF

# 解压
lz4 -d ci-recovery.lz4 /dev/stdout | cpio -id

# 删除 vendor 已提供的核心组件
rm -f system/bin/init system/bin/linker64 system/bin/adbd
rm -f system/bin/fastbootd system/bin/toybox

# 删除与 vendor 完全相同的库（同名同大小，vendor 已提供 API 35 版本）
# twrp16/ 兼容库处理 ABI 差异

# 添加 CJK 字体和全部 23 种语言（保留 stock-vendor-hal/ 目录）
# 参见下方"语言支持"和"兼容层原理"章节

# LZ4 压缩
find . | cpio -o -H newc > /tmp/twrp.cpio
lz4 -l -9 /tmp/twrp.cpio /tmp/twrp-cjk.lz4
```

### 3. 重打包

```bash
python3 tools/repack_vendor_boot.py \
    --template /path/to/stock_vendor_boot.img \
    --fragment /tmp/twrp-cjk.lz4 \
    --output dash-CJK-vendor_boot.img
```

> `repack_vendor_boot.py` 保留 template 的 fragment 0（此时需已改造好 vendor ramdisk）。
> 实际操作中可将改造后的 vendor ramdisk 合并为一个新的 template image，或直接修改 repack 脚本。

### 4. 刷入

```bash
adb reboot bootloader
fastboot flash vendor_boot dash-CJK-vendor_boot.img
fastboot reboot
```

刷前备份原厂 vendor_boot。dash 是 VAB 结构，`vendor_boot` 分槽位，刷前确认活动槽。

### 兼容层原理

API 36 编译的 TWRP recovery 需要与 API 35 的 vendor 分区服务通信。解决方式是在 vendor ramdisk 中增加两处修改：

1. **`system/etc/init/hw/init.rc`** — 在 `service recovery` 内部添加（必须放在服务内部而非全局）：
   ```
   setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64
   ```

2. **新增 `system/lib64/twrp16/`**，放入 9 个 API 36 系统库：
   `libbase.so`、`libbootloader_message.so`、`libc++.so`、`libcutils.so`、`libfs_mgr.so`、`liblog.so`、`liblp.so`、`libprotobuf-cpp-lite.so`、`libutils.so`

   linker 加载 recovery binary 时优先搜索 `twrp16/`，找到这 9 个有 ABI break 的库；其余库回退到 vendor 的 API 35 版本。

### 最终镜像结构

| 片段 | 内容 | 典型大小 |
|------|------|----------|
| Fragment 0 | **vendor ramdisk**（删 recovery + res/ + 加 twrp16/ + init.rc setenv） | ~34 MB |
| Fragment 1 | **TWRP recovery**（CI 基 + CJK 字体 + 23 语言 + stock-vendor-hal） | ~19 MB |
| **已用** | | **~53 MB / 64 MB** |

### 关键注意事项

- **vendor ramdisk 的 platform 组件不能动**：`init`、`linker64`、`adbd`、`system/lib64/` 必须保持原样，否则系统无法启动或进入 EDL
- **`stock-vendor-hal/` 不能删**：虽然名字像 "NFC HAL"，但 keymint、gatekeeper、weaver 的 `setenv LD_LIBRARY_PATH` 中都指向它；缺少时 FBE 解密失效
- **语言文件必须加全部 23 个**：仅加 zh_CN/zh_TW 会导致语言切换按钮不出现
- **DroidSansFallback 字体**（3.6 MB）：从另一版 dash TWRP 镜像中提取的已验证字体，覆盖 34,461 字形（含 20,902 CJK 汉字），是已知最小的高质量 CJK 中文字体

---

## 语言支持

```
TW_EXTRA_LANGUAGES := true
```

TWRP Settings → Language 中可选简体中文、繁体中文、日语等。默认 UI 为英语。

---

## 已知问题

- MTP 传输 >4GiB 时 PC 端可能卡死；请用 `adb push`
- `adb sideload` 完成后 recovery 可能卡在 sideload 页面
- 震动 / 截图 / OTG 未适配
- `/cache` 实际指向小米的 `rescue` 分区（显示为 "Cache (Rescue)"），`wipeduringfactoryreset=0` 排除 Factory Reset，但手动 Wipe Cache 仍会清除

## 内部存储

使用 `/data/media/0`。recovery 运行时不保留 `/sdcard` 作为内部存储别名。

## 解密

FBE 解密依赖原厂 A15 vendor/odm 提供的 KeyMint、Gatekeeper 和 Weaver 服务。通过 `TW_KEEP_VENDOR_MOUNTED` / `TW_KEEP_ODM_MOUNTED` 保持所需分区可用。

keystore2 schema 版本应读取 `version` 表：`SELECT version FROM version WHERE id=0`。不要用 `PRAGMA user_version`。

## 触摸

触摸驱动：`xiaomi_touch_dash.ko` + `nt38771_touch_dash.ko`（vendor_dlkm 模块）。触摸上报依赖 `dash-touch-bridge` init service，设置 `/odm/lib64` 的 `LD_LIBRARY_PATH`。

## Format Data

Format Data 前先执行 `dmctl delete userdata`，删除失败则 fail-closed；等待 `/dev/block/mapper/userdata` 节点消失后格式化原始块设备。

## 致谢

- [TeamWin Recovery Project](https://github.com/TeamWin/android_bootable_recovery)
- [TWRP-Test](https://github.com/TWRP-Test) 维护的 [TWRP 16](https://github.com/TWRP-Test/platform_manifest_twrp_aosp) 分支
- [YorokobiMaster](https://github.com/YorokobiMaster) — dash 原始设备树作者
- [kinguser981](https://github.com/kinguser981) — TWRP-Recovery-Builder CI
- AI 辅助工具链（详见原始上游 README）

## 免责声明

本项目与 Xiaomi 无关，不分发或修改 Xiaomi vendor/system 运行时专有组件。软件按"现状"提供，不作任何担保。刷机变砖、数据丢失或售后影响均由使用者自行承担。
