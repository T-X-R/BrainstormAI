# BrainstormAI

AI 群组头脑风暴 Web 应用：让多个 AI 围绕同一主题自由讨论，你可随时插话引导，并导出完整记录。

## 使用方式

### 安装依赖

在项目根目录执行：

```bash
uv sync
```

### 配置 API Key

推荐创建 `config/app.local.yaml`（会覆盖 `config/app.yaml` 的同名配置）

### 启动服务

```bash
uv run uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload
```

或：

```bash
uv run python main.py
```

### 打开与使用

- 浏览器访问 `http://localhost:8000`
- 输入讨论主题 → 选择 AI 数量（1–5）→ 开始
- 讨论过程中可发送消息引导方向
- 可暂停生成、结束会话，并导出记录（JSON）

### 常见问题

- 若提示 API Key 未设置：检查 `config/app.local.yaml` / `config/app.yaml` 的 `llm.api_key`是否已生效
