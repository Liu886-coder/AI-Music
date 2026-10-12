# 贡献指南

感谢你对智能音乐推荐 Agent 的关注。提交 Issue 或 Pull Request 前，请先确认问题尚未被记录，并尽量提供可复现步骤、运行环境和相关日志片段。请删除日志中的 API Key、访问令牌和个人数据。

## 本地开发

1. 按 [README.md](README.md) 启动后端和前端。
2. 在 `backend` 目录运行 `python -m pytest tests -q`。
3. 在 `frontend` 目录运行 `npm run build`。
4. 提交前确认没有加入 `.env`、数据库、日志、虚拟环境或 `node_modules`。

## Pull Request

- 说明改动解决的问题和用户可观察到的行为变化。
- 为行为变化补充或更新测试。
- 保持提交范围聚焦，并说明尚未验证的真实供应商行为。
- 不要提交真实 API Key、用户数据或供应商响应中的敏感信息。

## 代码风格

后端遵循现有的 Python、FastAPI、Pydantic 和 SQLAlchemy 结构；前端遵循现有的 Vue 3 和 Vite 结构。优先复用已有模块和测试工具。
