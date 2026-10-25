# -*- coding: utf-8 -*-
"""
红果短剧下载器 - 安卓版 v2 (KivyMD Material 界面 + 原生 ffmpeg)

核心逻辑全部复用桌面版 hongguo_downloader.py (同目录模块):
详情 -> video_model -> spade 派生密钥 -> CENC 解密, 与桌面版同一套代码。
界面流程与桌面交互模式一致: 输 id -> 查信息 -> 选集数 -> 探测分辨率 ->
选档 -> 下载。下载在后台线程进行, 日志实时回显。

界面 (v1.4 起): KivyMD 1.2.0 的 Material Design 组件 —— MDTopAppBar
顶栏、描边 MDTextField 输入框、MDRaisedButton / MDFlatButton、
MDDropdownMenu 选分辨率、MDCheckbox 合并开关、MDDialog 文件夹浏览器、
MDCard 包起来的日志区。下载核心与日志机制 (每行一个独立 Label,
彻底绕开整段纹理超限变空白) 与之前版本完全一致, 只换了界面层。

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

from kivy.clock import mainthread
from kivy.core.text import LabelBase
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDRaisedButton, MDFlatButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.label import MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.textfield import MDTextField
from kivymd.uix.toolbar import MDTopAppBar

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

# KivyMD 所有文字默认走 Roboto (无中文): 把 Roboto 各字重重注册为系统
# CJK 字体, 顶栏标题、弹窗标题、按钮文字里的中文才能正常显示 (图标字体
# Icons 不动)。要在任何 KivyMD 控件创建之前做, 模块导入时即生效。
if CJK_FONT:
    try:
        for _name in ("Roboto", "RobotoThin", "RobotoLight",
                      "RobotoMedium", "RobotoBlack"):
            LabelBase.register(name=_name, fn_regular=CJK_FONT,
                               fn_bold=CJK_FONT, fn_italic=CJK_FONT,
                               fn_bolditalic=CJK_FONT)
    except Exception:
        pass


def F(widget_cls, **kw):
    # 只给确实有 font_name 属性的控件设 CJK 字体 (顶栏等没有该属性的
    # 控件靠上面的 Roboto 重注册, 乱传未知属性会在运行时报错)。
    if CJK_FONT and "font_name" not in kw and hasattr(widget_cls, "font_name"):
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
        return os.path.join(MDApp.get_running_app().user_data_dir, "downloads")
    except Exception:
        return os.path.join(os.getcwd(), "downloads")


def _dest_file():
    """保存用户所选目录的小文件 (放应用私有目录, 不依赖存储权限)。"""
    try:
        base = MDApp.get_running_app().user_data_dir
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


class DownloaderUI(MDBoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=dp(16), spacing=dp(12),
                         adaptive_height=True, **kw)
        # 根布局自身不固定高度, 高度跟内容走; App.build 会把它放进
        # ScrollView, 小屏手机上整个表单可以滚动, 不再被压到重叠。
        self.size_hint_y = None
        self.detail = None          # fetch_detail 结果 (整剧模式)
        self.vids = []              # vid 列表
        self.single_vid = ""        # 单集模式 vid
        self.rows = []              # available_resolutions 结果
        self.device_id = hd.random_device_id()
        self.install_id = hd.random_device_id()
        self.busy = False
        self.quality_values = []    # 分辨率下拉菜单的显示文案
        self.quality_index = 0      # 当前选中的分辨率下标
        self.quality_menu = None    # MDDropdownMenu, 打开时持有

        self.id_input = F(MDTextField, hint_text="剧集 ID / 详情页 / 播放页链接",
                          mode="rectangle", size_hint_y=None, height=dp(56))
        self.add_widget(self.id_input)

        self.info_btn = F(MDRaisedButton, text="获取剧集信息",
                          size_hint=(1, None), height=dp(48))
        self.info_btn.bind(on_release=self.on_get_info)
        self.add_widget(self.info_btn)

        self.info_label = F(MDLabel, text="", adaptive_height=True,
                            size_hint_y=None, theme_text_color="Secondary")
        self.add_widget(self.info_label)

        row = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                          height=dp(56), spacing=dp(8))
        row.add_widget(F(MDLabel, text="集数:", size_hint=(None, None),
                         size=(dp(52), dp(56)), valign="middle"))
        self.eps_input = F(MDTextField, text="all", hint_text="all / 1-5 / 1,3,5",
                           mode="rectangle", size_hint_y=None, height=dp(56))
        row.add_widget(self.eps_input)
        self.add_widget(row)

        self.probe_btn = F(MDFlatButton, text="查询可下载分辨率",
                           size_hint=(1, None), height=dp(48), disabled=True)
        self.probe_btn.bind(on_release=self.on_probe)
        self.add_widget(self.probe_btn)

        # 分辨率原来是 Spinner, KivyMD 里没有 Spinner, 改成描边按钮
        # 点开 MDDropdownMenu 选档 (文案是纯英文数字, 不用操心字体)。
        self.quality_btn = F(MDRaisedButton, text="先查询分辨率",
                             size_hint=(1, None), height=dp(48), disabled=True)
        self.quality_btn.bind(on_release=self._open_quality_menu)
        self.add_widget(self.quality_btn)

        # ---- 保存目录: 手动输入 / 浏览选择, 选择结果自动持久化 ----
        drow = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                           height=dp(56), spacing=dp(8))
        drow.add_widget(F(MDLabel, text="保存到:", size_hint=(None, None),
                          size=(dp(64), dp(56)), valign="middle"))
        saved = load_saved_dest()
        self.dest_input = F(MDTextField, text=saved or download_base(),
                            mode="rectangle", size_hint_y=None, height=dp(56))
        drow.add_widget(self.dest_input)
        self.add_widget(drow)

        brow = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                           height=dp(44), spacing=dp(8))
        self.browse_btn = F(MDFlatButton, text="浏览目录...",
                            size_hint=(1, None), height=dp(44))
        self.browse_btn.bind(on_release=self.on_browse)
        brow.add_widget(self.browse_btn)
        self.default_btn = F(MDFlatButton, text="恢复默认",
                             size_hint=(1, None), height=dp(44))
        self.default_btn.bind(on_release=self.on_default_dest)
        brow.add_widget(self.default_btn)
        self.auth_btn = F(MDFlatButton, text="开全部文件权限",
                          size_hint=(1, None), height=dp(44))
        self.auth_btn.bind(on_release=self.on_all_files_auth)
        brow.add_widget(self.auth_btn)
        self.add_widget(brow)

        # 文件夹浏览器改成 MDDialog: 内嵌面板在部分机器上会跟外层滚动
        # 布局互相干扰, 浏览目录行会被压到上级行下面重叠, 列表区也会塌
        # 到 0 高。弹窗独立于主界面布局, 不参与主表单的高度计算, 从根
        # 上杜绝重叠。文件夹名可能是中文, 列表行沿用普通 Button (F()
        # 直接设 CJK 字体), 不走 KivyMD 的列表项。
        _pop_content = MDBoxLayout(orientation="vertical", spacing=dp(8),
                                   size_hint_y=None, height=dp(420))
        # 路径栏固定单行、超长打省略号: 之前自适应换行会让长路径
        # 撑高、反过来挤占列表区的空间。完整路径仍在下方「下载目录」里。
        self.browser_path_label = F(Label, text="", size_hint_y=None, height=dp(24),
                                    font_size=sp(12), shorten=True, halign="left",
                                    valign="middle")
        self.browser_path_label.bind(
            width=lambda *_: setattr(self.browser_path_label, "text_size",
                                     (self.browser_path_label.width, None)))
        _pop_content.add_widget(self.browser_path_label)
        nrow = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                           height=dp(56), spacing=dp(8))
        self.newdir_input = F(MDTextField, hint_text="新文件夹名",
                              mode="rectangle", size_hint_y=None, height=dp(56))
        nrow.add_widget(self.newdir_input)
        self.mkdir_btn = F(MDFlatButton, text="新建",
                           size_hint=(None, None), size=(dp(72), dp(48)))
        self.mkdir_btn.bind(on_release=self.on_mkdir)
        nrow.add_widget(self.mkdir_btn)
        _pop_content.add_widget(nrow)
        # 弹窗内容固定高度, 列表区用弹性高度即可, 不会再被压没。
        self.browser_scroll = ScrollView(size_hint=(1, 1))
        self.browser_list = BoxLayout(orientation="vertical", size_hint_y=None)
        self.browser_list.bind(minimum_height=self.browser_list.setter("height"))
        self.browser_scroll.add_widget(self.browser_list)
        _pop_content.add_widget(self.browser_scroll)
        _up = F(MDFlatButton, text="上级")
        _up.bind(on_release=self.on_browse_up)
        _sel = F(MDFlatButton, text="选定当前目录")
        _sel.bind(on_release=self.on_browse_select)
        _close = F(MDFlatButton, text="关闭")
        _close.bind(on_release=self.on_browse_close)
        self.browser_dialog = MDDialog(title="选择下载目录", type="custom",
                                       content_cls=_pop_content,
                                       buttons=[_up, _sel, _close])
        self.browse_dir = ""

        mrow = MDBoxLayout(orientation="horizontal", size_hint_y=None,
                           height=dp(48), spacing=dp(8))
        self.merge_cb = MDCheckbox(size_hint=(None, None),
                                   size=(dp(48), dp(48)), active=False)
        mrow.add_widget(self.merge_cb)
        mrow.add_widget(F(MDLabel, text="下载完成后按集序合并成一个整体视频",
                          size_hint=(1, None), height=dp(48), valign="middle"))
        self.add_widget(mrow)

        self.dl_btn = F(MDRaisedButton, text="开始下载", size_hint=(1, None),
                        height=dp(52), disabled=True)
        self.dl_btn.bind(on_release=self.on_download)
        self.add_widget(self.dl_btn)

        self.ff_btn = F(MDFlatButton, text="检测原生 ffmpeg",
                        size_hint=(1, None), height=dp(44))
        self.ff_btn.bind(on_release=self.on_check_ffmpeg)
        self.add_widget(self.ff_btn)

        self.dir_label = F(MDLabel, text="", adaptive_height=True,
                           size_hint_y=None, theme_text_color="Secondary",
                           font_style="Caption")
        self.add_widget(self.dir_label)

        scroll = ScrollView(size_hint=(1, None), height=dp(200))
        # 日志改成每行一个独立 Label: 整段文字渲成一张纹理时, 集数一多
        # 纹理超限会整段变空白 (截断到 40 行/2500 字实测仍未解决, 说明
        # 单张纹理这条路本身不可靠)。每行独立渲染后, 每张纹理只有一
        # 行高, 再长的下载也不会超限; 旧行从列表里摘掉, 内存也不涨。
        self.log_list = BoxLayout(orientation="vertical", size_hint_y=None)
        self.log_list.bind(minimum_height=self.log_list.setter("height"))
        scroll.add_widget(self.log_list)
        self.log_lines = []
        # 日志区不放进可滚动表单, 由 build() 包进 MDCard 固定在屏幕
        # 底部: 否则表单高于屏幕时日志区被挤到屏幕外, 下载时看不到日志
        # 在滚; 而且嵌套在外层 ScrollView 里时, 手指滑日志区会被外层
        # 抢走、划不动。
        self._scroll = scroll
        self.log_scroll = scroll

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
        self.browser_dialog.open()

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
        self.browser_dialog.dismiss()
        self.ui_log(f"已选择保存目录: {self.browse_dir}")

    def on_browse_close(self, *_):
        self.browser_dialog.dismiss()

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
    _LOG_MAX_LINES = 120

    @mainthread
    def _append_log(self, text):
        for line in str(text).split("\n"):
            lab = _wrap_label(line if line else " ", font_size=sp(12))
            self.log_list.add_widget(lab)
            self.log_lines.append(lab)
        while len(self.log_lines) > self._LOG_MAX_LINES:
            self.log_list.remove_widget(self.log_lines.pop(0))
        self._scroll.scroll_y = 0

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
        self.quality_btn.disabled = busy or not self.rows
        if stage:
            self.info_label.text = stage

    def _parse_raw_id(self):
        # 播放页 / 详情页链接直接取 id, 其余输入原样交给核心解析。
        return hd.extract_id((self.id_input.text or "").strip())

    def _open_quality_menu(self, *_):
        if not self.quality_values:
            return
        if self.quality_menu:
            self.quality_menu.dismiss()
        items = [{"viewclass": "OneLineListItem", "text": v,
                  "on_release": lambda _item, v=v: self._select_quality(v)}
                 for v in self.quality_values]
        self.quality_menu = MDDropdownMenu(caller=self.quality_btn,
                                           items=items, width_mult=4)
        self.quality_menu.open()

    def _select_quality(self, value):
        try:
            self.quality_index = self.quality_values.index(value)
        except ValueError:
            self.quality_index = 0
        self.quality_btn.text = value
        if self.quality_menu:
            self.quality_menu.dismiss()
            self.quality_menu = None

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
        self.quality_values = []
        self.quality_index = 0
        self.quality_btn.text = "先查询分辨率"
        if self.quality_menu:
            self.quality_menu.dismiss()
            self.quality_menu = None

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
        self.quality_values = list(values)
        self.quality_index = 0
        self.quality_btn.text = values[0]

    def _chosen_quality(self):
        if not self.rows:
            return "1080"
        return self.rows[self.quality_index]["q"]

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


class HongGuoApp(MDApp):
    def build(self):
        self.theme_cls.primary_palette = "Teal"
        self.theme_cls.accent_palette = "Amber"
        self.theme_cls.theme_style = "Light"
        ui = DownloaderUI()
        root = MDBoxLayout(orientation="vertical")
        # 顶栏: 中文标题靠模块顶部把 Roboto 重注册成 CJK 字体来显示。
        root.add_widget(F(MDTopAppBar, title="红果短剧下载器", elevation=4,
                          size_hint=(1, None), height=dp(56)))
        form_scroll = ScrollView(size_hint=(1, 1))
        form_scroll.add_widget(ui)
        root.add_widget(form_scroll)
        # 日志区包进带阴影的卡片, 固定在屏幕底部、一直在视野里,
        # 下载时日志滚动可见, 手指也能直接划日志区。
        card = MDCard(orientation="vertical", size_hint=(1, None),
                      height=dp(200), elevation=2, padding=dp(8))
        card.add_widget(ui.log_scroll)
        root.add_widget(card)
        return root


if __name__ == "__main__":
    HongGuoApp().run()
