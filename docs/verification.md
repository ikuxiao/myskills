# MVP 验证记录

验证日期：2026-10-30（Asia/Shanghai）。验证环境：Windows、本地 Python 3.12.15、NiceGUI 3.18.0、pytest 9.2.0、Playwright 1.64.1 / Chromium。

## 自动化结果

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app adapters main.py tools
```

发布前复验结果：**67 passed, 1 skipped in 27.16s**。编译检查退出码为 0。

跳过的是当前 Windows 普通用户无法创建符号链接的测试。真实 Windows junction 创建与拦截测试已通过；这不等同于已在具备符号链接权限的环境验证符号链接测试。

覆盖内容：

- 创建、搜索、分类/标签、所有资源类型与重新加载持久化。
- Skill 格式验证、辅助文件、ZIP 导出、复制后默认禁用。
- 全局/工具/项目状态继承与显式禁用优先。
- 正文、元数据、辅助文件的历史恢复，以及缺失资源目录的服务级恢复。
- 并行文件写入、失败后的事务回滚、进程中断恢复、损坏 JSON/索引/状态不被覆盖。
- 本地目录扫描、导入、保留 BOM 与原始字节、许可证与来源、风险报告、审核拒绝及审核哈希绑定。
- 超大文件、路径穿越、Windows 保留名、junction 与不安全远程地址拒绝。
- Prompt 变量与缺失参数；中文/英文 Token 估算、重复内容/名称/段落、过长正文与空白优化 diff。
- Codex 原生 TOML 启停，完整辅助文件部署，无关配置与注释保留。
- 外部编辑/增删文件/启停配置冲突，非托管目标阻止覆盖，预览指纹变化阻止执行。
- 同步失败恢复、启动恢复、手动回滚及回滚前冲突检测。
- 项目部署与项目禁用优先，以及资源历史恢复后的同步文件移除。

## 实际浏览器验收

6 项相关测试使用独立进程、本机动态端口与临时数据：

1. 桌面 1440×1000 与手机 390×844 页面加载；9 个导航页面无 JavaScript 错误，手机无横向溢出。
2. 创建 Prompt → 分类/标签/搜索 → 填写变量并渲染 → 编辑正文 → 查看版本差异 → 恢复初始版本。
3. 目录导入 → 查看所有文件与许可证 → 未确认时不可批准 → 确认审核 → 默认禁用 → 显式启用 → 预览同步 → 确认 → 检查隔离目录的正文、引用文件与原生配置。
4. Token 优化 → 预览差异前原文不变 → 显式接受 → 保存新版本。
5. 健康检查、Host 校验与同数据目录的第二实例拒绝。（此项为 HTTP/进程检查，不需要操作浏览器。）
6. 修改已导入资源 → 停止服务进程 → 重启服务 → 浏览器确认启用状态 → 检查正文及历史仍然保留。

截图：

- `.runtime/screenshots/overview.png`
- `.runtime/screenshots/mobile.png`

## 真实 GitHub 下载验证

执行：

```powershell
.\.venv\Scripts\python.exe tools/verify_github.py
```

实际通过 Git 下载 `https://github.com/openai/skills`，选择 `skills/.system/skill-creator`。

- commit：`49f948faa9258a0c61caceaf225e179651397431`
- 识别资源：`skill-creator`
- 取得 7 个文件，发现 `LICENSE.txt`，产生 5 项静态检查提示。
- 已验证隔离区文件哈希、来源和 commit 记录。
- 下载后在临时目录中拒绝审核，未批准、启用或部署该资源，临时目录正常清理。

自动化测试还通过模拟下载结果覆盖远程子目录与根许可证继承；真实网络验证单独执行，日常测试不依赖 GitHub 可用性。

## 可体验实例

交付时启动地址：`http://127.0.0.1:8765`。

数据目录：项目下的 `.runtime/demo`。包含 4 个本地示例，均默认禁用；运行数据不纳入版本控制。

如关闭了实例，重新运行：

```powershell
.\.venv\Scripts\python.exe main.py --data-dir .runtime\demo --port 8765 --no-open
```

## 验证边界

未修改真实 `%USERPROFILE%\.codex\config.toml`，未向真实 `.agents/skills` 部署，也未重载正在使用的 Codex 会话。Codex 适配通过原生 TOML 与文件结果验证，真实工具会话的发现/热加载效果需在用户选择同步后观察。

未在 Linux/macOS、网络共享盘或真实断电设备上验证。中断恢复使用构造未完成事务及写入故障注入测试。Token 为静态估算；不承诺实际计费节省。安全扫描是风险提示，不是安全认证。

GitHub 搜索/更新、实际 Token 日志分析、其他工具自动适配属于设计文档的后续迭代，不在本次 MVP 中。
