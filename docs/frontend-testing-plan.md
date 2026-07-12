# 前端拆分 + 自动测试计划

交接文档：由独立开发者执行。执行中如与实际代码冲突，以"验收标准"和"红线"为准，细节可调整；拿不准就问，不要猜。

## 0. 现状与动机

`static/app.js`：**7244 行单文件、275 个顶层函数、约 90 个共享全局变量**，纯 `<script>` 全局脚本（无模块化、无 package.json、无测试）。UI、状态、WebSocket、渲染、登录、计费全部混在一起。

近期多个 live bug 出在"后端数据正确、前端渲染错误"（例：2026-07-12 token 语言切换把同一 `llm_sentence_id` 拆成两个显示块，LLM 译文在每块下重复渲染）。修显示层像拆弹——没有测试、改一处怕碰坏三处。本计划一次解决两件事：**拆成可测的模块 + 给关键模块配自动测试**。

另有一个扫描中发现的**已知欠账**（见 §6）：app.js 内嵌了一份 Python `sentence_segmentation.py` 的手抄 JS 副本（`isSentenceEnderAt` / 缩写 / 小数 / 引号规则，约 3817–3930 行），Python 侧 2026-07-11/12 新增的"省略号不断句、未闭合引号不断句"规则 JS 副本没有，两边已经漂移。

## 1. 目标模块划分

按 app.js 现有功能簇划分（行号为当前近似位置，执行时以函数名为准）：

| # | 模块文件（static/js/） | 内容 | 现行号段 | 全局状态耦合 | 测试优先级 |
|---|---|---|---|---|---|
| 1 | `segmentation.js` | 句尾/缩写/小数/引号/分段规则（Python 镜像） | 3817–3930 | 无（纯函数） | ★★★ 必测 |
| 2 | `token-stream.js` | `insertFinalToken` / `mergeFinalTokens` / `joinTokenText` / `assignSequenceIndex` | 3693–3790, 3933 | allFinalTokens 等少量 | ★★★ 必测 |
| 3 | `render-model.js` | token 流 → 句子/块模型（`renderSubtitles` 前半段的构建逻辑）+ `buildRenderTokens` + 译文按 id 附着 | 4417–4600 | retract/override 等 Map | ★★★ 必测 |
| 4 | `refine-state.js` | refine 结果/确认/撤回/直译覆盖各 Map + `handleBackendRefineResult` / `handleSubtitleRetract` / `cleanupSentenceCaches` | 679–700, 1499–1660 | 自持有 Map | ★★★ 必测 |
| 5 | `render-html.js` | 句子/块模型 → HTML（`renderSubtitles` 后半段、token span、RTL、East-Asian 间距、escapeHtml、增量缓存） | 4001–5136 | renderedSentences 缓存 | ★★ DOM 层测 |
| 6 | `furigana.js` | kuromoji 假名注音 | 4015–4144 | tokenizer 单例 | ★ 可后置 |
| 7 | `ws-client.js` | `connect` / `handleMessage` 分发 | 3424–3692 | 大量（分发中心） | ★★ 集成层测 |
| 8 | `settings-store.js` | localStorage 读写（server/provider/mode/各开关） | 439–700, 5202+ | localStorage | ★★ 必测（纯读写易测） |
| 9 | `settings-ui.js` | 设置面板、pickers、popover、custom select、主题 | 1043–3100, 5138–5900 | DOM 重度 | ★ 冒烟即可 |
| 10 | `hosted.js` | 登录流程、余额条、计费估算、客户端更新弹窗 | 5900–7244 | DOM + 轮询 | ★ 计费纯函数必测 |
| — | `app.js`（保留） | 启动编排、共享可变状态的属主、把状态传给各模块 | 其余 | — | — |

计费相关纯函数（`compareVersions` / `formatCredits` / `estimatedSessionCost` / `sttRateMultiplier` 等）虽在 hosted 簇，属于"算错钱"级别，单独拉进必测名单。

## 2. 技术决策

### 2.1 模块形式：window 命名空间 + 双导出，不用 ESM、不引入构建工具

```js
// static/js/segmentation.js
(function (root) {
    function isSentenceEnderAt(value, index) { /* ... */ }
    const api = { isSentenceEnderAt, /* ... */ };
    root.Segmentation = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
```

- `index.html` 按依赖顺序加多个 `<script>`；加载语义与现状完全一致（非 ESM、非 defer），回归风险最低。
- Node/Vitest 直接 `require()`，无需打包。
- ESM 迁移可作为将来的独立项目，本计划不做。

### 2.2 全局状态：状态不搬家，逻辑搬家

约 90 个顶层 `let` 是最大风险源。原则：

- **纯函数模块**（1、2 计费函数）：直接搬，参数进结果出。
- **自持状态模块**（4、8）：状态（那几个 Map / localStorage 键）连同函数一起搬，模块内私有，对外只暴露方法。
- **重耦合模块**（3、5、7）：函数搬走，共享可变状态**留在 app.js**，以参数或一个显式 `ctx` 对象传入。禁止模块回头读 `window.xxx` 全局。
- 每处"原来读全局、现在改传参"都要在 PR 描述里列出来——这是唯一允许的代码改动形式。

### 2.3 测试栈

- **Vitest**（Node 22 已装）：模块单元测试直接 require；
- **Vitest + jsdom**：整页 DOM 测试（渲染 HTML、handleMessage 分发）；
- **Playwright**：可选的最终冒烟层，本计划仅预留（M6）。

## 3. 安全网先行：golden master（拆分第一步，不是最后一步）

任何搬运开始前，先建回归安全网：

1. **后端帧录制开关**：在 broadcast 统一出口加 env-gated（`FRONTEND_FRAME_LOG=1`）录制，把每帧 JSON 逐行写 `logs/frontend-frames/frames_<ts>.jsonl`（≤20 行改动，后端唯一允许的修改）。先提交、再录制 2–3 场真实会话（覆盖混合/准确模式、含引号和多说话人内容）。
2. **jsdom 整页 harness**（`tests/frontend/helpers/page-harness.js`）：加载 index.html + 全部 script，stub `WebSocket`（暴露 `emitFrame()`）、`kuromoji`（空实现）、`fetch`（固定 JSON）、fake timers。
3. **golden master 脚本**：把录制帧喂进 harness，逐帧快照 `subtitleContainer.innerHTML`，存 `logs/frontend-golden/`（不进 git）。
4. **每完成一个模块搬运，跑一次 golden master，HTML 必须逐字节一致**。不一致 = 搬运有误；禁止"顺手修掉差异"。

## 4. 执行顺序（每个里程碑一个 commit/PR，独立可回滚）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M0 | package.json + Vitest + harness + 帧录制开关 + golden master 基建 | `npm test` 全绿（哪怕只有 1 个冒烟用例）；golden master 能对当前代码出快照 |
| M1 | 拆 `segmentation.js` + `token-stream.js` + 单元测试 | golden master 一致；两模块用例全绿（§5.1） |
| M2 | 拆 `render-model.js` + `refine-state.js` + 单元测试 | golden master 一致；显示层 12 用例全绿（§5.2） |
| M3 | 拆 `render-html.js` + jsdom DOM 用例 | golden master 一致；DOM 用例绿 |
| M4 | 拆 `ws-client.js` + `settings-store.js`；handleMessage 分发集成测试 | golden master 一致；帧分发用例绿 |
| M5 | 拆 `settings-ui.js` / `hosted.js` / `furigana.js`；计费纯函数测试 | golden master 一致；手动全功能冒烟清单通过（见下） |
| M6（可选） | Playwright 冒烟 | 本地可跑 |

**每个里程碑的手动冒烟**（M1 起每次都做）：起真实会话跑 1 分钟字幕；开/关设置面板；切主题；切显示模式；混合模式看 refine 灰转正。M5 额外：登录流程、余额条、语言选择器、假名注音。

## 5. 测试用例清单

### 5.1 segmentation.js / token-stream.js（M1）

- 与 Python `tests/test_sentence_segmentation.py` **同表驱动**：把 Python 侧用例（省略号不断句、`……`/`...`、小数、缩写、引号后置句尾、未闭合「」不断句）做成共享 JSON 表（`tests/fixtures/segmentation-cases.json`，进 git），Python 与 JS 各写一个 runner 吃同一张表 —— 一处改规则、两边同时验证（解决 §6 的漂移问题）。
- mergeFinalTokens：相邻 token 合并、跨 id 不合并（id 集合 >1 时删除 id 的现行为为准——先写测试锁行为，不改逻辑）。

### 5.2 render-model.js / refine-state.js（M2，对应历史 live bug）

1. 同 id 混合语言句子只出一个块；refine 译文只渲染一次（2026-07-12 重复译文 bug）。
2. 不同 id 语言切换正常拆块，各配各的译文。
3. 无 id 的非最终 token 语言切换仍拆块（旧行为）。
4. 译文 token 晚到（带前一句 id）→ 附着回前一句。
5. refine_result applied：draft 替换、灰转正、只出现一次。
6. refine_result no_change：draft 保留、灰转正。
7. retraction：对应块消失、后续不受影响。
8. 空块不渲染（偶发空 JA 行；若用例揭示根因，单独报告，未经确认不改渲染）。
9. 说话人切换开新 speaker block。
10. displayMode 三档内容正确。
11. 增量渲染缓存：同 sentenceId 更新后 HTML 跟着更新。
12. 准确模式合成译文行渲染一次、语言标签正确。

### 5.3 ws-client / settings-store / 计费（M4–M5）

- handleMessage：每种帧类型（final_tokens / refine_result / spec_translation / clear / retract / segment_mode_changed / 余额帧）分发到正确处理器（spy 断言）。
- settings-store：读写往返、缺省值、坏数据容错（现行为为准）。
- 计费：`estimatedSessionCost` / `sttRateMultiplier` / `compareVersions` 边界值。

## 6. 已知欠账：JS/Python 分段规则漂移（随 M1 一并处理）

app.js 的 JS 手抄副本缺少 Python 侧 2026-07-11/12 的新规则：

- `…` / `...` 不作为句尾（Python `is_sentence_ender_at` 的省略号分支）；
- 未闭合「」『』（）等配对引号内不断句（`text_has_unclosed_quote` / `QUOTE_PAIRS`）；
- 引号后置句尾（`？"`）的处理差异。

M1 拆出 `segmentation.js` 时按 Python 版**逐函数对齐移植**，用 §5.1 的共享用例表验证两边一致。这是本计划中唯一"改行为"的改动，必须单独 commit，并在真实会话中冒烟（前端这份只影响显示级分行/推测性分割，不影响后端配对）。

## 7. 红线

- **搬运就是搬运**：除 §2.2 的"全局改传参"和 §6 的规则对齐外，不改任何逻辑；一切"顺手优化"单独开 issue。
- **不引入构建工具/打包/TS/框架**。
- **后端只允许加帧录制开关**，其余后端代码不动。
- **真实录制帧与 golden 快照不进 git**（`logs/` 已被 .gitignore 覆盖）；进 git 的 fixture 一律用编造内容（风格参考 `tests/test_sentence_pairing.py`）。
- 每个里程碑独立 commit + golden master 通过 + 手动冒烟通过，才能开始下一个。任何一步 golden master 不一致且找不到原因 → 回滚该步，报告。

## 8. 工作量预估

M0: 1 天；M1: 1 天；M2: 1–1.5 天；M3: 1 天；M4: 1 天；M5: 1–1.5 天；M6: 0.5 天。合计约 6–7.5 天。

## 9. 交接材料

- 本文档
- `logs/regression/README.md`：近期 live bug ↔ 后端合成测试对照表（理解故障模式）
- `tests/test_sentence_segmentation.py` / `tests/test_sentence_pairing.py`：Python 侧用例风格与共享表的来源
- 帧格式参考：`soniox_session.py` 中 `broadcast_callback` 各调用点
- 近期修复脉络：`git log --oneline -20`
