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

CI 只编译了 recovery binary，vendor ramdisk 必须用 **rc1 模板**（含 `system/lib64/twrp16/` 兼容库目录）。

### 工具

```bash
python3 device/xiaomi/dash/tools/repack_vendor_boot.py \
    --template dash-twrp16-v1.0.0-rc1-vendor_boot.img \
    --fragment twrp-fragment.lz4 \
    --output /tmp/dash-FINAL.img
```

### 参数

| 参数 | 说明 |
|------|------|
| `--template` | rc1 模板 vendor_boot（提供含 `twrp16/` 库的 vendor ramdisk） |
| `--fragment` | 新 recovery fragment（LZ4 压缩的 cpio） |
| `--output` | 输出路径，默认 `/tmp/dash-FINAL.img` |

脚本工作原理：解析 v4 header，保留 fragment 0（vendor ramdisk），替换 fragment 1（recovery），填充到 64MB 分区大小，拷贝 AVB footer。

### rc1 模板的兼容层原理

rc1 在 vendor ramdisk（fragment 0）中修改了两处，使得 API 36 编译的 recovery 能在 API 35 的 vendor 环境中运行：

1. **`system/etc/init/hw/init.rc`** 添加：
   ```
   setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64
   ```

2. **新增 `system/lib64/twrp16/`**，放入 9 个 API 36 系统库：
   `libbase.so`、`libbootloader_message.so`、`libc++.so`、`libcutils.so`、`libfs_mgr.so`、`liblog.so`、`liblp.so`、`libprotobuf-cpp-lite.so`、`libutils.so`

linker 加载 recovery binary 时优先搜索 `twrp16/`，找到这 9 个有 ABI break 的库；其余库回退到系统的 API 35 版本。

### 刷入

```bash
adb reboot bootloader
fastboot flash vendor_boot /tmp/dash-FINAL.img
fastboot reboot
```

刷前备份原厂 vendor_boot。dash 是 VAB 结构，`vendor_boot` 分槽位，刷前确认活动槽。

---

## 本地构建

```bash
cd source-twrp16
source build/envsetup.sh
lunch twrp_dash-bp2a-eng
# 或使用 local_manifest.xml 同步所有依赖
mkdir -p .repo/local_manifests
cp device/xiaomi/dash/local_manifest.xml .repo/local_manifests/dash.xml
repo sync

SOONG_GOMEMLIMIT=8GiB SOONG_GOGC=20 m recovery vendorbootimage -j4
```

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
