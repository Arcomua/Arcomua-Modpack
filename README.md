<div align="center">
    <img width="1280" alt="Arcomua Cloth" src="https://github.com/Arcomua/Arcomua-Modpack/assets/88249678/40c8c43a-3229-48f7-8dfd-099c5cfaff7e">
    <h1>Arcomua Modpack</h1>

![img-shidld-fabric]
![img-shidld-quilt]
![img-shidld-forge]
![img-shidld-neoforge]

</div>

<p align="center">
    <img src="https://www.arcomua.com/cloth_logo.png" width="50" height="50" />
    <img src="https://www.arcomua.com/anvil_logo.png" width="50" height="50" />
</p>

**Arcomua Modpack** is mainly designed to **improve your vanilla gameplay experience**. You can get better **performance** and **better graphics** without changing the game's core mechanics.

Arcomua Modpack = Arcomua Cloth + Arcomua Anvil

[中文文档](https://support.arcomua.com/)

## Install

1. Download from [Modrinth](https://modrinth.com/modpack/arcomua).
2. Import the `.mrpack` file into a compatible launcher.
3. Enjoy!

## Workflow deployment / 工作流部署

This repository uses `arpack` and GitHub Actions to build and publish version branches to Modrinth and GitHub Releases.

本仓库使用 `arpack` 与 GitHub Actions，将版本分支自动构建并发布到 Modrinth 和 GitHub Releases。

### Requirements / 环境要求

- Git
- Python 3.11+
- A launcher or management tool that can export the Modrinth `.mrpack` format
- GitHub CLI `gh` only when using local `yank` or `restore` commands

### 1. Deploy the files to `Main` / 将工作流部署到 `Main`

Copy the workflow package into the repository root while on `Main`, then commit these central files:

在 `Main` 分支中，将工作流包复制到仓库根目录，并提交中央工具文件：

```text
.github/workflows/
arpack/
ci/
config/
pyproject.toml
README.md
README-MAINTAINERS-CN.md
README-MAINTAINERS-EN.md
```

```bash
git switch Main
git add -A
git commit -m "Deploy Arcomua publishing workflow"
git push origin Main
```

Actual pack sources are not stored on `Main`. They are stored in compact version branches such as `cloth/26.2` and `anvil/26.2`.

实际整合包源不存放在 `Main`，而是存放在 `cloth/26.2`、`anvil/26.2` 等精简版本分支中。

### 2. Install `arpack` / 安装 `arpack`

Run from the repository root:

在仓库根目录运行：

```bash
python -m pip install --upgrade .
arpack --help
```

Windows can use:

```powershell
py -3 -m pip install --upgrade .
arpack --help
```

### 3. Configure GitHub / 配置 GitHub

Create this repository Actions secret:

在仓库的 Actions Secrets 中创建：

```text
MODRINTH_TOKEN
```

The token must be able to create and manage versions for the configured Modrinth projects.

该 Token 必须能够创建和管理配置中的 Modrinth 项目版本。

The workflows require repository content write permission so they can create tags and GitHub Releases.

工作流需要仓库内容写入权限，以创建标签和 GitHub Releases。

### 4. Verify / 验证

```bash
arpack --help
```

Available commands should include:

```text
import
check
yank
restore
```

For the day-to-day publishing procedure, see:

日常发布流程参见：

- [中文维护流程](README-MAINTAINERS-CN.md)
- [English maintainer workflow](README-MAINTAINERS-EN.md)

<!-- Links -->
[img-shidld-fabric]: <https://img.shields.io/badge/Mod%20Loader-Fabric-dbd0b4?style=flat>
[img-shidld-quilt]: <https://img.shields.io/badge/Mod%20Loader-Quilt-9f00ff?style=flat>
[img-shidld-forge]: <https://img.shields.io/badge/Mod%20Loader-Forge-272f3d?style=flat>
[img-shidld-neoforge]: <https://img.shields.io/badge/Mod%20Loader-NeoForge-d37731?style=flat>
