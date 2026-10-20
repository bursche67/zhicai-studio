# 知采 Studio

采购公告解析、实体抽取与关系分析网站，使用 Vue 3、ECharts、FastAPI 和 SQLite。

支持 HTML/ZIP 导入、采购附件解析、规则或模型抽取、原文证据核验、商品字段检索、采购包与供应商关系分析、关系图谱、数据质量检查和三表 XLSX/JSON/CSV 导出。

## 本地运行

环境：Python 3.13、Node.js 24。Windows PowerShell：

```powershell
./setup.ps1
./start.ps1
```

访问 http://127.0.0.1:8000 ，API 文档 http://127.0.0.1:8000/docs 。

Linux/macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
cd frontend && npm ci && npm run build && cd ..
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

开发前端可在 `frontend` 下运行 `npm run dev`。SQLite 任务队列使用单个后端 worker。

## 使用

1. 上传自有 HTML 或 ZIP；体验功能时可使用 `samples/` 中的自建样例并开启样例标记。
2. 选择规则抽取，或配置模型后选择模型抽取。
3. 在检索、分析和图谱页查看结果，下载标准三表。
4. 使用人工核验参考数据检查提取质量；字段非空率不代表准确率。

PDF、DOCX、XLSX 和 HTML 支持内置解析；旧 DOC 转换可选 LibreOffice，图片与扫描 PDF 可选 Tesseract 及中文语言包。缺少工具时会明确报告未处理。

## 模型配置

在 `.env` 设置 `MODEL_BASE_URL`（含 `/v1`）、`MODEL_NAME` 和所需的 `MODEL_API_KEY`。远端处理需显式开启 `MODEL_ALLOW_REMOTE=true`。模型配置为空时可使用规则模式，不会生成模型调用。

模型输出经过结构与连续原文引文校验。缺失字段保留空值，失败任务保留错误信息。DeepSeek 余额查询与可选内部预算估算分别展示，实际费用以提供方账单为准。

## 部署

提供 Dockerfile、Compose 和 Caddy HTTPS 配置。填写 `.env` 中的真实域名、`APP_ACCESS_USER` 与强密码 `APP_ACCESS_PASSWORD` 后运行：

```bash
docker compose up -d --build
```

服务器本地反向代理部署可用 `compose.server.yaml`，应用端口仅绑定回环地址。数据库与上传文件保存在持久卷。每次部署自行配置模型服务与访问凭据。

## 验证

```bash
python -m pytest -q
cd frontend
node --test tests/*.test.mjs
npm run build
```

真实数据回归测试在缺少可选数据集时跳过。仓库只提供自建样例，不包含业务数据库、上传原件、模型响应、密钥或服务器私有配置。
