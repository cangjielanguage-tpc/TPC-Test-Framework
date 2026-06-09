# @Copyright (c) Huawei Technologies Co., Ltd. 2024-2025. All rights reserved.
# Licensed under the Apache-2.0 License. See LICENSE file for details.

import csv
import glob
import pathlib
import zipfile
import argparse
import configparser
import json
import logging
import os
import re
import platform
import signal
import shutil
import subprocess
import sys
import threading
import uuid
import time
import urllib.request
import urllib.parse
import urllib.error
from subprocess import PIPE
from pathlib import Path
from xml.dom import minidom
from config import ArgConfig, llt_check_not_start_or_end_with_target
from tomlkit import parse, dump as dump_c
from logging import handlers
from logging.handlers import TimedRotatingFileHandler
import xml.etree.cElementTree as Et
from get_stdx_release import get_cjc_version_from_command, generate_possible_urls, find_valid_urls, \
    find_closest_version, download_and_extract

# 全局测试报告收集字典
test_report = {
    "total_cases": 0,
    "passed_cases": 0,
    "failed_cases": 0,
    "error_cases": 0,
    "skipped_cases": 0,
    "cases": {},  # {test_class: [[case_name, status, time_elapsed, error_trace]]}
    "class_times": {}  # {test_class: total_time}
}

if platform.system() == "Windows":
    dynamic_lib = ".dll"
    static_lib = ".lib"
elif platform.system() == "Darwin":
    dynamic_lib = ".dylib"
    static_lib = ".a"
else:
    dynamic_lib = ".so"
    static_lib = ".a"


# --------------------------
# 关键：抽离共享的参数配置函数（避免重复写参数）
# --------------------------
def add_llt_common_arguments(parser):
    test_parser_optimize = parser.add_mutually_exclusive_group()
    test_parser_optimize.add_argument("-O", help="编译构建优化选项")
    test_parser_optimize.add_argument("--coverage", action='store_true', help="跑测试用例是否用覆盖率方式测试")
    test_parser_path = parser.add_mutually_exclusive_group()
    test_parser_path.add_argument("--case", help="指定单跑一个用例")
    test_parser_path.add_argument("--target", help="适用于ohos")
    test_parser_path.add_argument("--clean", action='store_true', help="是否清空测试临时目录")
    test_parser_path.add_argument("-p", "--path", help="指定跑一个文件夹, 适用于在test/LLT文件夹多个文件夹方式")
    parser.add_argument("--log", help="指定日志输出文件路径，以追加形式写入")
    parser.add_argument("--log-cmd", choices=["simple", "full"], default="simple",
                        help="命令日志输出方式: simple=简化, full=全量")
    parser.set_defaults(func=test)


# --------------------------
# 关键：抽离共享的参数配置函数（避免重复写参数）
# --------------------------
def add_hlt_common_arguments(cjtest_parser):
    cjtest_parser.set_defaults(func=cjtest)
    cjtest_parser.add_argument("--target", help="指定运行环境方式, ohos和其他环境")
    cjtest_parser_optimize = cjtest_parser.add_mutually_exclusive_group()
    cjtest_parser_optimize.add_argument("-O", help="HLT用例测试方式")
    cjtest_parser_optimize.add_argument("--coverage", action='store_true', help="HLT用例测试方式")
    cjtest_parser_path = cjtest_parser.add_mutually_exclusive_group()
    cjtest_parser_path.add_argument("--case", help="HLT用例测试方式")
    cjtest_parser_path.add_argument("-p", "--path", help="HLT用例测试方式")
    cjtest_parser.add_argument("--root", help="HLT用例测试方式")
    cjtest_parser_branch = cjtest_parser.add_mutually_exclusive_group()
    cjtest_parser_branch.add_argument("--clean", action='store_true', help="是否清空测试临时目录")
    cjtest_parser_branch.add_argument("--main", action='store_true', help="HLT用例测试方式")
    cjtest_parser_branch.add_argument("--fuzz", action='store_true', help="HLT用例测试方式")
    cjtest_parser.add_argument("--log", help="指定日志输出文件路径，以追加形式写入")
    cjtest_parser.add_argument("--log-cmd", choices=["simple", "full"], default="simple",
                               help="命令日志输出方式, simple: 简化输出, full: 全量打印")


def __set_args_default_attribute(args, attr: str):
    if not hasattr(args, attr):
        setattr(args, attr, None)


def set_args_default_attribute(args):
    __set_args_default_attribute(args, "coverage")
    __set_args_default_attribute(args, "case")
    __set_args_default_attribute(args, "root")
    __set_args_default_attribute(args, "p")
    __set_args_default_attribute(args, "path")
    __set_args_default_attribute(args, "clean")
    __set_args_default_attribute(args, "main")
    __set_args_default_attribute(args, "fuzz")
    __set_args_default_attribute(args, "html")
    __set_args_default_attribute(args, "b")
    __set_args_default_attribute(args, "LLT")
    __set_args_default_attribute(args, "HLT")
    __set_args_default_attribute(args, "cangjie")
    __set_args_default_attribute(args, "cj_home")
    __set_args_default_attribute(args, "target")
    __set_args_default_attribute(args, "full")
    __set_args_default_attribute(args, "f")
    __set_args_default_attribute(args, "stdx_home")
    __set_args_default_attribute(args, "json")
    __set_args_default_attribute(args, "csv")
    __set_args_default_attribute(args, "update_stdx")
    __set_args_default_attribute(args, "update_toml")
    __set_args_default_attribute(args, "log")
    __set_args_default_attribute(args, "log_cmd")
    __set_args_default_attribute(args, "show_warn")


def parse_args(cfgs):
    parser = argparse.ArgumentParser(description="仓颉三方库功能集成脚本")
    sub_parser = parser.add_subparsers()
    coverage_parser = sub_parser.add_parser("coverage", help="生成覆盖率报告的命令")
    coverage_parser.set_defaults(func=coverage)
    coverage_parser.add_argument("--html", action='store_true', help="跑完用例之后执行的命令, 用于生成html覆盖率报告")
    coverage_parser.add_argument("-e", help="排除的文件路径")
    coverage_parser.add_argument("-b", action='store_true',
                                 help="跑完用例之后执行的命令, 和--html一起合用, 用于生成html覆盖率分支覆盖报告")
    coverage_parser_optimize = coverage_parser.add_mutually_exclusive_group()
    coverage_parser_optimize.add_argument('--LLT', action='store_true', help="指定跑LLT用例覆盖率")
    coverage_parser_optimize.add_argument('--HLT', action='store_true', help="指定跑HLT用例覆盖率")
    # clean_parser = sub_parser.add_parser("clean", help="清空缓存文件")
    # clean_parser.set_defaults(func=clean)
    version_parser = sub_parser.add_parser("version", help="打印脚本版本信息")
    version_parser.set_defaults(func=print_version)
    version_parser.add_argument("--cangjie", action='store_true', help="打印仓颉项目版本")
    version_parser.add_argument("--cj-home", help="打印仓颉项目版本")

    build_parser = sub_parser.add_parser("build", help="用于构建整个项目的命令")
    build_parser.add_argument("--target", help="适用于ohos的命令")
    build_parser.add_argument("-f", "--full", action='store_true', help="是否是全量进行构建, 默认cjpm build -i")
    build_parser.add_argument("--coverage", action='store_true', help="是否使用覆盖率方式构建")
    build_parser.add_argument("--cj-home", help="设置仓颉环境路径")
    build_parser.add_argument("--stdx-home", help="设置仓颉环境路径")
    build_parser.add_argument("--update-toml", action='store_true', help="更新仓颉toml文件")
    build_parser.add_argument("--update-stdx", action='store_true', help="更新仓颉stdx")
    build_parser.add_argument("--release", action='store_true', help="release模式编译")
    build_parser.add_argument("--debug", action='store_true', help="debug模式编译")
    build_parser.add_argument('--verbose', action='store_true', help="--verbose")
    build_parser.add_argument("--log", help="指定日志输出文件路径，以追加形式写入")
    build_parser.add_argument("--log-cmd", choices=["simple", "full"], default="simple",
                              help="命令日志输出方式, simple: 简化输出, full: 全量打印")
    build_parser.add_argument("--show-warn", action='store_true', help="显示编译告警日志, 默认不显示")
    build_parser.set_defaults(func=build)

    build_parser = sub_parser.add_parser("download", help="下载测试仓库文件")
    build_parser.add_argument("--owner", type=str, help="仓库所属空间地址")
    build_parser.add_argument("--repo", type=str, help="仓库名称")
    build_parser.add_argument("--bench", type=str, help="分支名称")
    build_parser.add_argument("--depth", type=int, help="克隆深度")
    build_parser.add_argument("--dir-name", type=str, help="克隆目录")
    build_parser.add_argument("--username", type=str, help="git用户")
    build_parser.add_argument("--password", type=str, help="git密码")
    build_parser.add_argument("-f", "--force", action='store_true', help="是否强制覆盖原来文件夹")
    build_parser.set_defaults(func=download)

    add_llt_common_arguments(sub_parser.add_parser("test", help="用于LLT测试命令"))
    add_llt_common_arguments(sub_parser.add_parser("llt", help="用于LLT测试命令"))

    ut_parser = sub_parser.add_parser("ut", help="单元测试的命令")
    ut_parser.set_defaults(func=cangjie_ut)
    sub_ut_parser = ut_parser.add_mutually_exclusive_group()
    sub_sub_ut_parser = sub_ut_parser.add_mutually_exclusive_group()
    sub_sub_ut_parser.add_argument("--no-run", action='store_true', help='用来仅编译单元测试产物')
    sub_sub_ut_parser.add_argument("--skip-build", action='store_true', help='用来仅执行单元测试产物')
    sub_sub_ut_parser.add_argument("-j", '--jobs', type=int,
                                   help='<N> 用来指定并行编译的最大并发数，最终的最大并发数取 N 和 2倍 CPU 核数 的最小值')
    sub_sub_ut_parser.add_argument("-V", '--verbose', action='store_true', help='配置项开启后，会输出单元测试的日志')
    sub_sub_ut_parser.add_argument("-g", action='store_true', help='用来生成 debug 版本的单元测试产物')
    sub_sub_ut_parser.add_argument("--bench", action='store_true', help='用来指定只执行 @bench 宏修饰用例的测试结果')
    sub_sub_ut_parser.add_argument("--coverage", action='store_true',
                                   help='配合 cjcov 命令可以生成单元测试的覆盖率报告。使用 cjpm test --coverage 统计覆盖率时，源代码中的 main 不会再作为程序入口执行，因此会显示为未被覆盖。建议使用 cjpm test 之后，不再手写多余的 main')
    sub_sub_ut_parser.add_argument("--condition", type=str, help='指定后，可按条件透传 module.json/cjpm.toml 中的命令')
    sub_sub_ut_parser.add_argument("--target", type=str,
                                   help='指定后，可交叉编译生成目标平台的单元测试结果，module.json/cjpm.toml 中的配置可参考')
    sub_sub_ut_parser.add_argument("--filter", type=str,
                                   help='<value> 用于过滤测试的子集，value 的形式如下所示：--filter=* 匹配所有测试类  --filter=*.* 匹配所有测试类所有测试用例(结果和*相同)  --filter=*.*Test,*.*case* 匹配所有测试类中以 Test 结尾的用例，或者所有测试类中名字中带有 case 的测试用例'
                                        '--filter=MyTest*.*Test,*.*case*,-*.*myTest 匹配所有 MyTest 开头测试类中以 Test 结尾的用例，或者名字中带有 case 的用例，或者名字中不带有 myTest 的测试用例')
    sub_sub_ut_parser.add_argument("--random-seed", type=int, help='<N> 用来指定随机种子的值')
    sub_sub_ut_parser.add_argument("--no-color", action='store_true', help=' 关闭控制台颜色显示')
    sub_sub_ut_parser.add_argument("--isolate-all", action='store_true',
                                   help='对所有用例进行独立测试（即单个用例单进程测试，详见标准库手册中的选项说明）')
    sub_sub_ut_parser.add_argument("--isolate-all-timeout", type=str,
                                   help='用于对所有用例进行独立测试（即单个用例单进程测试）并指定超时时间')
    sub_ut_parser.add_argument("-m", "--merage", action='store_true',
                               help='该命令不能其他命令共存, 用于把UT测试用例和源码进行合并, 并把源码进行备份')
    sub_ut_parser.add_argument("-b", "--back", action='store_true',
                               help='该命令不能其他命令共存, 和-m命令互为相反命令, 把UT测试用例和源码进行分开, 首先要保证test/UT中存在该测试用例')

    cjlint_parser = sub_parser.add_parser("cjlint", help="cjlint检查工具命令")
    cjlint_parser.set_defaults(func=cjlint_check)
    cjlint_parser.add_argument('--json', action='store_true', help="cjlint检查是否是json配置文件方式")
    cjlint_parser.add_argument('--csv', action='store_true', help="cjlint检查是否是csv配置文件方式")
    cjlint_parser.add_argument("--cj-home", help="设置仓颉环境路径")

    doc_parser = sub_parser.add_parser("doc", help="生成api文档的命令, 需要依赖cangjie-code-api2md仓库编译的二进制包")
    doc_parser.set_defaults(func=cangjie_doc)
    doc_parser.add_argument("-s", "--source", help='需要扫描的源码路径')
    doc_parser.add_argument("-o", "--output", help='需要生成的文件名, 生成文件位于当前执行命令的文件夹')
    doc_parser.add_argument("-c", "--clear", type=bool, default=False, help='是否生成文件时清空文件内容')

    add_hlt_common_arguments(sub_parser.add_parser("cjtest", help="HLT用例测试方式"))
    add_hlt_common_arguments(sub_parser.add_parser("hlt", help="HLT用例测试方式"))

    # fuzz_test
    fuzz_parser = sub_parser.add_parser("fuzz", help="fuzz测试用例方式")
    fuzz_parser.set_defaults(func=fuzz_test)
    fuzz_parser.add_argument("--root")
    fuzz_parser.add_argument("--case")
    fuzz_parser.add_argument("--clean", action='store_true', help="是否清空测试临时目录")
    fuzz_parser.add_argument("-p", "--path")

    bench_parser = sub_parser.add_parser("bench", help="性能用例测试方式, 会寻找test/bench文件夹是否存在性能用例")
    bench_parser.set_defaults(func=bench_mark)
    bench_parser.add_argument("--root", help="")
    bench_parser.add_argument("--case", help="")
    bench_parser.add_argument("--clean", action='store_true', help="是否清空测试临时目录")
    bench_parser.add_argument("-p", "--path", help="")

    ## 计算 DT个数方法
    count_parser = sub_parser.add_parser("count", help="默认会统计LLT和HLT总计的用例数")
    count_parser.set_defaults(func=count)
    count_parser.add_argument("--HLT", action='store_true', help="计算HLT用例数")
    count_parser.add_argument("--LLT", action='store_true', help="计算LLT用例数")

    # 新增 perf 生成火焰图方式
    perf_parser = sub_parser.add_parser("perf", help="PERF Generate Flame Graph Method")
    perf_parser.set_defaults(func=perf_test)
    perf_parser.add_argument("--case")

    par = parser.parse_args()
    par.CANGJIE_CI_TEST_CFGS = cfgs
    # raise Exception
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    # download 命令不需要 cjc 环境，跳过检查
    if not (hasattr(par, 'func') and par.func.__name__ == 'download'):
        config_cjc(par)
    try:
        par.func(par)
    except KeyboardInterrupt:
        cfgs.LOG.info("用户中断程序异常退出.")
        exit(1)


def perf_test(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    cfgs.LOG.info("请注意生成火焰图时需要开启O2编译选项优化. ")


def bench_mark(args):
    set_args_default_attribute(args)
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.root:
        set_cjtest_path(args, cfgs, '3rd_party_root')
    if get_cjtest_path(args, cfgs, "") == "":
        cfgs.LOG.error("请设置 --root=${project} 再试.")
        exit(1)
    args.fuzz = args.target = None
    args.main = True
    args.optimize = '-O2'
    HLTtest(args, cfgs)


def count(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    count_func = 0
    if args.HLT or (not args.HLT and not args.LLT):
        llt = os.path.join(cfgs.HOME_DIR, "test", "LLT")
        for path, _, files in os.walk(llt):
            for f in files:
                if str(f).endswith('.cj'):
                    testcase = 0
                    with open(os.path.join(path, f), "r", encoding='UTF-8') as ff:
                        for line in ff.readlines():
                            if line.__contains__("@TestCase"):
                                testcase += 1
                    if testcase == 1:
                        count_func += 1
                    else:
                        count_func = count_func + 1 + testcase
    if args.LLT or (not args.HLT and not args.LLT):
        hlt = os.path.join(cfgs.HOME_DIR, "test", "HLT")
        for path, _, files in os.walk(hlt):
            for f in files:
                if str(f).endswith('.cj'):
                    testcase = 0
                    with open(os.path.join(path, f), "r", encoding='UTF-8') as ff:
                        for line in ff.readlines():
                            if line.__contains__("@TestCase"):
                                testcase += 1
                    count_func = count_func + testcase


def cjlint_check(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.json and args.csv:
        cfgs.LOG.error("生成文件方式只为一种.")
    if args.json:
        cjlint_run(args, cfgs, out_type='json')
    elif args.csv:
        cjlint_run(args, cfgs, out_type='csv')
    else:
        cjlint_run(args, cfgs)


def fuzz_test(args):
    set_args_default_attribute(args)
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.root:
        set_cjtest_path(args, cfgs, '3rd_party_root')
    if get_cjtest_path(args, cfgs, "") == "":
        cfgs.LOG.error("请设置 --root=${project} 再试.")
        exit(1)
    args.fuzz = True
    args.optimize = '-O0'
    HLTtest(args, cfgs)


def cjtest(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.coverage:
        args.optimize = '-O0 --coverage'
    elif args.O is None:
        args.optimize = ' -O0'
    elif args.O not in {"0", "1", "2", "s", "z"}:
        cfgs.LOG.error(f"没有此优化选项的值:{args.O}")
        args.optimize = ' -O0'
    else:
        args.optimize = f" -O{args.O}"
    if args.root:
        if args.target:
            set_cjtest_path(args, cfgs, '3rd_party_root_ohos')
        else:
            set_cjtest_path(args, cfgs, '3rd_party_root')
    if args.target:
        if get_cjtest_path(args, cfgs, args.target) == "":
            cfgs.LOG.warn("请配置ohos的3rd_party_root")
        else:
            _get_DEVECO_CANGJIE_HOME(cfgs)
    else:
        if get_cjtest_path(args, cfgs, "") == "":
            cfgs.LOG.warn("请配置3rd_party_root, ciTest.py cjtest --root=CangjieObjectParentPath.")
    args.HLT = False
    HLTtest(args, cfgs)


def print_version(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.cangjie:
        cfgs.LOG.info(f"#{cfgs.EXPECT_CJC_VERSION}")
    else:
        cfgs.LOG.info("Cangjie Koolib Ci Test Version: 0.60.5.1")


def cangjie_ut(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    UT_test(args, cfgs)


def main(cfgs):
    cfgs.LOG = init_log(cfgs, "ci_test")
    parse_args(cfgs)


def coverage(args):
    set_args_default_attribute(args)
    cfgs = args.CANGJIE_CI_TEST_CFGS
    # args.e = None
    args.coverage = True
    args.clean = False
    args.target = args.ohos_home = args.cj_home = args.case = args.ut = args.path = args.O2 = args.O1 = args.O0 = args.full = None
    if not args.html:
        build(args)
        if args.LLT:
            test(args)
        elif args.HLT:
            cjtest(args)
        else:
            test(args)
            args.HLT = True
            cjtest(args)
    build_coverage(args, cfgs)


def cangjie_doc(args):
    set_args_default_attribute(args)
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if platform.system() == 'Windows':
        cfgs.LOG.error("暂时不支持windows版本..")
        return
    if cfgs.BUILD_TYPE == "ci_test":
        dir = os.path.join(cfgs.FILE_ROOT, "cangjiedoc")
    else:
        dir = os.path.join(cfgs.BASE_DIR, "cangjiedoc")
    clear = 'false' if args.clear == False else "true"
    output = subprocess.Popen(
        '{} -s {} -o {} -c {}'.format(str(dir), args.source, args.output, clear), shell=True, stderr=subprocess.PIPE,
        stdout=subprocess.PIPE
    )
    log_output(output, output.args, cfgs)


def build(args):
    set_args_default_attribute(args)
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.log:
        cfgs.LOG = init_log(cfgs, "ci_test", log_file_path=args.log)
    if args.full:
        cfgs.LOG.info(f"清理build构建目录: {os.path.join(cfgs.HOME_DIR, cfgs.BUILD_BIN)}")
        shutil.rmtree(os.path.join(cfgs.HOME_DIR, cfgs.BUILD_BIN), ignore_errors=True)
    runBuild(args, cfgs)


def download(args):
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.owner is None:
        args.owner = 'Cangjie-TPC'
    if args.repo is None:
        args.repo = 'test4tpc'
    if args.bench is None:
        # 默认使用当前项目目录名作为分支名
        args.bench = cfgs.MODULE_NAME if cfgs.MODULE_NAME else os.path.basename(cfgs.HOME_DIR)
    if args.depth is None:
        args.depth = 1
    if args.username is None and args.password is None:
        args.username = cfgs.GIT_USERNAME = get_config_value(cfgs.BUILD_CI_TEST_CFG, "git-config", "username")
        args.password = cfgs.GIT_PASSWORD = get_config_value(cfgs.BUILD_CI_TEST_CFG, "git-config", "password")
    bigtest = ["compress4cj", "qrcode4cj", "mp3tag4cj", "lottie4cj", "videocompress4cj"]
    if args.bench in bigtest:
        args.repo = 'test4bigtpc'
    cfgs.LOG.info(f"owner={args.owner}, repo={args.repo}, bench={args.bench}, bench={args.bench}, depth={args.depth}")

    if args.dir_name is None:
        args.dir_name = 'test'
    target_dir = os.path.join(cfgs.HOME_DIR, args.dir_name)
    if not args.force and os.path.exists(target_dir):
        cfgs.LOG.info(f"test目录已存在：{target_dir}")
        return

    # 1. 构建Git仓库HTTPS URL（处理账号密码+特殊字符编码）
    git_host = "gitcode.com"  # 可根据实际场景改为GitLab/Gitee等，或抽成配置
    # 对用户名/密码做URL编码（避免特殊字符如@、&导致URL错误）
    encoded_username = urllib.parse.quote(args.username or "", safe="")
    encoded_password = urllib.parse.quote(args.password or "", safe="")

    if encoded_username and encoded_password:
        # 带账号密码的HTTPS URL
        repo_url = f"https://{encoded_username}:{encoded_password}@{git_host}/{args.owner}/{args.repo}.git"
    else:
        # 无账号密码（依赖SSH/匿名访问）
        repo_url = f"https://{git_host}/{args.owner}/{args.repo}.git"
        cfgs.LOG.warning("未配置Git账号密码，将尝试匿名/SSH方式克隆仓库")

    # 2. 确定克隆目标目录
    clone_target_dir = str(uuid.uuid4()).replace('-', '')
    cfgs.LOG.info(f"开始克隆仓库：{os.path.abspath(clone_target_dir)}")

    # 3. 检查目标目录是否已存在
    if os.path.exists(clone_target_dir):
        cfgs.LOG.warning(f"目标目录 {clone_target_dir} 已存在，将删除后重新克隆（CI场景建议清理）")
        # CI环境下直接删除目录（可根据需求改为报错/跳过）
        try:
            if sys.platform == "win32":
                subprocess.run(f"rmdir /s /q {clone_target_dir}", shell=True, check=True)
            else:
                subprocess.run(f"rm -rf {clone_target_dir}", shell=True, check=True)
        except subprocess.CalledProcessError as e:
            cfgs.LOG.error(f"删除已有目录失败：{e}")
            raise

    # 4. 构建git clone命令（浅克隆+指定目录）
    clone_cmd = [
        "git", "clone", "--depth", str(args.depth), repo_url, '-b', args.bench, clone_target_dir  # 克隆到指定目录
    ]
    cfgs.LOG.info(f"执行Git命令：{' '.join(clone_cmd).replace(args.password, '****')}")

    # 5. 执行克隆命令（捕获异常并记录日志）
    clone_success = False
    clone_error_msg = ""

    # 第一次尝试：使用 args.bench 分支
    source_repo = os.path.basename(cfgs.HOME_DIR)
    try:
        cfgs.LOG.info(f"尝试克隆分支: {args.bench}")
        result = subprocess.run(
            clone_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True
        )
        clone_success = True
        cfgs.LOG.info(f"仓库克隆成功！分支: {args.bench}")
        cfgs.LOG.info(f"克隆输出：{result.stdout.strip()}")

    except subprocess.CalledProcessError as e:
        clone_error_msg = f"克隆分支 {args.bench} 失败！返回码：{e.returncode}，错误：{e.stderr.strip()}"
        cfgs.LOG.warning(clone_error_msg)

        # 第二次尝试：使用 args.repo 作为分支名

        if source_repo and source_repo != args.bench:
            cfgs.LOG.info(f"尝试使用仓库名作为分支重试: {source_repo}")

            # 清理第一次失败的目录
            if os.path.exists(clone_target_dir):
                try:
                    if sys.platform == "win32":
                        subprocess.run(f"rmdir /s /q {clone_target_dir}", shell=True, check=True)
                    else:
                        subprocess.run(f"rm -rf {clone_target_dir}", shell=True, check=True)
                    cfgs.LOG.info(f"已清理失败的克隆目录: {clone_target_dir}")
                except Exception as clean_err:
                    cfgs.LOG.warning(f"清理目录失败: {str(clean_err)}")

            # 构建新的 clone 命令
            retry_clone_cmd = [
                "git", "clone", "--depth", str(args.depth), repo_url, '-b', source_repo, clone_target_dir
            ]
            cfgs.LOG.info(f"重试Git命令：{' '.join(retry_clone_cmd).replace(args.password, '****')}")

            try:
                result = subprocess.run(
                    retry_clone_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    check=True
                )
                clone_success = True
                cfgs.LOG.info(f"重试成功！使用分支: {source_repo}")
                cfgs.LOG.info(f"克隆输出：{result.stdout.strip()}")

            except subprocess.CalledProcessError as retry_e:
                retry_error_msg = f"重试克隆分支 {source_repo} 也失败！返回码：{retry_e.returncode}，错误：{retry_e.stderr.strip()}"
                cfgs.LOG.error(retry_error_msg)
                clone_error_msg = f"{clone_error_msg}\n{retry_error_msg}"

    except Exception as e:
        clone_error_msg = f"克隆仓库时发生未知错误：{str(e)}"
        cfgs.LOG.error(clone_error_msg)

    # 检查克隆是否成功
    if not clone_success:
        error_msg = f"仓库克隆失败！尝试了分支 '{args.bench}' 和 '{source_repo}' 都失败。\n{clone_error_msg}"
        cfgs.LOG.error(error_msg)

        # 检查本地是否已存在测试文件
        local_test_dir = os.path.join(cfgs.HOME_DIR, 'test')
        hlt_dir = os.path.join(local_test_dir, 'HLT')
        llt_dir = os.path.join(local_test_dir, 'LLT')

        has_cj_files = False
        cj_file_count = 0

        # 检查 HLT 目录中的 .cj 文件
        if os.path.exists(hlt_dir):
            for root, dirs, files in os.walk(hlt_dir):
                for f in files:
                    if f.endswith('.cj'):
                        has_cj_files = True
                        cj_file_count += 1

        # 检查 LLT 目录中的 .cj 文件
        if os.path.exists(llt_dir):
            for root, dirs, files in os.walk(llt_dir):
                for f in files:
                    if f.endswith('.cj'):
                        has_cj_files = True
                        cj_file_count += 1

        if has_cj_files:
            cfgs.LOG.warning(f"克隆失败，但本地已存在测试文件，跳过下载。")
            cfgs.LOG.info(f"本地 test/HLT 和 test/LLT 目录中共有 {cj_file_count} 个 .cj 测试文件")
            cfgs.LOG.info(f"HLT 目录: {hlt_dir} (存在: {os.path.exists(hlt_dir)})")
            cfgs.LOG.info(f"LLT 目录: {llt_dir} (存在: {os.path.exists(llt_dir)})")
            return 0
        else:
            raise Exception(error_msg)

    # 打印克隆后的目录列表
    try:
        dir_list = os.listdir(clone_target_dir)
        cfgs.LOG.info(f"克隆目录 {clone_target_dir} 内容列表：")
        for item in dir_list:
            item_path = os.path.join(clone_target_dir, item)
            item_type = "[DIR]" if os.path.isdir(item_path) else "[FILE]"
            cfgs.LOG.info(f"  {item_type}: {item}")
    except Exception as list_err:
        cfgs.LOG.warning(f"读取克隆目录列表失败：{str(list_err)}")
    # 5 文件夹 check

    if os.path.exists(target_dir):
        if args.force:
            cfgs.LOG.info('正在删除test文件夹')
            # shutil.rmtree(os.path.join(cfgs.HOME_DIR, target_dir), ignore_errors=True)
        else:
            cfgs.LOG.error(f'{target_dir}文件夹已经存在.')
            raise Exception(f'{target_dir}文件夹已经存在.')
    possible_dirs = ['test', 'LLT', 'HLT']
    existing_dirs = []
    clone_base_dir = os.path.join(cfgs.HOME_DIR, clone_target_dir)

    def find_test_dirs(base_dir, target_names, max_depth=2, current_depth=1):
        """递归查找测试目录"""
        found_dirs = []
        if current_depth > max_depth:
            return found_dirs

        try:
            for item in os.listdir(base_dir):
                item_path = os.path.join(base_dir, item)
                if os.path.isdir(item_path):
                    if item in target_names:
                        found_dirs.append((item, item_path))
                        cfgs.LOG.info(f"找到源文件夹（深度{current_depth}）：{item_path}")
                    # 无论是否找到目标目录，都继续递归查找子目录
                    sub_found = find_test_dirs(item_path, target_names, max_depth, current_depth + 1)
                    found_dirs.extend(sub_found)
        except Exception as e:
            cfgs.LOG.warning(f"遍历目录 {base_dir} 失败：{str(e)}")

        return found_dirs

    existing_dirs = find_test_dirs(clone_base_dir, possible_dirs, max_depth=2)

    if not existing_dirs:
        raise FileNotFoundError(f"源文件夹不存在：{clone_target_dir} 下未找到 test/LLT/HLT 目录（递归深度2）")

    # 过滤结果：优先保留更深层次的匹配目录
    # 如果某个目录的子目录也在匹配列表中，则跳过该目录（避免重复路径）
    # 构建目录路径集合，用于检查子目录
    source_dir_paths = {source_dir for dir_name, source_dir in existing_dirs}

    filtered_dirs = []
    for dir_name, source_dir in existing_dirs:
        # 检查是否有子目录在匹配列表中
        has_child_in_list = False
        for other_name, other_path in existing_dirs:
            # 如果 other_path 是 source_dir 的子目录
            if other_path.startswith(source_dir + os.sep) and other_path != source_dir:
                has_child_in_list = True
                cfgs.LOG.info(f"跳过目录 {dir_name}（子目录 {other_name} 已在目标列表中）")
                break

        if not has_child_in_list:
            filtered_dirs.append((dir_name, source_dir))

    existing_dirs = filtered_dirs

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        cfgs.LOG.info(f"创建目标目录：{target_dir}")

    for dir_name, source_dir in existing_dirs:
        dest_dir = os.path.join(target_dir, dir_name)
        if os.path.exists(dest_dir):
            cfgs.LOG.info(f"目标目录已存在，合并内容：{dest_dir}")
            for item in os.listdir(source_dir):
                src_item = os.path.join(source_dir, item)
                dst_item = os.path.join(dest_dir, item)
                if os.path.exists(dst_item):
                    if os.path.isdir(dst_item):
                        shutil.rmtree(dst_item, ignore_errors=True)
                    else:
                        os.remove(dst_item)
                shutil.move(str(src_item), str(dst_item))
                cfgs.LOG.info(f"移动 {src_item} -> {dst_item}")
        else:
            cfgs.LOG.info(f"移动 {source_dir} -> {dest_dir}")
            shutil.move(str(source_dir), str(dest_dir))

        # 检查同级是否有 resources 文件夹，如果有也复制
        parent_dir = os.path.dirname(source_dir)
        resources_dir = os.path.join(parent_dir, "resources")
        if os.path.exists(resources_dir) and os.path.isdir(resources_dir):
            resources_dest = os.path.join(target_dir, "resources")
            if os.path.exists(resources_dest):
                cfgs.LOG.info(f"目标 resources 目录已存在，合并内容：{resources_dest}")
                for item in os.listdir(resources_dir):
                    src_item = os.path.join(resources_dir, item)
                    dst_item = os.path.join(resources_dest, item)
                    if os.path.exists(dst_item):
                        if os.path.isdir(dst_item):
                            shutil.rmtree(dst_item, ignore_errors=True)
                        else:
                            os.remove(dst_item)
                    shutil.move(str(src_item), str(dst_item))
                    cfgs.LOG.info(f"移动 {src_item} -> {dst_item}")
            else:
                cfgs.LOG.info(f"移动 {resources_dir} -> {resources_dest}")
                shutil.move(str(resources_dir), str(resources_dest))

        # 检查同级是否有 LLT 文件夹，如果有且不在 existing_dirs 中，也复制
        llt_dir = os.path.join(parent_dir, "LLT")
        if os.path.exists(llt_dir) and os.path.isdir(llt_dir):
            # 检查 LLT 是否已经在 existing_dirs 中
            llt_in_existing = any(src == llt_dir for _, src in existing_dirs)
            if not llt_in_existing:
                llt_dest = os.path.join(target_dir, "LLT")
                if os.path.exists(llt_dest):
                    cfgs.LOG.info(f"目标 LLT 目录已存在，合并内容：{llt_dest}")
                    for item in os.listdir(llt_dir):
                        src_item = os.path.join(llt_dir, item)
                        dst_item = os.path.join(llt_dest, item)
                        if os.path.exists(dst_item):
                            if os.path.isdir(dst_item):
                                shutil.rmtree(dst_item, ignore_errors=True)
                            else:
                                os.remove(dst_item)
                        shutil.move(str(src_item), str(dst_item))
                        cfgs.LOG.info(f"移动 {src_item} -> {dst_item}")
                else:
                    cfgs.LOG.info(f"移动 {llt_dir} -> {llt_dest}")
                    shutil.move(str(llt_dir), str(llt_dest))

    cfgs.LOG.info(f"清理临时：{os.path.join(cfgs.HOME_DIR, clone_target_dir)}")
    delete_path = str(os.path.join(cfgs.HOME_DIR, clone_target_dir))
    shutil.rmtree(delete_path, ignore_errors=True)
    if not os.path.exists(delete_path):
        cfgs.LOG.info(f"临时目录删除成功：{delete_path}")
    elif cfgs.OS_PLATFORM == 'windows':
        cmd = f'rmdir /s /q "{delete_path}"'  # 去掉长路径前缀
        cfgs.LOG.warning(f"Python内置删除失败，尝试系统命令{cmd}删除...")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            cfgs.LOG.info(f"系统命令删除成功：{delete_path}")
        else:
            cfgs.LOG.error(f"系统命令删除失败：{result.stderr}")
    else:
        cfgs.LOG.warn(f"系统命令删除失败：{result.stderr}, 请手动删除临时目录")
    cfgs.LOG.info(f"文件夹已移动到：{target_dir}")


def test(args):
    set_args_default_attribute(args)
    cfgs = args.CANGJIE_CI_TEST_CFGS
    if args.log:
        cfgs.LOG = init_log(cfgs, "ci_test", log_file_path=args.log)
    if args.path and args.case:
        cfgs.LOG.error("单跑用例和单跑指定文件夹, 不能同时设置. ")
        exit(1)
    elif args.path:
        if os.path.exists(args.path):
            if not os.path.isabs(args.path):
                args.path = os.path.join(cfgs.HOME_DIR, args.path)
        else:
            cfgs.LOG.error("指定文件夹不存在. 请重试")
            exit(1)
    # clean(cfgs)
    if args.coverage:
        args.optimize = ' -O0 --coverage'
    elif args.O is None:
        args.optimize = ' -O0'
    elif args.O not in {"0", "1", "2", "s", "z"}:
        cfgs.LOG.error(f"没有此优化选项的值:{args.O}")
        args.optimize = ' -O0'
    else:
        args.optimize = f" -O{args.O}"
    cfgs.Woff = ""
    try:
        version = os.popen('cjc -v').readline().split('Cangjie Compiler: ')[1].split(' (')[0]
        vs = version.split('.')
        if float(vs[0]) > 0 or float(vs[1]) >= 39.7:
            cfgs.Woff = " -Woff all"
    except:
        pass
    __find_cjpm_home_librarys(args, cfgs)

    def signal_handler(sig, frame):
        cfgs.LOG.info("\n收到中断信号，正在生成测试报告...")
        try:
            end_build(cfgs)
        except Exception as e:
            cfgs.LOG.error(f"生成报告时出错: {e}")
        exit(1)

    signal.signal(signal.SIGINT, signal_handler)

    runAll(args, cfgs)
    end_build(cfgs)


def __find_cjpm_home_librarys(args, cfgs):
    try:
        parms = parse(open(os.path.join(cfgs.HOME_DIR, "cjpm.lock"), "r", encoding='UTF-8').read())
        sss = ''
        for key, value in parms['requires'].items():
            for k, v in value.items():
                if k == 'commitId':
                    str_lib = __get_cjpm_library_cjpm_lock_foreign_requires_path(cfgs,
                                                                                 os.path.join(cfgs.BUILD_CJPM_PATH, key,
                                                                                              v))
                    sub_lib = os.path.join(cfgs.BUILD_CJPM_PATH, key, v, str_lib)
                    if os.path.exists(sub_lib):
                        sss = f' -L {sub_lib} '
                        __set_up_the_link_lib(cfgs, sub_lib)
                        sss += __improt_libs([sub_lib], cfgs)
                        if cfgs.OS_PLATFORM == "windows":
                            __get_windows_c_lib_arr(cfgs, sub_lib)
        str_lib = __get_cjpm_library_cjpm_lock_foreign_requires_path(cfgs, cfgs.HOME_DIR)
        sub_lib = os.path.join(cfgs.HOME_DIR, str_lib)
        if os.path.exists(sub_lib):
            sss = f' -L {sub_lib} '
            __set_up_the_link_lib(cfgs, sub_lib)
            sss += __improt_libs([sub_lib], cfgs)
            if cfgs.OS_PLATFORM == "windows":
                __get_windows_c_lib_arr(cfgs, sub_lib)
        cfgs.IMPORT_PATH += sss
    except Exception as e:
        cfgs.LOG.error(f"函数__find_cjpm_home_librarys，遇到异常错误 {e}")


def __get_windows_c_lib_arr(cfgs, sub_lib):
    for path, _, libs in os.walk(sub_lib):
        for lib in libs:
            if lib.startswith("lib") and lib.endswith(".dll"):
                cfgs.WINDOWS_C_LIB_ARR.add(os.path.join(path, lib))


def __get_cjpm_library_cjpm_lock_foreign_requires_path(cfgs, that_lib_path):
    if cfgs.BUILD_BIN != "build" and os.path.exists(os.path.join(that_lib_path, "cjpm.toml")):
        parm = parse(open(os.path.join(that_lib_path, "cjpm.toml"), "r", encoding='UTF-8').read())
        if parm.get('ffi') is not None:
            for key, value in parm['ffi'].items():
                if key == "c":
                    for ke, val in value.items():
                        for k, v in val.items():
                            if k == 'path':
                                return str(v).replace('./', '').replace('/', os.path.sep)
    else:
        # json
        parm = json.load(open(os.path.join(that_lib_path, "module.json"), 'r', encoding='UTF-8'))
        for ke, val in parm["foreign_requires"].items():
            for k, v in val.items():
                if k == 'path':
                    return str(v).replace('./', '').replace('/', os.path.sep)
    return 'lib'


# 下载stdx 文件
def download_large_with_urllib(url, save_path, chunk_size=1024 * 1024):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            # 获取文件总大小
            total_size = int(response.headers.get('Content-Length', 0))

            with open(save_path, 'wb') as f:
                downloaded_size = 0
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break  # 下载完成
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    # 显示进度
                    progress = (downloaded_size / total_size) * 100 if total_size > 0 else 0
                    print(f"\r下载进度：{downloaded_size}/{total_size} bytes ({progress:.2f}%)", end="")

        print(f"\n下载成功！文件保存到：{save_path}")
    except urllib.error.URLError as e:
        print(f"\n下载失败：{e}")


# 解压stdx 文件
def unzip_file(zip_path, extract_dir):
    """
    解压 ZIP 文件（无密码）
    :param zip_path: ZIP 文件路径
    :param extract_dir: 解压目标目录
    """
    try:
        # 确保目标目录存在
        os.makedirs(extract_dir, exist_ok=True)

        # 打开 ZIP 文件
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # 解压所有文件到目标目录
            zip_ref.extractall(extract_dir)
            print(f"解压成功！文件已提取到：{extract_dir}")
    except zipfile.BadZipFile:
        print(f"解压失败：{zip_path} 不是有效的 ZIP 文件")
    except Exception as e:
        print(f"解压失败：{e}")


def download_stdx_with_new_tool(cfgs, target_dir):
    """
    使用 get_stdx_release.py 的工具下载 stdx
    """
    try:
        cfgs.LOG.info("使用新工具下载 stdx...")

        cjc_info = get_cjc_version_from_command(cfgs.LOG)
        if not cjc_info:
            cfgs.LOG.error("无法获取 cjc 版本信息")
            return False

        cfgs.LOG.info(f"检测到 cjc 版本: {cjc_info.get('version', 'unknown')}")
        cfgs.LOG.info(f"目标平台: {cjc_info.get('target', 'unknown')}")

        possible_urls = generate_possible_urls(cjc_info, 'Cangjie', 'cangjie_stdx')
        if not possible_urls:
            cfgs.LOG.error("无法生成可能的 stdx URL")
            return False

        cfgs.LOG.info(f"生成了 {len(possible_urls)} 个可能的 URL，正在检查有效性...")

        valid_urls = find_valid_urls(possible_urls, max_workers=10, logger=cfgs.LOG)

        if not valid_urls:
            cfgs.LOG.error("未找到有效的 stdx 包")
            cfgs.LOG.info("提示: 请访问 https://gitcode.com/Cangjie/cangjie_stdx/releases 手动下载")
            return False

        cfgs.LOG.info(f"找到 {len(valid_urls)} 个有效的 stdx 包")

        closest_packages = find_closest_version(valid_urls, cjc_info.get('version', ''))

        pkg = closest_packages[0]
        cfgs.LOG.info(f"选择下载: {pkg['name']} (标签: {pkg['tag']})")

        success = download_and_extract(pkg['url'], target_dir, logger=cfgs.LOG)

        if success:
            cfgs.LOG.info(f"stdx 下载并解压成功到: {target_dir}")
            return True
        else:
            cfgs.LOG.error("stdx 下载失败")
            return False

    except Exception as e:
        cfgs.LOG.error(f"下载 stdx 时发生错误: {e}")
        return False


def get_base_version(output: str) -> str:
    pattern = r'(?<=Cangjie Compiler: )\d+\.\d+\.\d+'
    match = re.search(pattern, output)
    if match:
        version = match.group()
        return version
    else:
        print("未找到匹配的版本号")


def get_expected_cjc_version(cfgs):
    """
    获取期望的 cjc 版本（优先级：README.md > cjpm.toml > module.json）

    README.md 中的版本是最新版本，cjpm.toml/module.json 中的版本是最低兼容版本

    Args:
        cfgs: 配置对象

    Returns:
        str: 期望的 cjc 版本号，如果未找到返回 None
    """
    # 优先从 README 文件获取最新版本
    from config import find_readme_file, get_cjc_version_from_readme
    readme_path = find_readme_file(cfgs.HOME_DIR)
    if readme_path:
        readme_version = get_cjc_version_from_readme(readme_path)
        if readme_version:
            cfgs.LOG.info(f"从 {os.path.basename(readme_path)} 获取到 cjc 最新版本: {readme_version}")
            return readme_version

    # 如果 README 文件中没有找到版本，则从配置文件获取最低兼容版本
    cfg = os.path.join(cfgs.HOME_DIR, cfgs.CONFIG_FILE)
    try:
        if str(cfgs.CONFIG_FILE).endswith('.toml'):
            parm = parse(open(cfg, "r", encoding='UTF-8').read())
            if parm.get('package') is not None:
                for key, value in parm['package'].items():
                    if key == "cjc-version":
                        cfgs.LOG.info(f"从 cjpm.toml 获取到 cjc 最低兼容版本: {value}")
                        return value
        else:
            parm = json.load(open(cfg, 'r', encoding='UTF-8'))
            if 'cjc_version' in parm:
                cfgs.LOG.info(f"从 module.json 获取到 cjc 最低兼容版本: {parm['cjc_version']}")
                return parm['cjc_version']
    except:
        cfgs.LOG.warn("本项目没有配置文件 module.json 或 cjpm.toml")

    cfgs.LOG.warn("无法获取 cjc 版本信息")

    return None


def check_system_cjc(cfgs):
    """
    检查系统中是否有 cjc 环境
    支持多种检测方式：
    1. shutil.which("cjc")
    2. 直接执行 cjc -v 命令
    3. 检查 CANGJIE_ROOT 环境变量
    4. 检查常见安装路径

    Args:
        cfgs: 配置对象

    Returns:
        tuple: (是否有 cjc, cjc 版本号)
    """
    # 方法1: 使用 shutil.which 检查
    master_cjc = shutil.which("cjc")
    if master_cjc:
        try:
            output = os.popen('{} -v'.format(master_cjc)).read()
            version = get_base_version(output)
            cfgs.LOG.info(f"通过 which 找到 cjc: {master_cjc} (版本: {version})")
            return True, version
        except:
            cfgs.LOG.warn(f"找到 cjc 但无法获取版本: {master_cjc}")
            return True, None

    # 方法2: 直接尝试执行 cjc -v（可能 cjc 在 PATH 中但 which 找不到）
    try:
        output = os.popen('cjc -v').read()
        if output and 'Cangjie Compiler' in output:
            version = get_base_version(output)
            cfgs.LOG.info(f"通过直接执行找到 cjc (版本: {version})")
            return True, version
    except:
        pass

    # 方法3: 检查 CANGJIE_ROOT 环境变量
    cangjie_root = os.environ.get('CANGJIE_ROOT')
    if cangjie_root:
        cjc_path = os.path.join(cangjie_root, 'bin', 'cjc')
        if os.path.exists(cjc_path):
            try:
                output = os.popen('{} -v'.format(cjc_path)).read()
                version = get_base_version(output)
                cfgs.LOG.info(f"通过 CANGJIE_ROOT 找到 cjc: {cjc_path} (版本: {version})")
                return True, version
            except:
                cfgs.LOG.warn(f"通过 CANGJIE_ROOT 找到 cjc 但无法获取版本: {cjc_path}")
                return True, None

    # 方法4: 检查常见安装路径
    common_paths = [
        '/usr/local/bin/cjc',
        '/usr/bin/cjc',
        os.path.join(os.environ.get('HOME', ''), '.cangjie', 'bin', 'cjc'),
    ]

    for cjc_path in common_paths:
        if os.path.exists(cjc_path):
            try:
                output = os.popen('{} -v'.format(cjc_path)).read()
                if output and 'Cangjie Compiler' in output:
                    version = get_base_version(output)
                    cfgs.LOG.info(f"通过常见路径找到 cjc: {cjc_path} (版本: {version})")
                    return True, version
            except:
                continue

    cfgs.LOG.info("系统中未找到 cjc 环境")
    return False, None


def setup_stdx_if_needed(args, cfgs):
    """
    检查并设置 stdx 环境（如果需要）

    Args:
        args: 命令行参数
        cfgs: 配置对象
    """
    if compare_versions(cfgs.BASE_CJC_VERSION, '0.61.0') >= 0:
        if not cfgs.CANGJIE_STDX_DIR:
            cfgs.LOG.info(f"仓颉版本:{cfgs.BASE_CJC_VERSION}, 需要检查stdx是否配置")
            if cfgs.WHICH_CJC:
                cfgs.IS_SELF_CONFIGURATION = True
                cfgs.LOG.info("当前环境变量已经设置仓颉, 检查CANGJIE_HOME中是否存在stdx")
            else:
                cfgs.WHICH_CJC = shutil.which("cjc")
                cfgs.LOG.info("未配置仓颉环境, 正在配置指定的路径的stdx")

            # 获取 cjc 版本信息（如果还没有）
            if cfgs.WHICH_CJC_OUT_STR is None:
                cfgs.WHICH_CJC_OUT_STR = "".join(os.popen('{} -v'.format(shutil.which("cjc"))).readlines())
                cfgs.LOG.info(cfgs.WHICH_CJC_OUT_STR)

            if cfgs.CANGJIE_TARGET is None:
                cfgs.CANGJIE_TARGET = cfgs.WHICH_CJC_OUT_STR.split('Target: ')[1].replace('\n', '').replace('\r', '')

            # 确定 stdx 目录名称
            if cfgs.CANGJIE_TARGET == "aarch64-linux-ohos":
                targ = f"linux_ohos_aarch64_{cfgs.CJC_RUNTIME_SUFFIX}"
            elif cfgs.CANGJIE_TARGET == "x86_64-linux-ohos":
                targ = f"linux_ohos_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}"
            elif cfgs.CANGJIE_TARGET == "x86_64-unknown-linux-gnu":
                targ = f"linux_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}"
            elif "darwin" in cfgs.CANGJIE_TARGET or "apple" in cfgs.CANGJIE_TARGET:
                if "aarch64" in cfgs.CANGJIE_TARGET or "arm64" in cfgs.CANGJIE_TARGET:
                    targ = f"darwin_arm64_{cfgs.CJC_RUNTIME_SUFFIX}"
                else:
                    targ = f"darwin_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}"
            elif "windows" in cfgs.CANGJIE_TARGET:
                targ = f"windows_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}"
            elif "mingw32" in cfgs.CANGJIE_TARGET:
                targ = f"windows_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}"
            elif "aarch64" in cfgs.CANGJIE_TARGET:
                targ = f"linux_aarch64_{cfgs.CJC_RUNTIME_SUFFIX}"
            else:
                targ = "stdx"

            if 'llvm' in targ:
                targ_2 = targ.replace('llvm', "cjnative")
            else:
                targ_2 = targ.replace("cjnative", 'llvm')

            # 检查 stdx 目录是否存在
            stdx_base_path = Path(cfgs.WHICH_CJC).parent.parent
            if not os.path.exists(os.path.join(stdx_base_path, targ)) or not os.path.exists(
                    os.path.join(stdx_base_path, targ_2)):
                if hasattr(args, 'update_stdx') and args.update_stdx:
                    cfgs.LOG.info("stdx文件夹不存在, 正在下载stdx...")

                    if not cfgs.IS_SELF_CONFIGURATION:
                        stdx_target_dir = os.path.join(cfgs.cj_home, cfgs.BASE_CJC_VERSION)
                    else:
                        stdx_target_dir = cfgs.CANGJIE_HOME

                    download_success = download_stdx_with_new_tool(cfgs, stdx_target_dir)

                    if download_success:
                        if os.path.exists(os.path.join(stdx_target_dir, targ)):
                            if not os.path.exists(os.path.join(stdx_target_dir, targ_2)):
                                shutil.copytree(os.path.join(stdx_target_dir, targ),
                                                os.path.join(stdx_target_dir, targ_2))
                        elif os.path.exists(os.path.join(stdx_target_dir, targ_2)):
                            if not os.path.exists(os.path.join(stdx_target_dir, targ)):
                                shutil.copytree(os.path.join(stdx_target_dir, targ_2),
                                                os.path.join(stdx_target_dir, targ))
                        else:
                            cfgs.LOG.warn(f"下载的 stdx 包中未找到预期的目录: {targ} 或 {targ_2}")
                    else:
                        cfgs.LOG.warn("stdx 下载失败，尝试使用备用方法...")
                        cfgs.LOG.info("备用下载链接: " + cfgs.get_stdx_url())
                        if not cfgs.IS_SELF_CONFIGURATION:
                            stdx_zip_path = os.path.join(cfgs.cj_home, cfgs.BASE_CJC_VERSION, 'stdx.zip')
                            stdx_zip_path_dirname = os.path.join(cfgs.cj_home, cfgs.BASE_CJC_VERSION)
                        else:
                            stdx_zip_path = os.path.join(cfgs.CANGJIE_HOME, 'stdx.zip')
                            stdx_zip_path_dirname = cfgs.CANGJIE_HOME
                        if not os.path.exists(stdx_zip_path):
                            download_large_with_urllib(cfgs.get_stdx_url(), stdx_zip_path)
                        if os.path.exists(stdx_zip_path):
                            unzip_file(stdx_zip_path, stdx_zip_path_dirname)
                            if os.path.exists(os.path.join(stdx_zip_path_dirname, targ)):
                                shutil.copytree(os.path.join(stdx_zip_path_dirname, targ),
                                                os.path.join(stdx_zip_path_dirname, targ_2))
                            else:
                                shutil.copytree(os.path.join(stdx_zip_path_dirname, targ_2),
                                                os.path.join(stdx_zip_path_dirname, targ))
                        else:
                            cfgs.LOG.warn(f"stdx.zip不存在, 下载失败: {stdx_zip_path}")
                            exit(1)
                if not os.path.exists(os.path.join(stdx_base_path, targ)):
                    cfgs.LOG.warn("stdx路径不存在: " + os.path.join(stdx_base_path, targ))

            # 设置 CANGJIE_STDX_DIR
            if os.path.exists(os.path.join(stdx_base_path, targ)):
                cfgs.CANGJIE_STDX_DIR = os.path.join(stdx_base_path, targ, "dynamic", "stdx")
                if not os.path.exists(os.path.join(stdx_base_path, targ_2)):
                    shutil.copytree(os.path.join(stdx_base_path, targ),
                                    os.path.join(stdx_base_path, targ_2))
            elif os.path.exists(os.path.join(stdx_base_path, targ_2)):
                cfgs.CANGJIE_STDX_DIR = os.path.join(stdx_base_path, targ_2, "dynamic", "stdx")
                if not os.path.exists(os.path.join(stdx_base_path, targ)):
                    shutil.copytree(os.path.join(stdx_base_path, targ_2),
                                    os.path.join(stdx_base_path, targ))
            else:
                cfgs.LOG.warn("No STDX directory found.")

            if cfgs.CANGJIE_STDX_DIR:
                __set_cangjie_stdx_home(cfgs, cfgs.CANGJIE_STDX_DIR)
        else:
            cfgs.LOG.info(f"CANGJIE_STDX_DIR={cfgs.CANGJIE_STDX_DIR}")
    else:
        cfgs.LOG.info("仓颉版本小于0.60.*")


def find_cjc_in_cj_home(args, cfgs, expected_version):
    """
    从 args.cj_home 中查找对应版本的 cjc

    Args:
        args: 命令行参数
        cfgs: 配置对象
        expected_version: 期望的 cjc 版本

    Returns:
        str: 找到的 cjc 路径，如果未找到返回 None
    """
    if not args.cj_home:
        return None

    try:
        all_items = os.listdir(args.cj_home)
        version_dirs = []

        for item in all_items:
            item_path = os.path.join(args.cj_home, item)
            if os.path.isdir(item_path):
                # 尝试解析版本号，过滤掉非版本目录
                try:
                    compare_versions(item, "0.0.0")
                    version_dirs.append(item)
                    if expected_version and item == expected_version:
                        cfgs.LOG.info(f"在 {args.cj_home} 中找到匹配版本的 cjc: {item}")
                        return item_path
                    else:
                        cfgs.LOG.info(f"在 {args.cj_home} 中找到可用 cjc: {item}")
                except:
                    cfgs.LOG.debug(f"跳过非版本目录: {item}")

        # 如果没有找到期望版本，使用最新版本
        if version_dirs:
            def parse_version(v):
                return [int(x) if x.isdigit() else 0 for x in v.split('.')]

            sorted_versions = sorted(version_dirs, key=parse_version, reverse=True)
            latest_version = sorted_versions[0]
            if expected_version:
                cfgs.LOG.warn(f"未找到期望版本 {expected_version}，使用最新版本: {latest_version}")
            else:
                cfgs.LOG.info(f"未指定期望版本，使用最新版本: {latest_version}")
            return os.path.join(args.cj_home, latest_version)
    except Exception as e:
        cfgs.LOG.error(f"无法访问 cj_home 目录: {args.cj_home}, 错误: {e}")

    return None


def config_cjc(args):
    """
    配置 cjc 环境（优化后的逻辑）

    优先级：
    1. 系统中的 cjc（如果版本匹配）
    2. args.cj_home 中的 cjc
    3. 报错退出（Mac 平台特殊处理：退出码 0）
    """
    cfgs = args.CANGJIE_CI_TEST_CFGS

    # 设置命令日志输出模式
    if hasattr(args, 'log_cmd') and args.log_cmd:
        cfgs.LOG_CMD = args.log_cmd

    # 设置是否显示编译告警
    if hasattr(args, 'show_warn'):
        cfgs.SHOW_WARN = args.show_warn

    # 步骤1: 初始化配置
    cfgs.config_init()

    # 步骤2: 获取期望的 cjc 版本
    expected_version = get_expected_cjc_version(cfgs)
    if expected_version:
        cfgs.EXPECT_CJC_VERSION = expected_version
        cfgs.LOG.info(f"期望 cjc 版本: {expected_version}")
    else:
        cfgs.LOG.warn("未找到期望的 cjc 版本配置")

    # 步骤3: 设置 cjpm 路径
    cfgs.BUILD_CJPM_PATH = get_cangjie_path(cfgs, 'cjpm')
    if cfgs.BUILD_CJPM_PATH == '':
        if cfgs.OS_PLATFORM == "windows":
            cjpm_path = str(os.path.join(os.path.dirname(os.path.dirname(os.getenv("LOCALAPPDATA"))), '.cjpm', 'git'))
            if os.path.exists(cjpm_path):
                cfgs.BUILD_CJPM_PATH = cjpm_path
            cjpm_path = str(os.path.join(os.getenv("LOCALAPPDATA"), '.cjpm', 'git'))
            if os.path.exists(cjpm_path):
                cfgs.BUILD_CJPM_PATH = cjpm_path
        else:
            cfgs.BUILD_CJPM_PATH = str(os.path.join(os.getenv("HOME"), '.cjpm', 'git'))

    # 步骤4: 检查系统中的 cjc
    has_system_cjc, system_version = check_system_cjc(cfgs)

    if has_system_cjc:
        # 系统中有 cjc
        if expected_version and system_version:
            if compare_versions(system_version, expected_version) == 0:
                cfgs.LOG.info(f"系统中的 cjc 版本 {system_version} 与期望版本 {expected_version} 一致")
                cfgs.WHICH_CJC = shutil.which("cjc")
                cfgs.BASE_CJC_VERSION = system_version

                # 设置 build_bin 和 LIB_DIR
                if compare_versions(system_version, '0.49.0') < 0:
                    cfgs.set_build_bin("build")
                    cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "build")
                else:
                    cfgs.set_build_bin("target")
                    cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "target")

                # 检查并设置 stdx 环境
                setup_stdx_if_needed(args, cfgs)
                return
            else:
                cfgs.LOG.warn(f"⚠️ 系统中的 cjc 版本 {system_version} 与期望版本 {expected_version} 不一致")
                # 尝试从 cj_home 中找期望版本，如果找不到则使用系统的 cjc
                args.cj_home = os.environ.get('CANGJIE_ROOT', get_cangjie_path(cfgs, 'home'))
                if args.cj_home:
                    # 从 cj_home 中查找对应版本
                    cjc_home = find_cjc_in_cj_home(args, cfgs, expected_version)
                    if cjc_home:
                        # 找到期望版本，使用 cj_home 中的版本
                        actual_version = os.path.basename(cjc_home)
                        cfgs.BASE_CJC_VERSION = actual_version
                        cfgs.cj_home = args.cj_home

                        # 设置运行时后缀
                        if compare_versions(actual_version, '1.0.4') > 0:
                            cfgs.CJC_RUNTIME_SUFFIX = 'cjnative'

                        # 设置 cangjie 环境
                        set_cangjie_home(cfgs, cjc_home)

                        # 设置 build_bin 和 LIB_DIR
                        if compare_versions(actual_version, '0.49.0') < 0:
                            cfgs.set_build_bin("build")
                            cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "build")
                        else:
                            cfgs.set_build_bin("target")
                            cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "target")

                        cfgs.LOG.info(f"已配置 cjc 版本: {actual_version}")
                        setup_stdx_if_needed(args, cfgs)
                        return

                # cj_home 中未找到期望版本，使用系统的 cjc
                cfgs.LOG.warn(f"使用系统中的 cjc 版本 {system_version}（未找到期望版本 {expected_version}）")
                cfgs.WHICH_CJC = shutil.which("cjc")
                cfgs.BASE_CJC_VERSION = system_version

                # 设置 build_bin 和 LIB_DIR
                if compare_versions(system_version, '0.49.0') < 0:
                    cfgs.set_build_bin("build")
                    cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "build")
                else:
                    cfgs.set_build_bin("target")
                    cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "target")

                # 检查并设置 stdx 环境
                setup_stdx_if_needed(args, cfgs)
                return
        else:
            cfgs.LOG.info("系统中已配置 cjc 环境")
            cfgs.WHICH_CJC = shutil.which("cjc")
            if system_version:
                cfgs.BASE_CJC_VERSION = system_version

                # 设置 build_bin 和 LIB_DIR
                if compare_versions(system_version, '0.49.0') < 0:
                    cfgs.set_build_bin("build")
                    cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "build")
                else:
                    cfgs.set_build_bin("target")
                    cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "target")

            # 检查并设置 stdx 环境
            setup_stdx_if_needed(args, cfgs)
            return

    # 步骤5: 系统中没有 cjc，从 cj_home 中查找
    cfgs.LOG.info("系统中未找到 cjc，尝试从 cj_home 中查找...")

    # 获取 cj_home 路径
    try:
        set_cangjie_path(args, cfgs)
    except:
        pass

    args.cj_home = os.environ.get('CANGJIE_ROOT', get_cangjie_path(cfgs, 'home'))

    if not args.cj_home:
        cfgs.LOG.error("❌ 未配置 cangjie 环境，请设置 --cj-home 或 CANGJIE_ROOT 环境变量")
        sys.exit(1)

    # 从 cj_home 中查找对应版本
    cjc_home = find_cjc_in_cj_home(args, cfgs, expected_version)

    if cjc_home:
        # 获取实际版本
        actual_version = os.path.basename(cjc_home)
        cfgs.BASE_CJC_VERSION = actual_version
        cfgs.cj_home = args.cj_home

        # 设置运行时后缀（必须在 set_cangjie_home 之前设置）
        if compare_versions(actual_version, '1.0.4') > 0:
            cfgs.CJC_RUNTIME_SUFFIX = 'cjnative'

        # 设置 cangjie 环境
        set_cangjie_home(cfgs, cjc_home)

        # 设置 build_bin 和 LIB_DIR
        if compare_versions(actual_version, '0.49.0') < 0:
            cfgs.set_build_bin("build")
            cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "build")
        else:
            cfgs.set_build_bin("target")
            cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "target")

        cfgs.LOG.info(f"已配置 cjc 版本: {actual_version}")

        # 设置环境变量
        cangjie_home_env = os.getenv('CANGJIE_HOME')
        if cangjie_home_env and not os.getenv('CANGJIE_STDX_PATH'):
            os.environ['CANGJIE_STDX_PATH'] = cangjie_home_env
    else:
        # Mac 平台特殊处理：退出码 0
        if platform.system() == 'Darwin':
            cfgs.LOG.warn(f"[WARN] Mac 平台在 {args.cj_home} 中未找到期望的 cjc 版本: {expected_version}，跳过测试")
            sys.exit(0)

        cfgs.LOG.error(f"❌ 在 {args.cj_home} 中未找到期望的 cjc 版本: {expected_version}")
        sys.exit(1)
    try:
        cfgs.CANGJIE_HOME = os.path.dirname(os.path.dirname(shutil.which("cjc")))
    except:
        cfgs.LOG.warn("默认配置和用户都未设置仓颉环境, 请检查")

    if cfgs.WHICH_CJC_OUT_STR is None:
        cfgs.WHICH_CJC_OUT_STR = "".join(os.popen('{} -v'.format(shutil.which("cjc"))).readlines())
    cfgs.LOG.info(cfgs.WHICH_CJC_OUT_STR)
    cfgs.BASE_CJC_VERSION = get_base_version(cfgs.WHICH_CJC_OUT_STR)
    cfgs.CANGJIE_TARGET = cfgs.WHICH_CJC_OUT_STR.split('Target: ')[1].replace('\n', '').replace('\r', '')
    if compare_versions(cfgs.BASE_CJC_VERSION, '0.49.0') < 0:
        cfgs.set_build_bin("build")
        cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "build")
    else:
        cfgs.set_build_bin("target")
        cfgs.LIB_DIR = os.path.join(cfgs.HOME_DIR, "target")

    # 检查并设置 stdx 环境
    setup_stdx_if_needed(args, cfgs)


def init_log(cfgs, name, log_file_path=None):
    """init log config"""
    parser_maple_test_config_file(cfgs)
    log_path = cfgs.BASE_DIR
    create_file(log_path)
    log = logging.getLogger(name)

    # 清理现有的处理器，避免重复添加
    for handler in log.handlers[:]:
        log.removeHandler(handler)

    log.setLevel(logging.DEBUG)
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%m-%d %H:%M:%S")
    if platform.system() == 'Windows':
        sys.stdout.reconfigure(encoding='utf-8')
    streamhandler = logging.StreamHandler(sys.stdout)
    streamhandler.setLevel(logging.DEBUG)
    streamhandler.setFormatter(formatter)
    log.addHandler(streamhandler)
    if log_file_path:
        log_dir = os.path.dirname(log_file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        filehandler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
        log.addHandler(filehandler)
        log.info("CMD    : %s", " ".join(sys.argv))
    else:
        filehandler = TimedRotatingFileHandler(
            os.path.join(log_path, "ci_test.log"), when="W6", interval=1, backupCount=60, encoding='utf-8'
        )
    filehandler.setLevel(logging.DEBUG)
    filehandler.setFormatter(formatter)
    log.addHandler(filehandler)
    return log


def parser_maple_test_config_file(cfgs: ArgConfig):
    if cfgs.BUILD_TYPE == "ci_test":
        cfg = read_config(complete_path(os.path.join(cfgs.FILE_ROOT, "ci_test.cfg")))
    else:
        cfg = read_config(complete_path(os.path.join(cfgs.BASE_DIR, "ci_test.cfg")))
    cfgs.temp_dir = complete_path(
        os.path.join(cfgs.BASE_DIR, get_config_value(cfg, "running", "temp_dir", default="../test_temp/run")))
    cfgs.log_dir = complete_path(
        os.path.join(cfgs.BASE_DIR, get_config_value(cfg, "logging", "name", default="../test_temp/log")))
    cfgs.level = get_config_value(cfg, "logging", "level", default="INFO")
    cfgs.UPDATE_CJPM_TOML = get_config_value(cfg, "cangjie-home", "update_toml", default="false") == "true"
    cfgs.BUILD_CI_TEST_CFG = cfg


###  BUILD
SRC_FILES = ""
OTHER_BUILD_DICT = {".sh": "bash", ".java": "java", ".py": "python3", ".go": "go"}
if platform.system() == "Windows":
    dynamic_lib = ".dll"
    static_lib = ".lib"
elif platform.system() == "Darwin":
    dynamic_lib = ".dylib"
    static_lib = ".a"
else:
    dynamic_lib = ".so"
    static_lib = ".a"
lib_list = []


def build_coverage(args, cfgs):
    e = '' if args.e == None else args.e
    b = "" if args.b == None else "--branches"
    output = subprocess.Popen(
        'cjcov --root=./ -e "{}" --html-details -o output {}'.format(e, b), shell=True, stderr=PIPE, stdout=PIPE
    )
    log_output(output, output.args, cfgs)
    exit(output.returncode)


def __set_up_the_link_lib(cfgs, lib_path):
    if cfgs.OS_PLATFORM == "windows":
        os.environ['Path'] = f"{os.getenv('Path')};{lib_path}"
    else:
        os.environ['LD_LIBRARY_PATH'] = f"{lib_path}:" + os.environ.get('LD_LIBRARY_PATH', "")
    pass


def __check_git_credentials():
    """检查git是否配置了用户凭证"""
    try:
        result = subprocess.run(
            ['git', 'config', '--global', 'user.name'],
            capture_output=True,
            text=True
        )
        user_name = result.stdout.strip()

        result = subprocess.run(
            ['git', 'config', '--global', 'user.email'],
            capture_output=True,
            text=True
        )
        user_email = result.stdout.strip()

        if user_name and user_email:
            return True, f"Git已配置: {user_name} <{user_email}>"
        else:
            return False, "Git未配置用户凭证"
    except Exception as e:
        return False, f"检查Git凭证失败: {e}"


def __get_git_clone_url(base_url: str, use_ssh: bool = False) -> str:
    """根据环境变量生成带认证的git clone URL

    Args:
        base_url: 基础 URL
        use_ssh: 是否使用 SSH 方式（Mac 环境默认使用 SSH）

    Returns:
        带认证的 URL 或 SSH URL
    """
    # Mac 环境优先使用 SSH 方式
    if use_ssh or platform.system() == 'Darwin':
        if base_url.startswith('https://'):
            # 将 HTTPS URL 转换为 SSH URL
            # https://gitcode.com/Cangjie-TPC/ci_lib.git -> git@gitcode.com:Cangjie-TPC/ci_lib.git
            ssh_url = base_url.replace('https://', 'git@').replace('.com/', '.com:')
            return ssh_url

    # Windows/Linux 环境使用 HTTPS 方式
    gitcode_username = os.environ.get('gitcode_username', '')
    gitcode_password = os.environ.get('gitcode_password', '')

    if gitcode_username and gitcode_password:
        if base_url.startswith('https://'):
            return base_url.replace('https://', f'https://{gitcode_username}:{gitcode_password}@')
    return base_url


def __load_c_library(args, cfgs):
    ffi_lib = os.path.join(cfgs.HOME_DIR, "ci_test", f"lib_{cfgs.MODULE_NAME}")
    if not os.path.exists(ffi_lib):
        if not bool(os.environ.get('gitcode_username') and os.environ.get('gitcode_password')):
            ok, msg = __check_git_credentials()
            if not ok:
                cfgs.LOG.error(f"Git clone失败: {msg}")
                cfgs.LOG.error("请先配置Git用户: git config --global user.name 'Your Name'")
                cfgs.LOG.error("                  git config --global user.email 'your@email.com'")
                cfgs.LOG.error("或者设置环境变量: gitcode_username 和 gitcode_password")
                return

        base_url = "https://gitcode.com/Cangjie-TPC/ci_lib.git"
        clone_url = __get_git_clone_url(base_url)

        if clone_url != base_url:
            cfgs.LOG.info("使用环境变量中的gitcode凭证进行clone")

        cmd = f"git clone -b lib_{cfgs.MODULE_NAME} {clone_url} {ffi_lib} --depth 1"
        output = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cfgs.HOME_DIR,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        log_output(output, output.args, cfgs)
    ffi_bin_path = os.path.join(ffi_lib, cfgs.OS_PLATFORM, 'lib', f"lib_{cfgs.MODULE_NAME}")
    if os.path.isdir(ffi_bin_path):
        entries = os.listdir(ffi_bin_path)
        if len(entries) == 1 and os.path.isdir(os.path.join(ffi_bin_path, entries[0])):
            if not os.path.exists(os.path.join(cfgs.HOME_DIR, cfgs.CUSTOM_MAP['ci_lib'])):
                os.mkdir(os.path.join(cfgs.HOME_DIR, cfgs.CUSTOM_MAP['ci_lib']))
            for files in os.listdir(os.path.join(ffi_bin_path, entries[0])):
                shutil.copyfile(os.path.join(ffi_bin_path, entries[0], files),
                                os.path.join(cfgs.HOME_DIR, cfgs.CUSTOM_MAP['ci_lib'], files))
        else:
            for entry in entries:
                shutil.copyfile(os.path.join(ffi_bin_path, entry),
                                os.path.join(cfgs.HOME_DIR, cfgs.CUSTOM_MAP['ci_lib'], entry))
    else:
        cfgs.LOG.error("已配置ci_lib, clone时未找到该文件.")


# 删除后缀名文件
def delete_suffix_file(filepath, suffix_name):
    for file in os.listdir(filepath):
        if '.' in file and file.split('.')[-1] == suffix_name:
            os.remove(os.path.join(filepath, file))


def runBuild(args, cfgs):
    delete_suffix_file(cfgs.HOME_DIR, 'gcda')
    delete_suffix_file(cfgs.HOME_DIR, 'gcno')
    if cfgs.CUSTOM_MAP.get("ci_lib"):
        __load_c_library(args, cfgs)
    cfgs.LOG.info("Building with cjpm.....")
    cjpmbuild(args, cfgs)


def get_sublib_list(cfgs, path):
    global lib_list
    global dynamic_lib
    global static_lib
    sub_library_libdir = os.path.join(path, 'lib')
    if os.path.exists(sub_library_libdir):
        for path_2, dirs, files in os.walk(sub_library_libdir):
            for file in files:
                if dynamic_lib in str(file) or static_lib in str(file):
                    lib_list.append(str(os.path.join(path_2, file)))
    # for sub_lib in get_sublibrary_path(cfgs, path):
    #     get_sublib_list(cfgs, os.path.join(path, sub_lib))


def get_cjc_cpm(cfgs):
    try:
        version = os.popen('cjc -v').readline().split('Cangjie Compiler: ')[1].split(' (')[0]
        temp = version.split(".")
        ver = float(temp[0])
        if float(temp[1]) != 0:
            ver += float(f'0.{temp[1]}')
        if ver >= 0.38:
            return "cjpm"
        else:
            return 'cpm'
    except IndexError:
        cfgs.LOG.error("没有配置cangjie环境变量, 请配置后再试, ciTest.py build --cj-home=CangjiePath;")
        exit(1)


def cjpmbuild(args, cfgs):
    output = __do_cjpm_build(args, cfgs)
    out, err = __log_output(output, output.args, cfgs, cfgs.HOME_DIR)
    # set_build_log_warnings_count(cfgs, err)
    if "imports package 'stdx" in str(err):
        cfgs.LOG.info("Trying again using STDX package.")
        if cfgs.CANGJIE_STDX_DIR:
            stdx_lib = cfgs.CANGJIE_STDX_DIR
            ci_test_cfg = cfgs.BUILD_PARMS
            if "target" not in ci_test_cfg:
                ci_test_cfg['target'] = {}
            ci_test_cfg_target = ci_test_cfg['target']
            if cfgs.CANGJIE_TARGET not in ci_test_cfg_target:
                ci_test_cfg_target[cfgs.CANGJIE_TARGET] = {"bin-dependencies": {"path-option": [stdx_lib]}}
            else:
                if "bin-dependencies" not in ci_test_cfg_target[cfgs.CANGJIE_TARGET]:
                    ci_test_cfg_target[cfgs.CANGJIE_TARGET]['bin-dependencies'] = {"path-option": [stdx_lib]}
                else:
                    if "path-option" not in ci_test_cfg_target[cfgs.CANGJIE_TARGET]['bin-dependencies']:
                        ci_test_cfg_target[cfgs.CANGJIE_TARGET]['bin-dependencies']["path-option"] = [stdx_lib]
                    else:
                        ci_test_cfg_target[cfgs.CANGJIE_TARGET]['bin-dependencies']["path-option"].append(stdx_lib)
            if cfgs.UPDATE_CJPM_TOML or args.update_toml:
                with open(os.path.join(cfgs.HOME_DIR, "cjpm.toml"), "w", encoding='UTF-8') as toml_f:
                    cfgs.LOG.info("正在将stdx环境写入cjpm.toml")
                    dump_c(ci_test_cfg, toml_f)
            output = __do_cjpm_build(args, cfgs)
            out, err = __log_output(output, output.args, cfgs, cfgs.HOME_DIR)
            # set_build_log_warnings_count(cfgs, err)
            if output.returncode != 0:
                cfgs.LOG.error(str(out))
                cfgs.LOG.error("cjpm build error..")
                exit(output.returncode)
        else:
            cfgs.LOG.error("No configuration of STDX package was found.")
    elif output.returncode != 0 or (err and "build failed" in str(err)):
        cfgs.LOG.error(str(out))
        cfgs.LOG.error("cjpm build error..")
        exit(output.returncode)


def __do_cjpm_build(args, cfgs):
    cmd1 = [get_cjc_cpm(cfgs), "build"]
    if args.full:
        cmd0 = "{} update".format(get_cjc_cpm(cfgs))
        output = subprocess.Popen(cmd0, shell=True, cwd=cfgs.HOME_DIR, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        _, err_log = __log_output(output, output.args, cfgs, cfgs.HOME_DIR)
        if output.returncode != 0:
            if str(err_log).__contains__("can not find the library"):
                sub_librarys = []
                for item in re.split("\r?\n", err_log):
                    if item.__contains__("field at"):
                        sub_library = os.path.join(item.split('field at ')[1]).split("\\n")[0]
                        if sub_library.endswith("cjpm.toml") or sub_library.endswith("module.json"):
                            sub_librarys.append(os.path.dirname(sub_library))
                __loop_down_load_cjpm_librarys(cfgs, sub_librarys)
                output = subprocess.Popen(cmd0, shell=True, cwd=cfgs.HOME_DIR, stderr=subprocess.PIPE,
                                          stdout=subprocess.PIPE)
                _, err_log = __log_output(output, output.args, cfgs, cfgs.HOME_DIR)
            else:
                cfgs.LOG.error("build fail")
    else:
        cmd1.append("-i")
    if args.coverage:
        cmd1.append("--coverage")
    elif args.release:
        ci_test_cfg = cfgs.BUILD_PARMS
        if ci_test_cfg and 'profile' in ci_test_cfg and 'customized-option' in ci_test_cfg['profile'] and 'release' in \
                ci_test_cfg['profile']['customized-option']:
            cmd1.append("--release")
        else:
            if cfgs.UPDATE_CJPM_TOML or args.update_toml:
                ci_test_cfg['profile'] = {
                    'build': {
                        'incremental': True,
                        'lto': ""
                    },
                    'customized-option': {
                        'debug': "-g -Woff all -Won apilevel-check",
                        'release': "--fast-math -O2 -s -Woff all -Won apilevel-check"
                    },
                    'test': {}
                }
                with open(os.path.join(cfgs.HOME_DIR, "cjpm.toml"), "w", encoding='UTF-8') as toml_f:
                    cfgs.LOG.info("正在将profile环境写入cjpm.toml")
                    dump_c(ci_test_cfg, toml_f)
                cmd1.append("--release")
    elif args.debug:
        ci_test_cfg = cfgs.BUILD_PARMS
        if ci_test_cfg and 'profile' in ci_test_cfg and 'customized-option' in ci_test_cfg['profile'] and 'debug' in \
                ci_test_cfg['profile']['customized-option']:
            cmd1.append("--debug")
        else:
            if cfgs.UPDATE_CJPM_TOML or args.update_toml:
                ci_test_cfg['profile'] = {
                    'build': {
                        'incremental': True,
                        'lto': ""
                    },
                    'customized-option': {
                        'debug': "-g -Woff all -Won apilevel-check",
                        'release': "--fast-math -O2 -s -Woff all -Won apilevel-check"
                    },
                    'test': {}
                }
                with open(os.path.join(cfgs.HOME_DIR, "cjpm.toml"), "w", encoding='UTF-8') as toml_f:
                    cfgs.LOG.info("正在将profile环境写入cjpm.toml")
                    dump_c(ci_test_cfg, toml_f)
                cmd1.append("--debug")
    if hasattr(args, 'verbose') and args.verbose:
        cmd1.append("--verbose")
    if args.target and str(args.target).__contains__("ohos"):
        # 读取 DEVECO_CANGJIE_HOME 环境变量
        _get_DEVECO_CANGJIE_HOME(cfgs)
        cmd1.append("--target=aarch64-linux-ohos")
    return subprocess.Popen(" ".join(cmd1), shell=True, cwd=cfgs.HOME_DIR, stderr=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    # return log_output(output, output.args,cfgs, cfgs.HOME_DIR)


def __loop_down_load_cjpm_librarys(cfgs, sub_librarys):
    # sub_librarys
    for sub_library in sub_librarys:
        sub_library_config_file = os.path.join(sub_library, cfgs.CONFIG_FILE)
        if cfgs.BUILD_BIN != "build":
            if str(sub_library_config_file).endswith("toml"):
                __cjpm_git_download_toml(cfgs, sub_library, sub_library_config_file)
            elif str(sub_library_config_file).endswith("json"):
                __cjpm_git_download_json(cfgs, sub_library, sub_library_config_file)
            else:
                cfgs.LOG.warn("没有项目工程配置文件, 请配置module.json或者cjpm.toml配置文件")
        else:
            __cjpm_git_download_json(cfgs, sub_library, sub_library_config_file)


def __cjpm_git_download_json(cfgs, sub_library, sub_library_config_file):
    file = open(sub_library_config_file, 'r', encoding='UTF-8')
    parm = json.load(file)
    ffi = parm["foreign_requires"]
    name = parm['name']
    gitee = "https://gitcode.com/Cangjie-TPC/ci_lib.git"
    gitee = __get_git_clone_url(gitee)
    for ke, val in ffi.items():
        for k, v in val.items():
            if k == 'path':
                cmd0 = f"git clone -b lib_{name} {gitee} {os.path.join(cfgs.BASE_DIR, f'lib_{name}')} --depth=1"
                output = subprocess.Popen(cmd0, shell=True, cwd=cfgs.BASE_DIR, stderr=subprocess.PIPE,
                                          stdout=subprocess.PIPE)
                log_output(output, output.args, cfgs, cfgs.HOME_DIR)
                old_dir = os.path.join(cfgs.BASE_DIR, "lib_{}".format(name), cfgs.OS_PLATFORM, "lib", f"lib_{name}")
                str_lib = __get_cjpm_library_cjpm_lock_foreign_requires_path(cfgs, sub_library)
                new_dir = os.path.join(sub_library, str_lib)
                try:
                    shutil.copytree(old_dir, new_dir)
                except:
                    shutil.rmtree(new_dir)
                    shutil.copytree(old_dir, new_dir)


def __get_cjpm_target_from_os_platform(cfgs):
    """
    将 cfgs.OS_PLATFORM 映射到 cjpm.toml 中的 target 格式
    例如: linux_x86_64 -> x86_64-unknown-linux-gnu
    """
    platform_mapping = {
        'linux_x86_64': 'x86_64-unknown-linux-gnu',
        'linux_aarch64': 'aarch64-unknown-linux-gnu',
        'windows': 'x86_64-unknown-windows-gnu',
        'darwin_x86_64': 'x86_64-apple-darwin',
        'darwin_arm64': 'aarch64-apple-darwin'
    }
    return platform_mapping.get(cfgs.OS_PLATFORM)


def __cjpm_git_download_toml(cfgs, sub_library, sub_library_config_file):
    cfgs.LOG.info(f"check file {sub_library_config_file}")
    parm = parse(open(sub_library_config_file, "r", encoding='UTF-8').read())
    name = parm['package']['name']
    gitee = "https://gitcode.com/Cangjie-TPC/ci_lib.git"
    gitee = __get_git_clone_url(gitee)
    cfgs.LOG.info(
        f"使用 Git URL: {gitee.replace(os.environ.get('gitcode_password', ''), '****') if os.environ.get('gitcode_password') else gitee}")

    # 检查是否存在旧格式的 ffi.c 配置（且不为空）
    has_ffi_c = 'ffi' in parm and 'c' in parm['ffi'] and len(parm['ffi']['c']) > 0
    cfgs.LOG.info(f"检测到旧格式 ffi.c 配置: {has_ffi_c}")

    # 检查是否存在新格式的 target.<platform>.ffi.c 配置
    has_target_ffi_c = False
    target_ffi_c_config = None
    if 'target' in parm:
        current_target = __get_cjpm_target_from_os_platform(cfgs)
        cfgs.LOG.info(f"当前平台 target: {current_target}")
        if current_target and current_target in parm['target']:
            target_config = parm['target'][current_target]
            if 'ffi' in target_config and 'c' in target_config['ffi']:
                has_target_ffi_c = True
                target_ffi_c_config = target_config['ffi']['c']
                cfgs.LOG.info(f"检测到新格式 target.{current_target}.ffi.c 配置: {target_ffi_c_config}")

    # 处理旧格式 ffi.c
    if has_ffi_c:
        for ke, val in parm['ffi']['c'].items():
            for k, v in val.items():
                if k == 'path':
                    clone_dir = os.path.join(cfgs.BASE_DIR, f'lib_{name}')
                    cmd0 = f"git clone -b lib_{name} {gitee} {clone_dir} --depth=1"
                    output = subprocess.Popen(cmd0, shell=True, cwd=cfgs.BASE_DIR, stderr=subprocess.PIPE,
                                              stdout=subprocess.PIPE)
                    log_output(output, output.args, cfgs, cfgs.HOME_DIR)

                    # 构建源目录路径
                    old_dir = os.path.join(cfgs.BASE_DIR, "lib_{}".format(name), cfgs.OS_PLATFORM, "lib",
                                           f"lib_{name}")
                    new_dir = os.path.join(sub_library, "lib")

                    # 检查源目录是否存在
                    if not os.path.exists(old_dir):
                        cfgs.LOG.error(f"克隆的库目录不存在: {old_dir}")
                        cfgs.LOG.error(f"请检查 ci_lib 仓库中是否存在分支: lib_{name}")
                        cfgs.LOG.error(f"以及该分支下是否存在目录: {cfgs.OS_PLATFORM}/lib/lib_{name}")
                        # 尝试列出克隆目录的内容以便调试
                        if os.path.exists(clone_dir):
                            cfgs.LOG.info(f"克隆目录 {clone_dir} 的内容:")
                            for item in os.listdir(clone_dir):
                                cfgs.LOG.info(f"  - {item}")
                        return

                    try:
                        shutil.copytree(old_dir, new_dir)
                        cfgs.LOG.info(f"成功复制库文件: {old_dir} -> {new_dir}")
                    except Exception as e:
                        cfgs.LOG.warning(f"复制库文件失败，尝试清理后重试: {e}")
                        if os.path.exists(new_dir):
                            shutil.rmtree(new_dir)
                        shutil.copytree(old_dir, new_dir)
                        cfgs.LOG.info(f"成功复制库文件: {old_dir} -> {new_dir}")

    # 处理新格式 target.<platform>.ffi.c
    elif has_target_ffi_c:
        for ke, val in target_ffi_c_config.items():
            for k, v in val.items():
                if k == 'path':
                    clone_dir = os.path.join(cfgs.BASE_DIR, f'lib_{name}')
                    cmd0 = f"git clone -b lib_{name} {gitee} {clone_dir} --depth=1"
                    output = subprocess.Popen(cmd0, shell=True, cwd=cfgs.BASE_DIR, stderr=subprocess.PIPE,
                                              stdout=subprocess.PIPE)
                    log_output(output, output.args, cfgs, cfgs.HOME_DIR)

                    # 构建源目录路径
                    old_dir = os.path.join(cfgs.BASE_DIR, "lib_{}".format(name), cfgs.OS_PLATFORM, "lib",
                                           f"lib_{name}")
                    new_dir = os.path.join(sub_library, "lib")

                    # 检查源目录是否存在
                    if not os.path.exists(old_dir):
                        cfgs.LOG.error(f"克隆的库目录不存在: {old_dir}")
                        cfgs.LOG.error(f"请检查 ci_lib 仓库中是否存在分支: lib_{name}")
                        cfgs.LOG.error(f"以及该分支下是否存在目录: {cfgs.OS_PLATFORM}/lib/lib_{name}")
                        # 尝试列出克隆目录的内容以便调试
                        if os.path.exists(clone_dir):
                            cfgs.LOG.info(f"克隆目录 {clone_dir} 的内容:")
                            for item in os.listdir(clone_dir):
                                cfgs.LOG.info(f"  - {item}")
                        return

                    try:
                        shutil.copytree(old_dir, new_dir)
                        cfgs.LOG.info(f"成功复制库文件: {old_dir} -> {new_dir}")
                    except Exception as e:
                        cfgs.LOG.warning(f"复制库文件失败，尝试清理后重试: {e}")
                        if os.path.exists(new_dir):
                            shutil.rmtree(new_dir)
                        shutil.copytree(old_dir, new_dir)
                        cfgs.LOG.info(f"成功复制库文件: {old_dir} -> {new_dir}")


def compare_versions(v1: str, v2: str) -> int:
    """
    比较两个版本号字符串大小（遵循语义化版本规范核心逻辑）

    返回:
        -1: v1 < v2
         0: v1 == v2
         1: v1 > v2

    特性:
    自动清理空格/前导零
    处理不同长度（短版本号自动补0）
    严格校验：拒绝负数、非数字主版本段
    智能 fallback：优先使用 packaging 库（支持预发布/构建元数据），无依赖时启用安全解析
    详细异常提示（便于调试）

    示例:
        compare_versions("1.0.1", "1.0")      → 1
        compare_versions("1.10", "1.2")       → 1  (10 > 2)
        compare_versions("2.0.0-beta", "2.0.0") → -1 (预发布版 < 正式版)
        compare_versions(" 1.02 ", "1.2.0")   → 0
    """
    import re
    from typing import List, Tuple

    def _sanitize(v: str) -> str:
        """清理空格，保留关键分隔符"""
        if not isinstance(v, str):
            raise TypeError(f"Version must be string, got {type(v).__name__}")
        v = v.strip()
        if not v:
            raise ValueError("Version string cannot be empty")
        return v

    def _parse_numeric_parts(v: str) -> Tuple[List[int], str]:
        """
        解析主版本段（数字部分），分离预发布/构建元数据
        返回: (数字段列表, 剩余标识符)
        """
        # 提取主版本段（直到遇到非数字/点/空格）
        match = re.match(r'^[\d.]+', v)
        if not match:
            raise ValueError(f"Invalid version format: '{v}' (no numeric parts found)")

        num_str = match.group(0)
        rest = v[len(num_str):].strip()

        # 解析数字段
        parts = []
        for seg in num_str.split('.'):
            seg_clean = seg.strip()
            if not seg_clean:  # 处理连续点（如 "1..2"）
                parts.append(0)
                continue
            try:
                num = int(seg_clean)
                if num < 0:
                    raise ValueError(f"Negative version segment not allowed: '{seg_clean}'")
                parts.append(num)
            except ValueError:
                raise ValueError(f"Invalid numeric segment: '{seg_clean}' in version '{v}'")
        return parts, rest

    def _compare_with_packaging(v1: str, v2: str) -> int:
        """使用 packaging 库进行完整 SemVer 比较（含预发布/构建元数据）"""
        try:
            from packaging.version import parse
            ver1, ver2 = parse(v1), parse(v2)
            if ver1 < ver2:
                return -1
            elif ver1 > ver2:
                return 1
            return 0
        except ImportError:
            return None  # Fallback to manual parsing

    # ====== 主逻辑 ======
    v1_clean, v2_clean = _sanitize(v1), _sanitize(v2)

    # 优先尝试使用 packaging 库（最准确）
    res = _compare_with_packaging(v1_clean, v2_clean)
    if res is not None:
        return res

    # ====== 无依赖 fallback：安全解析数字主干 ======
    try:
        nums1, meta1 = _parse_numeric_parts(v1_clean)
        nums2, meta2 = _parse_numeric_parts(v2_clean)

        # 补齐长度（短版本号视为后续段为0）
        max_len = max(len(nums1), len(nums2))
        nums1 += [0] * (max_len - len(nums1))
        nums2 += [0] * (max_len - len(nums2))

        # 比较数字主干
        for a, b in zip(nums1, nums2):
            if a < b:
                return -1
            elif a > b:
                return 1

        # 数字主干相同：有预发布标识的版本 < 无标识的正式版
        # （简化逻辑：仅判断是否存在标识符，不深入解析 alpha/beta 等）
        has_meta1 = bool(meta1 and not meta1.startswith('+'))
        has_meta2 = bool(meta2 and not meta2.startswith('+'))

        if has_meta1 and not has_meta2:
            return -1
        elif not has_meta1 and has_meta2:
            return 1
        # 其他情况（均有/均无标识符）视为相等
        return 0

    except Exception as e:
        # 提供明确错误指引
        raise ValueError(
            f"Version comparison failed: {str(e)}\n"
            f"Tip: For full SemVer support (pre-releases, build metadata), install: pip install packaging"
        ) from e


def set_cangjie_home(cfgs, cjc_home):
    if cfgs.OS_PLATFORM == "windows":
        cfgs.LOG.info("The current environment is Windows")
        cangjie_bin = os.path.join(cjc_home, 'bin')
        cangjie_tools = os.path.join(cjc_home, 'tools', 'bin')
        cangjie_runtime = os.path.join(cjc_home, 'runtime', 'lib', f'windows_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}')
        cangjie_path = ""
        if cangjie_runtime not in os.environ['Path']:
            cangjie_path += f'{cangjie_runtime};'
        if cangjie_bin not in os.environ['Path']:
            cangjie_path += f'{cangjie_bin};'
        if cangjie_tools not in os.environ['Path']:
            cangjie_path += f'{cangjie_tools};'
        os.environ['Path'] = f"{cangjie_path}{os.environ['Path']}"
        if not os.getenv('CANGJIE_HOME'):
            os.environ['CANGJIE_HOME'] = f"{cjc_home}"
        if not os.getenv('CANGJIE_STDX_PATH'):
            os.environ['CANGJIE_STDX_PATH'] = f"{cjc_home}"
    elif cfgs.OS_PLATFORM.startswith("darwin"):
        cfgs.LOG.info("The current environment is Mac")
        cangjie_bin = os.path.join(cjc_home, 'bin')
        cangjie_tools = os.path.join(cjc_home, 'tools', 'bin')
        if 'aarch64' in cfgs.OS_PLATFORM:
            cangjie_runtime = os.path.join(cjc_home, 'runtime', 'lib', f'darwin_arm64_{cfgs.CJC_RUNTIME_SUFFIX}')
        else:
            cangjie_runtime = os.path.join(cjc_home, 'runtime', 'lib', f'darwin_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}')
        os.environ['PATH'] = f"{cangjie_bin}:{cangjie_tools}:" + os.environ['PATH']
        os.environ['CANGJIE_HOME'] = f"{cjc_home}"
        os.environ['CANGJIE_STDX_PATH'] = f"{cjc_home}"
        os.environ['DYLD_LIBRARY_PATH'] = f"{cangjie_runtime}:" + os.environ.get('DYLD_LIBRARY_PATH', "")
    elif cfgs.OS_PLATFORM.startswith("linux"):
        cfgs.LOG.info("The current environment is linux")
        if 'aarch64' in cfgs.OS_PLATFORM:
            cangjie_runtime = os.path.join(cjc_home, 'runtime', 'lib', f'linux_aarch64_{cfgs.CJC_RUNTIME_SUFFIX}')
        else:
            cangjie_runtime = os.path.join(cjc_home, 'runtime', 'lib', f'linux_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}')
        os.environ['PATH'] = f"{cjc_home}/bin:{cjc_home}/tools/bin:{cjc_home}/debugger/bin:" + os.environ['PATH']
        os.environ['CANGJIE_HOME'] = f"{cjc_home}"
        os.environ['CANGJIE_STDX_PATH'] = f"{cjc_home}"
        os.environ['LD_LIBRARY_PATH'] = f"{cangjie_runtime}:{cjc_home}/debugger/third_party/lldb/lib:" + os.environ.get(
            'LD_LIBRARY_PATH', "")


def __set_cangjie_stdx_home(cfgs, stdx_home):
    if not stdx_home:
        cfgs.LOG.warn("stdx_home is empty, skip setting cangjie stdx home.")
        return

    # 设置 CANGJIE_STDX_PATH 环境变量（cjpm 需要这个环境变量）
    cangjie_home_env = os.getenv('CANGJIE_HOME')
    if not cangjie_home_env:
        # 如果 CANGJIE_HOME 环境变量不存在，根据 cjc 路径推断
        if cfgs.WHICH_CJC:
            cangjie_home_env = os.path.dirname(os.path.dirname(cfgs.WHICH_CJC))
            os.environ['CANGJIE_HOME'] = cangjie_home_env
            cfgs.LOG.info(f"根据 cjc 路径推断并设置 CANGJIE_HOME: {cangjie_home_env}")

    if cangjie_home_env and not os.getenv('CANGJIE_STDX_PATH'):
        os.environ['CANGJIE_STDX_PATH'] = cangjie_home_env
        cfgs.LOG.info(f"设置 CANGJIE_STDX_PATH 环境变量: {cangjie_home_env}")

    # 设置库路径环境变量
    if cfgs.OS_PLATFORM == "windows":
        cangjie_stdx_path = ""
        if stdx_home not in os.environ['Path']:
            cangjie_stdx_path += f'{stdx_home};'
        os.environ['Path'] = f"{cangjie_stdx_path}{os.environ['Path']}"
    elif cfgs.OS_PLATFORM.startswith("macos"):
        os.environ['DYLD_LIBRARY_PATH'] = f"{stdx_home}:" + os.environ.get('DYLD_LIBRARY_PATH', "")
    else:
        os.environ['LD_LIBRARY_PATH'] = f"{stdx_home}:" + os.environ.get('LD_LIBRARY_PATH', "")
    cfgs.LOG.info("set cangjie stdx success.")


def get_cjc_version(path):
    readme_names = ['README.md', 'README.MD', 'README', 'readme.md', 'Readme.md']
    if not any(os.path.basename(path) == name for name in readme_names):
        return 0
    try:
        with open(path, "r", encoding="UTF-8") as file:
            content = file.read()
            if 'cjc-v' in content and '-brightgreen' in content:
                version = content.split('cjc-v')[1].split('-brightgreen')[0]
                if version:
                    return version
    except Exception as e:
        logging.warning(f"从 {path} 解析 cjc 版本失败: {e}")
    return 0


def copy_windows_lib(args):
    args.WINDOWS_DLLS = []
    for root, _, files in os.walk(args.LIB_DIR):
        if not os.path.join(root).endswith("bin"):
            for dll in files:
                if str(dll).endswith(".dll"):
                    args.WINDOWS_DLLS.append(os.path.join(root, dll))


def find_lib_path(lib_path, ci_lib_path):
    cj_3rd_libs = set()
    """查找三方依赖库 """
    if os.path.exists(os.path.join(lib_path, "release")):
        cj_3rd_libs.add(os.path.join(lib_path, "release"))
    elif os.path.exists(lib_path):
        cj_3rd_libs.add(lib_path)
    elif os.path.exists(ci_lib_path):
        cj_3rd_libs.add(ci_lib_path)
        if not os.path.exists(os.path.join(lib_path, "release")):
            for root, dirs, files in os.walk(ci_lib_path):
                for dir_name in dirs:
                    if os.path.join(ci_lib_path, dir_name) in cj_3rd_libs:
                        continue
                    if os.path.exists(os.path.join(ci_lib_path, dir_name)):
                        cj_3rd_libs.add(os.path.join(ci_lib_path, dir_name))
    cangjie_env_setup(cj_3rd_libs)
    return cj_3rd_libs


def cangjie_env_setup(lib_dir):
    if platform.system() == "Windows":
        for item in lib_dir:
            if f"{item}" not in os.environ['Path']:
                os.environ['Path'] = f"{os.getenv('Path')};{item}"
    elif platform.system() == "Darwin":
        for item in lib_dir:
            if os.path.exists(item):
                dyld_path = os.getenv('DYLD_LIBRARY_PATH', '')
                if str(item) not in dyld_path:
                    os.environ['DYLD_LIBRARY_PATH'] = f"{item}:{dyld_path}"
    else:
        for item in lib_dir:
            if os.path.exists(item):
                ld_path = os.getenv('LD_LIBRARY_PATH', '')
                if str(item) not in ld_path:
                    os.environ['LD_LIBRARY_PATH'] = f"{item}:{ld_path}"

                # if str(item) not in os.getenv('CANGJIE_HOME'):
                #     os.environ["CANGJIE_HOME"] = f"{item}:{os.environ.get('CANGJIE_HOME', '')}"


def do_load_library_cfg(cfg_path):
    config = read_config(Path(cfg_path))
    return config['cangjie_library'], config['cangjie_library_branch']


def set_build_log_warnings_count(cfgs, err):
    try:
        len = str(err).count('[33mwarning')
        cfg = read_config(complete_path(choose_method(cfgs)))
        if cfg.has_section("build-warning"):
            cfg.set("build-warning", 'warning', str(len))
        else:
            cfg.add_section("build-warning")
            cfg.set("build-warning", 'warning', str(len))
        cfg.write(open(choose_method(cfgs), 'w', encoding='UTF-8'))
    except:
        pass


def set_cjtest_path(args, cfgs, target):
    cfg = read_config(complete_path(choose_method(cfgs)))
    cfg.set("test", target, args.root)
    cfg.write(open(choose_method(cfgs), 'w', encoding='UTF-8'))


def get_cjtest_path(args, cfgs, target):
    cfg = read_config(complete_path(choose_method(cfgs)))
    key = '3rd_party_root' if target != 'ohos' else '3rd_party_root_ohos'
    if cfg.has_section("test"):
        return cfg.get("test", key)


def get_cangjie_path(cfgs, p):
    cfg = read_config(complete_path(choose_method(cfgs)))
    if cfg.has_section("cangjie-home"):
        return cfg.get("cangjie-home", p)
    else:
        return None


def _get_DEVECO_CANGJIE_HOME(cfgs):
    cfg = read_config(complete_path(choose_method(cfgs)))
    try:
        if cfg.has_section("cangjie-home"):
            compile_option = cfg.get("cangjie-home", "OHOS_compile_option")
            if compile_option:
                cfgs.OHOS_COMPILE_OPTION = compile_option
                return
            ohos_version = float(cfg.get("cangjie-home", "OHOS_version"))
            cfgs.OHOS_VERSION = ohos_version
            if ohos_version >= 4.0:
                temp = cfg.get("cangjie-home", "DEVECO_CANGJIE_HOME")
                os.environ["DEVECO_CANGJIE_HOME"] = temp
                cfgs.OHOS_CANGJIE_PATH = temp
            else:
                temp = cfg.get("cangjie-home", "OHOS_ROOT")
                os.environ["OHOS_ROOT"] = temp
                cfgs.OHOS_CANGJIE_PATH = temp
    except:
        cfgs.LOG.error("ohos环境设置失败.")
        return None


def set_cangjie_path(args, cfgs):
    cfg = read_config(complete_path(choose_method(cfgs)))
    if cfg.has_section("cangjie-home"):
        cfg.set("cangjie-home", 'home', args.cj_home)
    else:
        cfg.add_section("cangjie-home")
        cfg.set("cangjie-home", 'home', args.cj_home)
    cfg.write(open(choose_method(cfgs), 'w', encoding='UTF-8'))


def parser_run_config_file(run_config: Path):
    if not run_config or not run_config.exists() or not run_config.is_file():
        return None
    cfg = read_config(run_config)
    return {"shell": dict(cfg.items("shell")), "suffix": dict(cfg.items("suffix")),
            "internal_var": dict(cfg.items("internal_var"))}


def choose_method(cfgs):
    if cfgs.BUILD_TYPE == 'ci_test':
        return os.path.join(cfgs.FILE_ROOT, "ci_test.cfg")
    else:
        return os.path.join(cfgs.BASE_DIR, "ci_test.cfg")


def complete_path(path):
    """returns the canonical path of a path"""
    path = Path(path)
    if not path.exists():
        return Path(os.path.realpath(str(path)))
    return path.expanduser().resolve()


def split_and_complete_path(paths):
    """ Split the paths and returns the canonical path of each path"""
    canonicalPaths = []
    for path in paths.split(","):
        canonicalPaths.append(complete_path(path))
    return canonicalPaths


def read_config(file_path):
    if not file_path.exists() or not file_path.is_file():
        return None
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(str(file_path), encoding="utf-8")
    return config


def get_config_value(config, section, option, default=None):
    """read config value from test config"""
    try:
        return config[section][option]
    except:
        return default


def form_line(line, obj):
    for key, value in obj.items():
        end = 0
        while end < len(line):
            start = line.find("%{}".format(key), end)
            if start == -1:
                break
            end = len(key) + start + 1
            if end == len(line):
                line = line[:start] + value + line[end:]
            elif not line[end].isalnum() and line[end] != "_":
                line = line[:start] + value + line[end:]
            end = len(value) + start + 1
    return line


def filter_line(line, flag=None):
    """return the line starting with the flag"""
    if flag is None:
        return line
    line_flag = line.strip().split(":")[0].strip()
    if line_flag == flag:
        new_line = line.strip()[len(flag) + 1:].strip().lstrip(":").strip()
        return new_line
    return None


def cjlint_run(args, cfgs, out_type='json'):
    if os.path.exists("{}/report.{}".format(cfgs.HOME_DIR, out_type)):
        os.remove("{}/report.{}".format(cfgs.HOME_DIR, out_type))
        cfgs.LOG.info("删除旧文件....")
    if 'grpc' in str(cfgs.HOME_DIR):
        cmd = "cjlint -f {}/src -r {} -o {}/report".format(cfgs.HOME_DIR, out_type, cfgs.HOME_DIR)
        output = subprocess.Popen(cmd, shell=True, cwd=os.path.dirname(cfgs.HOME_DIR), stderr=PIPE, stdout=PIPE)
        log_output(output, output.args, cfgs, cfgs.HOME_DIR)
        if not os.path.exists("{}/report.{}".format(cfgs.HOME_DIR, out_type)):
            cfgs.LOG.info("check fail.")
            exit(1)
        cfgs.LOG.info("check success.")
    else:
        ohos_paths = []
        if os.path.exists(os.path.join(cfgs.HOME_DIR, "build-profile.json5")):
            ohos_json = open(os.path.join(cfgs.HOME_DIR, "build-profile.json5"), "r", encoding='UTF-8')
            for line in ohos_json.readlines():
                if line.strip().startswith('"srcPath":'):
                    ss = line.strip().replace('"srcPath":', '').strip().replace(",", "").replace('"', "")
                    ohos_paths.append(ss)
        if len(ohos_paths) > 0:
            dicts = []
            for p in ohos_paths:
                if p == './entry' or p in 'entry':
                    continue
                try:
                    os.remove(os.path.join(cfgs.HOME_DIR, "report_temp.json"))
                except OSError:
                    pass
                cmd = f"cjlint -f {p}/src/main/cangjie/src -r {out_type} -o ./report_temp"
                output = subprocess.Popen(cmd, shell=True, cwd=cfgs.HOME_DIR, stderr=PIPE, stdout=PIPE)
                log_output(output, output.args, cfgs, cfgs.HOME_DIR)
                if output.returncode != 0:
                    exit(output.returncode)
                temp_json = json.load(open(os.path.join(cfgs.HOME_DIR, "report_temp.json"), "r", encoding='UTF-8'))
                dicts.extend(temp_json)
            json.dump(dicts, open(os.path.join(cfgs.HOME_DIR, 'report.json'), 'w', encoding='UTF-8'))
        else:
            cmd = "cjlint -f ./src -r {} -o ./report".format(out_type)
            output = subprocess.Popen(cmd, shell=True, cwd=cfgs.HOME_DIR, stderr=PIPE, stdout=PIPE)
            log_output(output, output.args, cfgs, cfgs.HOME_DIR)
        if not os.path.exists("{}/report.{}".format(cfgs.HOME_DIR, out_type)):
            cfgs.LOG.info("check fail.")
            exit(1)
        cfgs.LOG.info("check success.")


def pareFile(path):
    try:
        file = open(file=path, mode="r", errors='ignore', encoding='UTF-8')
        lines = file.readlines()
        dicts = {
            "EXEC": [],
            "RESOURCES": [],
            "DEPENDENCE": []
        }
        is_unit_test = False
        for item in lines:
            exec = filter_line(item, "// EXEC")
            dep = filter_line(item, "// DEPENDENCE")
            res = filter_line(item, "// RESOURCES")
            if exec:
                if platform.system() == 'Windows' and exec[:6] == './main':
                    exec = '.\\main.exe ' + exec[6:]

                if is_unit_test and exec[:6] == './main':
                    exec += ' --no-progress'
                if 'cjc' in exec and '--test' in exec:
                    is_unit_test = True
                    exec += " --debug-macro"
                dicts.get("EXEC").append(exec)
            if dep:
                for item2 in dep.split(" "):
                    if item2.strip():
                        dicts.get("DEPENDENCE").append(item2)
            if res:
                for item2 in res.split(" "):
                    if item2.strip():
                        dicts.get("RESOURCES").append(item2)
        else:
            file.close()
            return dicts
    except UnicodeDecodeError:
        return None


LIBS = []
TOTAL_CASES = 0
COUNT_CURRENT_CASE = 0

error_set = set()


class ProcessLogger(threading.Thread):
    def __init__(self, stream, logger, prefix=""):
        super().__init__(daemon=True)
        self.stream = stream
        self.logger = logger
        self.prefix = prefix
        self._stop_event = threading.Event()
        self.daemon = True  # 设为守护线程，主进程退出时自动终止

    def run(self):
        try:
            # 读取流时捕获可能的异常
            while not self._stop_event.is_set():
                if self.stream.closed:
                    break
                line = self.stream.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                if not llt_check_not_start_or_end_with_target(line):
                    msg = line.decode('UTF-8', 'ignore').rstrip()
                    msg = msg.replace('\xa0', ' ').replace('\u00a0', ' ')
                    msg = ''.join(char if ord(char) < 0x10000 else '?' for char in msg)
                    self.logger.info(msg)
        except ValueError as e:
            if "info->buf must not be NULL" in str(e):
                pass
            else:
                raise
        except Exception as e:
            print(f"读取流时发生错误: {e}")
        finally:
            # 确保流被关闭
            if not self.stream.closed:
                self.stream.close()

    def stop(self):
        self._stop_event.set()


def __log_output(output, cmd, cfgs, filename=None):
    """ log command output"""
    cfgs.LOG.info("CMD    : %s", cfgs._simplify_cmd(str(cmd)))
    stdout, stderr = output.communicate()
    encode = cfgs.ENCODING
    error = stderr.decode(encode, "ignore").strip()
    out = stdout.decode(encode, "ignore").strip()
    if error:
        for item in re.split("\r?\n", error):
            item = re.sub(r"\x1b\[\d+m", "", item)
            if output.returncode == 0:
                if cfgs.SHOW_WARN:
                    cfgs.LOG.warn(item)
            else:
                cfgs.LOG.error(item)
    if out:
        for item in re.split("\r?\n", out):
            cfgs.LOG.info(re.sub(r"\x1b\[\d+m", "", item))
    return out, error


def log_output(proc, cmd, cfgs, filename=None):
    """ log command output"""
    cfgs.LOG.info("CMD    : %s", cfgs._simplify_cmd(str(cmd)))
    stdout_logger = ProcessLogger(proc.stdout, cfgs.LOG, "")
    stderr_logger = ProcessLogger(proc.stderr, cfgs.LOG, "")
    stdout_logger.start()
    stderr_logger.start()
    try:
        while proc.poll() is None:
            time.sleep(0.1)
        proc.wait()

        stdout, stderr = proc.communicate()
        return stdout.decode(cfgs.ENCODING, "ignore").strip(), stderr.decode(cfgs.ENCODING, "ignore").strip()
    finally:
        stdout_logger.stop()
        stderr_logger.stop()
        stdout_logger.join()
        stderr_logger.join()


def runAll(args, cfgs):
    subcmd = ""
    try:
        if args.coverage:
            subcmd = " --coverage"
    except:
        subcmd = ""
    cj_build_libs = find_lib_path(cfgs.LIB_DIR, '')
    find_cangjie_lib_arr = []
    for build_lib in cj_build_libs:
        cfgs.IMPORT_PATH += f" --import-path {build_lib}"
        for build_lib_item in os.listdir(build_lib):
            if build_lib_item.__contains__(".") or 'bin' in build_lib_item:
                continue
            if os.path.exists(os.path.join(build_lib, build_lib_item)):
                cfgs.LIBRARY_PATH += f" -L {os.path.join(build_lib, build_lib_item)}"
                find_cangjie_lib_arr.append(os.path.join(build_lib, build_lib_item))
                cangjie_env_setup(find_cangjie_lib_arr)
    if cfgs.CANGJIE_STDX_DIR:
        cfgs.IMPORT_PATH += f" --import-path {Path(cfgs.CANGJIE_STDX_DIR).parent}"
        cfgs.LIBRARY_PATH += f" -L {cfgs.CANGJIE_STDX_DIR}"
        __improt_stdx_libs([cfgs.CANGJIE_STDX_DIR], cfgs, args)
    __improt_libs(find_cangjie_lib_arr, cfgs)
    loop_dir(args, cfgs, lambda file: runOne(args, file, subcmd, cfgs))


def runOne(args, file, subcmd, cfgs):
    global RESULT
    path = Path(file)
    ci_lib_const = "ci_lib"
    ci_lib_dir = os.path.join(cfgs.HOME_DIR, ci_lib_const)
    global TOTAL_CASES, COUNT_CURRENT_CASE
    if path.is_file():
        name = (path.name + "_").split(".")
        name = "_".join(name)
        runPath = os.path.join(cfgs.temp_dir, name)
        lineDict = pareFile(file)
        if not lineDict:
            cfgs.LOG.warn("无法解析文件： {}".format(str(file)))
            return
        exec = lineDict.get("EXEC")
        copy = lineDict.get("DEPENDENCE")
        resources = lineDict.get("RESOURCES")
        copy.append(path.name)
        if cfgs.OS_PLATFORM and cfgs.OS_PLATFORM == "windows":
            copy_windows_lib(cfgs)
        if len(exec):
            create_file(runPath)
            source_file_path = os.path.join(cfgs.HOME_DIR, "test", "resources")
            os.environ['cangjie_test_path'] = str(source_file_path)
            for item in copy:
                try:
                    copy_path = path.parent
                    items = item.split('/')
                    for sps in items:
                        copy_path = os.path.join(copy_path, sps)
                    if os.path.isdir(copy_path):
                        shutil.copytree(copy_path, os.path.join(runPath, items[-1]))
                        shutil.copymode(copy_path, os.path.join(runPath, items[-1]))
                    else:
                        shutil.copyfile(copy_path, os.path.join(runPath, items[-1]))
                        shutil.copymode(copy_path, os.path.join(runPath, items[-1]))
                finally:
                    pass
            if cfgs.OS_PLATFORM and cfgs.OS_PLATFORM == "windows":
                if cfgs.WINDOWS_DLLS:
                    for dll in cfgs.WINDOWS_DLLS:
                        dll_name = dll.split(os.path.sep)[-1]
                        shutil.copyfile(dll, os.path.join(runPath, dll_name))
                if len(cfgs.WINDOWS_C_LIB_ARR) > 0:
                    for dll in cfgs.WINDOWS_C_LIB_ARR:
                        dll_name = dll.split(os.path.sep)[-1]
                        shutil.copyfile(dll, os.path.join(runPath, dll_name))
            for resource in resources:
                try:
                    copy_path = source_file_path
                    resource_split = resource.split('/')
                    for sps in resource_split:
                        copy_path = os.path.join(copy_path, sps)
                    shutil.copyfile(copy_path, os.path.join(runPath, resource_split[-1]))
                except Exception as e:
                    cfgs.LOG.warn(f"无法复制文件： {copy_path}, 异常：{e}")
            else:
                case_one_return_code = 0
                # 根据 import 语句过滤需要链接的库（合并源码和测试用例的 import）
                import_pkgs = parse_imports_from_case(file)
                import_pkgs |= parse_imports_from_source(cfgs.HOME_DIR)
                filtered_library = filter_library_by_imports(cfgs.LIBRARY, import_pkgs, cfgs.HOME_DIR)
                executed_cmds = set()  # 记录已执行的命令，避免重复执行和日志重复打印
                for item in exec:
                    cmd_temp = item
                    cmd = form_line(item, {"import-path": cfgs.IMPORT_PATH})
                    cmd = form_line(cmd, {"L": cfgs.LIBRARY_PATH})
                    cmd = form_line(cmd, {"l": filtered_library})
                    cmd = form_line(cmd, {"project-path": "--import-path {}".format(cfgs.HOME_DIR)})
                    cmd = form_line(cmd, {"project-L": "-L {}".format(cfgs.HOME_DIR)})
                    cmd = form_line(cmd, {"project": "{}".format(cfgs.HOME_DIR)})
                    if str(cmd) == str(cmd_temp):
                        sub_lib_cmd = get_library_cmd(cfgs, ci_lib_dir, ci_lib_const)
                        cmd = form_line(cmd, {"f": path.name + sub_lib_cmd})
                    else:
                        cmd = form_line(cmd, {"f": path.name})
                    if "cjc" in cmd:
                        cmd = cmd + subcmd + args.optimize + cfgs.Woff
                    # 去重：form_line 替换后相同的命令只执行一次
                    if cmd in executed_cmds:
                        continue
                    executed_cmds.add(cmd)
                    output = subprocess.Popen(cmd, shell=True, cwd=runPath, stderr=subprocess.PIPE,
                                              stdout=subprocess.PIPE)
                    if platform.system() == 'Windows' and cmd != '.\\main.exe':
                        subprocess.Popen("cp {}/* {}".format(cfgs.LIB_DIR, runPath),
                                         shell=True, cwd=runPath, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
                    out, err = log_output(output, output.args, cfgs, path.name)

                    if output.returncode != 0:
                        case_one_return_code = output.returncode
                    elif "Summary: TOTAL:" in str(out):
                        out = re.sub(r"\x1b\[\d+m", "", str(out))
                        error = int(
                            str(out).split("Summary: TOTAL:")[1].split("ERROR: ")[1].split(cfgs.LINE_SEPARATOR)[0])
                        try:
                            failed = int(out.split("Summary: TOTAL:")[1].split("FAILED: ")[1].split(",")[0])
                        except ValueError:
                            failed = 0
                        if failed > 0 or error > 0:
                            case_one_return_code = 1
                else:
                    # remove runPath
                    if args.clean:
                        shutil.rmtree(runPath)
                    COUNT_CURRENT_CASE += 1
                    cfgs.LOG.info("return : %s", str(case_one_return_code))
                    cfgs.LOG.info(" >>=============================================<<当前进度{:.2f}% ".format(
                        float(COUNT_CURRENT_CASE) / float(TOTAL_CASES) * 100))
                    cfgs.LOG.info("")
                    if case_one_return_code != 0:
                        RESULT.get("FAIL").append(str(path))
                    else:
                        RESULT.get("PASS").append(str(path))


def __improt_libs(libsdir, cfgs=None):
    """
    递归查找指定目录下所有平台的库文件，移除 'lib' 前缀和文件后缀，返回唯一文件名集合

    支持平台：
      - Linux: .a (静态), .so (动态)
      - macOS: .a (静态), .dylib (动态)
      - Windows: .lib (静态), .dll (动态)

    处理逻辑：
      1. 识别库文件（扩展名匹配）
      2. 移除文件名开头的 'lib'（不区分大小写）
      3. 移除文件后缀（如 .a, .so 等）
      4. 统一转为小写（避免大小写差异）

    示例:
        "libfoo.a" → "foo"
        "LIBBAR.SO" → "bar"
        "foo.dll" → "foo"
        "baz.dylib" → "baz"
    """
    # 定义所有支持的库扩展名（忽略大小写）
    LIB_EXTENSIONS: set[str] = {'.a', '.so', '.dylib', '.lib', '.dll'}
    # 递归遍历所有库文件
    LLT_Link_libs = set()
    str = ""
    for sub_lib in libsdir:
        # 验证输入目录
        root_path = pathlib.Path(sub_lib).resolve()
        if not root_path.exists():
            raise FileNotFoundError(f"Directory not found: {sub_lib}")
        if not root_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {sub_lib}")

        for dirpath, _, filenames in os.walk(root_path):
            for filename in filenames:
                # 检查扩展名（忽略大小写）
                ext = pathlib.Path(filename).suffix.lower()
                if ext not in LIB_EXTENSIONS:
                    continue

                # 1. 移除文件后缀
                base_name = os.path.splitext(filename)[0]

                # 2. 移除开头的 'lib'（不区分大小写）
                if base_name.lower().startswith('lib'):
                    base_name = base_name[3:]  # 移除前3个字符 'lib'

                # 3. 统一转为小写（避免大小写差异）
                LLT_Link_libs.add(base_name)

    if cfgs and len(cfgs.LIBRARY_PRIORITY) > 0:
        for ss in LLT_Link_libs:
            if not cfgs.LIBRARY_PRIORITY.__contains__(ss):
                cfgs.LIBRARY_PRIORITY.append(ss)
        LLT_Link_libs = cfgs.LIBRARY_PRIORITY
    for ss in LLT_Link_libs:
        cfgs.LIBRARY += "-l {} ".format(ss)
        str += "-l {} ".format(ss)
    return str


def __improt_stdx_libs(libsdir, cfgs=None, args=None, is_recursion=True):
    LLT_Link_libs = set()
    for sub_lib in libsdir:
        if is_recursion:
            for path, _, libs in os.walk(sub_lib):
                for lib in libs:
                    if lib.startswith("lib") and lib.endswith(".so"):
                        LLT_Link_libs.add(lib[3:len(lib) - 3])
                    elif lib.startswith("lib") and lib.endswith(".a"):
                        LLT_Link_libs.add(lib[3:len(lib) - 2])
                    elif platform.system() == 'Windows' and lib.startswith("lib") and lib.endswith(".dll"):
                        LLT_Link_libs.add(lib[3:len(lib) - 4])
                    elif platform.system() == 'Darwin' and lib.startswith("lib") and lib.endswith(".dylib"):
                        LLT_Link_libs.add(lib[3:len(lib) - 6])
        else:
            for so in glob.glob(os.path.join(sub_lib, "lib*.dll")):
                so = os.path.basename(so)
                LLT_Link_libs.add(so[3:len(so) - 4])
            for so in glob.glob(os.path.join(sub_lib, "lib*.so")):
                so = os.path.basename(so)
                LLT_Link_libs.add(so[3:len(so) - 3])
            for so in glob.glob(os.path.join(sub_lib, "lib*.a")):
                so = os.path.basename(so)
                LLT_Link_libs.add(so[3:len(so) - 2])
            for so in glob.glob(os.path.join(sub_lib, "lib*.dylib")):
                so = os.path.basename(so)
                LLT_Link_libs.add(so[3:len(so) - 6])
    if cfgs and len(cfgs.LIBRARY_PRIORITY) > 0:
        for ss in LLT_Link_libs:
            if not cfgs.LIBRARY_PRIORITY.__contains__(ss):
                cfgs.LIBRARY_PRIORITY.append(ss)
        LLT_Link_libs = cfgs.LIBRARY_PRIORITY
    for ss in LLT_Link_libs:
        if str(ss).__contains__('aspects'):  # 这里 1.0.3 测试报错. 先屏蔽这个链接
            continue
        if not args.fuzz:
            if "stdx.fuzz.fuzz" not in ss:
                cfgs.LIBRARY += "-l {} ".format(ss)
        else:
            cfgs.LIBRARY += "-l {} ".format(ss)


def loop_dir(args, cfgs, callBack):
    currentDirectory = cfgs.TEST_DIR
    global TOTAL_CASES
    if args.path:
        if os.path.abspath(args.path).startswith(currentDirectory):
            currentDirectory = args.path
        else:
            cfgs.LOG.error("指定测试文件夹不是当前工程的子文件夹. 请重试")
            exit(1)
    for path, dirs, files in os.walk(currentDirectory):
        for item in files:
            if Path(item).suffix == ".cj":
                TOTAL_CASES += 1
    for path, dirs, files in os.walk(currentDirectory):
        for item in files:
            if Path(item).suffix == ".cj":
                if args.case:
                    if args.case.endswith(".cj"):
                        if args.case == str(item):
                            callBack(os.path.join(path, item))
                    else:
                        if "{}.cj".format(args.case) == str(item):
                            callBack(os.path.join(path, item))
                else:
                    callBack(os.path.join(path, item))


SRC_FILES = ""


def ut_result(out, err):
    arr = [-1, -1, -1, -1, -1]
    resu = str(out).split("TOTAL")[1].split("\\n")
    arr[0] = resu[0].split(": ")[1]
    res = resu[1].split(",")
    for item in res:
        if "PASSED" in item:
            arr[1] = item.split(": ")[1]
        elif "SKIPPED" in item:
            arr[2] = item.split(": ")[1]
        elif "ERROR" in item:
            arr[3] = item.split(": ")[1]
    if "FAILED" in resu[2]:
        arr[4] = resu[2].split(": ")[1].split(",")[0]
    if int(arr[3]) > 0 or int(arr[4]) > 0:
        RESULT.get("FAIL").append(str(arr[3]))
        RESULT.get("FAIL").append(str(arr[4]))
        raise Exception()


def create_file(path):
    if not os.path.exists(path):
        os.makedirs(path)


def get_library_cmd(cfgs, ci_lib_dir, ci_lib_const):
    my_libs_cmd = ""
    my_lib_dir = os.path.join(cfgs.HOME_DIR, ci_lib_const)
    if platform.system() == "Windows":
        dynamic_lib = ".dll"
        static_lib = ".lib"
    elif platform.system() == "Darwin":
        dynamic_lib = ".dylib"
        static_lib = ".a"
    else:
        dynamic_lib = ".so"
        static_lib = ".a"
    if os.path.exists(ci_lib_dir):
        for sub_lib_dir in os.listdir(ci_lib_dir):
            global LIB_DIR
            temp_lib_dir = os.path.join(ci_lib_dir, str(sub_lib_dir))
            if not Path(temp_lib_dir).is_file():
                LIB_DIR = temp_lib_dir
                my_libs = []
                for lib in os.listdir(LIB_DIR):
                    if lib.startswith("lib") and lib.endswith(dynamic_lib):
                        suffix_len = len(dynamic_lib)
                        aa = lib[3:len(lib) - suffix_len]
                        my_libs.append(aa)
                    elif lib.startswith("lib") and lib.endswith(static_lib):
                        suffix_len = len(static_lib)
                        aa = lib[3:len(lib) - suffix_len]
                        my_libs.append(aa)
                for ss in my_libs:
                    my_libs_cmd = my_libs_cmd + "-l {} ".format(ss)

    return " --import-path {} -L {} {}".format(my_lib_dir, my_lib_dir, my_libs_cmd)


def UT_test(args, cfgs):
    try:
        if args.back:
            return
        shutil.copytree(cfgs.CANGJIE_SOURCE_DIR, os.path.join(cfgs.CI_TEST_DIR, 'src'), dirs_exist_ok=True)
        shutil.copytree(cfgs.UT_TEST_DIR, cfgs.CANGJIE_SOURCE_DIR, dirs_exist_ok=True)
        if args.merage:
            return
        cjpm_test_cmd = 'cjpm test '
        if args.no_run:
            cjpm_test_cmd += "--no-run "
        if args.skip_build:
            cjpm_test_cmd += "--skip-build "
        if args.jobs:
            cjpm_test_cmd += f"--jobs={args.jobs} "
        if args.verbose:
            cjpm_test_cmd += "--verbose "
        if args.g:
            cjpm_test_cmd += "-g "
        if args.bench:
            cjpm_test_cmd += "--bench "
        if args.coverage:
            cjpm_test_cmd += "--coverage "
        if args.condition:
            cjpm_test_cmd += f"--condition={args.condition} "
        if args.target:
            cjpm_test_cmd += f"--target={args.target} "
        if args.filter:
            cjpm_test_cmd += f"--filter={args.filter} "
        if args.random_seed:
            cjpm_test_cmd += f"--random-seed={args.random_seed} "
        if args.no_color:
            cjpm_test_cmd += "--no-color "
        if args.isolate_all:
            cjpm_test_cmd += "--isolate_all "
        if args.isolate_all_timeout:
            cjpm_test_cmd += "--isolate-all-timeout "

        cfgs.LOG.info(cjpm_test_cmd)
        return_code = cfgs.run_cmd(cjpm_test_cmd, file_dir=cfgs.HOME_DIR)
        exit(return_code)
    finally:
        if not args.merage:
            shutil.rmtree(cfgs.CANGJIE_SOURCE_DIR, ignore_errors=True)
            shutil.copytree(os.path.join(cfgs.CI_TEST_DIR, 'src'), cfgs.CANGJIE_SOURCE_DIR)


RESULT = {
    "FAIL": [],
    "PASS": []
}


def end_build(cfgs):
    cfgs.LOG.info(f"")
    for item in RESULT.get("FAIL"):
        cfgs.LOG.info(f"CASE: {item}, Result: FAIL")
    a = len(RESULT.get("FAIL"))
    b = len(RESULT.get("PASS"))
    cfgs.LOG.info("")
    cfgs.LOG.info("  TestSuiteTask: Total: {}, PASS: {}, FAIL: {}, Ratio  : {}%".format(str(a + b), str(b), str(a),
                                                                                        round(b / (a + b) * 100, 2) if (
                                                                                                                               a + b) > 0 else 0))
    if a:
        exit(1)


class Logger:
    def __init__(self, cfgs, log_path=None):
        self.cfgs = cfgs
        self.log_path = log_path
        if log_path:
            log_dir = os.path.dirname(log_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            file_name = log_path
            split_log_dir = os.path.join(log_dir, "split_log") if log_dir else "split_log"
        else:
            shutil.rmtree(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log"), ignore_errors=True)
            os.makedirs(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log", "split_log"), exist_ok=True)
            file_name = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log", "all.log")
            split_log_dir = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log", "split_log")
        self.split_log_dir = split_log_dir
        self.logger = cfgs.LOG
        self.logger.setLevel(logging.DEBUG)
        self.fmt = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%m-%d %H:%M:%S")
        self.sh = logging.StreamHandler()
        self.sh.setFormatter(self.fmt)
        self.th = handlers.TimedRotatingFileHandler(filename=file_name, encoding="utf-8", when="D")
        self.th.setFormatter(self.fmt)
        self.case_th = None
        # self.logger.addHandler(self.sh)
        self.logger.addHandler(self.th)

    def info(self, msg):
        self.logger.info(msg.encode('gbk', 'ignore').decode('gbk'))

    def debug(self, msg):
        self.logger.debug(msg.encode('gbk', 'ignore').decode('gbk'))

    def error(self, msg):
        self.logger.error(msg.encode('gbk', 'ignore').decode('gbk'))

    def warning(self, msg):
        self.logger.warning(msg.encode('gbk', 'ignore').decode('gbk'))

    def setStream(self, file_name):
        dirs = file_name.split(os.sep)
        file_name = ".".join(dirs) if dirs[0] != "" else file_name
        log_file_name = os.path.join(self.split_log_dir, file_name)
        if not os.path.exists(os.path.dirname(log_file_name)):
            os.makedirs(os.path.dirname(log_file_name))
            # os.makedirs(os.path.)
        if self.case_th is None:
            self.case_th = handlers.TimedRotatingFileHandler(filename=log_file_name, encoding="utf-8", when="D")
            self.case_th.setFormatter(self.fmt)
            self.logger.addHandler(self.case_th)
        if sys.version_info >= (3, 7):
            self.case_th.setStream(open(log_file_name, "a", encoding="utf-8"))
        else:
            self.case_th.stream = open(log_file_name, "a", encoding="utf-8")


# cfgs.CJ_TEST_WORK = ""

cp = configparser.ConfigParser()
cp.read(os.path.join(os.path.dirname(__file__), "ci_test.cfg"))
_3rd_party_root = ""

platform_str = sys.platform
ohos_dir = "/data/3rd/"
error_count = 0
total_count = 0
error_list = []


def get_cmd_info(file_name, target, cfgs):
    macro_cmd = ""
    dependence = []

    run_option = ""
    is_valid_case = False
    with open(file_name, "r", encoding="utf-8") as f:
        case_dir = os.path.dirname(file_name)
        try:
            for line in f.readlines():
                if line.startswith("// HLT"):
                    is_valid_case = True
                    break
                elif "3rd_party_lib:" in line:
                    is_valid_case = True
                elif "macro-lib:" in line:
                    if platform_str == "win32":
                        line = line.replace(".so", ".dll").replace("/", "\\")
                    is_valid_case = True
                    line = line.replace("\n", "").replace(" ", "")
                    marco_lib_str = line[line.index("macro-lib:") + 10:]
                    marco_libs_tmp = marco_lib_str.split(":")
                    marco_libs = []
                    for lib in marco_libs_tmp:
                        lib_path = os.path.join(_3rd_party_root, lib)
                        dirs = lib.split(os.sep)
                        dirs[0] = "source"  # for ci
                        ci_lib_path = os.path.join(_3rd_party_root, *dirs)
                        marco_libs = find_lib_path(lib_path, ci_lib_path)
                    for lib in marco_libs:
                        macro_cmd += f"{lib} "
                elif "dependence:" in line:
                    is_valid_case = True
                    temp = " " + line[line.index("dependence:") + 11:].replace("\n", "").strip()
                    temp = temp.replace(":", " ").strip()
                    if case_dir != "":
                        for dep in temp.split(" "):
                            if dep:
                                dependence.append(os.path.join(case_dir, dep))
                elif "data_file:" in line:
                    is_valid_case = True
                    line = line.replace("\n", "").replace(" ", "")
                    data_file_str = line[line.index("data_file:") + 10:]
                    logger.info(f"data_file Start to copy data files")
                    if target != "ohos":
                        for data in data_file_str.split(":"):
                            src = os.path.join(os.path.dirname(file_name), data)
                            dst = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "tmp",
                                               os.path.dirname(file_name).replace(cfgs.HOME_DIR,
                                                                                  "."), data)
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            logger.info(f"copy {src} to {dst}")
                            if os.path.isdir(src):
                                try:
                                    shutil.copytree(src, dst)
                                except:
                                    try:
                                        os.rmdir(dst)
                                        shutil.copytree(src, dst)
                                    except:
                                        logger.info(f"file existed")
                            else:
                                shutil.copy(src, dst)
                    else:
                        for data in data_file_str.split(":"):
                            src = os.path.join(os.path.dirname(file_name), data)
                            if os.path.exists(src):
                                dst = os.path.dirname(os.path.join(ohos_dir, data))
                                logger.info(f"hdc shell mkdir -p {dst}")
                                cfgs.run_cmd(f"hdc shell mkdir -p {dst}")
                                logger.info(f"hdc file send {src.replace('/', os.path.sep)} {dst}")
                                cfgs.run_cmd(f"hdc file send {src.replace('/', os.path.sep)} {dst}")
                elif "sources_file:" in line:
                    is_valid_case = True
                    line = line.replace("\n", "").replace(" ", "")
                    sources_file_str = line[line.index("sources_file:") + 12:]
                    logger.info(f"[{file_name}]: sources_file Start to copy sources files")
                    if target != "ohos":
                        for data in sources_file_str.split(":"):
                            if not len(data) == 0:
                                src = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "resources", data)
                                dst = os.path.join(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "tmp",
                                                                os.path.dirname(file_name).replace(cfgs.HOME_DIR,
                                                                                                   ".")), data)
                                logger.info(f"copy {src} to {dst}")
                                os.makedirs(os.path.dirname(dst), exist_ok=True)
                                if os.path.isdir(src):
                                    shutil.copytree(src, dst)
                                else:
                                    shutil.copy(src, dst)
                    else:
                        for data in sources_file_str.split(":"):
                            if not len(data) == 0:
                                src = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "resources", data)
                                dst = os.path.dirname(os.path.join(ohos_dir, data))
                                logger.info(f"hdc shell mkdir -p {dst}")
                                cfgs.run_cmd(f"hdc shell mkdir -p {dst}")
                                logger.info(f"hdc file send {src.replace('/', os.path.sep)} {dst}")
                                cfgs.run_cmd(f"hdc file send {src.replace('/', os.path.sep)} {dst}")
                elif "run_option:" in line:
                    is_valid_case = True
                    run_option += line[line.index('run_option:') + 11:].replace(":", " ")
        except UnicodeDecodeError:
            logger.error("'utf-8' codec can't decode byte 0xff in position 0: invalid start byte:>>" + file_name)

    macro_cmd = f"--macro-lib=\"{macro_cmd}\"" if macro_cmd != "" else ""

    return run_option, " ".join(dependence), macro_cmd, is_valid_case


def collect_test_result(file_path, output, return_code, run_time):
    """
    收集测试用例的结果到全局报告字典中
    解析测试框架的完整输出，统计所有用例
    """
    global test_report
    global logger

    # 从文件路径中提取测试类名
    file_name = os.path.basename(file_path)
    class_name = os.path.splitext(file_name)[0]

    # 解析测试框架的完整输出
    total_cases = 0
    passed_cases = 0
    failed_cases = 0
    error_cases = 0
    skipped_cases = 0

    # 去除ANSI转义序列
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    clean_output = ansi_escape.sub('', output)

    logger.info(f"[DEBUG] collect_test_result called for {file_name}")
    logger.info(f"[DEBUG] output length: {len(output)} chars")
    logger.info(f"[DEBUG] clean_output length: {len(clean_output)} chars")

    # 从测试框架摘要中提取统计信息（使用clean_output）
    for line in clean_output.split('\n'):
        # 提取总数
        total_match = re.search(r"TOTAL:\s*(\d+)", line)
        if total_match:
            total_cases = int(total_match.group(1))

        # 提取通过数
        passed_match = re.search(r"PASSED:\s*(\d+)", line)
        if passed_match:
            passed_cases = int(passed_match.group(1))

        # 提取跳过数
        skipped_match = re.search(r"SKIPPED:\s*(\d+)", line)
        if skipped_match:
            skipped_cases = int(skipped_match.group(1))

        # 提取错误数
        error_match = re.search(r"ERROR:\s*(\d+)", line)
        if error_match:
            error_cases = int(error_match.group(1))

        # 提取失败数
        failed_match = re.search(r"FAILED:\s*(\d+)", line)
        if failed_match:
            failed_cases = int(failed_match.group(1))

    logger.info(
        f"[DEBUG] Parsed values - total: {total_cases}, passed: {passed_cases}, failed: {failed_cases}, error: {error_cases}, skipped: {skipped_cases}")

    # 如果没有找到摘要信息，使用单个用例的逻辑
    if total_cases == 0 and passed_cases == 0 and failed_cases == 0 and error_cases == 0 and skipped_cases == 0:

        # 初始化默认值
        status = "UNKNOWN"
        error_trace = "-"

        # 检查测试框架的输出结果
        if "FAILED:" in output and "FAILED: 0" not in output:
            status = "FAILED"
            error_trace = output if output else "-"
        elif "ERROR:" in output and "ERROR: 0" not in output:
            status = "ERROR"
            error_trace = output if output else "-"
        elif "SKIPPED:" in output and "SKIPPED: 0" not in output:
            status = "SKIPPED"
            error_trace = output if output else "-"
        elif "PASSED:" in output and "PASSED: 0" not in output:
            status = "PASSED"
            error_trace = "-"
        elif return_code != 0:
            status = "FAILED"
            error_trace = output if output else f"Exit code: {return_code}"
        else:
            # 如果没有明确的输出信息且返回码为0，认为是通过
            status = "PASSED"
            error_trace = "-"

        # 更新全局报告
        test_report["total_cases"] += 1

        if status == "PASSED":
            test_report["passed_cases"] += 1
        elif status == "FAILED":
            test_report["failed_cases"] += 1
        elif status == "ERROR":
            test_report["error_cases"] += 1
        elif status == "SKIPPED":
            test_report["skipped_cases"] += 1

        # 按测试类组织用例结果
        if class_name not in test_report["cases"]:
            test_report["cases"][class_name] = []
            test_report["class_times"][class_name] = 0.0

        test_report["cases"][class_name].append([file_name, status, run_time, error_trace, 0.0, file_path])
        test_report["class_times"][class_name] += run_time
    else:
        # 使用测试框架的完整统计信息
        test_report["total_cases"] += total_cases
        test_report["passed_cases"] += passed_cases
        test_report["failed_cases"] += failed_cases
        test_report["error_cases"] += error_cases
        test_report["skipped_cases"] += skipped_cases

        # 为每个用例创建记录
        if class_name not in test_report["cases"]:
            test_report["cases"][class_name] = []
            test_report["class_times"][class_name] = 0.0

        # 解析具体的用例结果
        for line in output.split('\n'):
            if "[ PASSED ] CASE:" in line:
                case_match = re.search(r"\[ PASSED \] CASE:\s*(\w+)", line)
                if case_match:
                    case_name = case_match.group(1)
                    test_report["cases"][class_name].append(
                        [case_name, "PASSED", run_time / total_cases, "", 0.0, file_path])
            elif "[ FAILED ] CASE:" in line:
                case_match = re.search(r"\[ FAILED \] CASE:\s*(\w+)", line)
                if case_match:
                    case_name = case_match.group(1)
                    test_report["cases"][class_name].append(
                        [case_name, "FAILED", run_time / total_cases, "Failed", 0.0, file_path])
            elif "[ ERROR ] CASE:" in line:
                case_match = re.search(r"\[ ERROR \] CASE:\s*(\w+)", line)
                if case_match:
                    case_name = case_match.group(1)
                    test_report["cases"][class_name].append(
                        [case_name, "ERROR", run_time / total_cases, "Error", 0.0, file_path])
            elif "[ SKIPPED ] CASE:" in line:
                case_match = re.search(r"\[ SKIPPED \] CASE:\s*(\w+)", line)
                if case_match:
                    case_name = case_match.group(1)
                    test_report["cases"][class_name].append(
                        [case_name, "SKIPPED", run_time / total_cases, "Skipped", 0.0, file_path])

        test_report["class_times"][class_name] += run_time

    return "PASSED" if passed_cases == total_cases and failed_cases == 0 and error_cases == 0 else "FAILED"


def __parse_import_line(line):
    """解析单行 import 语句，提取包名
    支持的语法形式：
      import stdx.encoding.json.*              -> stdx.encoding.json
      import stdx.encoding.json.{Class}        -> stdx.encoding.json
      import stdx.encoding.json                -> stdx.encoding.json
      import stdx.crypto.crypto.SecureRandom   -> stdx.crypto.crypto
      protected import stdx.crypto.crypto.*     -> stdx.crypto.crypto
    """
    line = line.strip()
    # 去掉修饰词（protected/open 等）
    for modifier in ("protected ", "open ", "public ", "private ", "internal "):
        if line.startswith(modifier):
            line = line[len(modifier):].strip()
            break
    if not line.startswith("import "):
        return None
    pkg = line[len("import "):].strip().rstrip(";").strip()
    # 去掉 .* 后缀
    if pkg.endswith(".*"):
        pkg = pkg[:-2]
    # 去掉 .{xxx} 后缀
    elif "." in pkg and pkg[-1] == "}":
        idx = pkg.rfind(".{")
        if idx > 0:
            pkg = pkg[:idx]
    # 去掉直接类名（如 import stdx.crypto.crypto.SecureRandom -> stdx.crypto.crypto）
    # 类名首字母大写，包名首字母小写
    parts = pkg.split(".")
    if len(parts) > 1 and parts[-1][0].isupper():
        pkg = ".".join(parts[:-1])
    return pkg


def parse_imports_from_case(file_path):
    """解析测试用例文件中的 import 语句，返回导入的包名集合"""
    import_pkgs = set()
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                pkg = __parse_import_line(line)
                if pkg:
                    import_pkgs.add(pkg)
    except Exception:
        pass
    return import_pkgs


def parse_imports_from_source(home_dir):
    """扫描项目源码目录中的所有 .cj 文件，收集 import 语句，返回导入的包名集合"""
    import_pkgs = set()
    src_dir = os.path.join(home_dir, "src")
    if not os.path.isdir(src_dir):
        return import_pkgs
    try:
        for root, _, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".cj"):
                    continue
                file_path = os.path.join(root, f)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            pkg = __parse_import_line(line)
                            if pkg:
                                import_pkgs.add(pkg)
                except Exception:
                    continue
    except Exception:
        pass
    return import_pkgs


def load_dep_cache(home_dir):
    """加载 .dep-cache 文件，返回依赖映射字典"""
    dep_cache_path = os.path.join(home_dir, "target", ".dep-cache")
    if not os.path.exists(dep_cache_path):
        return None

    try:
        with open(dep_cache_path, "r", encoding="utf-8") as f:
            dep_cache = json.load(f)
        return dep_cache
    except Exception as e:
        return None


def get_transitive_dependencies(dep_cache, package_name, visited=None):
    """递归获取包的所有传递依赖（包括隐式依赖）

    Args:
        dep_cache: .dep-cache 文件内容
        package_name: 包名，如 "stdx.encoding.json"
        visited: 已访问的包集合，防止循环依赖

    Returns:
        所有依赖包名的集合（包括传递依赖）
    """
    if visited is None:
        visited = set()

    if package_name in visited:
        return set()

    visited.add(package_name)
    all_deps = set()

    if not dep_cache:
        return all_deps

    # 查找匹配的条目（包括 "bin" 和 "pkg-build" 部分）
    sections = ["bin", "pkg-build"]
    for section in sections:
        if section not in dep_cache:
            continue

        for cjo_path, cjo_info in dep_cache[section].items():
            if cjo_info.get("package") == package_name:
                # 获取直接依赖
                dependencies = cjo_info.get("dependencies", [])
                for dep in dependencies:
                    dep_pkg = dep.get("package", "")
                    if dep_pkg and not dep.get("isStd", False):
                        all_deps.add(dep_pkg)
                        # 递归获取传递依赖
                        transitive_deps = get_transitive_dependencies(dep_cache, dep_pkg, visited)
                        all_deps.update(transitive_deps)
                break

    return all_deps


def filter_library_by_imports(library_str, import_pkgs, home_dir=None):
    """根据 import 语句过滤 -l 库列表，只保留匹配的 stdx 库和非 stdx 库

    Args:
        library_str: 库字符串，如 "-l stdx.encoding.json -l boundscheck"
        import_pkgs: import 的包名集合
        home_dir: 项目根目录，用于查找 .dep-cache 文件

    Returns:
        过滤后的库字符串
    """
    if not import_pkgs:
        return library_str

    # 加载依赖缓存
    dep_cache = None
    if home_dir:
        dep_cache = load_dep_cache(home_dir)

    libs = library_str.split("-l ")
    filtered = []
    seen = set()  # 去重
    implicit_deps = set()  # 收集隐式依赖

    # 第一遍：收集所有匹配的库和它们的隐式依赖
    for lib in libs:
        lib = lib.strip()
        if not lib:
            continue

        # 判断是否应该包含这个库
        should_include = False

        # 非 stdx 库始终保留（如 boundscheck, asn1, cangjie-dynamicLoader-opensslFFI, httpclient4cj 等）
        if not lib.startswith("stdx."):
            should_include = True
        else:
            # stdx 库：检查是否匹配任何 import 的前缀
            for pkg in import_pkgs:
                # import stdx.encoding.json -> 匹配 stdx.encoding.json, stdx.encoding.json.stream 等
                # import stdx.encoding -> 匹配 stdx.encoding, stdx.encoding.json, stdx.encoding.hex 等
                if lib == pkg or lib.startswith(pkg + "."):
                    should_include = True
                    break

        if should_include and lib not in seen:
            seen.add(lib)
            filtered.append(f"-l {lib}")
            # 收集隐式依赖（对所有库都收集，包括非 stdx 库）
            if dep_cache:
                trans_deps = get_transitive_dependencies(dep_cache, lib)
                implicit_deps.update(trans_deps)

    # 第二遍：添加隐式依赖
    for implicit_dep in implicit_deps:
        if implicit_dep not in seen:
            seen.add(implicit_dep)
            filtered.append(f"-l {implicit_dep}")

    return " ".join(filtered) + " "


def run_one_case(args, file_path, run_option, compile_option, target, cfgs):
    global error_count
    global total_count
    global error_list
    logger.setStream(f"{os.path.basename(file_path)}.log")

    case_run_option, dependence, macro_cmd, is_valid_case = get_cmd_info(file_path, target, cfgs)
    if not is_valid_case:
        logger.warning(f"{file_path} is a invalid case, skip.")
        return
    run_option += f" {case_run_option}"
    file_dir = os.path.dirname(file_path)
    file_name = os.path.basename(file_path)
    case_dir = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "tmp", file_dir.replace(cfgs.HOME_DIR, "."))
    logger.info(f"************************Start to run case file: {file_path}************************")
    os.makedirs(case_dir, exist_ok=True)
    if cfgs.OS_PLATFORM and cfgs.OS_PLATFORM == "windows":
        if cfgs.WINDOWS_DLLS:
            for dll in cfgs.WINDOWS_DLLS:
                dll_name = dll.split(os.path.sep)[-1]
                if os.path.exists(os.path.join(case_dir, dll_name)):
                    break
                shutil.copyfile(dll, os.path.join(case_dir, dll_name))
        if len(cfgs.WINDOWS_C_LIB_ARR) > 0:
            for dll in cfgs.WINDOWS_C_LIB_ARR:
                dll_name = dll.split(os.path.sep)[-1]
                if os.path.exists(os.path.join(case_dir, dll_name)):
                    break
                shutil.copyfile(dll, os.path.join(case_dir, dll_name))
    out = os.path.join(case_dir, f"{file_name}.out")
    # 根据 import 语句过滤需要链接的库（合并源码和测试用例的 import）
    import_pkgs = parse_imports_from_case(file_path)
    import_pkgs |= parse_imports_from_source(cfgs.HOME_DIR)
    filtered_library = filter_library_by_imports(cfgs.LIBRARY, import_pkgs, cfgs.HOME_DIR)
    # case_import_cmd = '" --import-path="'
    # case_library_path_L_cmd = " -L "
    compile_cmd = f'cjc {cfgs.Woff} {args.optimize} {macro_cmd} ' \
                  f'{cfgs.IMPORT_PATH} ' \
                  f'{cfgs.LIBRARY_PATH} ' \
                  f'{filtered_library}' \
                  f'{cfgs.library_l_cmd} ' \
                  f'{file_path} {dependence} -o {os.path.realpath(out)} {compile_option}'

    # 记录编译开始时间
    compile_start_time = time.time()
    code = cfgs.run_cmd(compile_cmd)
    compile_time = time.time() - compile_start_time

    if code != 0:
        error_count += 1
        total_count += 1
        error_list.append(file_path)
        # 收集编译失败的结果
        collect_test_result(file_path, "Compilation failed", code, compile_time)
        return
    out_dir = os.path.dirname(out)
    out_file = os.path.basename(out)

    fuzz_cmd = ""
    if args.fuzz:
        fuzz_cmd = f"-runs={args.fuzz_runs} -rss_limit_mb={args.fuzz_rss_limit_mb}"

    if target == "ohos":
        # send test file to ohos device
        if platform_str == "linux" or platform_str == "darwin":
            cfgs.run_cmd(f"cd {out_dir};for f in `ls`;do hdc file send $f {ohos_dir};done")
        else:  # windows
            cfgs.run_cmd(f"cd {out_dir}&for %f in (*.*) do hdc file send %f {ohos_dir}")
        run_case_cmd = f"hdc shell cd {ohos_dir};chmod -R 777 *;export LD_LIBRARY_PATH={ohos_dir};./{out_file}"
    else:
        if platform_str == "linux":
            run_case_cmd = f"cd {out_dir};./{out_file} {run_option} {fuzz_cmd} -timeout=10800"  # -rss_limit_mb=16384
        elif platform_str == "darwin":
            dyld_path = os.environ.get('DYLD_LIBRARY_PATH', '')
            run_case_cmd = f"cd {out_dir};DYLD_LIBRARY_PATH=\"{dyld_path}:$DYLD_LIBRARY_PATH\" ./{out_file} {run_option} {fuzz_cmd} -timeout=10800"
        else:  # windows
            run_case_cmd = f"cd {out_dir}&{out_file} {run_option} {fuzz_cmd}"

    # 记录测试开始时间
    test_start_time = time.time()

    # 使用原来的等待逻辑，确保命令执行完成
    encode = 'gbk' if cfgs.OS_PLATFORM == "windows" else "utf-8"
    res = subprocess.Popen(run_case_cmd, shell=True, cwd=out_dir, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)

    # 收集输出内容
    output_lines = []

    try:
        while res.poll() is None:
            for msg in iter(res.stdout.readline, b''):
                msg = str(msg, encode, errors='ignore').strip()
                if msg != "":
                    output_lines.append(msg)
                    if not llt_check_not_start_or_end_with_target(msg):
                        logger.info(msg)
            res.kill()
    finally:
        if res.poll():
            res.kill()

    return_code = res.returncode
    test_time = time.time() - test_start_time

    # 将输出内容合并为字符串
    output = "\n".join(output_lines)

    # 收集测试结果
    collect_test_result(file_path, output, return_code, test_time)

    if args.clean:
        for root_p, _, out_dir_files in os.walk(out_dir):
            for out_dir_files_file_name in out_dir_files:
                os.remove(os.path.join(root_p, out_dir_files_file_name))
    if return_code != 0:
        logger.error(f"return === {return_code}")
        total_count += 1
        error_count += 1
        error_list.append(file_path)


def get_cases(cfgs):
    cases = {}  # {test_class:[[case, status, case_time_elapsed, error_trace]]}
    tcs_time = {}  # {test_class: tcs_time_elapsed}
    for log in glob.glob(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log", "split_log", "*.log")):
        pkg = log[14:-7]
        tcs = None
        with open(log, "r", encoding="utf-8") as f:
            lines = f.readlines()
            log_str = "".join(lines)
            log_str = re.sub(r"\x1b\[\d+m", "", log_str)
            i = 0
            while i < len(lines):
                line = re.sub(r"\x1b\[\d+m", "", lines[i])
                step = 1
                tcs_match_obj = re.match(r".* TCS: (.*), time elapsed: (.*) ns, RESULT:", line)
                if tcs_match_obj:
                    tcs = f"{pkg}.{tcs_match_obj.group(1)}"
                    tcs_time_elapsed = tcs_match_obj.group(2)

                    cases[tcs] = []
                    tcs_time[tcs] = float(tcs_time_elapsed)
                case_match_obj = re.match(r".* \[(.*)\] CASE: (\w*)( \((\d+) ns(, (\d+\.\d+|\d*) ns/op)?\))?", line)
                if case_match_obj:
                    status = case_match_obj.group(1)
                    case = case_match_obj.group(2)
                    case_time_elapsed = case_match_obj.group(4) if case_match_obj.group(4) is not None else 0
                    case_time_per_op = case_match_obj.group(6)
                    error_trace = ""

                    if "FAILED" in status or "ERROR" in status:
                        for j in range(i + 1, len(lines)):
                            next_line = re.sub(r"\x1b\[\d+m", "", lines[j])
                            tcs_mo = re.match(r".* TCS: (.*), time elapsed: (.*) ns, RESULT:", next_line)
                            case_mo = re.match(r".* \[(.*)\] CASE: (.*) \((.*) ns\)", next_line)
                            not_summary = "Summary: TOTAL" not in next_line
                            if tcs_mo is None and case_mo is None and not_summary:
                                step += 1
                                error_trace += next_line[33:]
                            else:
                                break
                    if tcs:
                        cases[tcs].append([case, status, float(case_time_elapsed), error_trace, case_time_per_op])
                i += step
        # compile error： get testcase count, and set Error
        if tcs is None:
            case_file = f"{pkg.replace('.', '/')}.cj"
            if not os.path.exists(case_file):
                continue
            with open(f"{pkg.replace('.', '/')}.cj", "r", encoding="utf-8") as cf:
                try:
                    lines = cf.readlines()
                except UnicodeDecodeError:
                    logger.error("'utf-8' codec can't decode byte 0xff in position 0: invalid start byte:>>")
                error_trace = log_str
            for i, line in enumerate(lines):
                if i == len(lines) - 1:
                    break
                next_line = lines[i + 1]
                if "@TestCase" in line and line.strip().startswith("@"):
                    case_match_obj = re.match(r".*func (.*)\(\)", next_line)
                    if case_match_obj:
                        case_name = case_match_obj.group(1)
                        cases[tcs].append([case_name, "ERROR", 0.0, error_trace, 0.0])
                elif "@Test" in line and line.strip().startswith("@"):
                    tcs_match_obj = re.match(r".*public class (.*){", next_line)
                    tcs_func_match_obj = re.match(r".*func (.*)\(\)", next_line)
                    if tcs_match_obj:
                        tcs = f"{pkg}.{tcs_match_obj.group(1)}"
                        if tcs not in cases.keys():
                            cases[tcs] = []
                        tcs_time[tcs] = 0.0
                    # Top-Level @Test func
                    elif tcs_func_match_obj:
                        tcs = f"{pkg}.{tcs_func_match_obj.group(1)}"
                        cases[tcs] = [[tcs, "ERROR", 0.0, error_trace, 0.0]]
                        tcs_time[tcs] = 0.0
    return cases, tcs_time


def get_fuzz_cases(cfgs):
    global total_count
    fail_count = 0
    global error_count
    skip_count = 0
    pass_count = 0
    fail_list = []
    global error_list
    for log in glob.glob(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log", "split_log", "*.log")):
        pkg = log[14:-7]
        tcs = None
        with open(log, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line_one in lines:
            rem = re.match(r".*Done \d+ runs in \d+ second", line_one)
            if rem:
                '''success'''
                total_count += 1
                pass_count += 1
                break
            elif 'Aborted (core dumped)' in line_one:
                total_count += 1
                fail_count += 1
                for line in lines:
                    if 'Start to run case file: ' in line:
                        fail_list.append(line.split('Start to run case file: ')[-1].split('************************'))
                break
        else:
            for line in lines:
                if 'Start to run case file: ' in line:
                    item = line.split('Start to run case file: ')[-1].split('************************')[0]
                    if not error_list.__contains__(item):
                        total_count += 1
                        error_count += 1
                        error_list.append(item)

    logger.info("*" * 50)
    logger.info("Test fuzz Summary")
    logger.info(f"Total  : {total_count}")
    logger.info(f"Passed : {pass_count}")
    logger.info(f"Failed : {fail_count}")
    logger.info(f"Error  : {error_count}")
    logger.info(f"Skipped: {skip_count}")
    logger.info(f"Ratio  : {round((pass_count + skip_count) / total_count * 100, 2) if total_count > 0 else 0}%")
    show_case_list(fail_list, "Failed")
    show_case_list(error_list, "Error")
    logger.info("*" * 50)
    logger.info("View the full log in log/all.log, or view the log of each case under log/split_log")
    if fail_count == 0 and error_count == 0:
        return 0
    else:
        return 1


def gen_junit_report(cfgs, cases, tcs_time):
    global total_count
    fail_count = 0
    global error_count
    skip_count = 0
    pass_count = 0
    fail_list = []
    global error_list
    skip_list = []
    testsuites = Et.Element("testsuites")
    tcs_info = {}  # {test_class:[total, passed, failed, error, skipped]}
    for tcs in cases.keys():
        class_name = tcs
        testsuite = Et.SubElement(testsuites, "testsuite", name=class_name,
                                  time=str(tcs_time[tcs] / 1000 / 1000 / 1000))
        tcs_info[tcs] = [0, 0, 0, 0, 0]
        for case in cases[tcs]:
            case_name, status, case_time_elapsed, error_trace, case_time_per_op = case
            testcase = Et.SubElement(testsuite, "testcase", class_name=class_name, name=case_name,
                                     time=str(case_time_elapsed / 1000 / 1000 / 1000))
            if "FAILED" in status:
                fail_info = Et.SubElement(testcase, "failure")
                fail_info.text = error_trace
                fail_count += 1
                tcs_info[tcs][2] += 1
                fail_list.append(f"{tcs}.{case_name}")
            elif "ERROR" in status:
                error_info = Et.SubElement(testcase, "error")
                error_info.text = error_trace
                error_count += 1
                tcs_info[tcs][3] += 1
                error_list.append(f"{tcs}.{case_name}")
            elif "SKIP" in status:
                Et.SubElement(testcase, "skipped")
                skip_count += 1
                tcs_info[tcs][4] += 1
                skip_list.append(f"{tcs}.{case_name}")
            else:
                pass_count += 1
                tcs_info[tcs][1] += 1
            total_count += 1
            tcs_info[tcs][0] += 1
        testsuite.set("tests", str(tcs_info[tcs][0]))
        testsuite.set("failures", str(tcs_info[tcs][2]))
        testsuite.set("errors", str(tcs_info[tcs][3]))
        testsuite.set("skipped", str(tcs_info[tcs][4]))
    testsuites.set("tests", str(total_count))
    testsuites.set("failures", str(fail_count))
    testsuites.set("errors", str(error_count))
    testsuites.set("skipped", str(skip_count))
    xml_str = Et.tostring(testsuites).decode()
    xml_str = re.sub(r'[^\x0A\x20-\x7e]', r'', xml_str)
    result_pretty = minidom.parseString(xml_str).toprettyxml(indent="    ")
    with open(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "report", "result.xml"), "w+", encoding='UTF-8') as f:
        f.write(result_pretty)

    logger.info("*" * 50)
    logger.info("Test Summary")
    logger.info(f"Total  : {total_count}")
    logger.info(f"Passed : {pass_count}")
    logger.info(f"Failed : {fail_count}")
    logger.info(f"Error  : {error_count}")
    logger.info(f"Skipped: {skip_count}")
    logger.info(f"Ratio  : {round((pass_count + skip_count) / total_count * 100, 2) if total_count > 0 else 0}%")

    show_case_list(fail_list, "Failed")
    show_case_list(error_list, "Error")
    show_case_list(skip_list, "Skipped")
    logger.info("*" * 50)
    logger.info("View the full log in log/all.log, or view the log of each case under log/split_log")
    if fail_count == 0 and error_count == 0:
        return 0
    else:
        return 1


def show_case_list(case_list, status):
    if len(case_list) > 0:
        logger.info("*" * 50)
        logger.info(f"{status} listed below:")
        for l in case_list:
            logger.info(l)


def gen_perf_csv(cases, cfgs):
    with open(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "report", "perf.csv"), "w+", encoding='UTF-8') as f:
        writer = csv.writer(f)
        writer.writerow(["class", "case_name", "status", "case_time_elapsed(ns)", "case_time_per_op(ns/op)"])
        for tcs in cases.keys():
            for case in cases[tcs]:
                case_name, status, case_time_elapsed, error_trace, case_time_per_op = case
                writer.writerow([tcs, case_name, status, case_time_elapsed, case_time_per_op])


def gen_report_from_dict(cfgs):
    """
    从全局报告字典生成测试报告
    """
    global test_report

    # 生成性能CSV报告
    with open(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "report", "perf.csv"), "w+", encoding='UTF-8') as f:
        writer = csv.writer(f)
        writer.writerow(
            ["class", "case_name", "status", "case_time_elapsed(s)", "case_time_per_op(ns/op)", "file_path"])
        for class_name, cases in test_report["cases"].items():
            for case in cases:
                if len(case) >= 6:
                    case_name, status, case_time_elapsed, error_trace, case_time_per_op, file_path = case[:6]
                else:
                    case_name, status, case_time_elapsed, error_trace, case_time_per_op = case[:5]
                    file_path = ""
                writer.writerow([class_name, case_name, status, case_time_elapsed, case_time_per_op, file_path])

    # 生成JUnit XML报告
    testsuites = Et.Element("testsuites")

    total_count = test_report["total_cases"]
    fail_count = test_report["failed_cases"]
    error_count = test_report["error_cases"]
    skip_count = test_report["skipped_cases"]
    pass_count = test_report["passed_cases"]

    fail_list = []
    error_list = []
    skip_list = []

    for class_name, cases in test_report["cases"].items():
        class_time = test_report["class_times"].get(class_name, 0.0)
        testsuite = Et.SubElement(testsuites, "testsuite",
                                  name=class_name,
                                  time=str(class_time))

        class_total = 0
        class_failures = 0
        class_errors = 0
        class_skipped = 0

        for case in cases:
            if len(case) >= 6:
                case_name, status, case_time_elapsed, error_trace, case_time_per_op, file_path = case[:6]
            else:
                case_name, status, case_time_elapsed, error_trace, case_time_per_op = case[:5]
                file_path = ""
            testcase = Et.SubElement(testsuite, "testcase",
                                     class_name=class_name,
                                     name=case_name,
                                     time=str(case_time_elapsed))

            if "FAILED" in status:
                fail_info = Et.SubElement(testcase, "failure")
                fail_info.text = error_trace
                class_failures += 1
                fail_list.append(file_path if file_path else f"{class_name}.{case_name}")
            elif "ERROR" in status:
                error_info = Et.SubElement(testcase, "error")
                error_info.text = error_trace
                class_errors += 1
                error_list.append(file_path if file_path else f"{class_name}.{case_name}")
            elif "SKIP" in status:
                Et.SubElement(testcase, "skipped")
                class_skipped += 1
                skip_list.append(file_path if file_path else f"{class_name}.{case_name}")

            class_total += 1

        testsuite.set("tests", str(class_total))
        testsuite.set("failures", str(class_failures))
        testsuite.set("errors", str(class_errors))
        testsuite.set("skipped", str(class_skipped))

    testsuites.set("tests", str(total_count))
    testsuites.set("failures", str(fail_count))
    testsuites.set("errors", str(error_count))
    testsuites.set("skipped", str(skip_count))

    # 保存XML报告
    xml_str = Et.tostring(testsuites).decode()
    xml_str = re.sub(r'[^\x0A\x20-\x7e]', r'', xml_str)
    result_pretty = minidom.parseString(xml_str).toprettyxml(indent="    ")
    with open(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "report", "result.xml"), "w+", encoding='UTF-8') as f:
        f.write(result_pretty)

    # 打印测试摘要
    logger.info("*" * 50)
    logger.info("Test Summary (From Real-time Collection)")
    logger.info(f"Total  : {test_report['total_cases']}")
    logger.info(f"Passed : {test_report['passed_cases']}")
    logger.info(f"Failed : {test_report['failed_cases']}")
    logger.info(f"Error  : {test_report['error_cases']}")
    logger.info(f"Skipped: {test_report['skipped_cases']}")
    logger.info(
        f"Ratio  : {round((test_report['passed_cases'] + test_report['skipped_cases']) / test_report['total_cases'] * 100, 2) if test_report['total_cases'] > 0 else 0}%")

    show_case_list(fail_list, "Failed")
    show_case_list(error_list, "Error")
    show_case_list(skip_list, "Skipped")
    logger.info("*" * 50)
    logger.info("View the full log in log/all.log, or view the log of each case under log/split_log")

    if test_report['failed_cases'] == 0 and test_report['error_cases'] == 0:
        return 0
    else:
        return 1


def gen_report(args, cfgs):
    if args.fuzz:
        return get_fuzz_cases(cfgs)
    else:
        # 使用实时收集的报告数据
        return gen_report_from_dict(cfgs)


def HLTtest(args, cfgs):
    global _3rd_party_root
    global logger
    _3rd_party_root = get_cjtest_path(args, cfgs, "")
    if _3rd_party_root == "":
        _3rd_party_root = Path(cfgs.HOME_DIR).parent
    log_path = getattr(args, 'log', None)
    logger = Logger(cfgs, log_path=log_path)
    if args.main:
        test_dir = 'benchmark'
    else:
        test_dir = 'HLT'
    cfgs.CJ_TEST_WORK = "test"
    if not os.path.exists(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, test_dir)):
        logger.error(f"{os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, test_dir)} test case not exists!")
        exit(0)
    run_options = cp.get("test", "run_options")
    compile_options = cp.get("test", "compile_options")
    target = "x86"
    if args.path:
        dirs = os.path.join(cfgs.HOME_DIR, args.path)
    else:
        dirs = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, test_dir)
    if args.target:
        target = args.target
        _3rd_party_root = get_cjtest_path(args, cfgs, args.target)
    elif args.main:
        os.environ['cjHeapSize'] = cp.get("test", "cjHeapSize")
        compile_options = compile_options.replace("--test", '')
    elif args.fuzz:
        # os.environ["CANGJIE_PATH"] = f"{fuzz_lib}:{os.getenv('CANGJIE_PATH')}"
        compile_options = compile_options.replace("--test", '')
        compile_options += f' --link-options="--whole-archive {cfgs.CANGJIE_HOME}/lib/linux_x86_64_{cfgs.CJC_RUNTIME_SUFFIX}/libclang_rt.fuzzer_no_main.a -no-whole-archive -lstdc++" --sanitizer-coverage-trace-compares --sanitizer-coverage-pc-table --sanitizer-coverage-inline-8bit-counters'
        fuzz_runs = cp.get("test", "fuzz_runs")
        fuzz_rss_limit_mb = cp.get("test", "fuzz_rss_limit_mb")
        if fuzz_runs:
            args.fuzz_runs = fuzz_runs
        else:
            args.fuzz_runs = 30000000
        if fuzz_rss_limit_mb:
            args.fuzz_rss_limit_mb = fuzz_rss_limit_mb
        else:
            args.fuzz_rss_limit_mb = 4096
        fuzz_dir = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "fuzz")
        if args.path and str(args.path).startswith(str(fuzz_dir)) and os.path.exists(args.path):
            cfgs.LOG.info(f"指定测试路径：{args.path}")
            dirs = args.path
        else:
            if os.path.exists(fuzz_dir):
                dirs = fuzz_dir
            elif os.path.exists(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "FUZZ")):
                dirs = os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "FUZZ")
            else:
                cfgs.LOG.error("当前仓库中没有test/fuzz目录, 请检查后再试.")
                exit(1)

    # else:
    #     run_options += f"{arg}"

    shutil.rmtree(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "tmp"), ignore_errors=True)
    shutil.rmtree(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "log"), ignore_errors=True)
    shutil.rmtree(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "report"), ignore_errors=True)
    os.makedirs(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "tmp"), exist_ok=True)
    os.makedirs(os.path.join(cfgs.HOME_DIR, cfgs.CJ_TEST_WORK, "report"), exist_ok=True)

    if cfgs.OS_PLATFORM and cfgs.OS_PLATFORM == "windows":
        copy_windows_lib(cfgs)
    if target == "ohos":
        cfgs.run_cmd(f"hdc shell rm -rf {ohos_dir};mkdir {ohos_dir};chmod -R 777 {ohos_dir}")
        if cfgs.BUILD_PARMS.get("target") is not None and cfgs.BUILD_PARMS["target"].get("aarch64-linux-ohos") \
                is not None and cfgs.BUILD_PARMS["target"]["aarch64-linux-ohos"].get("compile-option") is not None:
            cfgs.OHOS_CMD = str(cfgs.BUILD_PARMS["target"]["aarch64-linux-ohos"]["compile-option"]) \
                .replace('${DEVECO_CANGJIE_HOME}', f'{os.environ["DEVECO_CANGJIE_HOME"]}').replace('\\', '/')
    cfgs.libs_3rd = []
    cfgs.import_cmd = set()
    cfgs.library_path_L_cmd = []
    cfgs.library_l_cmd = ""
    __find_cjpm_home_librarys(args, cfgs)
    cj_build_libs = find_lib_path(cfgs.LIB_DIR, '')
    find_cangjie_lib_arr = []
    for build_lib in cj_build_libs:
        cfgs.IMPORT_PATH += f" --import-path {build_lib}"
        for build_lib_item in os.listdir(build_lib):
            if build_lib_item.__contains__(".") or 'bin' in build_lib_item:
                continue
            if os.path.exists(os.path.join(build_lib, build_lib_item)):
                cfgs.LIBRARY_PATH += f" -L {os.path.join(build_lib, build_lib_item)}"
                find_cangjie_lib_arr.append(os.path.join(build_lib, build_lib_item))
                cangjie_env_setup(find_cangjie_lib_arr)
    if cfgs.CANGJIE_STDX_DIR:
        cfgs.IMPORT_PATH += f" --import-path {Path(cfgs.CANGJIE_STDX_DIR).parent}"
        cfgs.LIBRARY_PATH += f" -L {cfgs.CANGJIE_STDX_DIR}"
        __improt_stdx_libs([cfgs.CANGJIE_STDX_DIR], cfgs, args)
    __improt_libs(find_cangjie_lib_arr, cfgs)

    # 设置用例运行环境变量
    os.environ["ci_project_home"] = cfgs.HOME_DIR

    def signal_handler(sig, frame):
        logger.info("\n收到中断信号，正在生成测试报告...")
        try:
            gen_report(args, cfgs)
        except Exception as e:
            logger.error(f"生成报告时出错: {e}")
        exit(1)

    signal.signal(signal.SIGINT, signal_handler)

    for root, _, files in os.walk(dirs):
        for f in files:
            if f.endswith(".cj"):
                if args.case:
                    if args.case.endswith(".cj"):
                        if args.case == str(f):
                            run_one_case(args, os.path.join(root, f), run_options, compile_options, target, cfgs)
                    else:
                        if "{}.cj".format(args.case) == str(f):
                            run_one_case(args, os.path.join(root, f), run_options, compile_options, target, cfgs)
                else:
                    run_one_case(args, os.path.join(root, f), run_options, compile_options, target, cfgs)
    try:
        if args.HLT:
            gen_report(args, cfgs)
        else:
            exit(gen_report(args, cfgs))
    except AttributeError:
        exit(gen_report(args, cfgs))
