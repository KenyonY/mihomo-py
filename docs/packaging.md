# 离线安装与发行

## 安装契约

Linux x86_64 / aarch64、Python 3.11+、Linux 5.3+（允许 pidfd）。

```bash
python -m pip install mihomo-py --index-url https://your-mirror.example/simple
```

pip 镜像需要同步项目发行包及 `pyproject.toml` 声明的 Python 依赖。目标机器不需要 GitHub、Go、编译器或预装 mihomo。wheel 按 CPU 架构分开，内含静态内核及四份数据库；源码包包含两个架构，pip 从源码构建也不会下载资源。构建依赖 hatchling 从 pip 源获取。

安装后默认使用包内内核，不探测系统 PATH；`--core-binary` / `MIHOMO_PY_BINARY` 可显式覆盖。包内资源只读，首次校验从本地复制到每个订阅的数据目录；后续保留有效的已有文件，不共享可变缓存。GEO 自动更新固定关闭。

远程订阅、用户自定义 `geox-url`、rule/proxy providers 是订阅内容，不属于通用软件依赖。如果它们也无法直连，首次启动使用本地完整 YAML、内嵌节点和规则。自定义 GEO 数据可放进 `MIHOMO_PY_GEODATA_DIR`；该环境变量指定唯一数据来源，不再回退到包内数据。自定义 `geox-url` 不会被默认数据库替代，即使 URL 指向公共下载站。

## 资源与更新

`src/mihomo_py/_vendor/manifest.json` 锁定 mihomo v1.19.19、GEO 的不可变 Git revision、下载内容 SHA-256、解压后 SHA-256。`NOTICE.txt` 和 `licenses/` 随所有发行包分发，提供上游许可、源码链接和归属信息。

二进制和数据直接提交 Git（不使用需要额外下载的 LFS 指针），构建 hook 只读取本地资源，缺少文件或哈希不符立即失败。维护者需要恢复缺失资源时可联网运行：

```bash
python scripts/restore_vendor.py
```

此脚本不参与 pip 安装、构建或运行。更新资源时先审查官方发行版本与数据来源，修改 manifest 并校验下载及解压哈希，更新 NOTICE，验证后一起提交。不要让安装流程使用 `latest` URL。

升级客户端会安装新版内核和快照；已有订阅的有效数据保持原样。如需改用新快照，应在停止内核后备份并删除对应 `core-data/<标识>/` 下的 GEO 文件，再启动使其重新复制。不要删除 provider 或节点选择缓存。

## 构建与验收

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
python -m build --installer uv
MIHOMO_BUILD_ARCH=aarch64 python -m build --wheel --installer uv
```

默认按构建主机架构生成 wheel；`MIHOMO_BUILD_ARCH` 仅用于维护者交叉打包预编译资源。wheel 使用 `py3-none-manylinux_2_17_<arch>.musllinux_1_2_<arch>`，内核是静态 ELF，不依赖 Python ABI 或 glibc。Python 依赖自身的 wheel/平台支持仍由 pip 解析。源码包必须包含全部资源、构建 hook、脚本和文档。

发行前必须验证：

- wheel 包含且仅包含目标架构内核，带可执行权限、四份数据库、许可和 manifest；源码包离线重建成功。
- 新虚拟环境只用 pip 镜像或本地 wheelhouse 安装完整依赖，不使用已有 mihomo 或用户数据。
- 真实内核在 `geodata-mode: false/true` 下校验 `GEOIP`、`GEOSITE`、`IP-ASN`，并完成启动、健康检查、代理访问本地 HTTP 服务、停止。
- 安装和首次启动期间阻断/审计外部联网，证明没有隐藏下载。Python mock 无法证明 Go 内核没有联网。
- aarch64 wheel 的资源校验和打包检查不等于 ARM 设备运行验证；运行验证需 ARM 主机或显式模拟器。

可在全新环境中运行 `python scripts/check_install.py`，验证包内资源、两种 GEO 模式以及真实 CLI 的校验、启动、本地代理访问和停止。使用该环境的 Python，避免误用开发环境的 editable 安装；可通过 `strace -f -e trace=network -o network.log` 包裹命令审计 Python 和 Go 子进程。

本地构建不代表已发布；只有上传发行包并等待镜像同步后，用户才能直接从镜像执行 `pip install mihomo-py`。
