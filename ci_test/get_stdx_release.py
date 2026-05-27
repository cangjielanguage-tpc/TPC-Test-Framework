import re
import json
import os
import tarfile
import zipfile
import shutil
import subprocess
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib import request as urllib_request
from urllib import error as urllib_error
from concurrent.futures import ThreadPoolExecutor, as_completed


def get_cjc_version_from_command(logger=None) -> Optional[Dict[str, str]]:
    try:
        result = subprocess.run(['cjc', '-v'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_match = re.search(r'Cangjie Compiler:\s+(\d+\.\d+\.\d+(?:-[a-zA-Z]+\.\d+)?)', result.stdout)
            target_match = re.search(r'Target:\s+([a-zA-Z0-9_-]+)', result.stdout)

            if version_match:
                version = version_match.group(1)
                target = target_match.group(1) if target_match else 'unknown'
                if logger:
                    logger.info(f"从cjc命令获取版本: {version}")
                    logger.info(f"目标平台: {target}")
                else:
                    print(f"✓ 从cjc命令获取版本: {version}")
                    print(f"✓ 目标平台: {target}")
                return {
                    'version': version,
                    'target': target
                }
        return None
    except Exception as e:
        if logger:
            logger.error(f"运行cjc -v失败: {e}")
        else:
            print(f"✗ 运行cjc -v失败: {e}")
        return None


def get_cjc_version(cjc_env_path: str, logger=None) -> Optional[str]:
    try:
        version_dirs = [d for d in os.listdir(cjc_env_path) if os.path.isdir(os.path.join(cjc_env_path, d))]

        if not version_dirs:
            if logger:
                logger.error(f"未找到cjc版本目录: {cjc_env_path}")
            else:
                print(f"✗ 未找到cjc版本目录: {cjc_env_path}")
            return None

        version_dirs.sort(reverse=True)
        latest_version = version_dirs[0]

        if logger:
            logger.info(f"找到cjc版本: {latest_version}")
        else:
            print(f"✓ 找到cjc版本: {latest_version}")
        return latest_version

    except Exception as e:
        if logger:
            logger.error(f"获取cjc版本失败: {e}")
        else:
            print(f"✗ 获取cjc版本失败: {e}")
        return None


def generate_possible_urls(cjc_info: Dict[str, str], owner: str, repo: str) -> List[Dict[str, str]]:
    version = cjc_info.get('version', '')
    target = cjc_info.get('target', '').lower()

    possible_urls = []

    match = re.match(r'(\d+\.\d+\.\d+)(?:-([a-zA-Z]+(?:\.\d+)?))?', version)
    if match:
        base_version = match.group(1)
        version_suffix = match.group(2)
    else:
        base_version = version
        version_suffix = None

    possible_tags = []

    if version_suffix:
        possible_tags.extend([
            version,
            f"v{version}",
            f"{base_version}-{version_suffix}",
            f"v{base_version}-{version_suffix}"
        ])

        for i in range(1, 10):
            possible_tags.extend([
                f"{base_version}-{version_suffix}.{i}",
                f"v{base_version}-{version_suffix}.{i}"
            ])
    else:
        possible_tags.extend([
            version,
            f"v{version}",
            base_version,
            f"v{base_version}"
        ])

    platform_suffixes = []
    if 'x86_64' in target and 'mingw' in target:
        platform_suffixes.extend(['windows-x64', 'mingw64', 'windows'])
    elif 'x86_64' in target and 'linux' in target:
        platform_suffixes.extend(['linux-x64', 'linux'])
    elif 'x86_64' in target and 'darwin' in target:
        platform_suffixes.extend(['macos-x64', 'macos', 'darwin'])
    else:
        platform_suffixes.extend(['windows-x64', 'linux-x64', 'macos-x64'])

    for tag in possible_tags:
        for platform in platform_suffixes:
            version_str = tag.replace('v', '')

            possible_filenames = [
                f"cangjie-stdx-{platform}-{version_str}.tar.gz",
                f"cangjie-stdx-{platform}-{version_str}.zip",
                f"stdx-{platform}-{version_str}.tar.gz",
                f"stdx-{platform}-{version_str}.zip",
                f"cangjie-stdx-{version_str}.tar.gz",
                f"cangjie-stdx-{version_str}.zip"
            ]

            for filename in possible_filenames:
                url = f"https://gitcode.com/{owner}/{repo}/releases/download/{tag}/{filename}"

                if not any(pkg['url'] == url for pkg in possible_urls):
                    possible_urls.append({
                        'name': filename,
                        'tag': tag,
                        'url': url,
                        'platform': platform
                    })

    return possible_urls


def check_url_exists(url: str) -> bool:
    try:
        req = urllib_request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
        })

        response = urllib_request.urlopen(req, timeout=5)
        if response.status == 200:
            response.close()
            return True
        return False

    except Exception:
        return False


def find_valid_urls(possible_urls: List[Dict[str, str]], max_workers: int = 10, logger=None) -> List[Dict[str, str]]:
    valid_urls = []
    print(f"logger: {logger is not None}")
    if logger:
        logger.info(f"准备检查 {len(possible_urls)} 个可能的URL...")
    else:
        print(f"准备检查 {len(possible_urls)} 个可能的URL...")
        print()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(check_url_exists, url['url']): url for url in possible_urls}

        completed = 0
        found = False
        for future in as_completed(future_to_url):
            if found:
                future.cancel()
                continue

            completed += 1
            if completed % 10 == 0:
                if logger:
                    logger.info(f"已检查 {completed}/{len(possible_urls)} 个URL...")
                else:
                    print(f"已检查 {completed}/{len(possible_urls)} 个URL...")

            url_info = future_to_url[future]
            try:
                if future.result():
                    valid_urls.append(url_info)
                    if logger:
                        logger.info(f"找到: {url_info['name']} (标签: {url_info['tag']})")
                    else:
                        print(f"✓ 找到: {url_info['name']} (标签: {url_info['tag']})")
                    found = True
                    for f in future_to_url:
                        if f != future:
                            f.cancel()
                    break
            except Exception:
                pass

    return valid_urls


def find_closest_version(packages: List[Dict[str, str]], target_version: str) -> List[Dict[str, str]]:
    def version_key(v: str) -> Tuple[int, int, int, str]:
        match = re.match(r'v?(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z]+(?:\.\d+)?)?)?', v)
        if match:
            major, minor, patch, suffix = match.groups()
            return (int(major), int(minor), int(patch), suffix or '')
        return (0, 0, 0, '')

    target_key = version_key(target_version)

    packages_with_diff = []
    for pkg in packages:
        tag = pkg['tag']
        tag_key = version_key(tag)

        diff = 0
        for i in range(3):
            diff += abs(target_key[i] - tag_key[i])

        suffix_diff = 0 if target_key[3] == tag_key[3] else 1

        packages_with_diff.append((diff + suffix_diff, pkg))

    packages_with_diff.sort(key=lambda x: x[0])
    closest_packages = [pkg for diff, pkg in packages_with_diff]

    return closest_packages


def get_download_info_from_url(url: str) -> Optional[Dict[str, str]]:
    try:
        req = urllib_request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
        }, method='HEAD')

        response = urllib_request.urlopen(req, timeout=10)

        if response.status == 200:
            content_type = response.headers.get('Content-Type', '')
            content_length = response.headers.get('Content-Length', 'unknown')

            filename = None
            if 'Content-Disposition' in response.headers:
                disposition = response.headers['Content-Disposition']
                filename_match = re.search(r'filename="?([^"]+)"?', disposition)
                if filename_match:
                    filename = filename_match.group(1)

            if not filename:
                parsed = urlparse(url)
                filename = os.path.basename(parsed.path)

            return {
                'url': url,
                'filename': filename,
                'content_type': content_type,
                'content_length': content_length,
                'valid': True
            }
        else:
            return {
                'url': url,
                'valid': False,
                'status_code': response.status
            }
    except Exception as e:
        return {
            'url': url,
            'valid': False,
            'error': str(e)
        }


def download_file(url: str, output_dir: str, filename: Optional[str] = None, logger=None) -> Optional[str]:
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        req = urllib_request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
        })

        if logger:
            logger.info(f"开始下载: {url}")
        else:
            print(f"开始下载: {url}")

        response = urllib_request.urlopen(req, timeout=60)

        if not filename:
            if 'Content-Disposition' in response.headers:
                disposition = response.headers['Content-Disposition']
                filename_match = re.search(r'filename="?([^"]+)"?', disposition)
                if filename_match:
                    filename = filename_match.group(1)

            if not filename:
                parsed = urlparse(url)
                filename = os.path.basename(parsed.path)

        output_path = os.path.join(output_dir, filename)

        total_size = int(response.headers.get('Content-Length', 0))
        downloaded_size = 0
        last_progress = 0

        def format_size(size):
            if size < 1024:
                return f"{size}B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f}KB"
            elif size < 1024 * 1024 * 1024:
                return f"{size / (1024 * 1024):.1f}MB"
            else:
                return f"{size / (1024 * 1024 * 1024):.1f}GB"

        import sys
        is_windows = sys.platform == 'win32'

        with open(output_path, 'wb') as f:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                downloaded_size += len(chunk)

                if total_size > 0:
                    progress = (downloaded_size / total_size) * 100
                    bar_length = 30
                    filled = int(bar_length * progress / 100)
                    if is_windows:
                        bar = '█' * filled + '-' * (bar_length - filled)
                    else:
                        bar = '█' * filled + '░' * (bar_length - filled)

                    if logger:
                        if progress - last_progress >= 10:
                            logger.info(
                                f"[{bar}] {progress:.1f}% ({format_size(downloaded_size)}/{format_size(total_size)})")
                            last_progress = progress
                    else:
                        if progress - last_progress >= 0.5:
                            print(
                                f"\r[{bar}] {progress:.1f}% ({format_size(downloaded_size)}/{format_size(total_size)})",
                                end='', flush=True)
                            last_progress = progress

        response.close()

        if logger:
            logger.info(f"下载完成: {output_path}")
        else:
            print(f"\n✓ 下载完成: {output_path}")
        return output_path

    except Exception as e:
        if logger:
            logger.error(f"下载失败: {e}")
        else:
            print(f"\n✗ 下载失败: {e}")
        return None


def extract_file(file_path: str, output_dir: Optional[str] = None, logger=None) -> bool:
    try:
        if not output_dir:
            output_dir = os.path.dirname(file_path)

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        if logger:
            logger.info(f"开始解压: {file_path}")
        else:
            print(f"\n开始解压: {file_path}")

        if file_path.endswith('.tar.gz') or file_path.endswith('.tgz'):
            with tarfile.open(file_path, 'r:gz') as tar:
                tar.extractall(path=output_dir)
            if logger:
                logger.info(f"解压完成: {output_dir}")
            else:
                print(f"✓ 解压完成: {output_dir}")
            return True
        elif file_path.endswith('.tar.bz2') or file_path.endswith('.tbz'):
            with tarfile.open(file_path, 'r:bz2') as tar:
                tar.extractall(path=output_dir)
            if logger:
                logger.info(f"解压完成: {output_dir}")
            else:
                print(f"✓ 解压完成: {output_dir}")
            return True
        elif file_path.endswith('.tar'):
            with tarfile.open(file_path, 'r') as tar:
                tar.extractall(path=output_dir)
            if logger:
                logger.info(f"解压完成: {output_dir}")
            else:
                print(f"✓ 解压完成: {output_dir}")
            return True
        elif file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(output_dir)
            if logger:
                logger.info(f"解压完成: {output_dir}")
            else:
                print(f"✓ 解压完成: {output_dir}")
            return True
        else:
            if logger:
                logger.error(f"不支持的文件格式: {file_path}")
            else:
                print(f"✗ 不支持的文件格式: {file_path}")
            return False

    except Exception as e:
        if logger:
            logger.error(f"解压失败: {e}")
        else:
            print(f"✗ 解压失败: {e}")
        return False


def download_and_extract(url: str, output_dir: str, filename: Optional[str] = None, logger=None) -> bool:
    try:
        file_path = download_file(url, output_dir, filename, logger)

        if not file_path:
            return False

        if extract_file(file_path, output_dir, logger):
            if logger:
                logger.info(f"下载和解压完成: {output_dir}")
            else:
                print(f"\n✓ 下载和解压完成: {output_dir}")
            return True
        else:
            return False

    except Exception as e:
        if logger:
            logger.error(f"下载和解压失败: {e}")
        else:
            print(f"✗ 下载和解压失败: {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description='GitCode stdx包下载工具')
    parser.add_argument('--url', type=str,
                        help='指定下载链接')
    parser.add_argument('--output', type=str, help='输出目录（默认：当前目录）')
    parser.add_argument('--filename', type=str,
                        help='指定文件名（用于下载时）')
    parser.add_argument('--cjc-env', type=str, default='D:\\ENV\\cangjie_env',
                        help='cjc环境路径')
    parser.add_argument('--owner', type=str, default='Cangjie',
                        help='仓库所有者')
    parser.add_argument('--repo', type=str, default='cangjie_stdx',
                        help='仓库名称')
    parser.add_argument('--check-url', action='store_true',
                        help='检查URL是否有效')
    parser.add_argument('--get-link', action='store_true',
                        help='获取下载链接信息')
    parser.add_argument('--download', action='store_true',
                        help='下载文件')
    parser.add_argument('--extract', action='store_true',
                        help='下载后自动解压')
    parser.add_argument('--no-extract', action='store_true',
                        help='下载后不解压')
    parser.add_argument('--auto', action='store_true',
                        help='自动模式：运行cjc -v获取版本，自动下载最接近的stdx包')
    parser.add_argument('--list-possible', action='store_true',
                        help='列出所有可能的stdx包')

    args = parser.parse_args()

    print("=" * 80)
    print("GitCode stdx包下载工具")
    print("=" * 80)

    cjc_info = None

    if args.auto or args.list_possible or not any(
            [args.url, args.check_url, args.get_link, args.download, args.extract]):
        print("自动模式：正在获取cjc版本...")
        cjc_info = get_cjc_version_from_command()
        if not cjc_info:
            print("✗ 无法通过cjc -v获取版本，尝试从环境目录获取...")
            cjc_version = get_cjc_version(args.cjc_env)
            if cjc_version:
                cjc_info = {
                    'version': cjc_version,
                    'target': 'unknown'
                }

        if not cjc_info:
            print("✗ 无法获取cjc版本号")
            return
    else:
        cjc_version = get_cjc_version(args.cjc_env)
        if cjc_version:
            cjc_info = {
                'version': cjc_version,
                'target': 'unknown'
            }

        if not cjc_info:
            print("✗ 无法获取cjc版本号")
            return

    cjc_version = cjc_info.get('version', '')
    target_platform = cjc_info.get('target', '')

    print(f"cjc版本: {cjc_version}")
    if target_platform != 'unknown':
        print(f"目标平台: {target_platform}")
    print(f"仓库: {args.owner}/{args.repo}")
    print("=" * 80)

    output_dir = args.output if args.output else os.getcwd()

    if args.auto or args.list_possible or not any(
            [args.url, args.check_url, args.get_link, args.download, args.extract]):
        possible_urls = generate_possible_urls(cjc_info, args.owner, args.repo)

        if not possible_urls:
            print("✗ 无法生成可能的URL")
            return

        if args.list_possible:
            print(f"生成了 {len(possible_urls)} 个可能的URL:")
            print()
            for i, url_info in enumerate(possible_urls, 1):
                print(f"{i}. {url_info['name']}")
                print(f"   标签: {url_info['tag']}")
                print(f"   平台: {url_info['platform']}")
                print(f"   URL: {url_info['url']}")
                print()
            return

        valid_urls = find_valid_urls(possible_urls)

        if not valid_urls:
            print("✗ 未找到有效的stdx包")
            print("\n说明:")
            print("GitCode的下载链接包含CDN和认证参数，无法通过简单URL构造获取。")
            print("这意味着:")
            print("1. 直接访问 https://gitcode.com/Cangjie/cangjie_stdx/releases 下载链接会返回401或404错误")
            print("2. 需要从网页界面点击下载按钮才能获取有效的CDN链接")
            print("3. 有效的下载链接包含 file-cdn.gitcode.com 和 auth_key 参数")
            print()
            print("解决方案:")
            print("1. 访问 https://gitcode.com/Cangjie/cangjie_stdx/releases")
            print(f"2. 找到匹配cjc版本 {cjc_version} 的stdx包")
            print("3. 点击下载按钮，复制实际的下载链接（包含file-cdn.gitcode.com和auth_key参数）")
            print("4. 使用 --url 参数指定下载链接")
            print()
            print("推荐的stdx包格式:")
            print(f"- cangjie-stdx-windows-x64-{cjc_version}.zip")
            print(f"- cangjie-stdx-linux-x64-{cjc_version}.tar.gz")
            print(f"- cangjie-stdx-macos-x64-{cjc_version}.tar.gz")
            print()
            print("示例:")
            print(f'python get_latest_release.py --url "https://file-cdn.gitcode.com/..." --extract')
            return

        print(f"\n✓ 找到 {len(valid_urls)} 个有效的stdx包:")
        print()

        closest_packages = find_closest_version(valid_urls, cjc_version)

        for i, pkg in enumerate(closest_packages[:5], 1):
            print(f"{i}. {pkg['name']}")
            print(f"   标签: {pkg['tag']}")
            print(f"   平台: {pkg['platform']}")
            print(f"   URL: {pkg['url']}")
            print()

        print("开始下载第一个stdx包...")
        pkg = closest_packages[0]

        if args.extract or not args.no_extract:
            download_and_extract(pkg['url'], output_dir)
        else:
            download_file(pkg['url'], output_dir)
        return

    if args.url:
        print(f"处理URL: {args.url}")
        print()

        download_info = get_download_info_from_url(args.url)

        if not download_info['valid']:
            print("✗ 该链接无效")
            if 'status_code' in download_info:
                print(f"  状态码: {download_info['status_code']}")
            if 'error' in download_info:
                print(f"  错误: {download_info['error']}")
            return

        print("✓ 该链接有效")
        print(f"  文件名: {download_info.get('filename', 'N/A')}")
        print(f"  内容类型: {download_info.get('content_type', 'N/A')}")
        print(f"  文件大小: {download_info.get('content_length', 'N/A')} bytes")
        print()

        if args.check_url:
            print("仅检查链接有效性，不进行下载")
            return

        if args.get_link:
            print("仅获取下载链接信息，不进行下载")
            return

        filename = args.filename if args.filename else download_info.get('filename')

        if args.download or args.extract:
            if args.extract or not args.no_extract:
                print("开始下载并解压...")
                download_and_extract(args.url, output_dir, filename)
            else:
                print("开始下载（不解压）...")
                download_file(args.url, output_dir, filename)
        else:
            print("提示:")
            print("  使用 --download 参数下载文件")
            print("  使用 --extract 参数下载并自动解压文件")
            print("  使用 --no-extract 参数下载但不解压")

        return

    print("使用说明:")
    print()
    print("功能列表:")
    print("  1. 下载功能: 使用 --download 参数下载文件")
    print("  2. 指定下载功能: 使用 --url 和 --filename 参数指定下载")
    print("  3. 根据环境下载对应的stdx功能: 自动获取cjc版本并匹配")
    print("  4. 获取下载链接功能: 使用 --get-link 参数获取链接信息")
    print("  5. 下载完成自动解压: 使用 --extract 参数自动解压")
    print("  6. 从对应网址中自动查找: 使用 --auto 参数自动查找并下载")
    print("  7. 自动模式: 使用 --auto 参数运行cjc -v获取版本，自动下载最接近的stdx包")
    print()
    print("使用步骤:")
    print("方式1 - 自动模式（推荐）:")
    print("  python get_latest_release.py --auto")
    print("  （自动运行cjc -v获取版本，查找并下载最接近的stdx包）")
    print()
    print("方式2 - 列出可能的包:")
    print("  python get_latest_release.py --list-possible")
    print()
    print("方式3 - 手动指定:")
    print("1. 访问 https://gitcode.com/Cangjie/cangjie_stdx/releases")
    print("2. 找到匹配cjc版本 {cjc_version} 的stdx包")
    print("3. 复制实际的下载链接（包含CDN和auth_key参数）")
    print("4. 使用本脚本下载和解压")
    print()
    print("示例:")
    print('  python get_latest_release.py --auto')
    print('  python get_latest_release.py --list-possible')
    print('  python get_latest_release.py --url "https://file-cdn.gitcode.com/..." --check-url')
    print('  python get_latest_release.py --url "https://file-cdn.gitcode.com/..." --get-link')
    print('  python get_latest_release.py --url "https://file-cdn.gitcode.com/..." --download')
    print('  python get_latest_release.py --url "https://file-cdn.gitcode.com/..." --extract')
    print('  python get_latest_release.py --url "https://file-cdn.gitcode.com/..." --download --output "D:\\downloads"')

    print("=" * 80)
    print("✓ 操作完成")
    print("=" * 80)


if __name__ == "__main__":
    main()