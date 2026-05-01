# 知识库管理系统

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

浏览器打开 http://localhost:8000

## 功能

- Markdown 笔记编辑（实时预览 + 代码高亮）
- 分类目录 + 标签系统
- 全文搜索（FTS5）
- 深色/浅色主题切换 + 中/英文切换
- AI 写作助手（接入 DeepSeek V4 API）

## AI 配置

1. 点击顶栏 ✨ 按钮进入 AI 写作页面
2. 点击齿轮图标展开设置面板
3. 填入 DeepSeek API Key（`sk-...`）
4. 选择模型：Flash（快速）或 Pro（旗舰）

API Key 仅保存在本地 `config.json`，不会上传。

## 技术栈

- 后端：Python + FastAPI + SQLAlchemy + SQLite
- 前端：HTML + Tailwind CSS + Alpine.js（CDN 零构建）
- 搜索：SQLite FTS5 全文索引
- AI：DeepSeek V4 API（OpenAI 兼容格式）
