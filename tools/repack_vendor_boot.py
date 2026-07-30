#!/usr/bin/env python3
"""Pure CI: F1 keeps all recovery essential libs, twrp16 only for known ABI breaks."""
import struct, subprocess, shutil, os
PAGE_SIZE=4096;PARTITION_SIZE=67108864
def align(v,a):return(v+a-1)//a*a
def parse(p):
    d=open(p,'rb').read();hs=struct.unpack_from('<I',d,2096)[0]
    rs=struct.unpack_from('<I',d,24)[0];ds=struct.unpack_from('<I',d,2100)[0]
    ro=align(hs,PAGE_SIZE);tblo=align(align(ro+rs,PAGE_SIZE)+align(ds,PAGE_SIZE),PAGE_SIZE)
    ec=struct.unpack_from('<I',d,2116)[0];es=struct.unpack_from('<I',d,2120)[0]
    return d[:PAGE_SIZE],ds,[d[ro+off:ro+off+struct.unpack_from('<I',d,tblo+i*es)[0]]for i in range(ec)for _,off,_ in [struct.unpack_from('<III',d,tblo+i*es)]],d[align(ro+rs,PAGE_SIZE):align(ro+rs,PAGE_SIZE)+ds]
def unpack(data,dest):
    if os.path.exists(dest):shutil.rmtree(dest)
    os.makedirs(dest)
    subprocess.run(['cpio','-idm'],input=subprocess.run(['lz4','-d','-c','/dev/stdin'],input=data,capture_output=True).stdout,capture_output=True,cwd=dest)
def pack(src):
    r=subprocess.run(['find','.','-print0'],capture_output=True,cwd=src)
    p=subprocess.run(['cpio','-o','-H','newc','--null'],input=r.stdout,capture_output=True,cwd=src)
    subprocess.run(['lz4','-l','-9','--force','-','/tmp/_out.lz4'],input=p.stdout,capture_output=True)
    return open('/tmp/_out.lz4','rb').read()

page0,ds,stk_frags,stk_dtb=parse('/home/lakitu/下载/vendor_boot.img')
_,_,ci_frags,_=parse('/tmp/vendor_boot.img')

# F0
f0d='/tmp/_f0';unpack(stk_frags[0],f0d)
for p in[f'{f0d}/system/bin/recovery',f'{f0d}/res']:
    if os.path.exists(p):(shutil.rmtree if os.path.isdir(p)else os.remove)(p)
irc=f'{f0d}/system/etc/init/hw/init.rc'
if os.path.exists(irc):
    c=open(irc).read()
    tag='service recovery /system/bin/recovery\n    socket recovery stream 422 system system\n    seclabel u:r:recovery:s0\n    user root'
    if tag in c and'setenv'not in c:
        c=c.replace(tag,tag+'\n    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64')
    c=c.replace('\nservice charger /system/bin/charger\n    critical\n','\nservice charger /system/bin/charger\n')
    open(irc,'w').write(c)

# F1
f1d='/tmp/_f1';unpack(ci_frags[1],f1d)

# Init cleanup: keep only keystore2.rc + fastboot-service.rc
if os.path.isdir(f'{f1d}/system/etc/init'):
    for f in os.listdir(f'{f1d}/system/etc/init'):
        if f not in('keystore2.rc','android.hardware.fastboot-service.example_recovery.rc'):
            p=os.path.join(f'{f1d}/system/etc/init',f)
            if os.path.isfile(p):os.remove(p)
            elif os.path.isdir(p):shutil.rmtree(p)

for f in os.listdir(f1d):
    if f.startswith('init.recovery.')and f not in('init.recovery.mt6991.rc','init.recovery.project.rc'):
        p=os.path.join(f1d,f)
        if os.path.isfile(p):os.remove(p)

for f in['odm','odm_dlkm','vendor_dlkm','oem','res']:
    p=os.path.join(f1d,f)
    if os.path.islink(p)or os.path.isfile(p):
        try:os.unlink(p)
        except:pass
    elif os.path.isdir(p):
        try:shutil.rmtree(p)
        except:pass

for f in os.listdir(f1d):
    if any(k in f for k in['timestamp','sha256','ramdisk-files','relink']):
        fp=os.path.join(f1d,f)
        if os.path.isfile(fp):os.remove(fp)

# Recovery transitive deps
def get_deps(bp,ld,vis=None):
    if vis is None:vis=set()
    if bp in vis:return set()
    vis.add(bp);deps=set()
    r=subprocess.run(['readelf','-d',bp],capture_output=True,text=True)
    for l in r.stdout.split('\n'):
        if'NEEDED'in l:
            lib=l.split('[')[1].split(']')[0];deps.add(lib)
            lp=os.path.join(ld,lib)
            if os.path.exists(lp):deps.update(get_deps(lp,ld,vis))
    return deps

essential=get_deps(os.path.join(f1d,'system/bin/recovery'),os.path.join(f1d,'system/lib64'))
essential.update(['libresetprop.so','libsysutils.so'])
print(f'Essential libs: {len(essential)}')

# Remove ALL F0-duplicate libs from F1 EXCEPT those needed by system services
# Services (health HAL, etc.) run WITHOUT LD_LIBRARY_PATH, need API 36 libs in /system/lib64/
f0_libs=set()
if os.path.isdir(f'{f0d}/system/lib64'):
    for f in os.listdir(f'{f0d}/system/lib64'):
        if os.path.isfile(os.path.join(f'{f0d}/system/lib64',f)):f0_libs.add(f)

# System libs that MUST stay in F1 (services don't use LD_LIBRARY_PATH)
# Reference F1 keeps only these 5 system libs from F0:
system_keep = {'libbinder.so','libc.so','libhidlbase.so','libzstd.so',
               'libboot_control_client.so'}

if os.path.isdir(f'{f1d}/system/lib64'):
    rem=0
    kept=0
    sys_kept=0
    for f in list(os.listdir(f'{f1d}/system/lib64')):
        if f=='twrp16':continue
        fp=os.path.join(f'{f1d}/system/lib64',f)
        if not os.path.isfile(fp):continue
        if f in system_keep:
            sys_kept+=1;continue  # Keep for system services
        if f in f0_libs:
            os.remove(fp);rem+=1
        else:
            kept+=1
    print(f'Libs: kept {kept} CI-specific + {sys_kept} system (for services), removed {rem} F0 dupes')

# Add setenv LD_LIBRARY_PATH to keystore2.rc (it needs API 36 libs from twrp16/)
k2rc=os.path.join(f1d,'system/etc/init/keystore2.rc')
if os.path.exists(k2rc):
    c=open(k2rc).read()
    if 'service keystore2' in c and 'setenv' not in c:
        c=c.replace('service keystore2 /system/bin/keystore2\n',
                    'service keystore2 /system/bin/keystore2\n'
                    '    setenv LD_LIBRARY_PATH /system/lib64/twrp16:/system/lib64\n')
        open(k2rc,'w').write(c)
        print('  + setenv in keystore2.rc')

# twrp16 compat: only libs with known API 36 ABI breaks
ci_f1_dir='/tmp/_ci_f1_orig'
unpack(ci_frags[1],ci_f1_dir)
tdir=f'{f0d}/system/lib64/twrp16'
os.makedirs(tdir,exist_ok=True)
compat=['libbase.so','libbootloader_message.so','libc++.so','libcutils.so','libfs_mgr.so',
        'liblog.so','liblp.so','libprotobuf-cpp-lite.so','libutils.so','libbinder_ndk.so','libbinder.so',
        'libsqlite.so']
for lib in compat:
    src=os.path.join(ci_f1_dir,'system/lib64',lib)
    dst=os.path.join(tdir,lib)
    if os.path.exists(src)and not os.path.exists(dst):
        shutil.copy2(src,dst,follow_symlinks=False)
print(f'twrp16/: {len(os.listdir(tdir))} libs')

# Bin dedup
f0_bins=set()
if os.path.isdir(f'{f0d}/system/bin'):
    for f in os.listdir(f'{f0d}/system/bin'):
        if os.path.isfile(os.path.join(f'{f0d}/system/bin',f)):f0_bins.add(f)
ttools={'recovery','minadbd','sgdisk','awk','bc','resize2fs','pigz','unpigz',
        'tune2fs','ziptool','fsck.fat','bu','sload_f2fs','parted','blkid','dmctl'}
if os.path.isdir(f'{f1d}/system/bin'):
    bd=0
    for f in list(os.listdir(f'{f1d}/system/bin')):
        fp=os.path.join(f'{f1d}/system/bin',f)
        if os.path.islink(fp):os.unlink(fp);bd+=1;continue
        if os.path.isdir(fp):
            if f=='hw':
                for hf in os.listdir(fp):
                    if os.path.isfile(os.path.join(fp,hf)):os.remove(os.path.join(fp,hf))
            continue
        if not os.path.isfile(fp):continue
        if f in ttools:continue
        if f in('keystore2','keystore_cli_v2'):continue
        if f in f0_bins:os.remove(fp);bd+=1
    print(f'Bin dedup: removed {bd}')

# Add missing libs from reference
ref_img='/home/lakitu/下载/vendor_boot-dash-OS3.0.305.WPLCNXM-lakitu-0.img'
if os.path.exists(ref_img):
    ref_d=open(ref_img,'rb').read()
    ref_hs=struct.unpack_from('<I',ref_d,2096)[0];ref_rs=struct.unpack_from('<I',ref_d,24)[0]
    ref_ro=align(ref_hs,PAGE_SIZE)
    ref_tblo=align(align(ref_ro+ref_rs,PAGE_SIZE)+align(struct.unpack_from('<I',ref_d,2100)[0],PAGE_SIZE),PAGE_SIZE)
    ref_es=struct.unpack_from('<I',ref_d,2120)[0]
    ref_sz,ref_off,ref_typ=struct.unpack_from('<III',ref_d,ref_tblo+1*ref_es)
    ref_r=subprocess.run(['lz4','-d','-c','/dev/stdin'],input=ref_d[ref_ro+ref_off:ref_ro+ref_off+ref_sz],capture_output=True)
    ref_td='/tmp/_ref_add'
    if os.path.exists(ref_td):shutil.rmtree(ref_td)
    os.makedirs(ref_td)
    subprocess.run(['cpio','-idm'],input=ref_r.stdout,capture_output=True,cwd=ref_td)
    for lib in['libresetprop.so','libsysutils.so','libfuse-lite.so','libfscrypttwrp.so']:
        s=os.path.join(ref_td,'system/lib64',lib);d=os.path.join(f1d,'system/lib64',lib)
        if os.path.exists(s)and not os.path.exists(d):shutil.copy2(s,d,follow_symlinks=False)
    for sd in['system/etc/vintf/manifest']:
        s=os.path.join(ref_td,sd);d=os.path.join(f1d,sd)
        if os.path.isdir(s):
            os.makedirs(d,exist_ok=True)
            for xml in os.listdir(s):
                if 'health' in xml:continue  # Reference has no health VINTF - vendor provides it
                shutil.copy2(os.path.join(s,xml),os.path.join(d,xml),follow_symlinks=False)
            print(f'  + {sd}/ ({len(os.listdir(d))} XMLs)')
    for sd in['system/lib64/stock-vendor-hal']:
        s=os.path.join(ref_td,sd);d=os.path.join(f1d,sd)
        if os.path.isdir(s)and not os.path.isdir(d):shutil.copytree(s,d,symlinks=True)
    for src in['lib/modules/nt38771_touch_dash.ko','lib/modules/xiaomi_touch_dash.ko',
               'lib/modules/modules.load.recovery','system/bin/touch_report_debug']:
        s=os.path.join(ref_td,src)
        if os.path.exists(s):
            d=os.path.join(f1d,src);os.makedirs(os.path.dirname(d),exist_ok=True)
            shutil.copy2(s,d,follow_symlinks=False)
    shutil.rmtree(ref_td)
shutil.rmtree(ci_f1_dir)

if not os.path.lexists(os.path.join(f1d,'init')):
    os.symlink('/system/bin/init',os.path.join(f1d,'init'))
for pf in['default.prop','prop.default']:
    fp=os.path.join(f1d,pf)
    if os.path.exists(fp):
        c=open(fp).read()
        if'ro.build.type=eng'in c:open(fp,'w').write(c.replace('ro.build.type=eng','ro.build.type=user'))
    # Set battery path for TWRP
    fp=os.path.join(f1d,'default.prop')
    if os.path.exists(fp):
        c=open(fp).read()
        if 'twrp.battery_path' not in c:
            c+='\nro.twrp.battery_path=/sys/class/power_supply/battery/capacity\n'
            open(fp,'w').write(c)

f0=pack(f0d);f1=pack(f1d)
print(f'F0: {len(f0)/1024/1024:.2f} MB')
print(f'F1: {len(f1)/1024/1024:.2f} MB')
t=len(f0)+len(f1);print(f'Total: {t/1024/1024:.2f} MB / 64 MB')
if t+2*PAGE_SIZE>PARTITION_SIZE:print('OVER!');exit(1)

rsz=len(f0)+len(f1);dtb_off=align(PAGE_SIZE+rsz,PAGE_SIZE);tblo=align(dtb_off+ds,PAGE_SIZE)
img=bytearray(PARTITION_SIZE)
img[:PAGE_SIZE]=page0[:PAGE_SIZE]
struct.pack_into('<I',img,24,rsz);struct.pack_into('<I',img,2100,ds)
img[PAGE_SIZE:PAGE_SIZE+len(f0)]=f0
img[PAGE_SIZE+len(f0):PAGE_SIZE+rsz]=f1
img[dtb_off:dtb_off+len(stk_dtb)]=stk_dtb
def entry(sz,off,typ,name=''):
    e=bytearray(108)
    struct.pack_into('<I',e,0,sz);struct.pack_into('<I',e,4,off);struct.pack_into('<I',e,8,typ)
    e[12:12+len(name.encode()[:31])]=name.encode()[:31]
    return e
img[tblo:tblo+108]=entry(len(f0),0,1,'')
img[tblo+108:tblo+216]=entry(len(f1),len(f0),2,'')
struct.pack_into('<I',img,2112,216);struct.pack_into('<I',img,2116,2);struct.pack_into('<I',img,2120,108)
with open('/home/lakitu/下载/dash-PURE-CI.img','wb')as f:f.write(bytes(img))
assert os.path.getsize('/home/lakitu/下载/dash-PURE-CI.img')==PARTITION_SIZE
print('Done!')
