# 健康分诊助手 · Medical Triage Agent

基于 **FastAPI + LangGraph + RAG** 的医疗智能分诊对话系统。用户在前端描述症状后，后端通过 LangGraph 状态机依次完成意图分类、危重检测、ESI 五级分诊、科室知识库问诊、安全审查和医生推荐；在急诊确认、选择城市、症状补充等场景下通过 `interrupt` 暂停对话，用户操作后再恢复执行。

> ⚠️ **免责声明**：本项目仅用于技术学习与演示，输出内容不能替代专业医生的诊断与治疗，请勿用于真实临床决策。

## 功能特性

- **五分类意图识别**：内科、外科、儿科、健康咨询、地址查询。
- **规则 + LLM 双层危重检测**：13 个危重关键词硬拦截，再由 LLM 输出 ESI 1–5 级分诊评分。
- **三科室 RAG 问诊**：内科 263 条、外科 142 条、儿科 186 条，共 591 条疾病知识；Chroma 向量检索 + `bge-reranker-large` 重排序。
- **安全审查护栏**：第二个 LLM 同时判断症状是否充分、回复是否存在用药、禁忌、过度诊断等风险。
- **症状追问**：信息不足时中断对话追问补充，最多追问 1 次，补充后回到对应科室重新回答。
- **急诊中断导航**：识别到危重后暂停并等待用户确认；确认后选择城市，调用高德 API 推荐周边医院。
- **医生推荐**：按分诊科室查询 MySQL 医生表，由 LLM 从候选医生中推荐一位并说明理由。
- **流式输出**：只透传面向用户节点的 LLM 内容，过滤分类、评分、审查等中间结构化输出。
- **多轮会话隔离**：基于 LangGraph `MemorySaver` + `thread_id` 保存会话状态。
- **H5 / App 前端**：UniApp + Vue3；H5 端使用 `fetch` + `ReadableStream` 实现真实流式渲染。

## 整体架构

### 架构概览

```mermaid
flowchart LR
    subgraph FE["前端 · UniApp + Vue3"]
        UI["对话页面<br/>中断卡片 / 会话管理"]
    end

    subgraph BE["后端 · FastAPI :8000"]
        API["/api/chat/stream<br/>/api/chat/resume"]
        GRAPH["LangGraph 状态机<br/>12 个节点 + 条件路由<br/>MemorySaver"]
    end

    subgraph DEP["依赖服务"]
        QWEN["通义千问<br/>OpenAI 兼容接口"]
        CHROMA["Chroma :8081<br/>按科室分 collection"]
        OLLAMA["Ollama<br/>nomic-embed-text"]
        RERANK["bge-reranker-large<br/>本地 CrossEncoder"]
        MYSQL["MySQL<br/>doctors 表"]
        GAODE["高德地图<br/>Web 服务 API"]
    end

    UI -->|"HTTP + 逐行 JSON 流"| API
    API --> GRAPH
    GRAPH --> QWEN
    GRAPH -->|"向量检索"| CHROMA
    GRAPH -->|"查询 embedding"| OLLAMA
    GRAPH -->|"检索结果重排序"| RERANK
    GRAPH -->|"查询医生"| MYSQL
    GRAPH -->|"周边医院搜索"| GAODE
```

系统分为三层：

- **前端**：单页对话界面，负责消息渲染、快捷提问、新建对话，以及三类中断卡片（急诊确认、城市选择、症状补充）。
- **后端**：FastAPI 暴露流式对话与恢复接口，LangGraph 状态机负责全部业务编排，`MemorySaver` 保存会话状态。
- **依赖服务**：千问负责全部 LLM 调用；Chroma + Ollama + reranker 组成 RAG 检索链路；MySQL 提供医生数据；高德提供地理位置与周边医院搜索。

后端未启用 CORS 中间件，H5 通过 `manifest.json` 中的 devServer 将 `/api` 代理到后端，以同源方式访问。

### 分诊流程

```mermaid
flowchart TD
    START([START]) --> classify["classify<br/>LLM 五分类"]
    classify --> emergency_check["emergency_check<br/>关键词硬拦截 + ESI 五级评分"]

    emergency_check -->|"is_emergency 或 level ≤ 2"| emergency["emergency<br/>interrupt：急诊确认"]
    emergency_check -->|"internal_medicine / surgery / pediatrics"| dept["科室节点<br/>RAG 检索 + 流式回答"]
    emergency_check -->|"chat"| chat["chat<br/>健康咨询"]
    emergency_check -->|"map"| map["map<br/>提取地址 + 医院推荐"]

    emergency -->|"confirm"| map_emergency["map<br/>interrupt：选择城市"]
    emergency -->|"cancel"| dept

    dept --> safety_review["safety_review<br/>充分性 + 安全性审查"]
    chat --> safety_review

    safety_review -->|"不充分且 followup_count < 1"| followup["followup<br/>interrupt：补充症状"]
    followup -->|"resume 后回到分类结果节点"| dept
    safety_review -->|"需要警示"| disclaimer["disclaimer<br/>流式输出 ⚠️ 警示"]
    safety_review -->|"通过"| doctor_node["doctor_node<br/>查 MySQL + LLM 推荐医生"]
    disclaimer --> doctor_node

    doctor_node --> END([END])
    map_emergency --> END
    map --> END
```

说明：急诊用户选择「暂不需要」时，会进入 `classify_result` 对应的节点（科室、`chat` 或 `map`），图中以科室节点示意；`followup` 恢复后同样回到 `classify_result` 对应节点。

### 节点职责

| 节点 | 职责 | 是否流式透传 |
|---|---|---|
| `classify` | 用 Pydantic 结构化输出五分类结果 | 否 |
| `emergency_check` | 13 个危重关键词硬拦截；LLM 输出 ESI 1–5 级 | 否 |
| `emergency` | `interrupt` 等待急诊确认；确认后输出行动指引并转到 `map`，取消则回到分类节点 | 是 |
| `internal_medicine` / `surgery` / `pediatrics` | 按科室检索知识库，结合 ESI 级别调整回答语气 | 是 |
| `chat` | 健康咨询 / 科普，直接基于会话历史回答 | 是 |
| `safety_review` | 第二个 LLM 同时判断症状充分性与回复安全性 | 否 |
| `followup` | `interrupt` 等待用户补充症状；恢复后计数 +1 并回到分类节点 | 否 |
| `disclaimer` | 审查发现问题时，流式生成 ⚠️ 安全警示 | 是 |
| `map` | 急诊场景中断选择城市；非急诊场景提取地址；调用高德搜索周边医院 | 是 |
| `doctor_node` | 按科室查询 MySQL 医生表，由 LLM 推荐一位；`chat` 分类跳过 | 是 |

### 条件路由

`route_after_triage`（`emergency_check` 之后）：

1. `is_emergency == true` 或 `triage_level <= 2` → `emergency`
2. 否则按 `classify_result` → 对应科室节点 / `chat` / `map`

`route_after_review`（`safety_review` 之后）：

1. `classify_result == "chat"` 且需要追加警示 → `disclaimer`
2. 症状不充分且 `followup_count < 1` → `followup`
3. 需要追加警示 → `disclaimer`
4. 其余 → `doctor_node`

### 会话状态与中断

`TriageState` 在 `MessagesState` 基础上扩展了以下字段：

| 字段 | 说明 |
|---|---|
| `messages` | LangChain 消息历史，节点输出自动追加 |
| `classify_result` | 五分类结果 |
| `is_emergency` | 是否命中危重关键词 |
| `triage_level` | ESI 分诊级别 1–5 |
| `safety_action` | `pass` 或 `append_disclaimer` |
| `safety_issues` | 审查发现的具体问题 |
| `sufficient` | 症状信息是否充分 |
| `missing_info` | 缺失的关键信息 |
| `followup_count` | 已追问次数，最多 1 次 |
| `selected_city` | 急诊场景用户选择的城市 |

三处 `interrupt` 的中断类型与恢复值：

| `type` | 触发节点 | `resume_value` |
|---|---|---|
| `emergency` | `emergency` | `confirm` / `cancel` |
| `address_select` | `map`（急诊分支） | 城市名，如 `北京` |
| `followup` | `followup` | 用户补充的症状文本 |

中断与恢复依赖 `thread_id`：流式响应结束时若触发中断，会在最后一行返回 `thread_id`；前端保存后调用恢复接口时必须传入同一个 `thread_id`。会话状态保存在进程内存中，后端重启后会清空。

## 核心模块

### 意图分类

`classify` 节点将最新一条用户消息交给千问，通过 `PydanticOutputParser` 约束输出为以下之一：

- `internal_medicine`：内科
- `surgery`：外科
- `pediatrics`：儿科
- `chat`：健康咨询 / 科普
- `map`：用户明确提供了地址信息

### 危重检测

`emergency_check` 采用规则与 LLM 两层判断：

1. **关键词硬拦截**：用户消息包含以下任一关键词即标记危重：呼吸困难、意识不清、昏迷、大出血、休克、抽搐、中毒、心脏骤停、窒息、剧烈腹痛、晕厥、吐血、高烧不退。
2. **ESI 五级评分**：LLM 根据全部用户消息输出 1–5 级：1 立即抢救、2 急诊、3 紧急、4 次紧急、5 非紧急。

两层任一命中即进入急诊分支。

### 知识库与 RAG

知识库为三份 Markdown 文件，每条疾病以二级标题组织，正文包含症状、治疗、用药和注意事项：

| 文件 | 科室 | 条目数 |
|---|---|---|
| `backend/knowledge_base/internal_medicine.md` | 内科 | 263 |
| `backend/knowledge_base/surgery.md` | 外科 | 142 |
| `backend/knowledge_base/pediatrics.md` | 儿科 | 186 |

检索链路：

1. 每个科室对应一个同名 Chroma collection，相似度使用 cosine。
2. 首次检索某科室时，若 collection 为空，自动加载对应 Markdown 并切分索引。
3. 切分参数：`chunk_size=512`、`chunk_overlap=50`，按段落、换行、中文句号/问号切分。
4. 向量检索：`similarity_score_threshold`，`k=5`，分数阈值 `0.5`；embedding 使用 Ollama 的 `nomic-embed-text`。
5. 重排序：`bge-reranker-large` CrossEncoder 对候选片段重排，保留 `top_n=3`，拼接后作为科室节点的参考资料。

科室节点会把检索到的资料与当前 ESI 级别一起交给 LLM，要求按紧急程度调整语气，资料不足时明确说明并建议就医。

### 安全审查与症状追问

`safety_review` 是一个独立的第二 LLM 节点，不向用户流式输出。它基于完整用户描述和上一节点的 AI 回复做两项判断：

- **症状充分性**：信息是否足以判断疾病；不足时列出缺失的病程、伴随症状、既往史等。
- **回复安全性**：检查用药剂量、禁忌症、危重提示、过度诊断、免责声明等问题，输出 `pass` 或 `append_disclaimer`。

若信息不足且尚未追问过，进入 `followup` 中断等待补充；若需要警示，进入 `disclaimer` 流式追加警示；否则进入 `doctor_node`。

### 急诊导航与医院推荐

- 急诊分支中，用户在中断卡片选择省 / 市后，`map` 节点调用高德：
  1. `place/text` 按城市和关键词「医院」获取中心坐标；
  2. `place/around` 以该坐标搜索周边医院，取前 10 条；
  3. 整理名称、地址、标签、营业时间，由 LLM 推荐并给出说明。
- 非急诊的 `map` 分类中，`map` 节点先从用户消息提取地址，再执行相同的医院搜索流程。

### 医生推荐

`doctor_node` 按 `classify_result` 查询 MySQL：

```sql
SELECT name, gender, age, practice_years, intro
FROM doctors
WHERE department = :dept
```

`department` 使用英文分类键（`internal_medicine` / `surgery` / `pediatrics`），不是中文科室名。查询结果全部交给 LLM，由其推荐最匹配的一位并说明理由。`chat` 分类不会推荐医生，查询无结果时也会跳过。

### 流式输出

- 后端以普通 HTTP 流式响应逐行输出 JSON（NDJSON），每行一个完整对象，不是 SSE。
- 只有白名单节点的 `on_chat_model_stream` 事件会透传给前端：`internal_medicine`、`surgery`、`pediatrics`、`chat`、`emergency`、`map`、`disclaimer`、`doctor_node`。
- `classify`、`emergency_check`、`safety_review`、`followup` 的 LLM 中间输出会被过滤，避免结构化 JSON 出现在聊天界面。
- H5 端使用 `fetch` + `ReadableStream` 边接收边渲染；App / 小程序端退化为 `uni.request`，收到完整响应后一次性展示。

## 技术栈

| 层次 | 技术 |
|---|---|
| 后端框架 | Python 3.12、FastAPI、Uvicorn |
| Agent 编排 | LangGraph（`StateGraph`、`interrupt/resume`、`MemorySaver`） |
| LLM | 通义千问，DashScope OpenAI 兼容接口，`langchain-openai` |
| RAG | Chroma 服务端、Ollama `nomic-embed-text`、`bge-reranker-large` CrossEncoder |
| 数据层 | MySQL 8、SQLAlchemy、PyMySQL |
| 地图服务 | 高德开放平台 Web 服务 API |
| 前端 | UniApp + Vue3（HBuilderX 构建） |

## 目录结构

```text
pr3/
├── backend/
│   ├── main.py                     # FastAPI 入口（0.0.0.0:8000）
│   ├── .env.example                # 环境变量模板
│   ├── requirements.txt            # Python 依赖
│   ├── knowledge_base/             # 三科室 Markdown 知识库
│   │   ├── internal_medicine.md    # 内科 263 条
│   │   ├── surgery.md              # 外科 142 条
│   │   └── pediatrics.md           # 儿科 186 条
│   ├── models/
│   │   ├── llm.py                  # 千问 LLM 初始化
│   │   └── chroma.py               # Chroma collection 与知识库索引
│   ├── routers/
│   │   └── chat_router.py          # /api/chat 流式接口
│   ├── tools/
│   │   └── nearby_search_tool.py   # 高德周边医院搜索
│   ├── triage_agent/
│   │   ├── graph.py                # LangGraph 状态机、节点与路由
│   │   └── knowledge.py            # 向量检索 + 重排序
│   └── utils/
│       ├── env_util.py             # 环境变量读取
│       └── logger.py               # 日志配置
├── frontend/
│   ├── pages/index/index.vue       # 对话主页面
│   ├── pages/index/regions.js      # 省市数据
│   ├── manifest.json               # UniApp 配置与 H5 devServer 代理
│   ├── pages.json                  # 页面路由
│   ├── App.vue
│   ├── main.js
│   └── index.html
├── README.md
└── 项目文档.md                     # 开发过程记录（非运行必需）
```

## 本地部署

### 环境要求

- Python 3.12+
- MySQL 8.x
- Chroma（以服务端模式运行，默认端口 `8081`）
- Ollama（本地 embedding 服务）
- HuggingFace 缓存目录中的 `BAAI/bge-reranker-large`
- HBuilderX（运行前端 H5）

### 1. 安装后端依赖

```bash
cd backend
pip install -r requirements.txt
```

建议先创建独立虚拟环境。

### 2. 配置环境变量

```bash
cd backend
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

然后按实际环境填写 `.env`。所有配置项见[环境变量](#环境变量)一节。`.env` 应保持在本机，不要提交到仓库。

### 3. 初始化 MySQL

创建数据库、医生表并插入示例医生数据：

```sql
CREATE DATABASE IF NOT EXISTS doctor_db DEFAULT CHARSET utf8mb4;
USE doctor_db;

CREATE TABLE IF NOT EXISTS doctors (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    name           VARCHAR(50)  NOT NULL,
    gender         VARCHAR(10)  NOT NULL,
    age            INT          NOT NULL,
    practice_years INT          NOT NULL,
    intro          TEXT         NOT NULL,
    department     VARCHAR(50)  NOT NULL,
    INDEX idx_department (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO doctors (name, gender, age, practice_years, intro, department) VALUES
('张建国', '男', 52, 28, '擅长高血压、冠心病、糖尿病等慢性病诊治', 'internal_medicine'),
('李慧敏', '女', 45, 20, '擅长普通外科、创伤清创与术后康复', 'surgery'),
('王雪梅', '女', 40, 16, '擅长小儿呼吸系统疾病、发热与消化道疾病', 'pediatrics');
```

说明：

- 每个科室可以插入多位医生，`doctor_node` 会把同科室全部医生交给 LLM 推荐。
- `department` 必须是 `internal_medicine`、`surgery`、`pediatrics` 之一。
- 如果数据库名、用户名或密码不同，请同步修改 `.env` 中的 `CONNECTION_STRING`。

### 4. 启动 Chroma

```bash
chroma run --path ./chroma_data --port 8081
```

`chroma_data/` 是向量数据目录，已在 `.gitignore` 中忽略。确保 `.env` 中的 `CHROMA_HOST`、`CHROMA_PORT` 与该服务一致。

### 5. 启动 Ollama 并拉取 embedding 模型

```bash
ollama serve
ollama pull nomic-embed-text
```

如果使用其他 embedding 模型，同步修改 `.env` 中的 `EMBEDDINGS_MODEL_NAME`。

### 6. 准备重排序模型

```bash
huggingface-cli download BAAI/bge-reranker-large --cache-dir D:/hf_cache/hub
```

并在 `.env` 中设置：

```env
HF_HOME=D:/hf_cache
```

`backend/triage_agent/knowledge.py` 按以下固定路径加载模型：

```python
_RERANKER_MODEL_PATH = os.path.join(
    hf_home,
    "hub", "models--BAAI--bge-reranker-large",
    "snapshots", "55611d7bca2a7133960a6d3b71e083071bbfc312",
)
```

如果本地缓存的 snapshot 哈希与上面不同，需要把该常量改成实际的 snapshot 路径；也可以直接改为模型名 `BAAI/bge-reranker-large`，由 `sentence-transformers` 从缓存中解析。

### 7. 启动后端

```bash
cd backend
python main.py
```

服务运行在 `http://localhost:8000`，访问 `/` 可做健康检查，预期返回：

```json
{"msg": "健康分诊助手 API 正在运行"}
```

首次提问某个科室时，若该科室的 collection 为空，后端会把对应 Markdown 知识库切分并写入 Chroma，因此该次请求会比已建好索引时慢，且必须保证 Chroma 和 Ollama 已启动。

### 8. 启动前端

1. 用 HBuilderX 导入 `frontend/` 目录。
2. 选择 **运行 → 运行到浏览器**。
3. H5 默认地址为 `http://127.0.0.1:5342`。

前端没有独立的 `package.json`，依赖 HBuilderX 内置编译。`frontend/manifest.json` 中已配置 H5 devServer：

```json
{
  "devServer": {
    "port": 5342,
    "host": "127.0.0.1",
    "proxy": {
      "/api": {
        "target": "http://localhost:8000",
        "changeOrigin": true,
        "secure": false
      }
    }
  }
}
```

如果修改了后端地址或端口，需要同步修改代理 `target`。不要直接双击打开 H5 页面，否则 `/api` 代理不生效；后端也未配置 CORS，直接跨域请求会被浏览器拦截。

### 启动顺序

```text
MySQL → Chroma → Ollama → 后端 → 前端
```

## 环境变量

配置文件为 `backend/.env`，模板见 `backend/.env.example`。

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `QWEN_MODEL_NAME` | ✅ | — | 通义千问模型名，如 `qwen-max` |
| `QWEN_API_KEY` | ✅ | — | DashScope 或兼容服务的 API Key |
| `QWEN_BASE_URL` | ✅ | — | OpenAI 兼容接口地址，如 `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `GAODE_API_KEY` | ✅ | — | 高德开放平台 Web 服务 Key |
| `GAODE_NEARBY_URL` | ✅ | — | 周边搜索接口，如 `https://restapi.amap.com/v3/place/around` |
| `GAODE_LOCATION_URL` | ✅ | — | 关键字搜索接口，如 `https://restapi.amap.com/v3/place/text` |
| `CONNECTION_STRING` | ✅ | — | SQLAlchemy MySQL 连接串，如 `mysql+pymysql://root:password@localhost:3306/doctor_db` |
| `EMBEDDINGS_MODEL_NAME` | ❌ | `nomic-embed-text` | Ollama embedding 模型名 |
| `CHROMA_HOST` | ❌ | `localhost` | Chroma 服务地址 |
| `CHROMA_PORT` | ❌ | `8081` | Chroma 服务端口 |
| `HF_HOME` | ❌ | 空 | HuggingFace 缓存根目录，用于定位 reranker；实际部署必须配置 |

## API 接口

后端统一前缀 `/api`。对话接口为流式响应，每行一个 JSON 对象（NDJSON）。

### 发起对话

```http
GET /api/chat/stream?question=我头疼发烧三天了&thread_id=demo-1
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `question` | ✅ | 用户输入 |
| `thread_id` | ❌ | 会话标识；不传时后端生成 UUID。多轮对话应传入并复用同一 ID |

### 恢复中断

```http
POST /api/chat/resume
Content-Type: application/json

{
  "thread_id": "demo-1",
  "resume_value": "confirm"
}
```

`resume_value` 取值由中断类型决定：

| 中断类型 | 取值 |
|---|---|
| `emergency` | `confirm`（需要紧急帮助）/ `cancel`（暂不需要） |
| `address_select` | 城市名，如 `北京` |
| `followup` | 用户补充的症状文本 |

### 响应消息格式

```json
{"c": "文本片段"}
{"interrupt": true, "thread_id": "demo-1", "type": "emergency", "payload": {"type": "emergency", "advice": "检测到您可能存在危重症状，建议立即就医或拨打120。"}}
{"interrupt": true, "thread_id": "demo-1", "type": "address_select", "payload": {"type": "address_select", "prompt": "请选择您所在的城市，我会为您推荐附近的医院"}}
{"interrupt": true, "thread_id": "demo-1", "type": "followup", "payload": {"type": "followup", "prompt": "为更准确判断，请补充更多的症状细节：..."}}
{"err": "错误信息"}
```

| 字段 | 说明 |
|---|---|
| `c` | 助手回复的文本片段，前端按顺序追加渲染 |
| `interrupt` | 对话已暂停，需要用户操作 |
| `type` | 中断类型：`emergency`、`address_select`、`followup` |
| `payload` | 中断携带的数据，如提示语、行动建议 |
| `err` | 后端异常信息 |

### 调试示例

```bash
# 发起对话（-N 关闭缓冲，便于观察流式输出）
curl -N -G "http://localhost:8000/api/chat/stream" \
  --data-urlencode "question=我突然呼吸困难" \
  --data-urlencode "thread_id=demo-1"

# 收到 interrupt 后恢复对话
curl -N -X POST "http://localhost:8000/api/chat/resume" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"demo-1","resume_value":"confirm"}'
```

## 运行约束

- **会话存储**：`MemorySaver` 保存在后端进程内存中，重启后历史会话和未完成的中断都会丢失。生产环境需替换为持久化 checkpointer。
- **流式能力**：只有 H5 端是真正的流式输出；App / 小程序端会等待完整响应后一次性展示。
- **数据为演示数据**：知识库和医生表均为示例内容，不能作为真实医疗依据。
- **知识库索引**：每个科室首次检索时自动建索引；修改 Markdown 后若希望重新索引，需要清空 Chroma 中对应 collection。
- **Reranker 路径**：模型通过本地 HuggingFace 缓存路径加载，部署到新机器时需要同步模型并核对 snapshot 路径。
- **前端访问方式**：H5 必须经 devServer 访问，依赖 `/api` 代理与后端通信。

## License

本项目仅用于学习与演示，未声明开源许可证。
