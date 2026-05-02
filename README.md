# 知识库管理系统

个人知识管理工具，支持 Markdown 笔记编辑、AI 辅助写作、知识图谱可视化。

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

浏览器打开 http://localhost:8000

## 功能

### 笔记管理
- Markdown 编辑（双栏实时预览 + 代码高亮 + 表格/图片支持）
- 分类目录 + 标签系统
- 全文搜索（SQLite FTS5）
- 批量操作（批量删除 / 添加标签 / 移动分类）

### 知识图谱
- `/graph` 页面，D3.js 力导向图展示笔记关联
- 共享标签或同分类的笔记自动连线
- 支持拖拽、缩放，点击节点跳转到笔记

### AI 写作助手
- `/ai` 页面，DeepSeek V4 API 生成知识笔记（标题 + 正文 + 分类 + 标签）
- **AI 摘要**：笔记详情页一键生成摘要 + 相关话题推荐
- **AI 润色**：编辑器内一键润色改写 Markdown 内容
- 分场景模型选择：文章生成 / 摘要 / 润色 / 标签建议 / 相关话题各可独立设置模型

### 主题与语言
- 深色/浅色主题切换
- 中文/英文界面切换
- 时间自动转换为用户本地时区显示

## AI 配置

1. 点击顶栏齿轮图标（⚙）打开 AI 设置
2. 填入 DeepSeek API Key（`sk-...`）
3. 选择默认模型：Flash（快速）或 Pro（旗舰）
4. 可分别为各场景指定不同模型

API Key 仅保存在本地 `config.json`，不上传到任何第三方。

## 技术栈

- 后端：Python + FastAPI + SQLAlchemy + SQLite
- 前端：HTML + Tailwind CSS + Alpine.js（CDN 零构建）
- 搜索：SQLite FTS5 全文索引
- 图谱：D3.js v7
- AI：DeepSeek V4 API（OpenAI 兼容格式）
