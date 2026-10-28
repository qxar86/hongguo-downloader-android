#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
红果短剧下载器 (csp_HongGuo 逆向复刻)
=====================================
来源: spider.jar -> com.github.catvod.spider.HongGuo
      + com.github.catvod.spider.merge.hg.StreamServer / Mp4Cenc / c.CencHelper

用法:
    python hongguo_downloader.py                           # 交互模式: 提示输入 id,
                                                           # 列出可下载分辨率, 选档后下载
    python hongguo_downloader.py -i                        # 同上 (交互模式)
    python hongguo_downloader.py -i <series_id>            # 已知 id, 只交互选集数/分辨率
    python hongguo_downloader.py <series_id>               # 只给 id: 进入交互模式, 依次问集数/分辨率/线程数
                                                           # (想直接批量下载就加下载选项, 如 -e / -q / -t)
    python hongguo_downloader.py <series_id> -e all -q 1080 # 批量模式: 不提问, 默认全集 1080p
    python hongguo_downloader.py <series_id> -e 1-5        # 只下 1~5 集
    python hongguo_downloader.py <series_id> -e 1,3,5 -q 720
    python hongguo_downloader.py <series_id> -t 8        # 同时下载 8 集 (默认 5, 范围 1-16)
    python hongguo_downloader.py <series_id> -t 1        # 单线程顺序下载
    python hongguo_downloader.py <series_id> -r 3        # 下载失败最多再重试 3 次 (默认 10 次)
    python hongguo_downloader.py <series_id> --merge       # 全集下完后按集序合并成一个
                                                           # 整体视频 (ffmpeg 流拷贝不重编码;
                                                           # 交互模式会在选档后询问是否合并)
    python hongguo_downloader.py --vid <vid>               # 只下单集 (vid = 每集 id)
    python hongguo_downloader.py <series_id> --list        # 只看剧名/集数, 不下载
    python hongguo_downloader.py "https://hongguoduanju.com/detail?series_id=xxxx"
    python hongguo_downloader.py "https://hongguoduanju.com/player/xxxx"
                                                           # 播放页链接: /player/ 后面的数字就是 series_id,
                                                           # 与详情页链接等价, 整剧下载 (原先的输入方式不变)

解密方式 (优先 ffmpeg):
    若本 py 文件同目录下有 ffmpeg / ffmpeg.exe (或系统 PATH 里有 ffmpeg, 或环境变量
    FFMPEG 指向它), 优先用 ffmpeg 解密:
        ffmpeg -decryption_key <内容密钥hex> -i 加密.mp4 -c copy 明文.mp4
    (ffmpeg 的 mov 解复用器原生支持 cenc-aes-ctr, 实测与 Python 解密同样全片 0 解码错误,
     且是流拷贝、速度快得多。) 找不到 ffmpeg 或 ffmpeg 解密失败时, 自动回退到内置的
    Python CENC 解密 (见链路第 5 步), 结果一致。

id 说明:
    series_id 是剧 id (详情页 URL 里的那个), vid 是单集 id (详情数据 vid_list 里每集一个)。
    位置参数给 series_id; 给单集 vid 时用 --vid, 或直接给 vid 也行 (自动识别:
    先按 series 查详情, 查不到就当 vid 下单集)。

链路 (与 jar 一致):
    1. GET  https://hongguoduanju.com/detail?series_id={id}
            页面 _ROUTER_DATA -> loaderData.detail_page.seriesDetail
            {series_name, series_cover, series_intro, vid_list:[vid,...]}
    2. POST https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/
            params/body 与 jar 的 StreamServer.videoModel 完全一致
            -> data[vid].video_model -> video_list[{definition, main_url,
               encrypt_info:{spade_a, kid, cenc-aes-ctr}, video_meta}]
       注: jar 里这一步还套了 Signing.coreSixgod (X-Gorgon/X-Medusa 六神签),
           但 2026-10-24 实测该接口不验签, 无签名头直接 POST 即返回 code=0
           和完整 video_model, 故本脚本不做签名 (若日后服务端开始验签,
           报错会是签名类错误, 需再移植 merge.hg.Signing)。
    3. 内容密钥: Mp4Cenc.deriveContentKey(spade_a) -> 16B AES key (见函数注释)
    4. main_url 有三种返回形态 (同 vid 不同请求都出现过, _resolve_url 全兼容):
            明文 https / base64(明文 https) / spade 加密串 (须 model 带 key_seed,
            走 StreamServer.decryptSpadeUrl)。jar 本身只处理明文与 spade 两种,
            拿到 base64 形态会把 base64 串当 URL 请求而失败。
    5. 下载 main_url (CENC 加密的 mp4), 按 c.CencHelper.decryptCenc 解密:
            AES-128-CTR, 每 sample 用 senc 里的 IV (tenc 声明的 iv_size, 右补 0 到 16B),
            sample 偏移/大小由 stsz/stsc/stco(co64) 展开;
            moov 打补丁: encv/enca -> sinf/frma 原格式 (hvc1/mp4a 等),
            sinf/senc/saio/saiz/sgpd/sbgp 改名为 free。
       产物是可直接播放的明文 mp4 (实测 hevc+aac, ffmpeg 解码无错)。

依赖:
    必需: requests            (pip install requests)
    可选: pycryptodome         (pip install pycryptodome, AES 加速;
                                不装也能跑, 自动用内置纯 Python AES, 慢一些)
"""

import argparse
import base64
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    sys.exit("缺少 requests, 先执行: pip install requests")

# ----------------------------------------------------------------------------
# 常量 (与 jar 对齐)
# ----------------------------------------------------------------------------
SITE = "https://hongguoduanju.com"
VIDEO_URL = "https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/"
WEB_UA = ("Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
APP_UA = ("com.phoenix.read/71332 (Linux; U; Android 16; zh_CN; 25053RT47C; "
          "Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 "
          "QuicVersion:c67e9834 2025-09-08)")
MEDIA_UA = "com.phoenix.read/71332"
QUALITY_ORDER = ["2160", "1440", "1080", "720", "576", "540", "480", "360"]

__VERSION__ = "2026-10-25.8"  # 每次改动加一档, 启动时打印, 供截图核对版本

SESSION = requests.Session()
SESSION.headers.update({"Accept-Encoding": "identity"})


def log(*a):
    print(*a, flush=True)


def retry(fn, times=3, desc=""):
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i + 1 < times:
                time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"{desc} 失败(重试{times}次): {last}")


def random_device_id():
    # jar: String.valueOf((long)(Math.random()*4875422447187506176 + 4861130398305394688))
    return str(random.randint(4861130398305394688, 4861130398305394688 + 4875422447187506176))


# ----------------------------------------------------------------------------
# AES: 优先 pycryptodome / cryptography, 否则内置纯 Python 实现
# ----------------------------------------------------------------------------
_AES_BACKEND = None
try:
    from Crypto.Cipher import AES as _CryptoAES
    from Crypto.Util import Counter as _CryptoCounter
    _AES_BACKEND = "pycryptodome"
except ImportError:
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher as _Cipher, algorithms as _algorithms, modes as _modes)
        _AES_BACKEND = "cryptography"
    except ImportError:
        _AES_BACKEND = "pure"

# --- 纯 Python AES (仅作兜底: ECB encrypt/decrypt block) ---
_SBOX = (
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16)
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_RCON = (0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36,0x6c,0xd8,0xab,0x4d)


def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


# 预乘表 (MixColumns 用), 避免逐块现算 _gmul 把纯 Python 后端拖得太慢
_MUL = {m: [_gmul(x, m) for x in range(256)] for m in (2, 3, 9, 11, 13, 14)}


class _PureAES:
    """最小 AES (128/192/256) 块加解密, 只供兜底。"""

    def __init__(self, key):
        nk = len(key) // 4
        self.nr = nk + 6
        w = [list(key[i * 4:(i + 1) * 4]) for i in range(nk)]
        for i in range(nk, 4 * (self.nr + 1)):
            t = list(w[-1])
            if i % nk == 0:
                t = [_SBOX[t[1]] ^ _RCON[i // nk - 1], _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]]
            elif nk > 6 and i % nk == 4:
                t = [_SBOX[x] for x in t]
            w.append([w[i - nk][j] ^ t[j] for j in range(4)])
        self._rk = [[w[r * 4 + c][row] for c in range(4) for row in range(4)]
                    for r in range(self.nr + 1)]

    @staticmethod
    def _shift(s, inv=False):
        # state 为列主序 list[16], s[r + 4c]
        out = [0] * 16
        for r in range(4):
            for c in range(4):
                src_c = (c + r) % 4 if not inv else (c - r) % 4
                out[r + 4 * c] = s[r + 4 * src_c]
        return out

    @staticmethod
    def _mix(s, inv=False):
        out = [0] * 16
        for c in range(4):
            a = s[4 * c:4 * c + 4]
            if not inv:
                out[4 * c + 0] = _MUL[2][a[0]] ^ _MUL[3][a[1]] ^ a[2] ^ a[3]
                out[4 * c + 1] = a[0] ^ _MUL[2][a[1]] ^ _MUL[3][a[2]] ^ a[3]
                out[4 * c + 2] = a[0] ^ a[1] ^ _MUL[2][a[2]] ^ _MUL[3][a[3]]
                out[4 * c + 3] = _MUL[3][a[0]] ^ a[1] ^ a[2] ^ _MUL[2][a[3]]
            else:
                out[4 * c + 0] = _MUL[14][a[0]] ^ _MUL[11][a[1]] ^ _MUL[13][a[2]] ^ _MUL[9][a[3]]
                out[4 * c + 1] = _MUL[9][a[0]] ^ _MUL[14][a[1]] ^ _MUL[11][a[2]] ^ _MUL[13][a[3]]
                out[4 * c + 2] = _MUL[13][a[0]] ^ _MUL[9][a[1]] ^ _MUL[14][a[2]] ^ _MUL[11][a[3]]
                out[4 * c + 3] = _MUL[11][a[0]] ^ _MUL[13][a[1]] ^ _MUL[9][a[2]] ^ _MUL[14][a[3]]
        return out

    def encrypt_block(self, block):
        s = [block[i] ^ self._rk[0][i] for i in range(16)]
        for rnd in range(1, self.nr):
            s = [_SBOX[x] for x in s]
            s = self._mix(self._shift(s))
            s = [s[i] ^ self._rk[rnd][i] for i in range(16)]
        s = [_SBOX[x] for x in s]
        s = self._shift(s)
        return bytes(s[i] ^ self._rk[self.nr][i] for i in range(16))

    def decrypt_block(self, block):
        s = [block[i] ^ self._rk[self.nr][i] for i in range(16)]
        for rnd in range(self.nr - 1, 0, -1):
            s = self._shift(s, inv=True)
            s = [_INV_SBOX[x] for x in s]
            s = [s[i] ^ self._rk[rnd][i] for i in range(16)]
            s = self._mix(s, inv=True)
        s = self._shift(s, inv=True)
        s = [_INV_SBOX[x] for x in s]
        return bytes(s[i] ^ self._rk[0][i] for i in range(16))


from functools import lru_cache


@lru_cache(maxsize=8)
def _pure_aes(key):
    return _PureAES(key)


def _ecb_encrypt_block(key, block):
    if _AES_BACKEND == "pycryptodome":
        return _CryptoAES.new(key, _CryptoAES.MODE_ECB).encrypt(block)
    if _AES_BACKEND == "cryptography":
        enc = _Cipher(_algorithms.AES(key), _modes.ECB()).encryptor()
        return enc.update(block) + enc.finalize()
    return _pure_aes(key).encrypt_block(block)


def aes_ctr_crypt(key, iv16, data):
    """AES-CTR (加解密同形), iv16 为 16B 初始计数器, 大端递增。与 Java AES/CTR/NoPadding 一致。"""
    if not data:
        return b""
    if _AES_BACKEND == "pycryptodome":
        ctr = _CryptoCounter.new(128, initial_value=int.from_bytes(iv16, "big"))
        return _CryptoAES.new(key, _CryptoAES.MODE_CTR, counter=ctr).decrypt(data)
    if _AES_BACKEND == "cryptography":
        dec = _Cipher(_algorithms.AES(key), _modes.CTR(iv16)).decryptor()
        return dec.update(data) + dec.finalize()
    # 纯 Python: 逐块生成密钥流
    aes = _pure_aes(key)
    counter = int.from_bytes(iv16, "big")
    out = bytearray(len(data))
    for off in range(0, len(data), 16):
        ks = aes.encrypt_block(counter.to_bytes(16, "big"))
        chunk = data[off:off + 16]
        for i, b in enumerate(chunk):
            out[off + i] = b ^ ks[i]
        counter += 1
    return bytes(out)


def aes_cbc_decrypt(key, iv, data):
    """AES-CBC NoPadding 解密 (长度须为 16 的倍数)。"""
    if not data:
        return b""
    data = data[:len(data) - (len(data) % 16)]
    if _AES_BACKEND == "pycryptodome":
        return _CryptoAES.new(key, _CryptoAES.MODE_CBC, iv[:16]).decrypt(data)
    if _AES_BACKEND == "cryptography":
        dec = _Cipher(_algorithms.AES(key), _modes.CBC(iv[:16])).decryptor()
        return dec.update(data) + dec.finalize()
    aes = _pure_aes(key)
    out = bytearray()
    prev = iv[:16]
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        dec = aes.decrypt_block(blk)
        out.extend(d ^ p for d, p in zip(dec, prev))
        prev = blk
    return bytes(out)


def b64decode_loose(s):
    """Util.b64decode: 补齐 padding, 标准表失败再试 URL 安全表。"""
    s = (s or "").strip()
    s += "=" * ((-len(s)) % 4)
    try:
        return base64.b64decode(s)
    except Exception:
        try:
            return base64.urlsafe_b64decode(s)
        except Exception:
            return b""


# ----------------------------------------------------------------------------
# spade: 内容密钥派生 + URL 解密 (Mp4Cenc.deriveContentKey / StreamServer.decryptSpadeUrl)
# ----------------------------------------------------------------------------
def derive_content_key(spade_a):
    """spade_a(base64) -> 16B 内容密钥。逐行对应 Mp4Cenc.deriveContentKey:
    raw = b64decode(spade_a)
    n   = len(raw) - (raw[0]^raw[1]^raw[2]) + 47; 越界则 n = len(raw)-1; 须 >=33
    buf = raw[1:1+n], 逐字节: out[i] = (-21 - bitCount(i) + (prev_parity_byte ^ buf[i])) & 0xFF
          其中 prev 初始 even=246, odd=85, 每步用同奇偶的上一个原始字节更新
    key = buf[1:33] 当作 32 个 hex 字符解码成的 16B
    """
    raw = b64decode_loose(spade_a)
    if len(raw) < 3:
        raise RuntimeError("spade_a too short")
    n = len(raw) - (raw[0] ^ raw[1] ^ raw[2]) + 47
    if n <= 0 or n + 1 > len(raw):
        n = len(raw) - 1
    if n < 33:
        raise RuntimeError("spade_a length invalid")
    buf = bytearray(raw[1:1 + n])
    prev_even, prev_odd = 246, 85
    for i in range(n):
        orig = buf[i]
        if i & 1:
            x = prev_odd
            prev_odd = orig
        else:
            x = prev_even
            prev_even = orig
        buf[i] = ((-21 - bin(i).count("1")) + (x ^ orig)) & 0xFF
    return bytes.fromhex(bytes(buf[1:33]).decode("ascii"))


_SPADE_CONST = bytes.fromhex(
    "4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524"
    "f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58")


def decrypt_spade_url(spade_url, key_seed):
    """StreamServer.decryptSpadeUrl: 头 4B (A8 ?? 01 00) + AES-CBC 密文。
    key/iv = SHA512(SHA512(key_seed) + CONST) 的前 16/次 16 字节。"""
    raw = b64decode_loose(spade_url)
    if len(raw) < 20 or raw[0] != 0xA8 or raw[2] != 1 or raw[3] != 0:
        raise RuntimeError("spade URL header invalid")
    cipher = raw[4:4 + ((len(raw) - 4) - (len(raw) - 4) % 16)]
    if not cipher:
        raise RuntimeError("spade URL cipher empty")
    material = hashlib.sha512(hashlib.sha512(key_seed).digest() + _SPADE_CONST).digest()
    plain = aes_cbc_decrypt(material[:16], material[16:32], cipher)
    if plain:
        pad = plain[-1]
        if 1 <= pad <= 16 and pad <= len(plain):
            plain = plain[:-pad]
    return plain.rstrip(b"\x00").decode("utf-8", "ignore")


# ----------------------------------------------------------------------------
# CENC 解密 (c.CencHelper.decryptCenc 的 Python 版)
# ----------------------------------------------------------------------------
def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def _u64(b, o):
    return struct.unpack_from(">Q", b, o)[0]


def _boxes(buf, start, end):
    out, i = [], start
    while i + 8 <= end:
        size, hdr = _u32(buf, i), 8
        typ = bytes(buf[i + 4:i + 8]).decode("latin1")
        if size == 1:
            size, hdr = _u64(buf, i + 8), 16
        elif size == 0:
            size = end - i
        if size < 8 or i + size > end + 8:
            break
        out.append((i, size, hdr, typ))
        i += size
    return out


def _child(buf, box, typ):
    if box is None:
        return None
    for b in _boxes(buf, box[0] + box[2], box[0] + box[1]):
        if b[3] == typ:
            return b
    return None


def _path(buf, box, *types):
    cur = box
    for t in types:
        cur = _child(buf, cur, t)
        if cur is None:
            return None
    return cur


def _expand_samples(data, stsz, stsc, stco, co64):
    """stsz/stsc/stco(co64) -> [(file_offset, size), ...] (CencHelper.expandSamples)"""
    body = stsz[0] + stsz[2]
    sample_size, count = _u32(data, body + 4), _u32(data, body + 8)
    if not (0 < count <= 2_000_000):
        return []
    sizes = ([sample_size] * count if sample_size
             else [_u32(data, body + 12 + i * 4) for i in range(count)])
    body = stsc[0] + stsc[2]
    entries = [(_u32(data, body + 8 + i * 12), _u32(data, body + 12 + i * 12))
               for i in range(_u32(data, body + 4))]
    if stco is not None:
        body = stco[0] + stco[2]
        chunk_offs = [_u32(data, body + 8 + i * 4) for i in range(_u32(data, body + 4))]
    else:
        body = co64[0] + co64[2]
        chunk_offs = [_u64(data, body + 8 + i * 8) for i in range(_u32(data, body + 4))]
    samples, idx = [], 0
    for ci, coff in enumerate(chunk_offs):
        spc = 0
        for first_chunk, per_chunk in entries:
            if first_chunk <= ci + 1:
                spc = per_chunk
            else:
                break
        pos = coff
        for _ in range(spc):
            if idx >= count:
                break
            samples.append((pos, sizes[idx]))
            pos += sizes[idx]
            idx += 1
    return samples


def _parse_senc(data, senc, iv_size):
    """senc -> [(iv, subsamples|None)]; subsamples=[(clear, protected), ...]"""
    body = senc[0] + senc[2]
    flags, count = _u32(data, body), _u32(data, body + 4)
    if not (0 < count <= 2_000_000):
        return []
    out, p = [], body + 8
    for _ in range(count):
        iv = bytes(data[p:p + iv_size])
        p += iv_size
        subs = None
        if flags & 0x000002:  # use_subsample_encryption
            n_sub = struct.unpack_from(">H", data, p)[0]
            p += 2
            subs = []
            for _ in range(n_sub):
                clear, prot = struct.unpack_from(">HI", data, p)
                subs.append((clear, prot))
                p += 6
        out.append((iv, subs))
    return out


def _find_sinf(data, start, end):
    """在 stsd entry 区域内定位 sinf box, 返回 (sinf_box, 原始格式 bytes, iv_size)。"""
    i = start
    while i + 8 <= end:
        if data[i + 4:i + 8] == b"sinf":
            size = _u32(data, i)
            if 8 <= size <= end - i:
                box = (i, size, 8, "sinf")
                frma = _child(data, box, "frma")
                schi = _child(data, box, "schi")
                tenc = _child(data, schi, "tenc") if schi else None
                if frma and tenc:
                    orig = bytes(data[frma[0] + frma[2]:frma[0] + frma[2] + 4])
                    iv_size = data[tenc[0] + tenc[2] + 7]
                    return box, orig, iv_size
        i += 1  # entry 内子 box 偏移不保证 4 对齐 (实测差 1), 必须逐字节扫
    return None, None, 8


def _ctr_keystream_xor(key, iv, subs, chunk, out, out_off):
    """带 subsample 的情况下按受保护区间连续推进 CTR (clear 区间不消耗密钥流)。"""
    # 用整块密钥流生成 + 偏移跟踪, 保证跨区间/非整块长度时计数器正确
    state = _ctr_keystream_xor.state
    for clear, prot in subs:
        if clear:
            out[out_off:out_off + clear] = chunk[:clear]
            chunk = chunk[clear:]
            out_off += clear
        if prot:
            seg = chunk[:prot]
            # 取/续密钥流
            while len(state["ks"]) < len(seg):
                state["ks"] += _ecb_encrypt_block(key, state["ctr"].to_bytes(16, "big"))
                state["ctr"] += 1
            ks, state["ks"] = state["ks"][:len(seg)], state["ks"][len(seg):]
            for j, b in enumerate(seg):
                out[out_off + j] = b ^ ks[j]
            chunk = chunk[prot:]
            out_off += prot
    return out_off


def decrypt_cenc(data_bytes, key):
    """CencHelper.decryptCenc: 整文件 CENC-AES-CTR 解密 + moov 补丁。
    无 senc 的轨道原样保留; 整体无加密轨道时返回原文。"""
    data = bytearray(data_bytes)
    moov = None
    for b in _boxes(data, 0, len(data)):
        if b[3] == "moov":
            moov = b
            break
    if moov is None:
        raise RuntimeError("MP4 moov not found")
    moov_end = moov[0] + moov[1]
    patched = bytearray(data[:moov_end])
    decrypted_any = False
    seen_senc = False

    for trak in _boxes(data, moov[0] + moov[2], moov_end):
        if trak[3] != "trak":
            continue
        stbl = _path(data, trak, "mdia", "minf", "stbl")
        if stbl is None:
            continue
        stsd, senc = _child(data, stbl, "stsd"), _child(data, stbl, "senc")
        stsz, stsc = _child(data, stbl, "stsz"), _child(data, stbl, "stsc")
        stco, co64 = _child(data, stbl, "stco"), _child(data, stbl, "co64")
        if senc:
            seen_senc = True
        if not (stsd and senc and stsz and stsc and (stco or co64)):
            continue
        # stsd 第一个 entry 的格式 (encv/enca) 与其中的 sinf
        sbody = stsd[0] + stsd[2]
        entry_size = _u32(data, sbody + 8)
        sinf, orig_fmt, iv_size = _find_sinf(data, sbody + 8, sbody + 8 + entry_size)
        if sinf is None:
            continue
        if not (1 <= iv_size <= 16):
            iv_size = 8
        samples = _expand_samples(data, stsz, stsc, stco, co64)
        ivs = _parse_senc(data, senc, iv_size)
        if not samples or not ivs:
            continue
        for (off, size), (iv, subs) in zip(samples, ivs):
            iv16 = iv.ljust(16, b"\x00")
            if subs is None:
                data[off:off + size] = aes_ctr_crypt(key, iv16, bytes(data[off:off + size]))
            else:
                _ctr_keystream_xor.state = {
                    "ctr": int.from_bytes(iv16, "big"), "ks": b""}
                _ctr_keystream_xor(key, iv, subs, bytes(data[off:off + size]),
                                   data, off)
            decrypted_any = True
        # moov 补丁: 还原编码格式, 加密相关 box 改 free
        patched[sbody + 12:sbody + 16] = orig_fmt
        patched[sinf[0] + 4:sinf[0] + 8] = b"free"
        patched[senc[0] + 4:senc[0] + 8] = b"free"
        for name in ("saio", "saiz", "sgpd", "sbgp"):
            bx = _child(data, stbl, name)
            if bx:
                patched[bx[0] + 4:bx[0] + 8] = b"free"

    if not decrypted_any:
        if seen_senc:
            # 有加密轨道却一集都没解开 = 解析异常, 绝不能把加密文件当成品交出去
            raise RuntimeError("CENC: found senc but no samples decrypted")
        return bytes(data)
    data[:moov_end] = patched
    return bytes(data)


# ----------------------------------------------------------------------------
# 站点/接口层
# ----------------------------------------------------------------------------
def fetch_detail(series_id):
    """HongGuo.detailContent: 详情页 _ROUTER_DATA -> seriesDetail (含 vid_list)。"""
    url = f"{SITE}/detail?series_id={series_id}"
    headers = {"User-Agent": WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9",
               "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
               "Cache-Control": "no-cache"}

    def _get():
        r = SESSION.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.text

    html = retry(_get, desc="详情页")
    m = re.search(r"(?:window\.)?_ROUTER_DATA\s*=\s*", html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.end():])
    except Exception:
        return None
    loader = (obj or {}).get("loaderData") or {}
    page = loader.get("detail_page") or {}
    detail = page.get("seriesDetail")
    return detail or None


def video_model(vid, device_id, install_id):
    """StreamServer.videoModel: 无签名 POST (实测不验签), 返回 video_model dict。"""
    params = {
        "iid": install_id, "device_id": device_id, "ac": "wifi",
        "channel": "update_64", "aid": "8662", "app_name": "novelread",
        "version_code": "71332", "version_name": "7.1.3.32",
        "device_platform": "android", "os": "android", "ssmix": "a",
        "device_type": "25053RT47C", "device_brand": "Redmi", "language": "zh",
        "os_api": "36", "os_version": "16", "manifest_version_code": "71332",
        "resolution": "1280*2772", "dpi": "520", "update_version_code": "71332",
        "host_abi": "arm64-v8a", "dragon_device_type": "phone",
        "pv_player": "71332", "compliance_status": "0",
        "need_personal_recommend": "1", "player_so_load": "1",
        "is_android_pad_screen": "0",
    }
    body = ('{"biz_param":{"detail_page_version":0,"device_level":3,'
            '"disable_digg_stat":false,"need_all_video_definition":true,'
            '"need_mp4_align":false,"use_os_player":false,"use_server_dns":false,'
            '"video_platform":1024},"mixed_video_id_map":{"1004":["%s"]}}' % vid)
    headers = {
        "User-Agent": APP_UA,
        "Accept": "application/json; charset=utf-8,application/x-protobuf",
        "Content-Type": "application/json; charset=UTF-8",
        "x-xs-from-web": "0",
        "x-ss-req-ticket": str(int(time.time() * 1000)),
        "x-tt-request-tag": "t=0;n=0",
        "sdk-version": "2",
        "passport-sdk-version": "50561",
        "x-vc-bdturing-sdk-version": "3.7.2.cn",
    }

    def _post():
        r = SESSION.post(VIDEO_URL, params=params, data=body.encode("utf-8"),
                         headers=headers, timeout=30)
        r.raise_for_status()
        # 服务端有时返回 HTTP 200 但 body 为空 (实测 2026-10-28 同一 vid
        # 连试 8 次, 3 次成功 5 次空响应, 疑似服务端限流/抖动), 直接
        # r.json() 只会报没头没尾的 "Expecting value: line 1 column 1"。
        # 这里先验 body, 空响应按可重试失败处理, 交给 retry()/上层重试。
        if not r.content:
            raise RuntimeError(
                f"video_model 返回空响应 (HTTP {r.status_code}, 0 字节, "
                f"疑似服务端限流/抖动)")
        try:
            return r.json()
        except ValueError as e:
            raise RuntimeError(
                f"video_model 返回非 JSON (HTTP {r.status_code}, "
                f"{len(r.content)} 字节, 开头: {r.content[:80]!r}): {e}")

    resp = retry(_post, desc="video_model")
    data = resp.get("data")
    if not data:
        raise RuntimeError(f"video_model data empty: {str(resp)[:300]}")
    entry = data.get(vid)
    if entry is None:
        entry = next((v for v in data.values() if isinstance(v, (dict, list))), data)
        if isinstance(entry, list):
            entry = entry[0] if entry else {}
    model = entry.get("video_model") if isinstance(entry, dict) else None
    if isinstance(model, str):
        model = json.loads(model)
    if not model:
        model = data.get("video_model")
    if not model:
        raise RuntimeError("video_model empty")
    return model


def _find_key(obj, key):
    """递归找 key (jar 只查 dynamic_video/video_info/data 三层, 这里放宽为全递归)。"""
    if isinstance(obj, dict):
        v = obj.get(key)
        if v not in (None, "", 0):
            return v
        for sub in obj.values():
            r = _find_key(sub, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for sub in obj:
            r = _find_key(sub, key)
            if r is not None:
                return r
    return None


def _video_rows(model):
    """video_model -> [(quality_digits, row_dict)] (兼容 list / dict 两种 video_list)。"""
    vlist = _find_key(model, "video_list")
    if vlist is None:
        vlist = _find_key(model, "dynamic_video_list")
    rows = []
    if isinstance(vlist, list):
        for row in vlist:
            if isinstance(row, dict):
                rows.append((_quality_of(row, ""), row))
    elif isinstance(vlist, dict):
        for k, row in vlist.items():
            if isinstance(row, dict):
                rows.append((_quality_of(row, str(k)), row))
    return rows


def _quality_of(row, fallback_key=""):
    meta = row.get("video_meta") or {}
    for cand in (meta.get("definition"), row.get("definition"),
                 meta.get("vheight"), row.get("vheight"), fallback_key):
        if cand is not None:
            m = re.search(r"(2160|1440|1080|720|576|540|480|360)", str(cand).lower())
            if m:
                return m.group(1)
    return "1080"


def select_quality(model, want):
    """按 jar 的思路选档: 先要请求档, 再按 QUALITY_ORDER 兜底;
    选中档是 bytevc2 时优先换到其它非 bytevc2 档 (老播放器兼容)。"""
    rows = _video_rows(model)
    if not rows:
        raise RuntimeError("play model has no quality")
    by_q = {}
    for q, row in rows:
        by_q.setdefault(q, row)

    def codec(row):
        return str((row.get("video_meta") or {}).get("codec_type", "")).lower()

    order = [want] + [q for q in QUALITY_ORDER if q != want]
    chosen_q = next((q for q in order if q in by_q), None)
    if chosen_q is None:
        chosen_q = max(by_q, key=lambda x: int(x))
    if codec(by_q[chosen_q]) == "bytevc2":
        alt = next((q for q in QUALITY_ORDER
                    if q in by_q and codec(by_q[q]) != "bytevc2"), None)
        if alt:
            chosen_q = alt
    return chosen_q, by_q[chosen_q]


def _resolve_url(u, key_seed=b""):
    """还原一档的真实下载 URL。服务端对 main_url/backup_url 存在三种返回形态
    (同 vid 不同请求都出现过, 必须全兼容):
      1. 明文 https://...                       -> 直接用
      2. base64(明文 https://...)              -> b64 解码即得 (实测 2026-10-24 出现)
      3. spade 加密串 (b64 解码后头 A8 ?? 01 00) -> 须 model 带 key_seed,
         走 decrypt_spadeUrl (jar 的路径)
    jar 只处理了 1 和 3: 它拿到形态 2 时也会失败 (把 base64 串当 URL 请求)。
    这里按解码结果判断, 三种都收。"""
    s = str(u or "").strip()
    if not s or s.startswith(("http://", "https://")):
        return s
    raw = b64decode_loose(s)
    if raw.startswith((b"http://", b"https://")):
        return raw.decode("utf-8", "ignore")
    if key_seed:
        try:
            return decrypt_spade_url(s, key_seed)
        except Exception:
            pass
    return s


def _prepare_episode(vid, want_quality, device_id, install_id):
    """取 model + 选档 + 还原真实 URL + 派生内容密钥 (下载前的全部准备)。"""
    model = video_model(vid, device_id, install_id)
    q, row = select_quality(model, want_quality)
    meta = row.get("video_meta") or {}

    seed = _find_key(model, "key_seed")
    key_seed = b64decode_loose(seed) if seed else b""

    urls = []
    for cand_key in ("main_url", "backup_url", "backup_url_1"):
        u = row.get(cand_key)
        if u and str(u).strip() and str(u) not in urls:
            urls.append(str(u))
    real_urls = []
    for u in urls:
        u = _resolve_url(u, key_seed)
        if u and u not in real_urls:
            real_urls.append(u)
    if not real_urls:
        raise RuntimeError(f"vid={vid} 无可用下载 URL")

    enc_info = row.get("encrypt_info") or {}
    spade = row.get("spade_a") or enc_info.get("spade_a") or ""
    encrypted = bool(enc_info.get("encrypt", spade))
    key = derive_content_key(spade) if (encrypted and spade) else None
    return {"q": q, "meta": meta, "urls": real_urls, "key": key,
            "encrypted": encrypted, "model": model}


def available_resolutions(model):
    """video_model -> 可下载分辨率列表 (按高度从高到低, 同档去重)。
    每项: {q, definition, vwidth, vheight, codec_type, size, bitrate}"""
    seen, out = set(), []
    for q, row in _video_rows(model):
        if q in seen:
            continue
        seen.add(q)
        meta = row.get("video_meta") or {}
        out.append({
            "q": q,
            "definition": meta.get("definition") or f"{q}p",
            "vwidth": meta.get("vwidth"), "vheight": meta.get("vheight"),
            "codec_type": meta.get("codec_type", ""),
            "size": meta.get("size"), "bitrate": meta.get("bitrate"),
        })
    out.sort(key=lambda r: int(r["q"]), reverse=True)
    return out


# ----------------------------------------------------------------------------
# ffmpeg 优先解密: py 同目录 ffmpeg(.exe) > 环境变量 FFMPEG > PATH
# ----------------------------------------------------------------------------
_FFMPEG_CACHE = "unset"


def _app_dir():
    """脚本所在目录; PyInstaller 打包成 exe (frozen) 时 __file__ 指向临时
    解压目录, 必须改用 exe 文件本身所在目录, 否则"ffmpeg 放 exe 旁边"会失效。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def find_ffmpeg():
    """查找 ffmpeg: py/exe 同目录优先 (用户把 ffmpeg 放在旁边的场景),
    其次环境变量 FFMPEG/FFMPEG_PATH, 最后系统 PATH。找不到返回 None。"""
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE != "unset":
        return _FFMPEG_CACHE
    script_dir = _app_dir()
    candidates = []
    env = os.environ.get("FFMPEG") or os.environ.get("FFMPEG_PATH")
    if env:
        candidates.append(env)
    for name in ("ffmpeg.exe", "ffmpeg"):
        candidates.append(os.path.join(script_dir, name))
        candidates.append(os.path.join(script_dir, "ffmpeg", name))
    found = None
    for c in candidates:
        if c and os.path.isfile(c):
            found = c
            break
    if not found:
        found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    _FFMPEG_CACHE = found
    return found


def _still_encrypted(path):
    """粗检产物是否仍是加密 mp4: 文件里还有 senc box / encv / enca 标记即未解开。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return True
    return (b"senc" in data) or (b"encv" in data) or (b"enca" in data)


def ffmpeg_decrypt(enc_path, out_path, key):
    """ffmpeg -decryption_key <hex> -i enc -c copy out (mov 解复用器原生解 cenc)。
    成功 (返回 0 且产物存在且不再带加密标记) 返回 True, 否则 False 交回 Python 解密。"""
    ff = find_ffmpeg()
    if not ff or not key:
        return False
    cmd = [ff, "-y", "-v", "error", "-decryption_key", key.hex(),
           "-i", enc_path, "-c", "copy", out_path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
    except Exception:  # noqa: BLE001
        return False
    if r.returncode != 0:
        return False
    if not (os.path.isfile(out_path) and os.path.getsize(out_path) > 1024):
        return False
    return not _still_encrypted(out_path)


def _stream_download(url, path):
    """流式下载到文件 (带简单进度), 返回字节数。"""
    headers = {"User-Agent": MEDIA_UA, "Referer": "https://novel.snssdk.com/"}

    def _dl():
        total = 0
        with SESSION.get(url, headers=headers, timeout=180, stream=True) as r:
            r.raise_for_status()
            expected = int(r.headers.get("Content-Length") or 0)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
        if expected and total != expected:
            raise RuntimeError(f"下载不完整: {total}/{expected}")
        return total

    return retry(_dl, desc="媒体下载")


def download_episode(vid, want_quality, device_id, install_id):
    """单集全流程 (内存版, 兼容旧调用) -> (quality, 明文 mp4 bytes, video_meta)"""
    prep = _prepare_episode(vid, want_quality, device_id, install_id)
    headers = {"User-Agent": MEDIA_UA, "Referer": "https://novel.snssdk.com/"}
    last_err = None
    for u in prep["urls"]:
        try:
            def _get(url=u):
                r = SESSION.get(url, headers=headers, timeout=120)
                r.raise_for_status()
                return r.content
            blob = retry(_get, desc="媒体下载")
            if prep["key"]:
                blob = decrypt_cenc(blob, prep["key"])
            return prep["q"], blob, prep["meta"]
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"vid={vid} 下载/解密失败: {last_err}")


def download_episode_to_file(vid, want_quality, device_id, install_id, out_path,
                             retries=10):
    """单集全流程 (文件版) -> (quality, 文件字节数, video_meta, 解密方式)
    解密方式: 'ffmpeg' (优先) / 'python' (回退) / 'none' (源本身未加密)。
    加密中间文件落在目标同目录的 .enc.mp4, 成功后删除; 失败保留以便排查。
    失败自动重试: retries=10 表示首次失败后最多再试 10 次 (共 11 次尝试),
    每次重试都重新取 model/取真实 URL/派生密钥 (URL 可能过期, 换一次
    可能就通了), 重试间隔 2/4/6... 秒递增、上限 20 秒。注意内层
    _stream_download/video_model 本身已有 3 次小重试, 外层是整集级重试。"""
    retries = _parse_retries(retries)
    last_err = None
    for attempt in range(retries + 1):
        try:
            return _download_episode_to_file_once(
                vid, want_quality, device_id, install_id, out_path)
        except Exception as e:  # noqa: BLE001
            last_err = e
            # 删掉本次失败留下的不完整成品, 免得被当成下好的集数 (enc 中间
            # 文件保留, 以便排查; 下次重试下载时会覆盖它)
            try:
                if os.path.isfile(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            if attempt < retries:
                wait = min(2 * (attempt + 1), 20)
                log(f"vid={vid} 下载失败 (第{attempt + 1}/{retries + 1}次尝试): "
                    f"{e}, {wait} 秒后自动重试 ({attempt + 1}/{retries})...")
                time.sleep(wait)
    raise RuntimeError(f"vid={vid} 下载/解密失败 (已重试{retries}次, "
                       f"共{retries + 1}次尝试): {last_err}")


def _download_episode_to_file_once(vid, want_quality, device_id, install_id, out_path):
    """单集全流程单次尝试 (不重试, 供 download_episode_to_file 循环调用)。"""
    prep = _prepare_episode(vid, want_quality, device_id, install_id)
    enc_path = out_path + ".enc.mp4"
    last_err = None
    for u in prep["urls"]:
        try:
            _stream_download(u, enc_path)
            if not prep["key"]:
                os.replace(enc_path, out_path)
                return prep["q"], os.path.getsize(out_path), prep["meta"], "none"
            if ffmpeg_decrypt(enc_path, out_path, prep["key"]):
                os.remove(enc_path)
                return prep["q"], os.path.getsize(out_path), prep["meta"], "ffmpeg"
            # ffmpeg 不可用或解密失败 -> Python 解密回退
            with open(enc_path, "rb") as f:
                blob = decrypt_cenc(f.read(), prep["key"])
            with open(out_path, "wb") as f:
                f.write(blob)
            os.remove(enc_path)
            return prep["q"], os.path.getsize(out_path), prep["meta"], "python"
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"vid={vid} 下载/解密失败: {last_err}")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def sanitize(name, default="hongguo"):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip()
    return (name[:80] or default)


def parse_episodes(spec, total):
    if not spec or spec.lower() == "all":
        return list(range(1, total + 1))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted({n for n in out if 1 <= n <= total})


def _parse_threads(value, default=5):
    """解析用户输入的线程数: 转 int 失败/越界时回落。
    1 = 单线程顺序下载; 上限 16 (再高对单机带宽/服务端限流无收益,
    反而容易触发限流或把 CPU/磁盘占满)。"""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(16, n))


def _parse_retries(value, default=10):
    """解析重试次数: 失败后最多再试几次 (0=失败即放弃, 不重试)。
    非法输入回落默认值; 上限 20, 防止误输过大把一集卡死。"""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(0, min(20, n))


def _ask_threads(preset=None):
    """交互模式问线程数: 预设值 (-t 传入) 直接用, 否则提示输入。"""
    if preset is not None:
        n = _parse_threads(preset)
        log(f"同时下载线程数: {n} (命令行 -t {preset})")
        return n
    while True:
        raw = _ask("同时下载几集? 线程数 [1-16] (直接回车=5, 1=单线程顺序下载): ", "5")
        if str(raw).strip().isdigit() and 1 <= int(raw) <= 16:
            return int(raw)
        log("输入无效, 请输入 1-16 的整数。")


def split_episode_token(token):
    """HongGuo.splitEpisodeToken / detail 的前缀处理。"""
    t = str(token or "").strip()
    t = t.replace("hg-series-v1:", "")
    if t.startswith("hg-episode-v1:"):
        t = t[len("hg-episode-v1:"):]
        head, _, rest = t.partition(":")
        if head.isdigit() and rest:
            t = rest
    return t


def extract_id(raw):
    """从用户输入里提取 series_id / vid, 支持的输入 (原先的全部保留):
      - 纯数字 series_id / vid
      - 详情页 URL: https://hongguoduanju.com/detail?series_id=xxxx
      - 播放页 URL: https://hongguoduanju.com/player/xxxx
        (实测 2026-10-25: /player/{id} 的 id 就是 series_id, 播放页
         _ROUTER_DATA 里 publicVideoSeo.seriesId 与路径 id 一致,
         itemId 才是当集 vid, 且是详情 vid_list 的第 1 集; 故播放页
         链接按整剧处理, 与详情页链接等价)
      - hg-series-v1: / hg-episode-v1: 前缀 token
    提取不到数字时返回原串经 split_episode_token 处理的结果, 供后续判错。"""
    t = str(raw or "").strip()
    m = re.search(r"series_id=(\d+)", t)
    if m:
        return m.group(1)
    m = re.search(r"/player/(\d+)", t)
    if m:
        return m.group(1)
    m = re.search(r"/detail/(\d+)", t)
    if m:
        return m.group(1)
    return split_episode_token(t)


def log_env(device_id, install_id):
    ff = find_ffmpeg()
    log(f"红果短剧下载器 v{__VERSION__} | AES 后端: {_AES_BACKEND} | 解密: "
        f"{('ffmpeg 优先 (' + ff + ')') if ff else 'Python (未找到 ffmpeg)'}"
        f" | device_id={device_id} iid={install_id}")


def _mp4_duration(path):
    """读 mp4 的 mvhd 时长 (秒), 读不到返回 None。只读 box 头, 不整读文件。"""
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size
            off = 0
            while off + 8 <= file_size:
                f.seek(off)
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                size, typ = struct.unpack(">I4s", hdr)
                hdr_len = 8
                if size == 1:
                    size = struct.unpack(">Q", f.read(8))[0]
                    hdr_len = 16
                elif size == 0:
                    size = file_size - off
                if typ == b"moov":
                    f.seek(off + hdr_len)
                    child_hdr = f.read(8)
                    if len(child_hdr) < 8:
                        return None
                    csize, ctyp = struct.unpack(">I4s", child_hdr)
                    if ctyp != b"mvhd":
                        return None
                    body = f.read(min(csize, 128))
                    if body[0] == 1:
                        timescale = _u32(body, 20)
                        duration = _u64(body, 24)
                    else:
                        timescale = _u32(body, 12)
                        duration = _u32(body, 16)
                    return duration / timescale if timescale else None
                if size < 8:
                    return None
                off += size
    except Exception:  # noqa: BLE001
        return None
    return None


def _probe_duration(path):
    """用 ffprobe 读文件时长 (秒)。ffprobe 一般和 ffmpeg 在同一目录
    (Windows 上就是 ffprobe.exe, 在 exe/ffmpeg.exe 旁边), 找不到时用 PATH
    里的 ffprobe; 都没有则回落到手写的 _mp4_duration (只读 mvhd)。
    ffprobe 的 format duration 是 ffmpeg 自己算的时长, 合并前后用同一把
    尺子量, 不会像手写 mvhd 解析那样和 ffmpeg 的结果对不上。"""
    ff = find_ffmpeg()
    candidates = []
    if ff:
        d = os.path.dirname(ff)
        candidates.append(os.path.join(d, "ffprobe.exe"))
        candidates.append(os.path.join(d, "ffprobe"))
    ffprobe = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not ffprobe:
        ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, timeout=60)
            if r.returncode == 0:
                return float((r.stdout or b"").decode("utf-8", "ignore").strip())
        except Exception:  # noqa: BLE001
            pass
    return _mp4_duration(path)


def merge_videos(paths, out_path):
    """按给定顺序用 ffmpeg concat 分离器 + 流拷贝合并 mp4 (不重编码)。
    各集须同编码/同分辨率 (本下载器同剧同档下载天然满足)。
    成功返回 True; 无 ffmpeg 或合并失败返回 False (单集文件不受影响)。
    时长校验用 ffprobe 量合并前后 (同一把尺子); 校验不通过时产物保留
    不删除, 只记警告并逐集打出时长对照, 供抽查播放/定位异常集数。"""
    ff = find_ffmpeg()
    if not ff:
        log("合并跳过: 未找到 ffmpeg (合并必须用 ffmpeg, 请把它放到 py 同目录或 PATH)")
        return False
    list_path = out_path + ".concat.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in paths:
                ap = os.path.abspath(p).replace("'", "'\\''")
                f.write(f"file '{ap}'\n")
        cmd = [ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
               "-i", list_path, "-c", "copy", out_path]
        r = subprocess.run(cmd, capture_output=True, timeout=3600)
        if r.returncode != 0:
            tail = (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
            log(f"合并失败: ffmpeg 退出 {r.returncode} "
                f"{tail[-1] if tail else ''}")
            return False
    except Exception as e:  # noqa: BLE001
        log(f"合并失败: {e}")
        return False
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    if not (os.path.isfile(out_path) and os.path.getsize(out_path) > 1024):
        log("合并失败: 产物为空")
        return False
    # 时长校验: 合并时长应约等于各集时长之和 (流拷贝不改时长)。
    # 旧版只读 mvhd 手算, 个别集数的 mvhd 时长与实际流时长对不上
    # (源文件带 edit list / 非零起始时间戳时常见), 求和会系统性偏小,
    # 而 ffmpeg 合出来的文件按真实包时间戳计, 两边能差出 20% 以上,
    # 于是好好的合并也被判失败删掉。改成 ffprobe 两边同尺子量。
    durs = [_probe_duration(p) for p in paths]
    merged_dur = _probe_duration(out_path)
    if merged_dur and all(d is not None for d in durs):
        total = sum(durs)
        if abs(merged_dur - total) > max(3.0, total * 0.02):
            log(f"合并警告: 时长校验不通过 (合并 {merged_dur:.1f}s vs 各集之和 "
                f"{total:.1f}s, 差 {merged_dur - total:+.1f}s), 产物已保留未删除, "
                f"请抽查播放确认 (拖到中间/结尾看画面是否正常、有无重复段落)")
            for pth, d in zip(paths, durs):
                mv = _mp4_duration(pth)
                flag = ""
                if mv and abs(d - mv) > 2:
                    flag = f" (mvhd {mv:.1f}s, 与 ffprobe 差 {d - mv:+.1f}s, 该集元数据异常)"
                log(f"  {os.path.basename(pth)}: {d:.1f}s{flag}")
            return False
        log(f"合并完成: {out_path} ({os.path.getsize(out_path)/1e6:.2f} MB, "
            f"{len(paths)} 集, 总时长 {merged_dur/60:.1f} 分钟)")
    else:
        log(f"合并完成: {out_path} ({os.path.getsize(out_path)/1e6:.2f} MB, "
            f"{len(paths)} 集, 时长未校验)")
    return True


def _download_episodes(eps, vids, outdir, want_q, device_id, install_id, overwrite,
                       merge=False, title="", max_workers=5, retries=10):
    """整剧批量下载 (CLI 与交互模式共用), 返回退出码。
    多集并行: 各集写各自的文件 (中间文件为目标同名的 .enc.mp4, 互不干扰),
    日志与结果汇总只在主线程做, 不存在跨线程写同一文件的竞争; max_workers
    由用户输入 (-t / 交互提问, 默认 5, 范围 1-16), <=1 时退回单线程顺序下载
    (线程池 worker 数为 1)。merge=True 时, 全部下完后把成功集数按集序合并为
    一个整体视频 (单集文件保留)。单集失败自动重试 retries 次 (默认 10,
    见 download_episode_to_file), 10 次都失败才记为失败集。"""
    max_workers = _parse_threads(max_workers)
    os.makedirs(outdir, exist_ok=True)
    ok, fail = 0, []
    failed_eps = set()
    total = len(vids)

    def _run_one(n):
        vid = vids[n - 1]
        path = os.path.join(outdir, f"第{n:02d}集.mp4")
        q, size, _meta, method = download_episode_to_file(
            vid, want_q, device_id, install_id, path, retries=retries)
        return n, vid, path, q, size, method

    jobs = []
    for n in eps:
        vid = vids[n - 1]
        path = os.path.join(outdir, f"第{n:02d}集.mp4")
        if os.path.exists(path) and not overwrite:
            log(f"[{n}/{total}] 跳过已存在: {path}")
            ok += 1
            continue
        log(f"[{n}/{total}] vid={vid} 请求 {want_q}p 下载中...")
        jobs.append(n)

    if jobs:
        workers = min(max_workers, len(jobs))
        log(f"开始下载 {len(jobs)} 集, 线程数 {workers}"
            f"{' (单线程顺序)' if workers <= 1 else ''}"
            f", 单集失败自动重试 {retries} 次")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_one, n): n for n in jobs}
            for fut in as_completed(futs):
                n = futs[fut]
                try:
                    n, vid, path, q, size, method = fut.result()
                except Exception as e:  # noqa: BLE001
                    vid = vids[n - 1]
                    fail.append((n, vid, str(e)))
                    failed_eps.add(n)
                    log(f"[{n}/{total}] 失败 vid={vid}: {e}")
                else:
                    ok += 1
                    log(f"[{n}/{total}] 完成 {path} ({size/1e6:.2f} MB, 实际 {q}p, 解密={method})")
    log(f"全部结束: 成功 {ok}, 失败 {len(fail)}")
    for n, vid, err in fail:
        log(f"  失败: 第{n}集 vid={vid} {err}")

    if merge:
        ready = [os.path.join(outdir, f"第{n:02d}集.mp4") for n in eps
                 if n not in failed_eps
                 and os.path.isfile(os.path.join(outdir, f"第{n:02d}集.mp4"))]
        if len(ready) < 2:
            log(f"合并跳过: 可合并的集数不足 2 集 (现有 {len(ready)} 集)")
        else:
            base = sanitize(title) if title else "红果短剧"
            if eps == list(range(1, len(vids) + 1)):
                name = f"{base}_全{len(vids)}集.mp4"
            elif eps == list(range(eps[0], eps[-1] + 1)):
                name = f"{base}_第{eps[0]}-{eps[-1]}集.mp4"
            else:
                name = f"{base}_合并{len(ready)}集.mp4"
            out_path = os.path.join(outdir, name)
            if failed_eps:
                log(f"提示: {len(failed_eps)} 集下载失败, 合并将只包含成功的 "
                    f"{len(ready)} 集 (顺序不变)")
            log(f"开始合并 {len(ready)} 集 -> {out_path}")
            merge_videos(ready, out_path)
    return 0 if not fail else 1


# ----------------------------------------------------------------------------
# 交互模式: 输入 id -> 显示可下载分辨率 -> 选择 -> 下载
# ----------------------------------------------------------------------------
def _ask(prompt, default=""):
    try:
        s = input(prompt)
    except (EOFError, KeyboardInterrupt):
        log("")
        return default
    s = (s or "").strip()
    return s or default


def _probe_quality(vids, eps, device_id, install_id, default_q="1080"):
    """取分辨率列表 (交互模式用): 依次试计划里的前 3 集, 哪集拿到 model
    就用哪集列分辨率 (同剧各集档位通常一致)。某集取不到 model 时不直接
    炸掉整个程序: 服务端 video_model 接口本身会抖 (实测同一 vid 连试
    8 次 3 成 5 空), 换一集通常就能拿到; 3 集都拿不到则默认 default_q
    继续下载, 每集下载时还有 10 次重试兜底, 失败集最后汇总里能看到。"""
    tried = []
    for n in eps[:3]:
        vid = vids[n - 1]
        tried.append(n)
        try:
            model = video_model(vid, device_id, install_id)
            return _choose_quality(model, f"以第{n}集为例")
        except Exception as e:  # noqa: BLE001
            log(f"取第{n}集分辨率失败: {e}, 换一集再试...")
    log(f"连续 {len(tried)} 集都取不到分辨率列表, 默认用 {default_q}p 继续下载 "
        f"(下载每集时还会自动重试, 失败的集最后会汇总)")
    return default_q


def _choose_quality(model, sample_desc):
    rows = available_resolutions(model)
    if not rows:
        raise RuntimeError("该集没有可下载的分辨率数据")
    log(f"可下载分辨率 ({sample_desc}, 同剧各集档位通常一致):")
    for i, r in enumerate(rows, 1):
        wh = f"{r['vwidth']}x{r['vheight']}" if r.get("vwidth") else ""
        size = f"{r['size']/1e6:.2f} MB" if r.get("size") else "大小未知"
        br = f"{int(r['bitrate'])//1000} kbps" if r.get("bitrate") else ""
        warn = "  <- bytevc2, 老播放器可能不兼容" if r.get("codec_type") == "bytevc2" else ""
        log(f"  {i}. {r['definition']:<6} {wh:<10} {str(r.get('codec_type') or ''):<8} "
            f"{size:<10} {br}{warn}")
    while True:
        sel = _ask(f"请选择分辨率序号 [1-{len(rows)}] (直接回车=1, 最高画质): ", "1")
        if sel.isdigit() and 1 <= int(sel) <= len(rows):
            chosen = rows[int(sel) - 1]
            log(f"已选择: {chosen['definition']} ({chosen['q']}p)")
            return chosen["q"]
        log("输入无效, 请输入列表中的序号。")


def run_interactive(first_id=None, is_vid=False, outdir_base="downloads",
                    overwrite=False, device_id="", install_id="", merge_preset=None,
                    threads=None, retries=10):
    device_id = device_id or random_device_id()
    install_id = install_id or random_device_id()
    log(f"=== 红果短剧下载器 v{__VERSION__} (交互模式) ===")
    log_env(device_id, install_id)

    raw = first_id
    tries = 0
    while not raw:
        raw = _ask("请输入剧集 ID (series_id / 详情页 URL / 播放页 URL / 单集 vid): ")
        tries += 1
        if not raw and tries >= 3:
            log("未输入 ID, 退出。")
            return 2
    raw = extract_id(raw)

    detail = None
    if not is_vid:
        try:
            detail = fetch_detail(raw)
        except Exception as e:  # noqa: BLE001
            log(f"查详情失败: {e}")

    if is_vid or not detail or not detail.get("vid_list"):
        vid = split_episode_token(raw)
        if not re.fullmatch(r"\d+", vid or ""):
            log(f"无法识别 id: {raw} (既不是有效 series_id, 也不是纯数字 vid)")
            return 1
        log(f"单集模式 vid={vid}")
        try:
            model = video_model(vid, device_id, install_id)
            want_q = _choose_quality(model, "该集")
        except Exception as e:  # noqa: BLE001
            log(f"取该集分辨率失败: {e}, 默认用 1080p 继续下载 "
                f"(下载时还会自动重试)")
            want_q = "1080"
        os.makedirs(outdir_base, exist_ok=True)
        path = os.path.join(outdir_base, f"{vid}.mp4")
        if os.path.exists(path) and not overwrite:
            log(f"跳过已存在: {path}")
            return 0
        log(f"开始下载 vid={vid} {want_q}p ...")
        q, size, meta, method = download_episode_to_file(
            vid, want_q, device_id, install_id, path, retries=retries)
        log(f"完成: {path} ({size/1e6:.2f} MB, 实际 {q}p, "
            f"{meta.get('vwidth')}x{meta.get('vheight')}, 解密={method})")
        return 0

    title = sanitize(detail.get("series_name") or raw)
    vids = [str(v) for v in detail.get("vid_list") or []]
    log(f"剧名: {detail.get('series_name')} | series_id={raw} | "
        f"共 {len(vids)} 集 | {detail.get('episode_right_text', '')}")
    if detail.get("series_intro"):
        log(f"简介: {str(detail['series_intro'])[:80]}...")
    spec = _ask(f"下载哪些集? 共 {len(vids)} 集 (直接回车=全部, 如 1-5 或 1,3,5): ", "all")
    try:
        eps = parse_episodes(spec, len(vids))
    except Exception:
        log(f"集数输入无效: {spec}")
        return 1
    if not eps:
        log("没有有效的集数, 退出。")
        return 1
    log(f"计划下载 {len(eps)} 集: {eps if len(eps) <= 20 else str(eps[:20]) + ' ...'}")
    want_q = _probe_quality(vids, eps, device_id, install_id)
    max_workers = _ask_threads(threads)
    merge = False
    if len(eps) >= 2:
        if merge_preset is not None:
            merge = bool(merge_preset)
        else:
            ans = _ask("下载完成后, 是否按集数顺序将这些集合并成一个整体视频? (y/N): ", "N")
            merge = ans.lower().startswith("y")
    outdir = os.path.join(outdir_base, title)
    return _download_episodes(eps, vids, outdir, want_q, device_id, install_id,
                              overwrite, merge=merge, title=title,
                              max_workers=max_workers, retries=retries)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="红果短剧下载器 (csp_HongGuo 逆向): 按 series_id / vid 解密下载")
    ap.add_argument("id", nargs="?", help="series_id (剧 id), 或详情页/播放页 URL, 或单集 vid "
                         "(只给 id 时进入交互模式, 问集数/分辨率/线程数; 加 -e/-q/-t 等选项走批量)")
    ap.add_argument("--vid", help="只下这一集 (vid), 与位置参数二选一")
    ap.add_argument("-i", "--interactive", action="store_true",
                    help="交互模式: 提示输入 id, 显示可下载分辨率并选择后下载 "
                         "(不带任何参数启动时也自动进入交互模式)")
    ap.add_argument("-q", "--quality", default="1080",
                    help="清晰度: 2160/1440/1080/720/576/540/480/360 (默认 1080, 无此档自动兜底)")
    ap.add_argument("-e", "--episodes", default="all",
                    help="集数范围: all / 1 / 1-5 / 1,3,5 (默认 all)")
    ap.add_argument("-o", "--outdir", default="downloads", help="输出目录 (默认 downloads)")
    ap.add_argument("-t", "--threads", default=None, type=int,
                    help="同时下载的集数/线程数: 1-16 (默认 5, 1=单线程顺序下载; "
                         "交互模式下不带 -t 时会提示输入线程数, 带 -t 则直接用该值)")
    ap.add_argument("-r", "--retries", default=10, type=int,
                    help="单集下载失败后自动重试次数: 0-20 (默认 10, 即首次失败后"
                         "最多再试 10 次, 共 11 次尝试; 0=不重试)")
    ap.add_argument("--list", action="store_true", help="只打印剧信息和集列表, 不下载")
    ap.add_argument("--device-id", default="", help="固定 device_id (默认随机, 与 jar 一致)")
    ap.add_argument("--install-id", default="", help="固定 install_id/iid (默认随机)")
    ap.add_argument("--overwrite", action="store_true", help="已存在文件也重新下载")
    ap.add_argument("--merge", action="store_true",
                    help="全部集数下载完毕后, 按集数顺序用 ffmpeg 将它们合并成一个 "
                         "整体视频 (流拷贝不重编码, 需要 ffmpeg; 单集文件保留)")
    args = ap.parse_args(argv)

    device_id = args.device_id or random_device_id()
    install_id = args.install_id or random_device_id()
    want_q = re.search(r"\d+", str(args.quality))
    want_q = want_q.group(0) if want_q else "1080"

    # 交互模式: 显式 -i, 或不带 id 启动, 或只给了 id 没给任何下载选项
    # (只给 id 时也进交互: 依次问集数/分辨率/线程数, id 已预填不用重输。
    #  给了 -e/-q/-t/-r/--merge/--overwrite/--list/--vid 任一选项才走批量,
    #  这样脚本/定时任务的批量用法不受影响。)
    batch_opt = (args.vid or args.list or args.merge or args.overwrite
                 or args.episodes != "all" or args.quality != "1080"
                 or args.threads is not None or args.retries != 10)
    if args.interactive or not batch_opt:
        return run_interactive(first_id=args.vid or args.id, is_vid=bool(args.vid),
                               outdir_base=args.outdir, overwrite=args.overwrite,
                               device_id=device_id, install_id=install_id,
                               merge_preset=True if args.merge else None,
                               threads=args.threads, retries=args.retries)

    raw_id = extract_id(args.vid or args.id)

    log_env(device_id, install_id)

    # --vid 或位置参数查不到详情时按单集处理
    detail = None
    if not args.vid:
        try:
            detail = fetch_detail(raw_id)
        except Exception as e:  # noqa: BLE001
            log(f"查详情失败: {e}")
    if args.vid or not detail or not detail.get("vid_list"):
        vid = split_episode_token(raw_id)
        if not re.fullmatch(r"\d+", vid or ""):
            log(f"无法识别 id: {raw_id} (既不是有效 series_id, 也不是纯数字 vid)")
            return 1
        outdir = args.outdir
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, f"{vid}.mp4")
        if os.path.exists(path) and not args.overwrite:
            log(f"跳过已存在: {path}")
            return 0
        log(f"单集模式 vid={vid} 请求 {want_q}p ...")
        q, size, meta, method = download_episode_to_file(
            vid, want_q, device_id, install_id, path, retries=args.retries)
        log(f"完成: {path} ({size/1e6:.2f} MB, 实际 {q}p, "
            f"{meta.get('vwidth')}x{meta.get('vheight')}, {meta.get('codec_type')}, 解密={method})")
        return 0

    # 整剧模式
    title = sanitize(detail.get("series_name") or raw_id)
    vids = [str(v) for v in detail.get("vid_list") or []]
    log(f"剧名: {detail.get('series_name')} | series_id={raw_id} | "
        f"共 {len(vids)} 集 | {detail.get('episode_right_text', '')}")
    if detail.get("series_intro"):
        log(f"简介: {str(detail['series_intro'])[:80]}...")
    eps = parse_episodes(args.episodes, len(vids))
    log(f"计划下载 {len(eps)} 集: {eps if len(eps) <= 20 else str(eps[:20]) + ' ...'}")
    if args.list:
        for n, v in enumerate(vids, 1):
            log(f"  第{n}集 vid={v}")
        return 0

    outdir = os.path.join(args.outdir, title)
    return _download_episodes(eps, vids, outdir, want_q,
                              device_id, install_id, args.overwrite,
                              merge=args.merge, title=title,
                              max_workers=args.threads, retries=args.retries)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("已取消。")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        log(f"出错: {e}")
        sys.exit(1)
