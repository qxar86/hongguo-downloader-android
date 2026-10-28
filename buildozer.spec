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
version = 1.4.8

# (list) Application requirements
# ffmpeg + av_codecs 是 v2 的关键: p4a 的 ffmpeg 配方把 ffmpeg 可执行文件
# 拷成 libffmpegbin.so 打进 APK, 启动时 start.c 链接到 .bin 目录并追加进
# PATH, Python 里 shutil.which("ffmpeg") 即可找到, 核心模块的
# ffmpeg_decrypt() / merge_videos() 原样走通。
# av_codecs 让 ffmpeg 开全部 demuxer/muxer/parser (含 concat 和 hevc),
# 否则基础配置缺 concat demuxer, 合并会失败; 代价是包体变大、构建变慢。
# pycryptodome: 若配方构建失败, 删掉它即可 (核心模块有纯 Python AES 兜底,
# 只在 ffmpeg 解密失败时用)。
# charset-normalizer 固定 3.3.2: 3.5.2 在 p4a 里打出 cp314/android 平台
# wheel, pip --target 装到安卓目标时报 not a supported wheel, 致 CI #1
# (2026-10-24) 在 pip 装模块那步失败; 3.3.2 是纯 Python 包
# (py3-none-any), requests 要求 charset-normalizer<4,>=2, 兼容。
# CI #2/#3 证明光 pin 版本、加配方都不够: p4a build.py 的跳过检查是
# mname.lower().replace("-","_") 对 Requirement().name, 而后者保留连字符,
# 故 charset-normalizer 的 3.5.2 wheel URL 总被当成额外纯依赖塞回安装列表。
# 所以需求名故意写下划线 charset_normalizer==3.3.2, 使跳过检查命中、
# 3.5.2 URL 不再进入最终安装; p4a-recipes/ 下的连字符配方目录此后不再被匹配,
# 留置不用。pip 按 PEP 503 视下划线与连字符为同一包, 不影响安装。
requirements = python3,kivy,kivymd==1.2.0,pillow,requests,certifi,urllib3,idna,charset_normalizer==3.3.2,pycryptodome,ffmpeg,av_codecs

# (str) p4a 本地配方目录: 配方名即子目录名, 须与 requirements 里的
# 包名一致 (charset-normalizer)。CI 上同样要把 p4a-recipes/ 推上去,
# 否则 buildozer 找不到本地配方, 会退回 pip 模块老路。
p4a.local_recipes = ./p4a-recipes

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

# (int) Target Android API, minimum Android API
# android.api = 33
# android.minapi = 24

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = false, 1 = true)
warn_on_root = 0
