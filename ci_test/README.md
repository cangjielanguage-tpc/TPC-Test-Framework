# 三方库使用脚本运行LLT/HLT测试用例

## 下载ci脚本

`git clone https://gitcode.com/Cangjie-TPC/TPC-Test-Framework.git`

可选两种方式使用ci脚本 本质上是一样的

1. 将`ci_tools/src/ci_test`配置到PATH环境变量 之后可使用`ciTest`命令
1. 使用`python ci_tools/src/main.py`命令

## 使用脚本编译三方库

1. 进入三方库根目录
1. 执行`ciTest build`命令
    1. 本质就是执行`cjpm build`命令

## 使用脚本运行LLT测试用例

1. 进入三方库根目录
1. 运行所有用例: 执行`ciTest test`命令
    1. 本质就是将test/LLT目录下的测试用例与三方库so联合编译生成可执行文件并执行
1. 运行某个单独的用例: `ciTest test --case test1`
    1. `test1.cj`是用例的名称
    1. 加不加后缀都可以识别
    1. 无需考虑目录层级 会扫描整个LLT目录树

## 使用脚本运行HLT测试用例

1. 进入三方库根目录
1. 运行所有用例: 执行`ciTest cjtest`命令
    1. 本质就是将test/HLT目录下的测试用例与三方库so联合编译生成可执行文件并执行
1. 运行某个单独的用例: `ciTest cjtest --case test1`
    1. `test1.cj`是HLT目录里用例的名称
    1. 加不加后缀都可以识别
    1. 无需考虑目录层级 会扫描整个HLT目录树

## case_config.cfg 配置文件

在 `test/LLT/` 或 `test/HLT/` 目录下放置 `case_config.cfg` 文件，可对测试流程进行配置。配置文件对所在目录下所有测试用例生效，用例名即 `.cj` 文件名（不含 `.cj` 后缀）。每个测试目录需要有自己的 `case_config.cfg`，如果目录下没有配置文件则不会进行任何外部库处理。

### 配置段说明

#### [external_libs] - 第三方动态库

用于声明测试用例依赖的第三方动态库（非 std、非项目自身的库）。脚本会自动下载并添加到编译链接路径，下载后的库存放在 `test/libs/` 目录下。

支持三种方式:

**1. git 方式**

格式: `key = git:git地址,分支名`

行为: 执行 `git clone -b 分支名 --depth=1` 克隆仓库，然后扫描仓库中的 `.so/.a/.dylib/.dll/.lib` 文件添加到 `-L` 和 `-l` 链接选项，扫描 `.cjo` 文件添加到 `--import-path`。

```ini
[external_libs]
mylib = git:https://gitcode.com/Cangjie-TPC/somelib.git,master
```

**2. url 方式**

格式: `key = url:下载地址`

行为: 直接下载文件到 `test/libs/key/` 目录。如果是 `.zip` 文件会自动解压。然后扫描下载的文件添加到链接路径。

```ini
[external_libs]
mylib2 = url:https://example.com/libmylib.so
```

**3. cjpm 方式**

格式: `key = cjpm:git地址,分支名,子目录路径`

行为: 先 `git clone` 克隆仓库，然后进入指定的子目录执行 `cjpm update && cjpm build` 编译。编译产物在 `target/` 目录下，脚本只扫描 `target/` 目录获取 `.cjo` 和 `.so` 文件，不会链接仓库根目录中的原生 C 库（如 crypto、ssl 等，cjpm 已通过 `cjpm.toml` 的 `link-option` 处理这些依赖）。

```ini
[external_libs]
image = cjpm:https://gitcode.com/Cangjie-TPC/ci_lib.git,lib_QRCode4cj,src/image/image
```

子目录路径指向包含 `cjpm.toml` 或 `module.json` 的项目根目录。如果指定的子目录不存在（例如仓库结构变更），脚本会自动搜索仓库中含 `cjpm.toml` 或 `module.json` 的目录作为编译目录。

cjpm 方式的特殊处理:

- **重复编译保障**: 即使仓库目录已存在（之前已下载），也会重新执行 `cjpm build`，以确保 `.cjo` 文件与当前 cjc 版本兼容，避免出现 `validation of ast file failed` 错误
- **旧产物清理**: `cjpm build` 成功后，会自动清理仓库中除 `target/` 目录（新构建产物）和根目录（原生 C 库）外的所有旧预编译产物（`.cjo/.bchir2/.so/.dll/.dylib/.a/.lib`），避免版本冲突和重复链接
- **运行时库路径**: 编译成功后，脚本会自动将 `target/` 下包含 `.so` 文件的目录加入 `LD_LIBRARY_PATH`（Linux）或 `DYLD_LIBRARY_PATH`（macOS）环境变量，确保运行时能正确加载动态库

#### [skip] - 屏蔽用例

屏蔽指定的测试用例，用例名逗号分隔。被屏蔽的用例在执行时会被跳过。

```ini
[skip]
cases = test_case1, test_case2
```

#### [global_setup] / [global_teardown] - 全局前置/后置脚本

所有用例执行前运行一次前置脚本，所有用例执行后运行一次后置脚本。脚本路径相对于配置文件所在目录。

```ini
[global_setup]
script = ./global_setup.sh

[global_teardown]
script = ./global_teardown.sh
```

#### [pre_setup] / [post_teardown] - 单用例前置/后置脚本

指定用例执行前/后运行的脚本。key 为用例名（不含 `.cj` 后缀），value 为脚本路径（相对于配置文件所在目录）。脚本执行的工作目录为配置文件所在目录。

```ini
[pre_setup]
test_case1 = ./setup.sh

[post_teardown]
test_case1 = ./cleanup.sh
```

前置脚本可用于准备测试环境（如启动服务、生成临时文件），后置脚本可用于关闭资源/清理环境。

### 完整配置文件示例

```ini
# 用例配置文件 case_config.cfg
# 放置在 test/LLT/ 目录下，对 LLT 目录下所有用例生效
# 用例名即 .cj 文件名(不含 .cj 后缀)

[global_setup]
# 所有用例执行前运行一次
# script = ./global_setup.sh

[global_teardown]
# 所有用例执行后运行一次
# script = ./global_teardown.sh

[external_libs]
# 第三方动态库，非 std、非项目自身的库，自动下载并添加到编译链接路径
# git方式: key = git:url,branch          (git clone 指定分支，depth=1)
# url方式: key = url:url                  (直接下载文件，.zip自动解压)
# cjpm方式: key = cjpm:url,branch,subdir  (git clone 后进入 subdir 用 cjpm build 编译)
image = cjpm:https://gitcode.com/Cangjie-TPC/ci_lib.git,lib_QRCode4cj,src/image/image

[skip]
# 屏蔽的用例列表，逗号分隔
# cases = test_case1, test_case2

[pre_setup]
# 用例执行前运行的脚本
# test_case1 = ./setup.sh

[post_teardown]
# 用例执行后运行的脚本，用于关闭资源/清理环境
# test_case1 = ./cleanup.sh
```

## tips

脚本的目的就是利用cangjie的包管理器和cjc编译器, 简化编译测试流程.

本质就是拷贝资源/拼接cjc编译命令/运行, 脚本运行时会打印使用的cjc编译命令.

如有更多需求, 仔细研究cjc编译命令选项, 本质都是一样的.
