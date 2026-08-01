# Arcomua Modpack 工作流维护说明（v4）

## 1. 分支结构

`Main` 是控制分支，只保存公共工具、GitHub Actions、产品配置和文档。它不保存实际整合包的 `pack/` 或活动发布信息。

每个整合包版本分支只保存：

```text
.github/workflows/publish-version.yml
.gitignore
pack/
release/
```

版本分支命名：

```text
cloth/<Minecraft版本>
anvil/<Minecraft版本>
```

例如：

```text
cloth/26.2
anvil/26.2
```

新版本分支使用孤立分支创建，不继承 `Main` 的全部历史和文件。旧的 v3 版本分支可使用 `arpack clean-branch` 清理当前目录结构。

## 2. 环境要求

- Git
- Python 3.11 或更高版本
- 任意能够把整合包导出为 Modrinth 格式 `.mrpack` 的启动器或管理工具
- GitHub CLI `gh`，仅在本地执行 `yank` 或 `restore` 时需要

## 3. 安装或升级 `arpack`

在 `Main` 分支仓库根目录运行：

```bash
python -m pip install --upgrade .
```

检查：

```bash
arpack --help
```

## 4. 从 v3 升级

在 `Main`：

```bash
git switch Main
git pull --ff-only origin Main
```

复制 v4 文件后，删除不应保存在 `Main` 的活动发布源：

```bash
git rm -r pack release
```

提交：

```bash
git add -A
git commit -m "Upgrade Arcomua workflow to v4"
git push origin Main
python -m pip install --upgrade .
```

清理当前已有的版本分支，例如 `cloth/26.2`：

```bash
git switch cloth/26.2
git pull --ff-only origin cloth/26.2
arpack clean-branch --commit --push
```

确认时输入 `CLEAN`。该命令保留 `pack/` 和 `release/`，删除从 `Main` 重复继承的工具、文档和产品目录。

## 5. 导入并发布整合包

从任意兼容工具导出 `.mrpack`，然后在本地仓库运行：

```bash
arpack import "/path/to/export.mrpack" \
  --channel release \
  --notes-file "/path/to/notes.md"
```

工具会自动：

1. 识别 Fabric 或 NeoForge；
2. 读取 Minecraft 版本；
3. 创建或切换到 `cloth/<版本>` 或 `anvil/<版本>`；
4. 新建分支时使用孤立分支；
5. 将 `.mrpack` 解压到版本分支的 `pack/`；
6. 生成 `release/release.toml`；
7. 本地生成 Changelog 和最终 `.mrpack`；
8. 检查构建是否可复现。

## 6. 版本号与版本副标题

基础版本号自动生成：

```text
<Minecraft版本>-<加载器小写名称>-<YYMMDD>
```

例如：

```text
26.2-fabric-260801
26.2-neoforge-260801
```

Modrinth 的版本副标题使用同样的组成部分，但用空格分隔，并采用加载器正式名称：

```text
26.2 Fabric 260801
26.2 NeoForge 260801
```

## 7. 同一天发布多个版本

使用可选的 `--release-id`：

```bash
arpack import export.mrpack \
  --channel beta \
  --release-id fix1
```

生成：

```text
版本号：26.2-fabric-260801-fix1
Modrinth副标题：26.2 Fabric 260801 fix1
Git标签：cloth-26.2-260801-fix1
文件：Arcomua-Cloth-26.2-Fabric-260801-fix1.mrpack
```

`release-id` 支持 1–32 个字母、数字、点、下划线或连字符。不指定时保持原来的日期版本格式。

## 8. Channel

必须手动选择：

```text
--channel release
--channel beta
--channel alpha
```

该值会直接同步到 Modrinth。

## 9. 本地检查

```bash
arpack check
```

结果位于：

```text
dist/
├── *.mrpack
├── *.mrpack.sha256
├── CHANGELOG.md
└── release-metadata.json
```

使用一个全新实例导入 `dist/*.mrpack`，确认下载、启动、世界加载、配置和资源包均正常。

## 10. 提交和发布

```bash
git add -A
git commit -m "Release Arcomua Cloth 26.2"
git push -u origin cloth/26.2
```

也可以自动提交并推送：

```bash
arpack import export.mrpack \
  --channel release \
  --release-id fix1 \
  --commit \
  --push
```

版本分支推送后，GitHub Actions会自动上传 Modrinth并创建 GitHub Release。

## 11. 下架和恢复

在目标版本分支运行：

```bash
arpack yank --reason "Crash on startup"
```

指定带 `release-id` 的版本：

```bash
arpack yank \
  --line cloth \
  --minecraft 26.2 \
  --date 260801 \
  --release-id fix1 \
  --reason "Crash on startup"
```

恢复：

```bash
arpack restore --line cloth --minecraft 26.2 --date 260801 --release-id fix1
```
