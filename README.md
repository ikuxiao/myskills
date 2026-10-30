# AI Dev Center · AI 开发效率与资源管理中心

依据 [v1.md](v1.md) 实现的本地 MVP：用一个中文界面管理 Skills、Prompts、Rules 和开发技巧，支持文件版本、导入审核与 Codex Skill 同步。

Python 3.12+，NiceGUI + JSON + Markdown。无需数据库、Node.js、前端构建或大模型 API。页面及字体/脚本资源由本机 NiceGUI 提供，核心功能无需网络。

## Windows 启动

推荐使用 uv 启动脚本，自动选择 Python 3.12 并按 `requirements.txt` 准备依赖，无需手动激活虚拟环境：

```powershell
cd C:\path\to\myskills
uv run start.py
```

也可以运行 `python start.py`，脚本会调用 PATH 中的 uv。尚未安装 uv 时先执行 `python -m pip install uv`。

所有参数透传到 `main.py`；相对路径以项目目录为基准，例如：

```powershell
uv run start.py --data-dir .runtime\demo --port 8765 --no-open
```

首次准备 Python 或缺失依赖需要联网；后续启动可复用 uv 缓存与已有环境。缺少 uv 或启动失败时脚本返回非零退出码。

当前工作区已安装依赖，可直接运行：

```powershell
cd C:\path\to\myskills
.\.venv\Scripts\python.exe main.py
```

首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

如果使用 uv 管理 Python：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

打开 `http://127.0.0.1:8765`。默认仅监听 `127.0.0.1`，浏览器会自动打开。关闭终端中的进程即可停止服务。

可指定独立工作区与端口：

```powershell
.\.venv\Scripts\python.exe main.py --data-dir .runtime\demo --port 8765 --no-open
```

- 默认数据位置：Windows `%LOCALAPPDATA%\AI-Dev-Center`；其他系统 `$XDG_DATA_HOME/ai-dev-center` 或 `~/.local/share/ai-dev-center`。
- `--no-seed`：首次启动不生成 4 个示例资源。示例资源均默认禁用。
- 同一数据目录只允许启动一个服务实例；多实例需同时切换数据目录与端口。
- 健康检查：`GET /api/health`。

## 已实现的第一阶段能力

- 总览：资源数量、已启用 Skill 数、静态 Token 估算、待审核项和最近操作记录。
- 资源库：创建、编辑、复制、完整 ZIP 导出，分类、标签、全文搜索。
- Skills：YAML frontmatter 验证，扫描已有目录，导入完整附属文件，正文与辅助文本文件编辑。
- Prompts：`{{variable}}` 占位符、必填变量检查、渲染、复制和 Markdown 导出。
- Rules 与开发技巧：统一资源服务和 Markdown 编辑，可按语言或主题分类。
- 历史：完整正文、元数据、二进制与辅助文件快照；正文 diff 和版本恢复。回滚本身也生成新版本。
- 状态：全局、Codex 工具、项目三级设置；任一级显式禁用优先，未设置继承，默认禁用。
- Codex：原生 `[[skills.config]]` 配置，先预览后确认，完整文件冲突检测、写后校验、同步备份和手动回滚。
- Token 分析：中英文加权静态估算、相同正文/名称/重复段落、长正文提醒。空白清理需预览 diff 并接受，保存后可回滚。
- 导入审核：本地目录或公开 GitHub 仓库，完整文件清单、来源、Git commit、许可证及风险提示；批准后仍禁用。
- 可靠存储：全局文件锁、临时文件 + 原子替换、跨文件事务日志、启动时中断恢复、关键配置备份、编辑版本冲突检测。

## 建议的首次操作

1. 在 Prompts 中打开“代码审查模板”，点击“使用模板”填写变量并复制。
2. 在 Skills 中编辑或创建 Skill，正文必须包含合法的 `name` 与 `description` frontmatter。
3. 已有资源通过“导入与审核”扫描或导入。检查许可证、所有文件和风险提示后批准。
4. 在 Skill 卡片上打开全局开关，需要时在 Codex 同步页设置工具或项目级限制。
5. 在“Codex 同步”选择目录，先预览；无冲突时点击确认同步。
6. 重新加载 Codex 会话。原生配置变更不承诺在已开启的会话中即时生效。

## Codex 同步的范围与冲突处理

“Codex 配置目录”默认 `~/.codex`，配置文件为其中的 `config.toml`。

- 用户全局 Skill：部署到配置目录同级的 `.agents/skills/adc-<资源 ID>/`。
- 项目 Skill：部署到指定项目的 `.agents/skills/adc-<资源 ID>/`，项目目录必须已存在。
- 为每个部署的 `SKILL.md` 维护一个绝对路径的 `[[skills.config]]` 条目，使用 `enabled = false` 禁用而不删除 Skill。
- 保留无关 TOML 字段、表和注释。`.adc-manifest.json` 记录本工具托管的完整文件哈希。
- Prompt、Rules、技巧仅支持复制或导出。不会声称它们已经被 Codex 自动加载。
- 托管目标的正文、辅助文件、额外文件、缺失文件或启停配置有外部变化时，停止同步。
- 预览后任何相关资源、状态或目标配置发生变化，要求重新预览。
- 非托管同名目录或原生配置条目不会被直接覆盖。

发生冲突时，先保留目标文件，把需要保留的变化复制回资源库；可通过“附属文件”编辑对应内容。若需要重新建立同步关系，可在停止程序后备份并移动冲突的 **单个** `adc-<ID>` 目录，同时从 `.adc-manifest.json` 和 `config.toml` 中移除它对应的托管条目，再预览；不要删除其他工具或用户的条目。

同步页记录中的“回滚本次同步”恢复该次写入前的精确字节。若同步后文件又被修改，则阻止回滚，防止覆盖新内容。同步失败会自动恢复文件和配置；异常断电后的未完成事务在下次启动时恢复。空目录可能保留，不包含用户文件。

官方配置依据：

- https://developers.openai.com/codex/skills
- https://developers.openai.com/codex/config-reference

适配已在隔离目录验证 TOML 和文件结果；未操作本机正在使用的真实 Codex 配置，也未自动重启当前 Codex 会话。

## 导入与审核

本地导入请选择单个资源目录。Skill 根目录应包含 `SKILL.md`；其他类型应含 `content.md`，或仅有一个主要 Markdown 文件。

远程输入示例：仓库 `https://github.com/owner/repo`，仓库内目录 `skills/example`。仅支持公开 HTTPS GitHub 仓库根地址；不支持凭据、查询参数、GitHub tree URL、私有仓库或子模块。远程下载需要本机安装 Git，有 90 秒超时。GitHub 搜索、远程更新检查和差异更新尚属后续迭代。

导入限制为 500 个文件、单文件 2 MB、总大小 20 MB。拒绝路径穿越、符号链接和 Windows junction；跳过版本库内部目录。

所有导入进入 `staging/`，显示脚本、网络行为、危险命令、可疑指令与凭据文件提示。本工具不执行导入内容或安装命令。审核与文件哈希绑定；待审核内容被修改后必须重新导入。批准仅加入本地库，不会自动启用。拒绝后也保留原件及来源记录。

安全扫描为静态启发式，不能代替人工审核；许可证文件原文可在文件清单中查看，不自动认定授权有效。

## 数据、备份与恢复

```text
<data-dir>/
  data/resources.json        资源索引、分类、来源
  data/states.json           三级状态
  data/settings.json         工具路径
  data/imports.json          审核状态与清单
  data/events.jsonl          操作记录
  data/sync_records.jsonl    同步记录
  library/<type>/<id>/       原始 Markdown 与辅助文件
  history/resources/<id>/    完整资源快照
  staging/imported/<id>/     待审核/原始导入
  backups/                  关键 JSON 的旧版本
  backups/sync/             同步前字节快照
  logs/app.log              可轮换日志
```

关闭程序后复制整个数据目录即可备份。不要只复制索引：正文、状态、历史和备份需要一并保留。

JSON 损坏时程序会报错并保留损坏文件，不会用空索引覆盖。恢复步骤：

1. 关闭服务，先复制整个数据目录。
2. 在 `backups/` 按修改时间找到对应文件的最近有效版本，如 `*-states.json` 或 `*-resources.json`。
3. 将选定备份复制到 `data/` 中对应文件名，再启动程序。
4. 如恢复较旧的资源索引，创建时间较新的资源文件仍保留在 `library/`，可经导入与审核重新加入索引。

`backups/sync/<id>.json` 使用 Base64 保存原始字节，优先通过同步页执行恢复。不要随意编辑事务日志 `.transaction.json` 或 `data/sync_transaction.json`；它们用于中断恢复。

## 验证

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app adapters main.py
```

只运行不需要浏览器的验证：

```powershell
.\.venv\Scripts\python.exe -m pytest --ignore=tests/test_browser.py -q
```

浏览器测试自动启动独立临时服务、分配本机端口，并使用隔离的 Codex 目录。测试不会同步到真实用户配置。截图保存到 `.runtime/screenshots/`，实际验证结果见 `docs/verification.md`。

可选的真实网络导入验证（下载公开官方仓库，在临时目录中审查后拒绝，不部署）：

```powershell
.\.venv\Scripts\python.exe tools/verify_github.py
```

## 首版边界

Token 数是透明启发式估算，未使用模型 tokenizer，不能代表计费、实际上下文或真实节省。重复检测覆盖正文、名称和相同段落，不进行大模型语义能力判断。优化仅清理空白，Markdown 行尾空格可能表示换行，接受前仍需检查差异。

外部 Git 仓库整库克隆后才对选定资源应用大小上限；请优先选择可信且大小合理的公开仓库。首版不提供后台任务持久队列、GitHub 全站搜索、自动更新、实际 Token 使用日志分析或其他 AI 工具的自动适配。

本工具定位为可信本机用户单实例服务，未设计多人登录、远程开放或网络共享盘数据目录。禁止直接暴露到公网。
