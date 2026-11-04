# @Copyright (c) Huawei Technologies Co., Ltd. 2024-2025. All rights reserved.
# Licensed under the Apache-2.0 License. See LICENSE file for details.

import json
import os
import re
import shutil
import platform
import subprocess
from tomlkit import parse
from textio import decode_text

str_head_1 = [233, 166, 131, 208, 152, 32, 116, 101, 115, 116, 32]
str_head_2 = [27, 91, 52, 70, 27, 55, 27, 91, 57, 57, 57, 57, 69, 27, 91, 51, 70, 233, 166, 131, 230, 145, 157, 32, 103, 114, 111, 117, 112, 32, 100, 101, 102, 97, 117, 108, 116]
str_tail_1 = [41, 32, 27, 56, 27, 91, 48, 74, 27, 55, 27, 91, 59, 114, 27, 56] # 尾


def find_readme_file(home_dir):
    """
    在项目目录中查找 README 文件
    支持多种常见的 README 文件命名格式：
    - README.md (最常见)
    - README
    - readme.md
    - 其他包含 "readme" 的文件（模糊匹配）
    
    Args:
        home_dir: 项目根目录路径
        
    Returns:
        str: README 文件的完整路径，如果未找到则返回 None
    """
    # 定义最常见的 README 文件名（优先级最高）
    common_readme_names = [
        'README.md',
        'README',
        'readme.md',
        'README.MD'
    ]
    
    # 首先检查最常见的文件名
    for readme_name in common_readme_names:
        readme_path = os.path.join(home_dir, readme_name)
        if os.path.exists(readme_path) and os.path.isfile(readme_path):
            return readme_path
    
    # 如果常见名称都没找到，使用模糊匹配查找包含 "readme" 的文件
    try:
        for file_name in os.listdir(home_dir):
            file_path = os.path.join(home_dir, file_name)
            # 检查是否是文件且包含 "readme"（不区分大小写）
            if os.path.isfile(file_path) and 'readme' in file_name.lower():
                # 排除一些特殊情况（通常是辅助文档而非主 README）
                excluded_patterns = [
                    'opensource',      # README.OpenSource
                    '_cn', '_en',      # README_cn.md, README_en.md
                    '.opensource',     # README.opensource
                    'changelog',       # README_CHANGELOG
                    'license',         # README_LICENSE
                ]
                # 如果文件名不包含排除模式，则认为是主 README
                if not any(pattern in file_name.lower() for pattern in excluded_patterns):
                    return file_path
    except Exception as e:
        pass
    
    return None


def get_cjc_version_from_readme(readme_path):
    """
    从 README.md 文件中提取 cjc 版本信息
    支持多种常见格式：
    1. badge 格式: badge/cjc-v<version>-... 或 badge/cjc-<version>-...
    2. 直接文本格式: cjc: <version>, 仓颉版本: <version>, Cangjie: <version>
    3. 其他格式
    
    Args:
        readme_path: README.md 文件的路径
        
    Returns:
        str: cjc 版本号，如果未找到则返回 None
    """
    if not os.path.exists(readme_path):
        return None
    
    try:
        with open(readme_path, 'r', encoding='UTF-8') as f:
            content = f.read()
            lines = content.split('\n')
            
            # 优先尝试从 badge 格式提取（通常在前几行）
            for line in lines[:20]:  # 只检查前20行，badge 通常在文件开头
                # badge 格式: badge/cjc-v<version>-... 或 badge/cjc-<version>-...
                badge_match = re.search(r'badge/cjc-(?:v)?([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)-', line)
                if badge_match:
                    return badge_match.group(1)
                
                # 其他 badge 格式变体
                badge_match2 = re.search(r'cjc.*?(?:v)?([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)', line, re.IGNORECASE)
                if badge_match2:
                    return badge_match2.group(1)
            
            # 尝试从文本格式提取
            for line in lines:
                # 格式: cjc: <version>, 仓颉版本: <version>, Cangjie: <version>
                text_match = re.search(r'(?:cjc|仓颉版本|Cangjie)[:\s]+(?:v)?([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)', line, re.IGNORECASE)
                if text_match:
                    return text_match.group(1)
                
                # 格式: cjc version: <version>
                text_match2 = re.search(r'cjc\s+version[:\s]+(?:v)?([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)', line, re.IGNORECASE)
                if text_match2:
                    return text_match2.group(1)
            
            return None
    except Exception as e:
        if hasattr(get_cjc_version_from_readme, 'LOG'):
            get_cjc_version_from_readme.LOG.warning(f"读取 README.md 文件失败: {e}")
        return None


class ArgConfig:
    CANGJIE_STDX_DOWNLOAD_MAP = {
        "1.0.0":
            {
                "aarch64-linux-ohos": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.0.1/cangjie-stdx-ohos-aarch64-1.0.0.1.zip",
                "aarch64": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.0.1/cangjie-stdx-linux-aarch64-1.0.0.1.zip",
                "x86_64-unknown-linux-gnu": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.0.1/cangjie-stdx-linux-x64-1.0.0.1.zip",
                "x86_64-linux-ohos": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.0.1/cangjie-stdx-ohos-x64-1.0.0.1.zip",
                "windows": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.0.1/cangjie-stdx-windows-x64-1.0.0.1.zip",
            },
        "1.0.1":
            {
                "aarch64-linux-ohos": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-ohos-aarch64-1.0.1.1.zip",
                "aarch64": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-linux-aarch64-1.0.1.1.zip",
                "x86_64-unknown-linux-gnu":"https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-linux-x64-1.0.1.1.zip",
                "x86_64-linux-ohos": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-ohos-x64-1.0.1.1.zip",
                "windows": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-windows-x64-1.0.1.1.zip"
            },
        "1.0.3":
            {
                "aarch64-linux-ohos": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-ohos-aarch64-1.0.1.1.zip",  # TODO 未发现release版本
                "aarch64": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.3.1/cangjie-stdx-linux-aarch64-1.0.3.1.zip",
                "x86_64-unknown-linux-gnu":"https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.3.1/cangjie-stdx-linux-x64-1.0.3.1.zip",
                "x86_64-linux-ohos": "https://gitcode.com/Cangjie/cangjie-stdx-bin/releases/download/v1.0.1.1/cangjie-stdx-ohos-x64-1.0.1.1.zip",  # TODO 未发现release版本
                "windows": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.3.1/cangjie-stdx-windows-x64-1.0.3.1.zip"
            },
        "1.0.4":
            {
                "aarch64-linux-ohos": "",  # TODO 未发现release版本
                "aarch64": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.4.1/cangjie-stdx-linux-aarch64-1.0.4.1.zip",
                "x86_64-unknown-linux-gnu": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.4.1/cangjie-stdx-linux-x64-1.0.4.1.zip",
                "x86_64-linux-ohos": "",  # TODO 未发现release版本
                "windows": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.4.1/cangjie-stdx-windows-x64-1.0.4.1.zip"
            },
        "1.0.5":
            {
                "aarch64-linux-ohos": "",  # TODO 未发现release版本
                "aarch64": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.5.1/cangjie-stdx-linux-aarch64-1.0.5.1.zip",
                "x86_64-unknown-linux-gnu": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.5.1/cangjie-stdx-linux-x64-1.0.5.1.zip",
                "x86_64-linux-ohos": "",  # TODO 未发现release版本
                "windows": "https://gitcode.com/Cangjie/cangjie_stdx/releases/download/v1.0.5.1/cangjie-stdx-windows-x64-1.0.5.1.zip"
            },
        "1.1.0":
            {
                "aarch64-linux-ohos": "https://gitcode.com/Cangjie/nightly_build/releases/download/1.1.0-alpha.20260210010001/cangjie-stdx-ohos-aarch64-1.1.0-alpha.20260210010001.1.zip",
                "aarch64": "https://gitcode.com/Cangjie/nightly_build/releases/download/1.1.0-alpha.20260210010001/cangjie-stdx-linux-aarch64-1.1.0-alpha.20260210010001.1.zip",
                "x86_64-unknown-linux-gnu": "https://gitcode.com/Cangjie/nightly_build/releases/download/1.1.0-alpha.20260210010001/cangjie-stdx-linux-x64-1.1.0-alpha.20260210010001.1.zip",
                "x86_64-linux-ohos": "https://gitcode.com/Cangjie/nightly_build/releases/download/1.1.0-alpha.20260210010001/cangjie-stdx-ohos-x64-1.1.0-alpha.20260210010001.1.zip",
                "windows": "https://gitcode.com/Cangjie/nightly_build/releases/download/1.1.0-alpha.20260210010001/cangjie-stdx-windows-x64-1.1.0-alpha.20260210010001.1.zip"
            },
    }
    BUILD_TYPE = None
    LOG = None
    Woff = ""
    CANGJIE_SOURCE_DIR = ""
    CI_TEST_DIR = ""
    TEST_DIR = ""
    UT_TEST_DIR = ""
    BASE_DIR = None
    HOME_DIR = None
    HOME = None
    FILE_ROOT = None
    LIB_DIR = None
    BUILD_BIN = "build"
    CJ_TEST_WORK = "test"
    OS_PLATFORM = "windows"
    LINE_SEPARATOR = "\\r\\n"
    CONFIG_FILE = "module.json"
    MODULE_NAME = None
    EXPECT_CJC_VERSION = None
    REALLY_CJC_VERSION = None
    build_output_dir = None
    BUILD_PARMS = None  # 解析的cjpm.toml参数, 以字典的形式
    BUILD_CI_TEST_CFG = None  #
    BUILD_DEPENDENCIES = []
    BUILD_CJPM_PATH = None
    IMPORT_PATH = ""  # --import-path
    LIBRARY_PATH = ""  # -L
    LIBRARY = ""  # -l
    MODULE_FOREIGN_REQUIRES = None
    WINDOWS_C_LIB_ARR = set()
    CUSTOM_MAP = {}
    LIBRARY_PRIORITY = list()
    OHOS_CANGJIE_PATH = None
    OHOS_COMPILE_OPTION = None
    OHOS_VERSION = None
    CANGJIE_TARGET = None
    CANGJIE_STDX_DIR = None
    CANGJIE_HOME = None
    cj_home = None
    BASE_CJC_VERSION = "0.0.0"
    UPDATE_CJPM_TOML = False
    GIT_USERNAME = None
    GIT_PASSWORD = None
    CJC_RUNTIME_SUFFIX = 'llvm'  # cjc 1.0.4(包含)以下为llvm, 以上是 cjnative
    WHICH_CJC = None
    WHICH_CJC_OUT_STR = None
    IS_SELF_CONFIGURATION = False
    LOG_CMD = "simple"  # simple: 简化命令输出, full: 全量打印
    SHOW_WARN = False   # 是否显示编译告警日志

    def __init__(self):
        master_cjc = shutil.which("cjc")
        if master_cjc:
            out = os.popen('{} -v'.format(master_cjc))
            self.REALLY_CJC_VERSION = out.readline().split('Cangjie Compiler: ')[1].split(' (')[0]
        if platform.system() == 'Linux':
            if platform.uname().processor == "x86_64" or platform.uname().machine == "x86_64":
                self.OS_PLATFORM = 'linux_x86_64'
            elif platform.uname().processor == "aarch64" or platform.uname().machine == "aarch64":
                self.OS_PLATFORM = 'linux_aarch64'
            else:
                self.OS_PLATFORM = None
        elif platform.system() == 'Windows':
            self.OS_PLATFORM = "windows"
        elif platform.system() == 'Darwin':
            if platform.uname().processor == "x86_64" or platform.uname().machine == "x86_64":
                self.OS_PLATFORM = 'darwin_x86_64'
            elif platform.uname().processor == "arm64" or platform.uname().machine == "arm64":
                self.OS_PLATFORM = 'darwin_arm64'
            else:
                self.OS_PLATFORM = 'darwin_x86_64'
        else:
            self.OS_PLATFORM = None
        if self.OS_PLATFORM and self.OS_PLATFORM == "windows":
            self.LINE_SEPARATOR = "\r\n"
        elif str(self.OS_PLATFORM).startswith("linux") or str(self.OS_PLATFORM).startswith("darwin"):
            self.LINE_SEPARATOR = "\n"
        else:
            self.LINE_SEPARATOR = "\r"

    def get_stdx_url(self):
        if self.CANGJIE_TARGET == "aarch64-linux-ohos":
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION][self.CANGJIE_TARGET]
        elif self.CANGJIE_TARGET == "x86_64-linux-ohos":
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION][self.CANGJIE_TARGET]
        elif self.CANGJIE_TARGET == "x86_64-unknown-linux-gnu":
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION][self.CANGJIE_TARGET]
        elif "windows" in self.CANGJIE_TARGET:
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION]["windows"]
        elif "mingw32" in self.CANGJIE_TARGET:
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION]["windows"]
        elif "aarch64" in self.CANGJIE_TARGET:
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION]["aarch64"]
        elif "darwin" in self.CANGJIE_TARGET or "apple" in self.CANGJIE_TARGET:
            if self.LOG:
                self.LOG.warn("macOS 平台暂无官方 stdx 下载链接，尝试使用 Linux x86_64 版本")
            return self.CANGJIE_STDX_DOWNLOAD_MAP[self.BASE_CJC_VERSION]["x86_64-unknown-linux-gnu"]
        return None

    def config_init(self):
        if os.path.exists(os.path.join(self.HOME_DIR, "cjpm.toml")):
            self.__handle_toml()
        elif os.path.exists(os.path.join(self.HOME_DIR, "module.json")):
            self.__handle_json()
        else:
            self.LOG.warn("没有项目工程配置文件, 请配置module.json或者cjpm.toml配置文件")

    def set_build_bin(self, new_build):
        self.BUILD_BIN = new_build

    def __handle_toml(self):
        self.CONFIG_FILE = "cjpm.toml"
        cfg_file = os.path.join(self.HOME_DIR, self.CONFIG_FILE)
        self.BUILD_PARMS = parse(open(cfg_file, "r", encoding='UTF-8').read())
        try:
            self.MODULE_NAME = self.BUILD_PARMS['package']['name']
        except:
            self.MODULE_NAME = os.path.basename(self.HOME_DIR)
        
        # 优先从 README 文件获取 cjc 版本
        readme_path = find_readme_file(self.HOME_DIR)
        if readme_path:
            readme_version = get_cjc_version_from_readme(readme_path)
            if readme_version:
                self.EXPECT_CJC_VERSION = readme_version
                self.LOG.info(f"从 {os.path.basename(readme_path)} 获取到 cjc 版本: {readme_version}")
            else:
                # 如果 README 文件中没有找到版本信息，则从 cjpm.toml 获取（最低兼容版本）
                try:
                    self.EXPECT_CJC_VERSION = self.BUILD_PARMS['package']['cjc-version']
                    self.LOG.info(f"从 cjpm.toml 获取到 cjc 版本（最低兼容版本）: {self.EXPECT_CJC_VERSION}")
                except:
                    pass
        else:
            # 如果没有 README 文件，则从 cjpm.toml 获取（最低兼容版本）
            try:
                self.EXPECT_CJC_VERSION = self.BUILD_PARMS['package']['cjc-version']
                self.LOG.info(f"从 cjpm.toml 获取到 cjc 版本（最低兼容版本）: {self.EXPECT_CJC_VERSION}")
            except:
                pass
        
        try:
            for key, value in self.BUILD_PARMS['ffi'].items():
                if key == "c":
                    self.MODULE_FOREIGN_REQUIRES = value
        except:
            pass
        try:
            file = open(cfg_file, "r", encoding='UTF-8')
            for line in file.readlines():
                if line.startswith("#"):
                    self.CUSTOM_MAP[re.search(r"\[.*?\]", line).group()[1:-1]] = re.search(r"\{.*?\}", line).group()[
                                                                                 1:-1]
            file.close()
        except:
            self.LOG.warn("toml 配置文件定义出错, 格式 # [key]={value}, 请检查")

    def __handle_json(self):
        try:
            file = open(os.path.join(self.HOME_DIR, self.CONFIG_FILE), "r", encoding='UTF-8')
            self.BUILD_PARMS = json.load(file)
            try:
                self.MODULE_NAME = self.BUILD_PARMS['name']
                
                # 优先从 README 文件获取 cjc 版本
                readme_path = find_readme_file(self.HOME_DIR)
                if readme_path:
                    readme_version = get_cjc_version_from_readme(readme_path)
                    if readme_version:
                        self.EXPECT_CJC_VERSION = readme_version
                        self.LOG.info(f"从 {os.path.basename(readme_path)} 获取到 cjc 版本: {readme_version}")
                    else:
                        # 如果 README 文件中没有找到版本信息，则从 module.json 获取
                        self.EXPECT_CJC_VERSION = self.BUILD_PARMS['cjc-version']
                        self.LOG.info(f"从 module.json 获取到 cjc 版本: {self.EXPECT_CJC_VERSION}")
                else:
                    # 如果没有 README 文件，则从 module.json 获取
                    self.EXPECT_CJC_VERSION = self.BUILD_PARMS['cjc-version']
                    self.LOG.info(f"从 module.json 获取到 cjc 版本: {self.EXPECT_CJC_VERSION}")
                
                self.MODULE_FOREIGN_REQUIRES = self.BUILD_PARMS['foreign_requires']
            except:
                self.MODULE_NAME = os.path.basename(self.HOME_DIR)
        except FileNotFoundError:
            self.LOG.warn("未发现module.json文件")

    def _simplify_cmd(self, cmd):
        """简化命令输出，折叠 --import-path、-L、-l 等冗长参数"""
        if self.LOG_CMD == "full":
            return cmd
        parts = cmd.split()
        simplified = []
        seen = {"--import-path": False, "-L": False, "-l": False}
        i = 0
        while i < len(parts):
            part = parts[i]
            if part in seen and i + 1 < len(parts):
                if not seen[part]:
                    count = parts.count(part)
                    simplified.append(f"{part} ...({count}个)")
                    seen[part] = True
                i += 2  # skip the option value
            else:
                simplified.append(part)
                i += 1
        return " ".join(simplified)

    def run_cmd(self, cmd, file_dir="./"):
        self.LOG.info("CMD    : %s", self._simplify_cmd(cmd))
        res = subprocess.Popen(cmd, shell=True, cwd=file_dir, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
        try:
            while res.poll() is None:
                for msg in iter(res.stdout.readline, b''):
                    msg = decode_text(msg).strip()
                    if msg != "":
                        if not llt_check_not_start_or_end_with_target(msg):
                            self.LOG.info(msg)
        finally:
            if res.poll() is None:
                res.kill()
        return res.returncode


def llt_check_not_start_or_end_with_target(msg) -> bool:
    return check_not_start_or_end_with_target(msg, str_head_1, True) and \
            check_not_start_or_end_with_target(msg, str_head_2, True) and \
            check_not_start_or_end_with_target(msg, str_tail_1, False)


def check_not_start_or_end_with_target(input_data, target_byte_arr, flag, encoding='utf-8'):
    """
    纯字节手动对比：判断字符串/字节序列 既不是以目标字节序列开头，也不是以其结尾
    不使用startswith/endswith，完全手动截取+逐字节比对
    :param input_data: 待判断的字符串 或 直接传入bytes字节序列
    :param encoding: 若input_data是字符串，使用该编码转字节（默认utf-8）
    :return: True（既不是开头也不是结尾）/ False（开头或结尾匹配）
    """
    target_bytes = bytes(target_byte_arr)

    # 2. 统一转为bytes字节序列（兼容字符串/bytes输入）
    if isinstance(input_data, str):
        try:
            str_bytes = input_data.encode(encoding)
        except UnicodeEncodeError as e:
            print(f"编码错误：{e}")
            return True  # 编码失败默认视为“不匹配”
    elif isinstance(input_data, bytes):
        str_bytes = input_data
    else:
        print("输入类型错误，仅支持字符串或bytes")
        return True

    # ------------------- 手动判断“开头匹配” -------------------
    if flag:
        # 截取开头target_len个字节，逐字节对比
        for n, c in zip(target_bytes, str_bytes):
            if n != c:
                return False
    else:
        # ------------------- 手动判断“结尾匹配” -------------------
        # 截取结尾target_len个字节，逐字节对比（计算结尾起始索引：str_len - target_len）
        for n, c in zip(target_bytes[::-1], str_bytes[::-1]):
            if n != c:
                return False

    # 4. 核心逻辑：只要开头/结尾有一个匹配，返回False；否则返回True
    return True

