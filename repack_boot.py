#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repack_boot.py —— 把 CI 编译出的 Image.gz 塞回原厂 boot.img（Android boot header v2）

用法：
    python repack_boot.py --kernel Image.gz
    python repack_boot.py --kernel Image.gz --orig "D:\\...\\boot.img" --out boot_sukisu.img

设计原则（尽量"外科手术式"替换，其余字节全部保全）：
  * 只替换内核段（kernel），保留原厂 ramdisk 段与 dtb 段 **原样字节**
    —— 设备树用原厂的更安全（新内核与它同版本源码，DTB 天然兼容）。
  * boot.img 的 AVB(vbmeta) 结构与 AVBf 页脚原样保留在文件尾部。
  * 输出文件大小 == 原文件大小（= 分区大小），避免 fastboot 因尺寸不符报错。

局限（重要）：
  * 内核被替换后，AVB 里的哈希/签名必然失效，而 **我们没有 OEM 私钥，无法重新签名**。
    因此刷机前必须让校验失效：
        fastboot flash vbmeta --disable-verity --disable-verification vbmeta.img
    （或使用你原有的 FLASH.bat 全量刷写流程；工程机/已解锁机器通常本身就不校验）
"""

import argparse
import os
import struct
import sys

BOOT_MAGIC = b'ANDROID!'
AVB_FOOTER_MAGIC = b'AVBf'


def be_u32(buf, off):
    return struct.unpack_from('>I', buf, off)[0]


def be_u64(buf, off):
    return struct.unpack_from('>Q', buf, off)[0]


def pad(x, page):
    return (x + page - 1) // page * page


def parse_boot(data):
    if data[:8] != BOOT_MAGIC:
        raise SystemExit('不是 Android boot 镜像（magic 不是 ANDROID!）')
    hdr = {
        'kernel_size': struct.unpack_from('<I', data, 8)[0],
        'kernel_addr': struct.unpack_from('<I', data, 12)[0],
        'ramdisk_size': struct.unpack_from('<I', data, 16)[0],
        'ramdisk_addr': struct.unpack_from('<I', data, 20)[0],
        'second_size': struct.unpack_from('<I', data, 24)[0],
        'page_size': struct.unpack_from('<I', data, 36)[0],
        'header_version': struct.unpack_from('<I', data, 40)[0],
        'os_version': struct.unpack_from('<I', data, 44)[0],
        'header_size': struct.unpack_from('<I', data, 1644)[0] if len(data) > 1648 else 0,
        'dtb_size': struct.unpack_from('<I', data, 1648)[0] if len(data) > 1652 else 0,
    }
    return hdr


def main():
    ap = argparse.ArgumentParser(description='把 Image.gz 重打包回 Android boot.img (header v2)')
    ap.add_argument('--orig', default=r'd:\54188\C12最新版\C12最新版\images\boot.img',
                    help='原始 boot.img 路径（默认使用你提供的原厂镜像）')
    ap.add_argument('--kernel', default='Image.gz', help='新编译的 Image.gz 路径')
    ap.add_argument('--out', default='boot_sukisu.img', help='输出 boot.img 路径')
    ap.add_argument('--backup-suffix', default='.orig', help='未指定时也会提示手动备份')
    args = ap.parse_args()

    orig_path = os.path.abspath(args.orig)
    kern_path = os.path.abspath(args.kernel)
    out_path = os.path.abspath(args.out)

    if not os.path.isfile(orig_path):
        raise SystemExit('找不到原始 boot.img: %s' % orig_path)
    if not os.path.isfile(kern_path):
        raise SystemExit('找不到 Image.gz: %s（请确认已从 GitHub Actions 下载 artifact 并解压）' % kern_path)

    orig = open(orig_path, 'rb').read()
    new_kernel = open(kern_path, 'rb').read()
    total = len(orig)

    hdr = parse_boot(orig)
    ps = hdr['page_size']
    if hdr['header_version'] not in (1, 2):
        raise SystemExit('仅支持 boot header v1/v2，当前为 v%d' % hdr['header_version'])

    ks, rs, ss, ds = hdr['kernel_size'], hdr['ramdisk_size'], hdr['second_size'], hdr['dtb_size']

    # ---- 分段定位 ----
    o_kernel = ps
    o_ramdisk = o_kernel + pad(ks, ps)
    o_second = o_ramdisk + pad(rs, ps)
    o_dtb = o_second + pad(ss, ps)
    o_tail = o_dtb + pad(ds, ps)

    if o_tail > total:
        raise SystemExit('镜像分段越界，可能不是标准 boot.img')

    kernel_old = orig[o_kernel:o_kernel + ks]
    ramdisk = orig[o_ramdisk:o_ramdisk + rs]
    second = orig[o_second:o_second + ss] if ss else b''
    dtb = orig[o_dtb:o_dtb + ds] if ds else b''
    tail = orig[o_tail:]

    print('=' * 74)
    print('原始 boot.img : %s' % orig_path)
    print('  文件大小     : %d 字节 (%.1f MB)' % (total, total / 1048576.0))
    print('  header v%d / page_size=%d / header_size=%d' % (hdr['header_version'], ps, hdr['header_size']))
    print('  kernel   @%-9d size=%-10d (%.1f MB gz)' % (o_kernel, ks, ks / 1048576.0))
    print('  ramdisk  @%-9d size=%-10d' % (o_ramdisk, rs))
    print('  second   @%-9d size=%-10d' % (o_second, ss))
    print('  dtb      @%-9d size=%-10d (%d 个设备树)' % (o_dtb, ds, dtb.count(b'\xd0\x0d\xfe\xed') if ds else 0))
    print('  尾部(AVB) @%-9d size=%-10d' % (o_tail, len(tail)))

    if not kernel_old[:2] == b'\x1f\x8b':
        print('  [注意] 原内核段不是 gzip 头，仍按原布局处理')

    # ---- AVB 页脚解析（页脚位于文件最后 64 字节）----
    blob = b''
    footer = b''
    blob_size = 0
    if tail[-64:-60] == AVB_FOOTER_MAGIC:
        footer = tail[-64:]
        blob_size = be_u64(footer, 28)          # vbmeta_size
        blob = tail[:blob_size]
        print('  AVB 页脚     : original_image_size=%d  vbmeta_offset=%d  vbmeta_size=%d'
              % (be_u64(footer, 12), be_u64(footer, 20), blob_size))
    else:
        print('  未检测到 AVBf 页脚（unsigned 镜像），尾部将原样保留')

    # ---- 构造新镜像前缀 ----
    header = bytearray(orig[:ps])
    struct.pack_into('<I', header, 8, len(new_kernel))     # 更新 kernel_size

    prefix = bytes(header) + new_kernel.ljust(pad(len(new_kernel), ps), b'\0')
    prefix += ramdisk.ljust(pad(len(ramdisk), ps), b'\0')
    if ss:
        prefix += second.ljust(pad(len(second), ps), b'\0')
    if ds:
        prefix += dtb.ljust(pad(len(dtb), ps), b'\0')

    print('-' * 74)
    print('新内核        : %s (%d 字节 gz, %.2f MB)  %+d 字节 vs 原内核'
          % (kern_path, len(new_kernel), len(new_kernel) / 1048576.0, len(new_kernel) - ks))

    # ---- 拼装输出，保持文件大小 == 原文件大小 ----
    free = total - len(tail)          # 尾部 AVB 区之前可用的空间
    if len(prefix) <= free:
        # 最优路径：AVB 区整体锚定在原偏移，页脚/vbmeta 内容 100% 不变
        out = prefix + b'\0' * (free - len(prefix)) + tail
        mode = 'AVB 区锚定原位（页脚与 vbmeta 字节完全未改动）'
    else:
        if not blob:
            raise SystemExit('新内核超出可用空间且镜像无 AVB 结构，无法保持原尺寸：请把原 boot.img 一并提供')
        blob_off = total - 64 - blob_size
        if len(prefix) > blob_off:
            raise SystemExit('新内核过大，超出 boot 分区可用空间（prefix=%d > %d）' % (len(prefix), blob_off))
        out = bytearray(prefix + b'\0' * (blob_off - len(prefix)) + blob + footer)
        # 修正页脚字段，使其指向新的 vbmeta 位置（哈希与签名仍是旧的 —— 无法重新签名）
        struct.pack_into('>Q', out, total - 64 + 12, blob_off)   # original_image_size
        struct.pack_into('>Q', out, total - 64 + 20, blob_off)   # vbmeta_offset
        struct.pack_into('>Q', out, total - 64 + 28, blob_size)  # vbmeta_size
        out = bytes(out)
        mode = 'AVB(vbmeta) 区已后移，页脚偏移字段已同步修正'

    if len(out) != total:
        raise SystemExit('内部错误：输出大小 %d != 原大小 %d' % (len(out), total))

    open(out_path, 'wb').write(out)

    print('-' * 74)
    print('输出          : %s' % out_path)
    print('  大小         : %d 字节 (%.1f MB)  == 原镜像大小' % (len(out), len(out) / 1048576.0))
    print('  布局         : %s' % mode)
    print('  kernel_size  : %d (头部字段已更新)' % len(new_kernel))
    print('=' * 74)
    print()
    print('刷机提示：')
    print('  1) 务必先备份原 boot.img（本工具不会自动改原文件）')
    print('  2) fastboot flash boot %s' % os.path.basename(out_path))
    print('  3) 内核被替换后 AVB 校验必然失败，且无 OEM 私钥无法重签，需先关闭校验：')
    print('     fastboot flash vbmeta --disable-verity --disable-verification <vbmeta.img>')
    print('     （或直接跑你原有的 FLASH.bat 全量刷写流程）')
    print('  4) 起不来/卡开机：用 FLASH.bat 回刷原厂包即可救砖')


if __name__ == '__main__':
    sys.exit(main())
