# Arcomua Modpack 维护流程


> Changelog 说明：如果当前 Minecraft/加载器版本线此前没有 workflow 发布标签，自动部分会显示 `Initial version`。如果本次导入使用了 `--notes-file` 或 `--note`，维护者说明仍会追加到 `Initial version` 后面。

## 1. 安装 `arpack`

环境要求：Git、Python 3.11+，以及能够导出 Modrinth `.mrpack` 格式的启动器或管理工具。

在仓库根目录执行：

```bash
python -m pip install --upgrade .
arpack --help
```

Windows也可以使用：

```powershell
py -3 -m pip install --upgrade .
arpack --help
```

使用本地一键下架或恢复功能时，还需要安装并登录 GitHub CLI：

```bash
gh auth login
```

## 2. 制作并导出整合包

在外部工具中完成 Mod增删、更新、配置调整和游戏内测试，然后导出 Modrinth格式的 `.mrpack`。

可在仓库外创建手写更新说明，例如 `notes.md`：

```markdown
- Fixed the default graphics preset.
- Improved resource-pack compatibility.
```

## 3. 导入并生成发布源

普通发布：

```bash
arpack import "/path/to/exported.mrpack" \
  --channel release \
  --notes-file "/path/to/notes.md"
```

同一天发布多个版本时增加 `--release-id`：

```bash
arpack import "/path/to/exported.mrpack" \
  --channel beta \
  --release-id fix1 \
  --notes-file "/path/to/notes.md"
```

可选发布通道：

```text
release
beta
alpha
```

`arpack` 会自动：

1. 识别 Fabric或NeoForge及Minecraft版本；
2. 创建或切换到 `cloth/<版本>`、`anvil/<版本>`；
3. 生成 `<mc>-<loader>-<YYMMDD>[-release-id]` 版本号；
4. 将 `.mrpack` 解包到版本分支的 `pack/`；
5. 识别远程 Mod和 `overrides/mods/` 内嵌 JAR的增删更新；
6. 清理 `options.txt` 中的 `lastServer`，并将语言改为 `en_us`；
7. 生成 Changelog并完成本地可复现构建检查。

## 4. 检查构建结果

可再次运行：

```bash
arpack check
```

查看：

```text
dist/*.mrpack
dist/*.mrpack.sha256
dist/CHANGELOG.md
dist/release-metadata.json
```

将 `dist/*.mrpack` 导入一个全新实例，确认能够下载、启动和进入世界。

## 5. 提交并发布

确认无误后：

```bash
git add -A
git commit -m "Release Arcomua Cloth 26.2"
git push -u origin cloth/26.2
```

也可以让导入命令自动提交和推送：

```bash
arpack import pack.mrpack \
  --channel release \
  --notes-file notes.md \
  --commit \
  --push
```

推送版本分支后，GitHub Actions会自动构建、上传Modrinth，并创建GitHub Release。

## 6. 下架或恢复版本

下架当前版本分支记录的版本：

```bash
arpack yank --reason "Crash on startup"
```

恢复：

```bash
arpack restore
```

也可以在 GitHub 网页中运行：

```text
Actions → Manage published version → Run workflow
```
