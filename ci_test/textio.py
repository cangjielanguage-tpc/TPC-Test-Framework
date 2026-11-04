# @Copyright (c) Huawei Technologies Co., Ltd. 2024-2025. All rights reserved.
# Licensed under the Apache-2.0 License. See LICENSE file for details.
"""统一文本解码与标准流初始化。

CI 流水线里同一份日志的来源并不统一：
- cjc/cjpm/测试二进制输出 UTF-8，与系统 locale 无关；
- Windows 下 cmd 内置命令、hdc、hvigorw 等工具输出 GBK(CP936)；
- 部分工具还会夹带 ANSI 控制序列。

所以这里按 UTF-8 -> GB18030(GBK 超集) -> 替换符 的顺序解码：
既能正确还原 UTF-8，也能兜住 GBK，且任何情况下都不抛 UnicodeDecodeError、
也不会像 errors='ignore' 那样静默丢字（丢掉的字往往会连带吞掉整行语义）。
"""

import os
import sys

# 解码回退顺序：UTF-8 优先，GB18030 兜底
FALLBACK_ENCODINGS = ("utf-8", "gb18030")


def decode_text(data):
    """把 bytes 解码成 str；传入 str 时原样返回，便于调用点无差别使用。"""
    if isinstance(data, str):
        return data
    if not data:
        return ""
    for encoding in FALLBACK_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def setup_stdio():
    """固定标准流编码。

    Python 在输出被重定向（CI 抓取日志）时会退回 locale 编码：
    Linux C locale 下是 ASCII，中文直接抛异常或被丢弃；
    Windows 下是 CP936，UTF-8 字节会被按 GBK 解读成乱码。
    Windows 控制台本身走 Unicode 接口渲染，只需把异常字节替换掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if os.name == "nt" and stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
