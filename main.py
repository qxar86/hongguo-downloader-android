# -*- coding: utf-8 -*-
"""
红果短剧下载器 - 安卓版 v2 (Kivy 界面 + 原生 ffmpeg)

核心逻辑全部复用桌面版 hongguo_downloader.py (同目录模块):
详情 -> video_model -> spade 派生密钥 -> CENC 解密, 与桌面版同一套代码。
界面流程与桌面交互模式一致: 输 id -> 查信息 -> 选集数 -> 探测分辨率 ->
选档 -> 下载。下载在后台线程进行, 日志实时回显。

原生 ffmpeg 的来源 (不是外挂二进制, 不是 Termux):
buildozer.spec 的 requirements 里加了 p4a 的 ffmpeg + av_codecs 配方,
p4a 编译 ffmpeg 8.0.1 时把 ffmpeg 可执行文件拷成 libffmpegbin.so
打进 APK; 启动时 p4a 的 start.c 会把 lib*bin.so 链接到应用的 .bin
目录并追加进 PATH, 所以 Python 里 shutil.which("ffmpeg") 就能找到它,
核心模块 find_ffmpeg() -> ffmpeg_decrypt() / merge_videos() 的桌面
路径原样走通, 不需要改核心模块一行业务代码。
解密: ffmpeg -decryption_key <key> -i enc -c copy out (失败自动回退
Python 解密, 每集日志标注 解密=ffmpeg/python)。
合并: 恢复桌面版 --merge 行为, ffmpeg concat 流拷贝, 单集文件保留,
合并后读 mvhd 校验总时长 (核心模块 merge_videos 已实现)。
av_codecs 的作用: 它让 ffmpeg 配方开全部 demuxer/muxer/parser
(含 concat demuxer 和 hevc), 否则基础配置缺 concat, 合并会失败。

输出目录 (用户可手动选):
默认是应用的外部专属目录 (文件管理里 Android/data/<包名>/files/downloads),
不需要任何存储权限。界面上有「保存到」输入框: 可以直接改路径,
也可以点「浏览目录」用内置文件夹浏览器选目录 / 新建文件夹,
选定后自动保存, 下次打开仍是上次的目录。
安卓 11+ 的限制要说清楚: 应用只能无条件写自己的专属目录;
想写到 Download 等公共目录, 须在系统设置里给本应用开
「所有文件访问权限」(界面有按钮可跳到该设置页)。
下载开始前会实测所选目录是否可写, 写不进去就明确提示并
自动回退到默认专属目录, 不会让下载中途丢文件。
"""

import os
import re
import threading

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

import hongguo_downloader as hd

# ---------------------------------------------------------------------------
# 中文字体: 优先用系统自带 CJK 字体, 避免方块字
# ---------------------------------------------------------------------------
_CJK_CANDIDATES = [
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/NotoSansCJKsc-Regular.otf",
    "/system/fonts/DroidSansFallback.ttf",
    "/system/fonts/NotoSansHans-Regular.otf",
]
CJK_FONT = next((p for p in _CJK_CANDIDATES if os.path.exists(p)), None)


def F(widget_cls, **kw):
    if CJK_FONT and "font_name" not in kw:
        kw["font_name"] = CJK_FONT
    return widget_cls(**kw)


def _wrap_label(text="", font_size=None, **kw):
    """可能很长的文本用的自适应 Label: 宽度变了就重排, 高度跟纹理走。
    固定高度 + 不换行是旧界面在手机上裁字/溢出的原因之一, 剧名、
    目录路径这类长文本都走这里, 不再写死高度。"""
    lab = F(Label, text=text, halign="left", valign="top", size_hint_y=None, **kw)
    if font_size is not None:
        lab.font_size = font_size
    lab.bind(width=lambda *_: setattr(lab, "text_size", (lab.width, None)))
    lab.bind(texture_size=lambda *_: setattr(lab, "height", max(lab.texture_size[1], 1)))
    return lab


def download_base():
    """应用外部专属目录下的 downloads (免权限, 文件管理器可访问)。"""
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        ext = activity.getExternalFilesDir(None)
        if ext is not None:
            return os.path.join(ext.getAbsolutePath(), "downloads")
    except Exception:
        pass
    try:
        return os.path.join(App.get_running_app().user_data_dir, "downloads")
    except Exception:
        return os.path.join(os.getcwd(), "downloads")


def _dest_file():
    """保存用户所选目录的小文件 (放应用私有目录, 不依赖存储权限)。"""
    try:
        base = App.get_running_app().user_data_dir
    except Exception:
        base = os.getcwd()
    return os.path.join(base, "dest_dir.txt")


def load_saved_dest():
    try:
        with open(_dest_file(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_dest(path):
    try:
        os.makedirs(os.path.dirname(_dest_file()), exist_ok=True)
        with open(_dest_file(), "w", encoding="utf-8") as f:
            f.write(path.strip())
        return True
    except Exception:
        return False


def is_writable_dir(path):
    """实测目录是否可写: 能建目录 + 能建删一个测试文件才算。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "wb") as f:
            f.write(b"ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def request_all_files_access():
    """尝试打开系统「所有文件访问权限」设置页 (安卓 11+, 须用户手动开)。
    返回 True 表示已打开设置页 (不代表已授权), False 表示打不开
    (此时请用户自己去设置里找)。"""
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        Uri = autoclass("android.net.Uri")
        pkg = activity.getPackageName()
        intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
        intent.setData(Uri.parse("package:" + pkg))
        activity.startActivity(intent)
        return True
    except Exception:
        return False


class DownloaderUI(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=dp(12), spacing=dp(8), **kw)
        # 根布局自身不固定高度, 高度跟内容走; App.build 会把它放进
        # ScrollView, 小屏手机上整个表单可以滚动, 不再被压到重叠。
        self.size_hint_y = None
        self.bind(minimum_height=self.setter("height"))
        self.detail = None          # fetch_detail 结果 (整剧模式)
        self.vids = []              # vid 列表
        self.single_vid = ""        # 单集模式 vid
        self.rows = []              # available_resolutions 结果
        self.device_id = hd.random_device_id()
        self.install_id = hd.random_device_id()
        self.busy = False

        self.add_widget(F(Label, text="红果短剧下载器", font_size=sp(22),
                          size_hint_y=None, height=dp(36)))
        self.id_input = F(TextInput, hint_text="剧集 ID / 详情页 URL / 单集 vid",
                          multiline=False, size_hint_y=None, height=dp(48))
        self.add_widget(self.id_input)

        self.info_btn = F(Button, text="获取剧集信息", size_hint_y=None, height=dp(48))
        self.info_btn.bind(on_release=self.on_get_info)
        self.add_widget(self.info_btn)

        self.info_label = _wrap_label("")
        self.add_widget(self.info_label)

        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        row.add_widget(F(Label, text="集数:", size_hint_x=None, width=dp(52)))
        self.eps_input = F(TextInput, text="all", hint_text="all / 1-5 / 1,3,5",
                           multiline=False)
        row.add_widget(self.eps_input)
        self.add_widget(row)

        self.probe_btn = F(Button, text="查询可下载分辨率", size_hint_y=None,
                           height=dp(48), disabled=True)
        self.probe_btn.bind(on_release=self.on_probe)
        self.add_widget(self.probe_btn)

        self.quality = F(Spinner, text="先查询分辨率", values=(),
                         size_hint_y=None, height=dp(48), disabled=True)
        self.add_widget(self.quality)

        # ---- 保存目录: 手动输入 / 浏览选择, 选择结果自动持久化 ----
        drow = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        drow.add_widget(F(Label, text="保存到:", size_hint_x=None, width=dp(64)))
        saved = load_saved_dest()
        self.dest_input = F(TextInput, text=saved or download_base(),
                            multiline=False)
        drow.add_widget(self.dest_input)
        self.add_widget(drow)

        brow = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(8))
        self.browse_btn = F(Button, text="浏览目录...")
        self.browse_btn.bind(on_release=self.on_browse)
        brow.add_widget(self.browse_btn)
        self.default_btn = F(Button, text="恢复默认")
        self.default_btn.bind(on_release=self.on_default_dest)
        brow.add_widget(self.default_btn)
        self.auth_btn = F(Button, text="开全部文件权限")
        self.auth_btn.bind(on_release=self.on_all_files_auth)
        brow.add_widget(self.auth_btn)
        self.add_widget(brow)

        # 文件夹浏览器改成弹窗: 内嵌面板在部分机器上会跟外层滚动布局
        # 互相干扰, 浏览目录行会被压到上级行下面重叠, 列表区也会塌到 0 高。
        # 弹窗独立于主界面布局, 不参与主表单的高度计算, 从根上杜绝重叠。
        _pop_content = BoxLayout(orientation="vertical", spacing=dp(8))
        # 路径栏固定单行、超长打省略号: 之前自适应换行会让长路径
        # 撑高、反过来挤占列表区的空间。完整路径仍在下方「下载目录」里。
        self.browser_path_label = F(Label, text="", size_hint_y=None, height=dp(24),
                                    font_size=sp(12), shorten=True, halign="left",
                                    valign="middle")
        self.browser_path_label.bind(
            width=lambda *_: setattr(self.browser_path_label, "text_size",
                                     (self.browser_path_label.width, None)))
        _pop_content.add_widget(self.browser_path_label)
        nrow = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(8))
        self.newdir_input = F(TextInput, hint_text="新文件夹名",
                              multiline=False)
        nrow.add_widget(self.newdir_input)
        self.mkdir_btn = F(Button, text="新建", size_hint_x=None, width=dp(72))
        self.mkdir_btn.bind(on_release=self.on_mkdir)
        nrow.add_widget(self.mkdir_btn)
        _pop_content.add_widget(nrow)
        # 弹窗尺寸固定, 列表区用弹性高度即可, 不会再被压没。
        self.browser_scroll = ScrollView(size_hint=(1, 1))
        self.browser_list = BoxLayout(orientation="vertical", size_hint_y=None)
        self.browser_list.bind(minimum_height=self.browser_list.setter("height"))
        self.browser_scroll.add_widget(self.browser_list)
        _pop_content.add_widget(self.browser_scroll)
        brow2 = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(8))
        self.up_btn = F(Button, text="上级")
        self.up_btn.bind(on_release=self.on_browse_up)
        brow2.add_widget(self.up_btn)
        self.select_btn = F(Button, text="选定当前目录")
        self.select_btn.bind(on_release=self.on_browse_select)
        brow2.add_widget(self.select_btn)
        self.close_btn = F(Button, text="关闭")
        self.close_btn.bind(on_release=self.on_browse_close)
        brow2.add_widget(self.close_btn)
        _pop_content.add_widget(brow2)
        self.browser_popup = Popup(title="选择下载目录", content=_pop_content,
                                   size_hint=(0.92, 0.72), auto_dismiss=False)
        self.browse_dir = ""

        mrow = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40), spacing=dp(8))
        self.merge_cb = CheckBox(size_hint_x=None, width=dp(36), active=False)
        mrow.add_widget(self.merge_cb)
        mrow.add_widget(F(Label, text="下载完成后按集序合并成一个整体视频",
                          halign="left"))
        self.add_widget(mrow)

        self.dl_btn = F(Button, text="开始下载", size_hint_y=None, height=dp(52),
                        disabled=True)
        self.dl_btn.bind(on_release=self.on_download)
        self.add_widget(self.dl_btn)

        self.ff_btn = F(Button, text="检测原生 ffmpeg", size_hint_y=None, height=dp(44))
        self.ff_btn.bind(on_release=self.on_check_ffmpeg)
        self.add_widget(self.ff_btn)

        self.dir_label = _wrap_label("", font_size=sp(12))
        self.add_widget(self.dir_label)

        scroll = ScrollView(size_hint=(1, None), height=dp(200))
        self.log_label = F(Label, text="", valign="top", halign="left",
                           size_hint_y=None)
        self.log_label.bind(texture_size=self._on_log_texture)
        self.log_label.bind(width=self._on_log_width)
        scroll.add_widget(self.log_label)
        # 日志区不放进可滚动表单, 由 build() 固定在屏幕底部: 否则表单
        # 高于屏幕时日志区被挤到屏幕外, 下载时看不到日志在滚; 而且嵌套
        # 在外层 ScrollView 里时, 手指滑日志区会被外层抢走、划不动。
        self._scroll = scroll
        self.log_scroll = scroll
        self._log_text = ""

        # 接管核心模块的日志输出
        hd.log = self.ui_log
        base = (self.dest_input.text or "").strip() or download_base()
        self.dir_label.text = f"下载目录: {base}"
        ff = hd.find_ffmpeg()
        if ff:
            self.ui_log(f"原生 ffmpeg: {ff} (解密/合并走 ffmpeg, 失败自动回退 Python 解密)")
        else:
            self.ui_log("警告: 未找到 ffmpeg, 将走 Python 解密且合并不可用 "
                        "(点「检测原生 ffmpeg」看详情)")
        self.ui_log(f"AES 后端: {hd._AES_BACKEND} (仅作 ffmpeg 失败时的回退)")
        self.ui_log(f"输出目录: {base}")
        self.ui_log("输入剧集 ID 后点「获取剧集信息」。")

    def on_check_ffmpeg(self, *_):
        """跑一遍 ffmpeg -version, 证明原生 ffmpeg 真的在 PATH 上且能执行。"""
        def work():
            import subprocess
            ff = hd.find_ffmpeg()
            if not ff:
                self.ui_log("检测失败: PATH / 应用目录里都找不到 ffmpeg, "
                            "请确认 APK 是用新版 buildozer.spec (含 ffmpeg) 打的")
                return
            try:
                r = subprocess.run([ff, "-version"], capture_output=True,
                                   timeout=30)
                first = (r.stdout or b"").decode("utf-8", "ignore").splitlines()
                self.ui_log(f"ffmpeg 可执行: {first[0] if first else '(无输出)'}"
                            f" (退出码 {r.returncode})")
            except Exception as e:  # noqa: BLE001
                self.ui_log(f"ffmpeg 执行失败: {e}")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- 保存目录选择
    @mainthread
    def _set_dir_label(self, text):
        self.dir_label.text = f"下载目录: {text}"

    def resolve_dest(self):
        """下载开始时确定最终保存目录: 用户输入优先, 不可写则回退默认
        并明确提示 (绝不让下载中途因权限丢文件)。返回 (目录, 是否回退)。"""
        dest = (self.dest_input.text or "").strip() or download_base()
        save_dest(dest)
        if is_writable_dir(dest):
            return dest, False
        fallback = download_base()
        return fallback, True

    def on_default_dest(self, *_):
        self.dest_input.text = download_base()
        save_dest(self.dest_input.text)
        self._set_dir_label(self.dest_input.text)
        self.ui_log(f"已恢复默认保存目录: {self.dest_input.text}")

    def on_all_files_auth(self, *_):
        if request_all_files_access():
            self.ui_log("已打开系统设置: 请给本应用开「所有文件访问权限」, "
                        "再回来选公共目录 (Download 等)")
        else:
            self.ui_log("打不开设置页: 请自己去系统设置 -> 应用 -> 本应用 -> "
                        "权限 -> 开「所有文件访问权限」")

    def on_browse(self, *_):
        cur = (self.dest_input.text or "").strip()
        if cur and os.path.isdir(cur):
            self.browse_dir = cur
        elif os.path.isdir("/storage/emulated/0"):
            self.browse_dir = "/storage/emulated/0"
        else:
            self.browse_dir = download_base()
        self._refresh_browser()
        self.browser_popup.open()

    def _refresh_browser(self):
        self.browser_path_label.text = f"浏览: {self.browse_dir}"
        self.browser_list.clear_widgets()
        try:
            names = sorted(os.listdir(self.browse_dir))
        except Exception as e:  # noqa: BLE001
            self.ui_log(f"打不开目录 {self.browse_dir}: {e}")
            names = []
        dirs = [n for n in names
                if os.path.isdir(os.path.join(self.browse_dir, n))]
        if not dirs:
            # 空目录也要明说, 否则空白一片、和列表被压没分不出来。
            self.browser_list.add_widget(F(Label, text="(该目录下没有子文件夹)",
                                           size_hint_y=None, height=dp(44),
                                           font_size=sp(12)))
        for name in dirs:
            full = os.path.join(self.browse_dir, name)
            btn = F(Button, text=name, size_hint_y=None, height=dp(44))
            btn.bind(on_release=lambda _b, p=full: self._browse_into(p))
            self.browser_list.add_widget(btn)
        self.browser_list.height = max(dp(44) * len(self.browser_list.children), dp(44))

    def _browse_into(self, path):
        self.browse_dir = path
        self._refresh_browser()

    def on_browse_up(self, *_):
        parent = os.path.dirname(self.browse_dir.rstrip("/"))
        if parent and parent != self.browse_dir:
            self.browse_dir = parent
            self._refresh_browser()

    def on_browse_select(self, *_):
        self.dest_input.text = self.browse_dir
        save_dest(self.browse_dir)
        self._set_dir_label(self.browse_dir)
        self.browser_popup.dismiss()
        self.ui_log(f"已选择保存目录: {self.browse_dir}")

    def on_browse_close(self, *_):
        self.browser_popup.dismiss()

    def on_mkdir(self, *_):
        name = hd.sanitize((self.newdir_input.text or "").strip())
        if not name:
            self.ui_log("请先输入新文件夹名。")
            return
        try:
            os.makedirs(os.path.join(self.browse_dir, name), exist_ok=True)
            self.newdir_input.text = ""
            self._refresh_browser()
        except Exception as e:  # noqa: BLE001
            self.ui_log(f"新建文件夹失败: {e}")

    # ------------------------------------------------------------------ 日志
    def _on_log_width(self, *_):
        self.log_label.text_size = (self.log_label.width, None)

    def _on_log_texture(self, *_):
        self.log_label.height = max(self.log_label.texture_size[1], 1)
        self._scroll.scroll_y = 0

    @mainthread
    def _append_log(self, text):
        self._log_text = (self._log_text + text + "\n")[-30000:]
        self.log_label.text = self._log_text

    def ui_log(self, *a):
        self._append_log(" ".join(str(x) for x in a))

    # ------------------------------------------------------------------ 状态
    @mainthread
    def _set_busy(self, busy, stage=""):
        self.busy = busy
        for w in (self.info_btn,):
            w.disabled = busy
        self.probe_btn.disabled = busy or not (self.detail or self.single_vid)
        self.dl_btn.disabled = busy or not self.rows
        self.quality.disabled = busy or not self.rows
        if stage:
            self.info_label.text = stage

    def _parse_raw_id(self):
        raw = (self.id_input.text or "").strip()
        m = re.search(r"series_id=(\d+)", raw)
        if m:
            raw = m.group(1)
        return hd.split_episode_token(raw)

    # ------------------------------------------------------------------ 流程
    def on_get_info(self, *_):
        if self.busy:
            return
        raw = self._parse_raw_id()
        if not raw:
            self.ui_log("请先输入剧集 ID。")
            return
        self._set_busy(True, "正在获取剧集信息...")
        self.detail, self.vids, self.single_vid, self.rows = None, [], "", []
        self.quality.values = ()
        self.quality.text = "先查询分辨率"

        def work():
            try:
                detail = None
                try:
                    detail = hd.fetch_detail(raw)
                except Exception as e:  # noqa: BLE001
                    self.ui_log(f"查详情失败: {e}")
                if detail and detail.get("vid_list"):
                    self.detail = detail
                    self.vids = [str(v) for v in detail["vid_list"]]
                    name = detail.get("series_name") or raw
                    self.ui_log(f"剧名: {name} | 共 {len(self.vids)} 集 | "
                                f"{detail.get('episode_right_text', '')}")
                    self._set_busy(False, f"{name} · 共 {len(self.vids)} 集")
                elif re.fullmatch(r"\d+", raw or ""):
                    self.single_vid = hd.split_episode_token(raw)
                    self.ui_log(f"单集模式 vid={self.single_vid}")
                    self._set_busy(False, f"单集 vid={self.single_vid}")
                else:
                    self.ui_log(f"无法识别 id: {raw}")
                    self._set_busy(False, "未识别的 ID")
            except Exception as e:  # noqa: BLE001
                self.ui_log(f"出错: {e}")
                self._set_busy(False, "出错")

        threading.Thread(target=work, daemon=True).start()

    def _selected_eps(self):
        if self.single_vid:
            return None
        return hd.parse_episodes(self.eps_input.text.strip() or "all", len(self.vids))

    def on_probe(self, *_):
        if self.busy:
            return
        self._set_busy(True, "正在查询可下载分辨率...")

        def work():
            try:
                if self.single_vid:
                    sample, desc = self.single_vid, "该集"
                else:
                    eps = self._selected_eps()
                    if not eps:
                        self.ui_log("没有有效的集数。")
                        self._set_busy(False, "集数无效")
                        return
                    sample, desc = self.vids[eps[0] - 1], f"以第{eps[0]}集为例"
                model = hd.video_model(sample, self.device_id, self.install_id)
                rows = hd.available_resolutions(model)
                if not rows:
                    self.ui_log("没有可下载的分辨率数据。")
                    self._set_busy(False, "无可用分辨率")
                    return
                self.rows = rows
                values = []
                for r in rows:
                    wh = f"{r['vwidth']}x{r['vheight']}" if r.get("vwidth") else ""
                    size = f"{r['size']/1e6:.2f}MB" if r.get("size") else ""
                    values.append(f"{r['definition']} {wh} {size} {r.get('codec_type') or ''}".strip())
                self.ui_log(f"可下载分辨率 ({desc}): " + " | ".join(values))
                self._fill_quality(values)
                self._set_busy(False, "选择分辨率后点「开始下载」")
            except Exception as e:  # noqa: BLE001
                self.ui_log(f"查询失败: {e}")
                self._set_busy(False, "查询失败")

        threading.Thread(target=work, daemon=True).start()

    @mainthread
    def _fill_quality(self, values):
        self.quality.values = values
        self.quality.text = values[0]

    def _chosen_quality(self):
        if not self.rows:
            return "1080"
        idx = 0
        try:
            idx = self.quality.values.index(self.quality.text)
        except ValueError:
            idx = 0
        return self.rows[idx]["q"]

    def on_download(self, *_):
        if self.busy or not self.rows:
            return
        want_q = self._chosen_quality()
        merge = bool(self.merge_cb.active)
        if merge and not hd.find_ffmpeg():
            self.ui_log("合并需要 ffmpeg, 但未找到 ffmpeg, 本次只下载单集不合并")
            merge = False
        self._set_busy(True, "下载中...")

        def work():
            try:
                base, fell_back = self.resolve_dest()
                if fell_back:
                    self.ui_log(f"所选目录不可写, 已回退到默认目录: {base} "
                                f"(公共目录请先开「所有文件访问权限」)")
                self._set_dir_label(base)
                if self.single_vid:
                    os.makedirs(base, exist_ok=True)
                    path = os.path.join(base, f"{self.single_vid}.mp4")
                    if os.path.exists(path):
                        self.ui_log(f"跳过已存在: {path}")
                    else:
                        q, size, meta, method = hd.download_episode_to_file(
                            self.single_vid, want_q, self.device_id,
                            self.install_id, path)
                        self.ui_log(f"完成: {path} ({size/1e6:.2f} MB, 实际 {q}p, 解密={method})")
                else:
                    eps = self._selected_eps()
                    title = hd.sanitize(self.detail.get("series_name") or "hongguo")
                    outdir = os.path.join(base, title)
                    hd._download_episodes(eps, self.vids, outdir, want_q,
                                          self.device_id, self.install_id, False,
                                          merge=merge, title=title)
                self.ui_log("全部结束。")
                self._set_busy(False, "下载完成")
            except Exception as e:  # noqa: BLE001
                self.ui_log(f"下载出错: {e}")
                self._set_busy(False, "下载出错")

        threading.Thread(target=work, daemon=True).start()


class HongGuoApp(App):
    def build(self):
        # 表单放进 ScrollView (内容高于屏幕时可滚动); 日志区固定在屏幕
        # 底部、一直在视野里, 下载时日志滚动可见, 手指也能直接划日志区。
        ui = DownloaderUI()
        root = BoxLayout(orientation="vertical")
        form_scroll = ScrollView(size_hint=(1, 1))
        form_scroll.add_widget(ui)
        root.add_widget(form_scroll)
        root.add_widget(ui.log_scroll)
        return root


if __name__ == "__main__":
    HongGuoApp().run()
