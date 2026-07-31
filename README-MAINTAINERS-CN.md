# Arcomua Modpack Maintain Instruction

## 1. 仓库组织结构

`Main` 是控制分支，保存公共脚本、GitHub Actions、配置和维护文档。实际整合包按整合包名称和 Minecraft 版本保存到长期分支：

```text
cloth/26.1.2
cloth/26.2
anvil/26.1.2
anvil/26.2
```

映射规则：

```text
Fabric   → Arcomua Cloth → cloth/<Minecraft版本>
NeoForge → Arcomua Anvil → anvil/<Minecraft版本>
```

维护者仍需使用外部工具制作、更新和测试整合包。仓库中的工具只负责导入、校验、生成 Changelog、构建和发布。

## 2. 初次安装

### 2.1 环境要求

需要：

- Git
- Python 3.11 或更高版本
- 允许你把整合包导出为 Modrinth 格式的游戏启动器
- GitHub CLI `gh`，仅在使用一键下架或恢复命令时需要

### 2.2 安装 `arpack`

安装：

```bash
python -m pip install --upgrade .
```

检查：

```bash
arpack --help
```

没有全局命令时，也可以运行：

```bash
python -m arpack --help
```

### 2.3 配置 GitHub

在仓库中创建 Actions Secret：

```text
MODRINTH_TOKEN
```

该 Token 必须能管理 Arcomua Cloth 和 Arcomua Anvil 的版本。

进入：

```text
Settings → Actions → General → Workflow permissions
```

选择：

```text
Read and write permissions
```

如需使用本地一键下架命令，安装 GitHub CLI 后执行：

```bash
gh auth login
```

## 3. 日常发布流程

### 第一步：在外部工具中维护

建议每个整合包至少保留两个实例：

```text
Arcomua Cloth - Dev
Arcomua Cloth - Clean Test
```

在 `Dev` 中更新 Mod、调整配置并启动游戏测试。

### 第二步：导出 `.mrpack`

使用外部工具把整合包导出为 Modrinth 格式。其中包内填写的临时版本号不重要，`arpack` 会自动改写。

### 第三步：准备可选的手写 Changelog

例如创建仓库外的 `notes.md`：

```markdown
- Fixed the default graphics preset.
- Improved compatibility with several resource packs.
```

### 第四步：导入并本地检查

```bash
arpack import "/path/to/Arcomua-export.mrpack" \
  --channel beta \
  --notes-file "/path/to/notes.md"
```

Windows PowerShell 可写成一行：

```powershell
arpack import "D:\Modpack\Arcomua-export.mrpack" --channel beta --notes-file "D:\Modpack\notes.md"
```

工具会自动：

1. 识别 Fabric 或 NeoForge；
2. 读取 Minecraft 版本；
3. 创建或切换到对应版本分支；
4. 按 UTC+8 当前日期生成 `YYMMDD`；
5. 将版本号改成 `<Minecraft版本>-<加载器>-<YYMMDD>`；
6. 解包到 `pack/`；
7. 生成 `release/release.toml`；
8. 生成 Changelog；
9. 两次构建 `.mrpack` 并校验可复现性。

例如在 2026 年 7 月 31 日导入 Cloth 26.2：

```text
分支：cloth/26.2
版本号：26.2-fabric-260731
标签：cloth-26.2-260731
文件：Arcomua-Cloth-26.2-Fabric-260731.mrpack
```

发布通道必须手动指定：

```text
--channel release
--channel beta
--channel alpha
```

只有迁移历史版本时才使用日期覆盖：

```bash
arpack import pack.mrpack --channel release --date 260701
```

### 第五步：查看生成结果

```bash
arpack check
```

产物位于：

```text
dist/
├── *.mrpack
├── *.mrpack.sha256
├── CHANGELOG.md
└── release-metadata.json
```

### 第六步：全新导入测试

将 `dist/*.mrpack` 导入 `Clean Test` 实例，至少确认：

- Mod 能完整下载；
- 加载器与 Minecraft 版本正确；
- 能进入主菜单；
- 能创建并进入世界；
- 配置和资源包生效；
- 没有缺失依赖或启动崩溃。

### 第七步：提交和发布

确认无误后：

```bash
git add pack release
git commit -m "Release Arcomua Cloth 26.2 Fabric 260731"
git push -u origin cloth/26.2
```

也可以在导入时自动提交并推送：

```bash
arpack import pack.mrpack \
  --channel release \
  --notes-file notes.md \
  --commit \
  --push
```

推送版本分支后，GitHub Actions 会自动：

1. 再次校验；
2. 自动生成 Changelog；
3. 构建 `.mrpack`；
4. 发布到 Modrinth；
5. 创建 GitHub Release。

## 4. 自动 Changelog

每个整合包和 Minecraft 版本分支的第一次发布只生成：

```text
Initial version
```

第二次及以后会与上一个对应标签比较，自动列出：

```markdown
## Mod changes

### Added
- 新增的 Mod

### Updated
- Mod：旧版本 → 新版本

### Removed
- 删除的 Mod

## Maintainer notes
- 维护者手写内容
```

手写内容保存在：

```text
release/manual.md
```

## 5. 一键下架有 Bug 的版本

下架操作是可逆的：

- Modrinth 版本改为 `archived`；
- GitHub Release 改为草稿；
- Git 标签和历史记录保留。

在目标版本分支运行：

```bash
arpack yank --reason "Crash on startup"
```

程序会自动读取当前分支、Minecraft 版本和 `release/release.toml` 中的日期。

也可以在 `Main` 或任意分支明确指定：

```bash
arpack yank \
  --line cloth \
  --minecraft 26.2 \
  --date 260731 \
  --reason "Crash on startup"
```

执行前需要输入：

```text
YANK
```

跳过确认：

```bash
arpack yank --yes
```

只查看目标、不执行：

```bash
arpack yank --dry-run
```

没有安装 GitHub CLI 时，可以打开：

```text
GitHub → Actions → Manage published version → Run workflow
```

选择 `yank` 并填写产品、Minecraft 版本和日期。

## 6. 恢复下架版本

确认问题不存在后：

```bash
arpack restore
```

或明确指定：

```bash
arpack restore --line cloth --minecraft 26.2 --date 260731
```

恢复操作会把 Modrinth 状态改回 `listed`，并重新公开 GitHub Release。

## 7. 重要限制

版本号严格使用：

```text
<Minecraft版本>-<加载器>-<YYMMDD>
```

因此同一整合包、同一 Minecraft 版本和同一加载器**每天只能发布一个不同内容的版本**。如果同一天发布的版本有 Bug，应先下架；新的正式版本应在新的日期发布。重复运行完全相同的构建不会重复上传。
