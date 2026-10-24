[app]

# (str) Title of your application
title = 红果短剧下载器

# (str) Package name
package.name = hongguodownloader

# (str) Package domain (needed for android/ios packaging)
package.domain = app.hongguo

# (str) Source code where the main.py live
source.dir = .

# (list) Source files extensions
source.include_exts = py

# (str) Application versioning (method 1)
# 1.2: 新增用户手动选择下载目录 (1.1 的 build1 因网络下载失败, 从未出包,
# 所以 1.2 才是第一版真正要装的包)
version = 1.2

# (list) Application requirements
# ffmpeg + av_codecs 是 v2 的关键: p4a 的 ffmpeg 配方把 ffmpeg 可执行文件
# 拷成 libffmpegbin.so 打进 APK, 启动时 start.c 链接到 .bin 目录并追加进
# PATH, Python 里 shutil.which("ffmpeg") 即可找到, 核心模块的
# ffmpeg_decrypt() / merge_videos() 原样走通。
# av_codecs 让 ffmpeg 开全部 demuxer/muxer/parser (含 concat 和 hevc),
# 否则基础配置缺 concat demuxer, 合并会失败; 代价是包体变大、构建变慢。
# pycryptodome: 若配方构建失败, 删掉它即可 (核心模块有纯 Python AES 兜底,
# 只在 ffmpeg 解密失败时用)。
requirements = python3,kivy,requests,certifi,urllib3,idna,charset-normalizer==3.3.2,pycryptodome,ffmpeg,av_codecs

# (str) Preset of the mobile OS
orientation = portrait

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (list) Permissions
# READ/WRITE_EXTERNAL_STORAGE: 让内置文件夹浏览器能列出 / 写入用户
# 选的公共目录 (安卓 11+ 上光有这两个还不够, 写公共目录还须用户在
# 系统设置里开 MANAGE_EXTERNAL_STORAGE, 界面有按钮跳到该设置页;
# 默认专属目录不需要任何存储权限)。
android.permissions = INTERNET,ACCESS_NETWORK_STATE,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# (list) Target Android architectures
android.archs = arm64-v8a

# (bool) Automatically accept Android SDK licenses
android.accept_sdk_license = True

# (bool) Skip sdkmanager update calls: SDK components (platform-tools,
# build-tools 34.0.0, platform android-33) are pre-installed manually,
# because this VM's authenticated egress proxy breaks sdkmanager's HTTP.
android.skip_update = True
p4a.local_recipes = ./p4a-recipes

# (int) Target Android API, minimum Android API
# android.api = 33
# android.minapi = 24

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = false, 1 = true)
warn_on_root = 0
