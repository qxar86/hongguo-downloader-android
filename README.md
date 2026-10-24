# 红果短剧下载器 - 安卓版 v2 (Kivy + 原生 ffmpeg)

v1 的改写版。v1 没有 ffmpeg, 解密走 Python、合并被禁用;
v2 把原生 ffmpeg 打进 APK, 解密和合并都走 ffmpeg, Python 解密
只作失败回退。核心解密逻辑仍未重写。

## 原生 ffmpeg 是怎么进 APK 的 (关键机制, 已在本机 p4a 源码里核实)

1. `buildozer.spec` 的 `requirements` 里有 `ffmpeg,av_codecs`。
   p4a 的 ffmpeg 配方 (版本 8.0.1, 本机
   `.buildozer/android/platform/python-for-android/.../recipes/ffmpeg/__init__.py`)
   编译 ffmpeg 后执行 `cp ffmpeg ./lib/libffmpegbin.so`,
   这个文件作为原生库打进 APK 的 `lib/arm64-v8a/`。
2. 启动时 p4a 的 `start.c` (同一份源码, `bootstraps/common/build/jni/application/src/start.c`
   第 90 / 135-187 行) 扫描原生库, 把所有 `lib*bin.so` 链接到应用
   `.bin` 目录, 并把该目录追加进 `PATH`:
   `libffmpegbin.so -> ffmpeg`。
3. 所以应用 Python 进程里 `shutil.which("ffmpeg")` 直接命中,
   核心模块 `find_ffmpeg()` (先查应用目录, 再查 PATH) 不用改,
   `ffmpeg_decrypt()` 的
   `ffmpeg -decryption_key <key hex> -i enc -c copy out` 和
   `merge_videos()` 的 `ffmpeg -f concat -safe 0 -i list -c copy out`
   在手机上原样跑通。这是 p4a 官方文档的 "Running executables"
   机制, 不是往 APK 里塞一个来路不明的外挂二进制, 也不是 Termux。
4. `av_codecs` 不可省: 它让 ffmpeg 配方开全部 demuxer / muxer /
   parser (含 concat demuxer 和 hevc)。只加 `ffmpeg` 的基础配置里
   demuxer 只有 aac/h264/m4v/mov/mpegvideo/vc1/rtsp, 没有 concat,
   解密 (mov + mp4 流拷贝) 或许可行, 合并必失败。
   代价: 包体变大 (ffmpeg 库 + libx264/libshine/libvpx),
   构建时间变长。

## 文件

- `main.py` - Kivy 界面 (v2 改动: 合并勾选框启用, 下拉选档后
  按勾选传 `merge` 给核心批量下载; 启动时检测并打印 ffmpeg 路径;
  新增「检测原生 ffmpeg」按钮, 跑 `ffmpeg -version` 验真)
- `hongguo_downloader.py` - 核心模块, 与桌面版逐字节一致,
  未改一行业务代码 (详情 -> video_model -> spade 派生密钥 ->
  main_url 三形态还原 -> ffmpeg 解密 / Python 解密回退 -> 可选合并)
- `buildozer.spec` - 包名与 v1 相同
  `app.hongguo.hongguodownloader`, 版本升到 1.1, 仅 `arm64-v8a`,
  权限只有 `INTERNET` / `ACCESS_NETWORK_STATE`

## 手机上怎么用

1. 打开先点「检测原生 ffmpeg」, 日志出现 ffmpeg 版本行
   (如 `ffmpeg version 8.0.1 ...`) 才说明原生 ffmpeg 真就位了;
   若提示找不到, 说明 APK 不是用本版 spec 打的, 别往下走。
2. 输入剧集 ID / 详情页 URL / 单集 vid, 点「获取剧集信息」。
3. 集数框填 `all` (默认) 或 `1-5` / `1,3,5`, 点「查询可下载分辨率」,
   下拉选一档 (默认最高画质)。
4. 保存目录自己选 (1.2 新增): 「保存到」框默认是应用专属目录,
   可以直接改路径, 也可以点「浏览目录」用内置浏览器选目录 /
   新建文件夹, 点「选定当前目录」即生效, 下次打开仍是上次的目录。
   注意安卓 11+ 的限制: 只有专属目录能无条件写; 想存到 Download
   等公共目录, 须先点「开全部文件权限」去系统设置里授权, 否则
   下载开始前实测不可写时会明确提示并自动回退到默认专属目录,
   不会中途丢文件。
5. 需要合全集就勾上「下载完成后按集序合并成一个整体视频」,
   再点「开始下载」。合并产物与桌面版同名规则:
   `<剧名>_全N集.mp4` / `<剧名>_第A-B集.mp4` / `<剧名>_合并K集.mp4`,
   单集文件保留, 合并后读 mvhd 校验总时长 (误差 > max(3s, 2%)
   删产物判失败, 有失败集只合成功集)。
6. 成片默认在 `Android/data/app.hongguo.hongguodownloader/files/downloads/<剧名>/`,
   改了保存目录则在所选目录下。每集日志标注 `解密=ffmpeg`
   (或失败回退时的 `解密=python`)。

## 自己打包 (Linux)

与 v1 同一套命令, 只是目录换成 v2, 且**不要和 v1 的构建同时跑**:
本机内存只剩几百 MB, 两个 buildozer 并行 (尤其 ffmpeg + libx264
编译) 很容易 OOM; 等 v1 那轮跑完、或停掉它, 再起 v2。

```bash
cd ~/workspace/hongguo_apk_ffmpeg
export JAVA_HOME=~/workspace/android_toolchain/jdk-17.0.21+9
export PATH=~/workspace/android_toolchain/bin:$JAVA_HOME/bin:~/.local/bin:$PATH
export PIP_BREAK_SYSTEM_PACKAGES=1 TAR_OPTIONS=--no-same-owner
# 先起本地代理垫片 (127.0.0.1:18080, 解决 Java 工具过带认证出口代理的问题):
# python3 ~/workspace/android_toolchain/proxy_shim.py &
export http_proxy=http://127.0.0.1:18080 https_proxy=http://127.0.0.1:18080
export HTTP_PROXY=http://127.0.0.1:18080 HTTPS_PROXY=http://127.0.0.1:18080
export GRADLE_OPTS="-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=18080 \
  -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=18080 \
  -Dhttp.nonProxyHosts=localhost|127.0.0.1"
buildozer android debug
# 产物: bin/hongguodownloader-1.2-arm64-v8a-debug.apk
# 注意: build1.log 是 1.1 的一次失败记录 (hostpython3 下载到 99.8% 时
# 断连, 非代码问题; 残留的不完整包无完成标记, 重跑会自动删掉重下),
# 续建日志请看 build2.log。
```

首次打包要从头编译 python + SDL2 + ffmpeg + libx264 等,
比 v1 慢得多, `bin/` 为空 = 还没打完; 看日志尾部有没有
`Command failed` 才算失败。

## 回退链 (按顺序, 哪环断了都不至于交出加密文件)

ffmpeg 解密失败 / 找不到 ffmpeg -> Python CENC 解密
(核心模块, 有 senc 却零解密会直接报错, 不会把加密文件当成品);
无 ffmpeg 时合并跳过并明示, 单集下载不受影响。

## GitHub Actions 打包 (本地环境写文件被挡时的替代路)

仓库根目录已有 `.github/workflows/build-apk.yml`:
推到 GitHub 后, 在仓库 Actions 页手动点 Run workflow,
跑完在该次运行的 Artifacts 里下载 `hongguodownloader-apk`
(即 `bin/hongguodownloader-1.2-arm64-v8a-debug.apk`)。
工作流里只改了一处以适配干净 runner:
把 `android.skip_update` 从 True 改成 False,
让 buildozer 能用 sdkmanager 装 SDK; 提交的 spec 本身没动,
本地仍按手动装好的 SDK 走。
注意: 不要把本地 `build*.log` 推上去 (里面含本机环境信息),
`.gitignore` 已把 `*.log`、`.buildozer/`、`bin/` 排除。
首次干净构建要从头编译 python + SDL2 + ffmpeg + libx264,
耗时较长, 耐心等它跑完; 后续运行会复用缓存的 `.buildozer`。
