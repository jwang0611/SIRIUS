# ADR 0001: 使用 uv 锁定 Python 依赖

- 状态：Accepted
- 日期：2026-08-01
- 关联：GitHub issue #12 / A7

## 背景

项目原先把 runtime、test 和 build 依赖混在一个带宽松下限的 `requirements.txt` 中，并把公司镜像的 `/latest/` 浮动地址写入仓库。GitHub Actions 通过临时 `grep` 删除镜像行后重新解析依赖，因此本地、内部环境和公共 runner 可能安装不同版本。

## 决策

采用 uv 0.11.32：

- `pyproject.toml` 是直接依赖的唯一编辑入口，runtime、`dev`、`build` 分组维护。
- `uv.lock` 是跨平台、跨 Python marker 的权威锁文件；CI 只运行 `uv sync --locked`。
- `requirements.txt`、`requirements-dev.txt`、`requirements-build.txt` 是从同一个锁文件生成的、带 SHA-256 哈希的 pip 兼容导出，不手工编辑。
- 仓库不保存内部源 URL 或凭据。内部环境用 `UV_DEFAULT_INDEX`（uv）或 `SIRIUS_PACKAGE_INDEX_URL`（`start.sh` 的 pip 兼容路径）注入经运维冻结的版本化 PEP 503 地址，禁止使用 `/latest/`。
- GitHub runner 使用公共 PyPI；内部镜像必须提供锁文件中相同版本且内容哈希一致的发行包。
- coverage 基线使用锁定的 Python 3.11 环境、完整自动化测试集（排除手工 `smoke_test.py`）测得 61.0%；初始阻断阈值设为 60%，报告以 XML/JSON artifact 保存。阈值只能在有新基线证据时调整。

## 更新与验证

```bash
uv lock --upgrade
uv export --locked --no-header --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv export --locked --no-header --no-emit-project --format requirements-txt --output-file requirements-dev.txt
uv export --locked --no-header --no-dev --group build --no-emit-project --format requirements-txt --output-file requirements-build.txt
uv lock --check
python scripts/verify_locked_install.py --python 3.11
```

内部镜像验证使用同一条命令，但由执行环境安全设置 `UV_DEFAULT_INDEX`。验证脚本通过带哈希的 `requirements.txt` 从所选索引连续同步两个全新 runtime 环境，再比较规范化后的版本集合；因此不会绕过内部镜像直接使用 `uv.lock` 中记录的公共 artifact URL。输出只包含包数量与哈希，不输出镜像 URL 或凭据。

## 后果

- 锁文件和三个导出文件的 diff 会较大，但安装解析不再随时间漂移。
- 新增或升级依赖必须同步提交 `pyproject.toml`、`uv.lock` 和三个导出文件。
- 普通运行只安装 runtime 依赖；测试和构建工具不会进入生产环境。
