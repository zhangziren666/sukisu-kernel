# SukiSU-Ultra 内核编译包 · OPPO A32/A53（sm4250 · non-GKI · Android 12）

云端（GitHub Actions）编译 **OPPO 官方 sm4250 内核源码 + 最新版 SukiSU-Ultra**，
产出 `Image.gz`，再用本地脚本把内核塞回你的原厂 `boot.img`。

> 你本地是 Windows、没有 git / WSL / Docker，所以**编译放在 GitHub 云端**做；
> 本地只做两件事：上传 3 个小文件、以及最后一步重打包。

---

## 一、包内文件

```
.github/workflows/build.yml   # GitHub Actions 编译流水线（全部逻辑在这里）
repack_boot.py                # 本地重打包工具（Windows 直接跑，无需 git/WSL）
README.md                     # 本说明
```

---

## 二、已实测确认的目标环境

| 项 | 值 |
|---|---|
| 机型 | OPPO A32 / A53（OPlus，Android 12） |
| SoC | 高通 Bengal（SM4250 / SM6115） |
| 内核 | `Linux 4.19.152-perf+`，**非 GKI**（clang 10.0.7 编译） |
| boot.img | Android boot header **v2**，page_size 4096，96 MB |
| boot.img 分段 | kernel(16.2MB gz) + ramdisk(1.0MB) + **dtb(4.3MB, 15 个设备树)** + vbmeta/AVB 页脚 |
| 内核源码 | `oppo-source/android_kernel_oppo_sm4250`，分支 `oppo/sm4250_s_12.1_f19` |
| 配套源码 | `oppo-source/android_kernel_modules_and_devicetree_oppo_sm4250`，同分支 |
| defconfig | `arch/arm64/configs/vendor/bengal-perf_defconfig` |
| 工具链 | clang **r383902 (=10.0.7，与原厂一致)** + GNU binutils/gcc 4.9 |
| SukiSU 方案 | **main 分支（kprobe/kretprobe 运行时注册）** —— 不改内核源码，风险最低 |

---

## 三、操作步骤

### 第 1 步 · 把本目录内容变成你自己的 GitHub 仓库

1. GitHub 上新建一个仓库（Public 或 Private 都行），例如 `sukisu-kernel`。
2. 用网页 **Add file → Upload files** 上传 `README.md` 和 `repack_boot.py`。
3. 用 **Add file → Create new file**，文件名直接填
   `.github/workflows/build.yml`（斜杠会自动创建目录），把 `build.yml` 内容粘进去。
4. 提交到 `main` 分支 —— **推送即自动触发一次编译**。

> 不需要 fork 任何仓库，也不需要本地 git。

### 第 2 步 · 在 GitHub 上等编译完成

- 打开仓库 **Actions** 页 → `Build SukiSU-Ultra Kernel`。
- 若想手动重跑：**Run workflow**，`sukisu_branch` 保持 `main`。
- 首次约 **30–60 分钟**（工具链会缓存，之后重跑快很多）。
- 编译结束会产出 artifact：`sukisu-kernel-main`
  （含 `Image.gz` / `vmlinux` / `System.map`，**刷机只需要 `Image.gz`**）。

### 第 3 步 · 本地重打包

下载 artifact 解压，把 `Image.gz` 放到任意目录（例如 `D:\sukisu\Image.gz`）。
在你的 Windows 上执行（用你已有的 Python）：

```bat
python d:\tare\sukisu_build\repack_boot.py --kernel D:\sukisu\Image.gz --out boot_sukisu.img
```

默认会自动使用你提供的原厂镜像
`d:\54188\C12最新版\C12最新版\images\boot.img`。
输出 `boot_sukisu.img`，**大小与分区一致（96 MB）**，只替换了内核段，
原厂 ramdisk / dtb / AVB 结构原样保留。

### 第 4 步 · 刷机

```bat
fastboot flash boot boot_sukisu.img
```

**必须先关掉 AVB 校验**，否则新内核哈希对不上会被 bootloader 拒绝：

```bat
fastboot flash vbmeta --disable-verity --disable-verification <vbmeta.img>
```

（或直接跑你原有的 `oppoa32-a53-FLASH.bat` 全量流程；工程机 / 已解锁机器通常本身就不校验。）

---

## 四、本次做了哪些优化

| 项目 | 改动 | 作用 | 风险 |
|---|---|---|---|
| **SukiSU 集成** | 内核源码接入 SukiSU-Ultra `main` 分支；`CONFIG_KSU=y`、`CONFIG_KPROBES=y`、`CONFIG_KRETPROBES=y` | 获得 KernelSU/SukiSU root 能力 | 低 |
| **模块签名兼容** | 关闭 `CONFIG_MODULE_SIG_FORCE` | 重编内核会换签名公钥，不关掉的话 **vendor 分区预置的 `.ko` 会全部拒载**，可能直接不开机 | 低（必要项） |
| **编译期** | 关闭 `CONFIG_DEBUG_INFO` | vmlinux 变小、编译更快 | 无（仅少调试符号） |
| **运行期开销** | 关闭 `CONFIG_PAGE_OWNER` / `CONFIG_SCHEDSTATS` / `CONFIG_DEBUG_LIST` / `CONFIG_SLUB_DEBUG` / `CONFIG_KMALLOC_DEBUG` / `CONFIG_VMALLOC_DEBUG` / `CONFIG_DUMP_TASKS_MEM` | 去掉一批调试用内存/链表检查，降低内存与调度路径开销 | 中低（出问题时排查信息变少） |
| **网络** | `CONFIG_TCP_CONG_BBR=y`、`CONFIG_NET_SCH_FQ=y`、`CONFIG_DEFAULT_BBR=y` | BBR 拥塞控制 + fq 队列，弱网/高延迟下吞吐更稳 | 低 |
| **存储** | `CONFIG_ZRAM_DEDUP=y`、`ZSTD`/`LZ4` 压缩（原 defconfig 已开，重申） | 内存压缩更快更省 | 低 |
| **调度/温控** | **未改动**（`CPU_BOOST`、`SCHED_TUNE`、`SCHED_WALT`、`CPU_FREQ_GOV_SCHEDUTIL` 原厂已开） | 调频/温控策略改动很容易导致过热或掉帧，收益不确定 | —— |

> 想要更激进的调频 / 温控放宽，建议**刷完开机后用 sysfs 运行时调整**（可随时改回），
> 而不是写进内核 defconfig。需要的话告诉我，我另出脚本。

---

## 五、风险与回刷（务必先读）

1. **务必先备份原 `boot.img`（以及 `dtbo` / `vbmeta`）。** 本工具不会改动原文件。
2. **AVB 签名**：内核被替换后哈希必然失效，而我们**没有 OEM 私钥，无法重新签名**。
   所以必须 `--disable-verification` 或走全量刷写流程，否则 bootloader 会拒绝启动。
3. **bootloader 必须已解锁**，且已按 `FLASH.bat` 刷过 `abl` / `debugabl`。
4. **非官方内核可能不亮机**：源码与 defconfig 取自 OPPO 官方同分支同版本，匹配度较高，
   但仍不排除与原厂 ROM 的兼容问题（首次开机可能较慢、需双清 `fastboot -w`）。
5. **救砖**：起不来时用 `oppoa32-a53-FLASH.bat` 全量回刷原厂包即可恢复。
6. 这是**一次性云编译方案**：我没有在本地实际跑过这次 CI（本地无编译环境），
   首次运行若报错，把 Actions 日志发我，我按日志调。

---

## 六、常见问题

**Q：Actions 报 "无法获取 clang r383902 工具链"？**
A：workflow 里 `Fetch toolchains` 步骤已内置 3 个镜像仓库的备选，
   若全挂则说明这些第三方镜像被删了 —— 把日志发我，换源即可。

**Q：`CONFIG_KSU 未生效`？**
A：说明 SukiSU 的 `setup.sh` 没能把 `drivers/kernelsu` 挂上（通常是
   `CONFIG_KPROBES` 依赖没满足或 setup 脚本网络失败）。日志里有
   `drivers/kernelsu` 与 `drivers/Kconfig` 的检查输出，发我即可定位。

**Q：想编 SukiSU 的其他分支 / 版本？**
A：Actions → Run workflow，把 `sukisu_branch` 改成 `builtin` 等（但 builtin 需要改内核源码，本流程未适配）。

**Q：`Image.gz` 比原内核大，会不会有问题？**
A：`repack_boot.py` 会保持输出大小 == 原镜像 96 MB，并把 AVB 区后移 + 同步修正页脚偏移字段，
   已用真实镜像做过大小两种路径的验证。

**Q：为什么 DTB 用原厂的，不用新编的？**
A：新内核与原厂内核是**同一份源码同一版本**，DTB 天然兼容；用原厂 DTB 少一个出错来源，
   也让 CI 不必编 dtbs、产物更小。
