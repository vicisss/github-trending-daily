# GitHub Trending 日报 - 设计文档

**日期：** 2026-04-28
**状态：** 已确认

---

## 1. 概述

每天自动抓取 GitHub Trending Top 10 项目，使用 DeepSeek API 对每个项目进行深度中文分析，生成一份精美的自包含 HTML 日报，并通过飞书发送简短提醒。

## 2. 核心需求

- 全自动：每天上午 9:00 由 macOS launchd 触发
- 深度分析：每个项目 800-1500 字中文分析（项目简介、适合人群、思维发散、优缺点锐评）
- HTML 日报：深色主题，卡片式布局，自包含单文件，存档可回溯
- 飞书提醒：简短通知 "日报已生成"，不承载正文
- 低成本：使用 DeepSeek API，月成本约 ¥2-3

## 3. 架构

```
macOS launchd (每天 9:00)
       │
       ▼
  daily_github_trending.py
       │
       ├──→ ① httpx + BeautifulSoup 抓取 GitHub Trending
       │       https://github.com/trending
       │
       ├──→ ② DeepSeek API 逐项目深度分析
       │       模型: deepseek-chat
       │       每个项目独立请求，避免 token 超限
       │
       ├──→ ③ 生成 HTML 日报
       │       保存到 ~/GitHub日报/YYYY-MM-DD.html
       │       自包含、深色主题、响应式
       │
       ├──→ ④ 在浏览器打开 HTML（可选）
       │
       └──→ ⑤ 飞书 Webhook 发提醒
```

## 4. 文件结构

```
工具/
├── daily_github_trending.py    # 主脚本
├── config.yaml                  # 配置（API key、webhook、偏好）
├── com.github-trending.daily.plist  # launchd 配置
└── 日报/
    ├── 2026-04-28.html
    ├── 2026-04-29.html
    └── ...
```

## 5. 配置项 (config.yaml)

```yaml
deepseek:
  api_key: ${DEEPSEEK_API_KEY}  # 从环境变量读取
  base_url: https://api.deepseek.com
  model: deepseek-chat

feishu:
  webhook_url: ""  # 飞书机器人 Webhook 地址

daily:
  output_dir: ~/GitHub日报
  open_browser: true  # 生成后是否打开浏览器
  language: zh-CN
```

## 6. HTML 日报设计

- **主题：** 深色背景，现代简洁风格
- **顶部：** 标题 "GitHub Trending 日报" + 日期 + 一句 AI 生成的本日趋势总结
- **项目卡片：** 每个项目包含
  - 排名徽章 (#1 ~ #10)
  - 仓库名 (owner/repo) + GitHub 链接
  - Star 数 + 编程语言
  - 项目描述（从 Trending 页获取）
  - 📖 项目简介（AI 生成）
  - 🎯 适合人群（AI 生成）
  - 💡 思维发散（AI 生成，创新/变现思路）
  - ⚔️ 锐评：优点 ✅ / 缺点 ❌（AI 生成）
- **底部：** 时效声明 + GitHub Trending 来源链接
- **技术：** 纯 HTML + 内嵌 CSS，无外部依赖，可直接分享

## 7. AI 分析 Prompt 结构

每个项目发送独立请求，Prompt 分为两部分：

**System Prompt:** 角色设定 + 输出格式约束
**User Prompt:** 项目信息（名称、描述、语言、Star 数等）

要求输出结构化 markdown，便于拼入 HTML。

## 8. 错误处理

- 网络错误：重试 3 次，间隔 5s
- API 限流：退避重试
- 部分项目分析失败：标记为 "分析暂不可用"，不影响整体日报生成
- 飞书推送失败：记录日志，不影响 HTML 生成
- 全流程失败：记录错误日志到 `~/GitHub日报/error.log`

## 9. 成本估算

| 指标 | 值 |
|------|------|
| 每次 token 消耗 | ~35K input + ~8K output |
| 单次成本 | ~¥0.07 |
| 月成本（30天） | ~¥2.1 |

## 10. 不做的

- 不做历史趋势对比（保持简洁）
- 不做用户偏好定制（MVP 先全量 Top 10）
- 不做多语言支持（只看中文）
- 不做邮件推送（飞书就够了）
- 不做数据持久化（HTML 文件即数据）
