# Contributing to RAG Proxy

Thank you for helping improve RAG Proxy.

感谢你帮助改进 RAG Proxy。

## Before You Start

Please search existing Issues before opening a new one. For a bug, include the steps to reproduce, the expected result, the actual result, and relevant logs. Do not include API keys, passwords, or private documents.

提交 Issue 前，请先搜索已有 Issue。报告 Bug 时，请写清楚复现步骤、预期结果、实际结果和相关日志。不要提交 API Key、密码或私有文档。

## Development

The project has a FastAPI backend and a Vue frontend.

```powershell
python -m compileall -q ragproxy
cd frontend
npm install
npm run build
```

The frontend build output is written to `static/` and should be included when the project expects a built frontend.

## Pull Requests

Keep changes focused and explain why the change is needed. Update the README or translations when user-facing behavior changes. Run the relevant checks before opening a pull request.

保持改动集中，并说明为什么需要这次改动。如果用户看到的功能发生变化，请同步更新 README 或翻译。提交 Pull Request 前，请运行相关检查。

Do not commit:

- secrets, API keys, or private data;
- local configuration files;
- generated caches or temporary files.

## Code Style

Follow the existing style of the file you are editing. Avoid unrelated refactors. Keep user-facing text simple and add translations when practical.

遵循正在修改的文件已有风格。不要顺便做无关重构。用户看到的文字尽量简单，并在条件允许时补充翻译。
