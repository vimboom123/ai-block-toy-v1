# Voice Runtime & Device Integration

目标：在不依赖硬件的前提下，先把本机语音链路打通，再进入设备接入。

本阶段优先顺序：
- 本机音频输入 / 语音转文字
- Session runtime turn 提交
- 自然回复 provider（默认 Qwen）
- 文字转语音
- 基础交互流程串联
- 设备接入准备

当前不做：
- 完整硬件联调
- 多设备管理
- 正式生产部署

原则：先把语音软件链跑通，再接硬件，不反过来。

## 当前已落地的最小单轮语音入口

这一轮已经不是“只跑文本原型”了，而是补到了最小真实语音链路：

1. `mic record once -> wav file`
2. `wav / audio file -> Qwen realtime ASR websocket transcript`
3. `transcript -> signal_resolution`
4. `signal_resolution -> interaction_generation + interaction_context`
5. richer Phase 7 package -> Phase 6 `child_input_text + task_signal` bridge payload
6. `interaction_generation.reply_text -> Qwen realtime TTS websocket audio file`

当前策略故意收得很窄：

- 单轮入口继续保留：`--audio-file` 和一次性 `--record-seconds`
- 新增 `scripts/run_voice_session.py`，做会话式 turn loop
- 会话式入口会在播上一轮回复时录下一轮输入，作为电脑端的全双工模拟
- 真正的硬件级 barge-in / VAD / 设备层 mic runtime 还留到后面
- 文本入口 `scripts/run_text_prototype.py` 保持可用，不被语音入口替换
- `scripts/run_voice_fast.py` 继续保留为快链入口，默认还是单轮 HTTP 录音 / ASR / LLM / TTS；但如果加 `--submit-phase6`，它也会按 Fire Station 全任务链继续跑完整流程，并默认把 Qwen TTS 切成流式边播边出，只是仍然不走 WebSocket / echo cancel

## 当前实现重点

- `signal_resolver` 继续保持规则优先，不把 task signal 判断交回给模型
- `interaction_generator` 支持 `qwen | minimax | ark_doubao | template | auto`
- `interaction_generator / interaction_provider` 共用结构化 `interaction_context`
- 默认 provider 改成 `qwen`；CLI / runtime pipeline / generator 现在都一致
- `qwen` 走 DashScope OpenAI-compatible `/chat/completions`；不硬编码 key，只读 `.env.local` / shell env
- 录音入口直接复用本机已装好的 `sounddevice + soundfile`，先录出 wav，再送进 realtime ASR
- `run_voice_input.py` 默认 `--runtime-mode realtime`；realtime 模式下 ASR / TTS 都走 Qwen websocket，`legacy` 只保留老 whisper + HTTP TTS 兼容路径
- `run_voice_input.py` 末尾现在会额外产出 `tts_output`；默认 `--tts-provider auto`，先试 Qwen realtime TTS，再退 macOS `say`
- `auto` 作为兼容别名时，会按 `qwen -> ark_doubao -> minimax` 顺序尝试
- `keep_trying` 场景先走一次常规 provider；失败、超时、回包坏掉或口吻太机械时，再给一次更宽松重试机会
- `task_completed / end_session` 继续走更快的单次快路径，失败就直接退回本地自然模板
- 新增 `runtime_pipeline.py`，把文本和语音入口都收敛到同一条 Phase 7 turn pipeline
- 新增 `voice_input/whisper_cli.py`，默认走本机 `whisper` CLI
- Phase 6 bridge 契约不变，仍然只提交 `child_input_text + task_signal`
- 为了让最小语音 smoke 更稳，`signal_resolver` 额外补了一个非常窄的 ASR 错词归一：`灭货 -> 救火`

目录：

- `input_understanding/`
  - `signal_resolver.py`：规则优先 resolver，保留 LLM stub 钩子
  - `interaction_generator.py`：规则控制结构，默认 Qwen 自然化，可切 MiniMax / 豆包，失败退模板
  - `interaction_provider.py`：Qwen / MiniMax / Ark provider 封装
  - `models.py`：Task / Signal / Interaction 数据结构
- `voice_input/`
  - `realtime_asr.py`：Qwen realtime ASR websocket transcription 封装
  - `whisper_cli.py`：本机 `whisper` CLI transcription 兼容封装
  - `recorder.py`：一次性 mic -> wav 录音封装，复用 `sounddevice + soundfile`
  - `models.py`：最小音频录音 / 转写结果结构
- `voice_output/`
  - `realtime_tts.py`：Qwen realtime TTS websocket / macOS `say` 输出封装
  - `synthesizer.py`：Qwen HTTP TTS / macOS `say` 旧路径封装
  - `models.py`：最小 TTS 输出结果结构
- `phase6_bridge/`
  - `payloads.py`：把 Phase 7 richer package 压成 Phase 6 turn payload
  - `client.py`：可选的本地 HTTP bridge client
- `runtime_pipeline.py`
  - 文本入口 / 语音入口共用的 Phase 7 turn pipeline
- `scripts/run_text_prototype.py`
  - 命令行文本版入口
- `scripts/run_voice_input.py`
  - 命令行单轮语音入口
- `scripts/run_voice_session.py`
  - 命令行会话式语音入口，支持 turn loop 和 Phase 6 同步
- `tests/test_text_prototype.py`
  - prototype 自测
- `tests/test_voice_input.py`
  - realtime ASR/TTS fake websocket + whisper CLI / voice script 自测
- `tests/test_voice_session.py`
  - 会话式 turn loop + Phase 6 连接自测

## ASR 路径

当前默认 ASR 路径：

- voice runtime: `--runtime-mode realtime`
- runtime: Qwen realtime ASR websocket
- model: `qwen3-asr-flash-realtime`
- base URL: `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`
- mode: 单轮音频文件转写

legacy 兼容路径保留：

- record: 本机 `sounddevice + soundfile`
- runtime: 本机 `whisper` CLI
- command: `/usr/local/bin/whisper`
- default model: `large-v3-turbo`
- current local cache: `~/.cache/whisper/large-v3-turbo.pt`

为什么先切 realtime：

- 这次已经用真实 websocket smoke 跑通了
- 不再依赖本机 whisper CLI 的转写时延
- `legacy` 还留着，出问题时能回退

## TTS 路径

当前默认 TTS 路径：

- voice runtime: `--runtime-mode realtime`
- priority: `qwen_tts_realtime`
- fallback: `macOS say`
- default realtime base: `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`
- default realtime TTS model: `qwen3-tts-flash-realtime`
- default realtime TTS voice: `Cherry`
- default realtime output format: `wav`
- 默认会直接播放生成的回复音频；如果只想落文件不出声，传 `--no-playback`

legacy 兼容路径保留：

- default Qwen base: `https://dashscope.aliyuncs.com/api/v1`
- default speech endpoint: `/services/aigc/multimodal-generation/generation`
- default Qwen TTS model: `qwen-tts-latest`

这里故意做成两层：

- 第一优先级还是千问 / DashScope，满足“真人录音 smoke 先跑到模型语音输出”
- 如果当前环境没配 key、没通外网、或者 DashScope speech 接口拒绝当前模型，`auto` 会退到本机 `say`
- 如果你只想验证理解链，不产出回复音频，可以显式传 `--tts-provider none`

## 快速运行

先看文本入口：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_text_prototype.py \
  --child-text "消防车真帅" \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --provider-keep-trying-timeout-seconds 10 \
  --provider-keep-trying-retry-timeout-seconds 10 \
  --completion-point "救火:救火,灭火"
```

再看最小语音入口：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_voice_input.py \
  --audio-file /path/to/sample.wav \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --completion-point "救火:救火,灭火" \
  --runtime-mode realtime \
  --interaction-provider template \
  --tts-provider auto
```

如果想直接录一段 wav，再立刻走现有链：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_voice_input.py \
  --record-seconds 20 \
  --record-output-file /tmp/ai-block-toy-phase7-record.wav \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --completion-point "救火:救火,灭火" \
  --runtime-mode realtime \
  --interaction-provider template \
  --tts-provider auto
```

这条命令会做两件事：

- 先录一段 `wav` 到 `/tmp/ai-block-toy-phase7-record.wav`
- 再把这段 wav 直接送进 `realtime ASR -> phase7 pipeline -> realtime TTS` 链

录音模式当前最小可配参数：

- `--record-seconds`
  - 必填，最长监听时长；默认 `20s`，realtime 模式会在连续约 `1s` 无声后提前停
- `--record-output-file`
  - 可选，输出 wav 路径；不写时默认落到 `/tmp/ai-block-toy-phase7-recording-<timestamp>.wav`
- `--record-sample-rate`
  - 默认 `16000`
- `--record-channels`
  - 默认 `1`
- `--record-device`
  - 可选，传 `sounddevice` 的输入设备名或 index

语音入口输出会包含：

- `audio_recording`（仅在 `--record-seconds` 模式下出现）
- `audio_transcription`
- `signal_resolution`
- `interaction_generation`
- `interaction_context`
- `phase6_turn_payload`
- `tts_output`（默认开启；`--tts-provider none` 时不产出）
- `tts_output.playback_ok`（默认会尝试播放；`--no-playback` 时不出现）

如果 completion point 需要多个词，可以重复传：

```bash
python3 scripts/run_text_prototype.py \
  --child-text "我要开消防车去灭火" \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --interaction-provider template \
  --completion-match-mode all \
  --completion-point "救火:救火,灭火" \
  --completion-point "消防车:消防车"
```

## 最小 smoke

如果你现在就想测“录一段 -> 跑 phase7”，最小命令直接用这个：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_voice_input.py \
  --record-seconds 20 \
  --record-output-file /tmp/ai-block-toy-phase7-smoke.wav \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --completion-point "救火:救火,灭火" \
  --runtime-mode realtime \
  --interaction-provider template \
  --tts-provider auto
```

如果当前机器麦克风权限或设备环境不方便，也可以继续走原来的文件烟测；这条更稳：

```bash
say -v 'Eddy (中文（中国大陆）)' -o /tmp/ai-block-toy-phase7-smoke.aiff '消防车去灭火'
```

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_voice_input.py \
  --audio-file /tmp/ai-block-toy-phase7-smoke.aiff \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --completion-point "救火:救火,灭火" \
  --runtime-mode legacy \
  --interaction-provider template \
  --tts-provider auto \
  --whisper-model large-v3-turbo
```

这条 smoke 我已经在本机实跑过，输出会带：

- `audio_transcription.transcript`
- `signal_resolution.task_signal`
- `interaction_generation.reply_text`
- `interaction_context`
- `phase6_turn_payload`
- `tts_output.audio_path`
- `tts_output.provider_name`

录音模式多出来的关键字段是：

- `audio_recording.audio_path`
- `audio_recording.sample_rate`
- `audio_recording.channels`

## 会话式语音模拟

如果你要的是“语音交互 + 父端 UI 同步”的整段模拟，优先用这个入口：

### 一键启动

现在可以直接一条命令起完整链路：

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/start_voice_ui_demo.py
```

这条会默认做这些事：

- 探活 `http://127.0.0.1:4183/api/health`
- 如果 `4183` 没起，就自动拉起 `runtimes/session`
- 自动打开家长端 UI：`http://127.0.0.1:4183/`
- 直接跑真实麦克风 `run_voice_fast.py --submit-phase6`
- 默认走 `qwen + qwen tts + stream-tts + full fire station flow`

常用可选参数：

```bash
python3 scripts/start_voice_ui_demo.py \
  --record-seconds 20 \
  --max-turns 12 \
  --no-open-browser
```

如果你还想手动拆开跑，下面还是老入口：

1. 先起 Phase 6 session runtime
2. 打开 `http://127.0.0.1:4183/`
3. 再跑会话式 voice loop

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/session
python3 -m session_runtime.server --port 4183
```

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 scripts/run_voice_session.py \
  --record-seconds 20 \
  --max-turns 4 \
  --task-id fs_001 \
  --task-name "场景识别" \
  --task-goal "说出哪些是能动的，哪些只是画在墙上的" \
  --expected-child-action "区分可操作元素与背景元素" \
  --completion-point "背景可动:背景,可动,能动,会动,墙上,画在墙上,固定,不能动" \
  --runtime-mode realtime \
  --interaction-provider qwen \
  --phase6-api-base http://127.0.0.1:4183/api/session-runtime \
  --submit-phase6 \
  --tts-provider auto \
  --no-playback
```

这条会：

- 先播一段火情开场故事，再进入 turn loop
- 先录一轮，再播一轮，默认做 turn loop
- 每轮把 `child_input_text + task_signal` 提交到 Phase 6
- 父端 UI 自动轮询 snapshot，所以可以直接看 `turn_count / current_turn / current_task` 怎么变
- 真正的硬件接入和 barge-in 以后再挂
- 默认会做本地回声消除，拿上一轮回复音频去抵当前麦克风录音。它是 best-effort，不是系统级 AEC；如果扬声器离麦克风太近、音量太大，还是可能串进去。要关掉这层处理可以传 `--no-echo-cancel`
- 如果现场还是串麦，同机无耳机时最稳的方式是直接加 `--no-playback`；要保留播音就把 `--playback-gain` 压得更低

默认如果你不显式传 `--phase6-task-id`，新建的 Phase 6 session 会把整组 Fire Station task 一次性拉起来，按 `fs_001 -> fs_006` 继续往下跑。要只跑某几个步骤，就显式重复传 `--phase6-task-id`。

## 可选：桥接到正在运行的 Phase 6

如果本机已经启动 `runtimes/session`，可以额外把 bridge payload 提交进去：

```bash
python3 scripts/run_text_prototype.py \
  --child-text "我要开消防车去救火" \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --completion-point "救火:救火,灭火" \
  --session-id <phase6_session_id> \
  --phase6-api-base http://127.0.0.1:4183/api/session-runtime \
  --submit-phase6
```

语音入口也一样，只是把前面的文本输入替换成 `--audio-file` 或 `--record-seconds`：

```bash
python3 scripts/run_voice_input.py \
  --record-seconds 20 \
  --record-output-file /tmp/ai-block-toy-phase7-phase6.wav \
  --task-id fs_004 \
  --task-name "消防车出动" \
  --task-goal "让孩子说出消防车要去做什么" \
  --expected-child-action "说出消防车要去救火" \
  --completion-point "救火:救火,灭火" \
  --interaction-provider template \
  --session-id <phase6_session_id> \
  --phase6-api-base http://127.0.0.1:4183/api/session-runtime \
  --submit-phase6
```

链路顺序就是：

`mic -> wav -> realtime ASR transcript -> phase7(signal_resolution + interaction_generation + interaction_context) -> phase6_turn_payload -> optional phase6 submit`

默认如果没关 TTS，后面还会再接：

`-> reply_text -> realtime TTS output`

默认不会主动提交，只会打印 bridge payload，所以不会碰坏 Phase 6。

## interaction provider 说明

- 默认值：`--interaction-provider qwen`
  - text CLI / voice CLI / `runtime_pipeline.run_phase7_turn_pipeline()` 默认都已经切到 `qwen`
  - 走 DashScope OpenAI-compatible `/chat/completions`
  - 默认模型：`qwen-plus`
  - key / model / base URL 只从环境里读，不在代码里硬编码
- `--interaction-provider minimax`
  - 显式切到 MiniMax OpenAI-compatible 路径
- `--interaction-provider ark_doubao`
  - 显式切到 Ark / 豆包路径
- `--interaction-provider template`
  - 完全不走模型，方便本地测试和回归
- `--interaction-provider auto`
  - 兼容旧入口
  - 会按 `qwen -> ark_doubao -> minimax` 顺序尝试
  - `task_completed / end_session` 仍然只走单次快路径
  - 成功时 `interaction_generation.generation_source=llm_provider`
  - 失败时自动回退 `template_fallback`

切换示例：

```bash
# 默认 qwen，可不写 --interaction-provider
python3 scripts/run_text_prototype.py ...

# 显式切 MiniMax
python3 scripts/run_text_prototype.py ... --interaction-provider minimax

# 显式切豆包
python3 scripts/run_text_prototype.py ... --interaction-provider ark_doubao

# 完全离线模板回归
python3 scripts/run_text_prototype.py ... --interaction-provider template
```

当前可配 timeout：

- `--provider-fast-timeout-seconds`
  - 默认 `2.5`
  - 用于 `task_completed / end_session`
- `--provider-keep-trying-timeout-seconds`
  - 默认 `4.0`
  - 用于 `keep_trying` 首次尝试
- `--provider-keep-trying-retry-timeout-seconds`
  - 默认 `7.0`
  - 用于 `keep_trying` 的第二次、更宽松重试

当前复用的配置来源：

- 默认仍复用 `../dialog/.env.local` 的同一套 `build_runtime_env()` 读法，再叠加 shell env
- `qwen`
  - `QWEN_API_KEY` 或 `DASHSCOPE_API_KEY`
  - `QWEN_MODEL` 或 `DASHSCOPE_MODEL`
  - `QWEN_BASE_URL` 或 `DASHSCOPE_BASE_URL`
- `minimax`
  - `MINIMAX_API_KEY`
  - `MINIMAX_MODEL`
  - `MINIMAX_BASE_URL`
- `ark_doubao`
  - `ARK_API_KEY`
  - `ARK_MODEL / ARK_MODEL_ID / ARK_ENDPOINT_ID`

所以这里没有把任何 key 写死到代码里；只认已有环境变量或 `.env.local`。

## TTS provider 说明

- 默认值：`--tts-provider auto`
  - realtime 模式下先试 `Qwen realtime TTS`
  - legacy 模式下先试 `Qwen HTTP TTS`
  - 失败时退 `macOS say`
  - 成功时 `tts_output.ok=true`，并带真实 `provider_name`
  - fallback 成功时，`tts_output.fallback_reason` 会写第一次 Qwen 失败原因
- `--tts-provider qwen`
  - realtime 模式下只走 DashScope / Qwen realtime TTS websocket
  - legacy 模式下只走 DashScope / Qwen HTTP TTS
  - 如需强制改模型 / voice / format，可用 CLI 覆盖
- `--tts-provider say`
  - 只走本机 `say`
  - 更适合无网或不想依赖 DashScope 的本地回归
- `--tts-provider none`
  - 完全跳过 TTS，只保留文字输出和 bridge payload

Qwen TTS 当前支持的配置来源：

- API key
  - `QWEN_TTS_API_KEY`
  - `DASHSCOPE_TTS_API_KEY`
  - `QWEN_API_KEY`
  - `DASHSCOPE_API_KEY`
- model
  - `QWEN_TTS_MODEL`
  - `DASHSCOPE_TTS_MODEL`
- request/base URL
  - `QWEN_TTS_REQUEST_URL`
  - `DASHSCOPE_TTS_REQUEST_URL`
  - `QWEN_TTS_BASE_URL`
  - `DASHSCOPE_TTS_BASE_URL`
  - 默认 base 会退到通用 DashScope TTS HTTP endpoint
- voice / format / timeout
  - `QWEN_TTS_VOICE`
  - `DASHSCOPE_TTS_VOICE`
  - `QWEN_TTS_FORMAT`
  - `DASHSCOPE_TTS_FORMAT`
  - `QWEN_TTS_TIMEOUT_SECONDS`
  - `DASHSCOPE_TTS_TIMEOUT_SECONDS`

realtime ASR / TTS 也能单独通过这些环境变量覆盖：

- `QWEN_RT_API_KEY`
- `QWEN_RT_BASE_URL`
- `QWEN_RT_ASR_MODEL`
- `QWEN_RT_ASR_LANGUAGE`
- `QWEN_RT_ASR_TIMEOUT_SECONDS`
- `QWEN_RT_TTS_MODEL`
- `QWEN_RT_TTS_VOICE`
- `QWEN_RT_TTS_LANGUAGE_TYPE`
- `QWEN_RT_TTS_RESPONSE_FORMAT`
- `QWEN_RT_TTS_MODE`
- `QWEN_RT_TTS_TIMEOUT_SECONDS`

voice CLI 还额外补了几组最小参数：

- `--tts-output-file`
- `--tts-timeout-seconds`
- `--qwen-tts-model`
- `--qwen-tts-voice`
- `--qwen-tts-format`
- `--say-voice`

当前 fallback 触发条件：

- 当前选中的 provider 没配 key / model / base URL
- provider 请求失败、超时、回包不是可解析 JSON
- 回包缺 `reply_text`
- 回包虽然成功，但措辞落回“你来告诉我 / 你来试试 / 请回答”这类机械口吻

## interaction context

这轮新增了单独的 `interaction_context`，至少包含：

- `child_input_text`
- `normalized_child_text`
- `task_signal`
- `engagement_state`
- `matched_completion_points`
- `missing_completion_points`
- `interaction_goal`
- `scene_style`
- `redirect_strength`
- `expected_child_action`

另外补了几个轻字段，方便模板 fallback 和调试少猜一点：

- `interaction_mode`
- `emotion_tone`
- `preferred_acknowledged_child_point`
- `preferred_followup_question`
- `recent_turn_summary`
- `rule_reason`
- `session_memory`

现在 prompt 结构也换了：

- system prompt 还是硬约束版，但现在会明确带上故事背景、上一轮会话记忆和必须拉回的动作
- user prompt 依旧是 compact JSON，只保留 `task / child / reply` 三块大框架
- `task` 现在会额外带 `expected_action / scene_style / scene_context / session_memory`
- `child` 现在会额外带 `normalized / summary`
- `recent_turn_summary / rule_reason` 继续保留在运行态 `interaction_context` 里，`session_memory` 也保留在运行态，同时会随 prompt 一起发给 provider

`keep_trying` 现在发给 provider 的一条典型 prompt payload 会长这样：

```json
{
  "task": {
    "name": "消防车出动",
    "signal": "keep_trying",
    "goal": "先接住孩子提到的消防车，再把话题拉回消防车要去救火。",
    "expected_action": "说出消防车要去救火",
    "scene_style": "playful_companion",
    "scene_context": "消防车从消防站出发，路上会遇到不同的火情，但这轮流程始终是先发现火源，再去救火。",
    "session_memory": "任务：场景识别；孩子说「消防车真帅」；我们回「是啊，消防车很帅。那它现在要去帮谁呀？」；信号：keep_trying；下一步：接警判断",
    "need": ["救火"]
  },
  "child": {
    "said": "消防车真帅",
    "normalized": "消防车真帅",
    "state": "playful",
    "summary": "孩子刚提到消防车，但还没说到救火"
  },
  "reply": {
    "mode": "playful_probe",
    "tone": "playful",
    "redirect": "soft",
    "ack": "消防车",
    "ask": "那消防车现在是去帮什么忙呢？"
  }
}
```

这样比原来更合理的点在于：

- provider 还是吃结构化 JSON，但不再背一堆对自然化收益很低的字段
- `task.goal + task.need + child.said + reply.ack/ask` 已经够它判断“接什么、拉回什么”
- 一行 compact JSON 比之前那层外壳 + 多个冗余字段短很多，重试时只额外挂一个很短的 `retry_hint`
- Phase 6 bridge 仍然不变，所以主链不受影响

重试策略补充：

- `keep_trying`
  - 第 1 次：常规 prompt，默认 `4.0s`
  - 第 2 次：仅一次，默认 `7.0s`，prompt 会改成更短的重试版，并只补一句短 `retry_hint`
  - 只有两次都失败，才退 `template_fallback`
- `task_completed / end_session`
  - 单次快路径，默认 `2.5s`
  - 失败后直接 fallback，不额外等待

所以现在不是“超时就立刻退模板”，而是只有 `keep_trying` 会多给当前 provider 一次更自然化的机会；但最后仍然保留 graceful fallback，不会把 turn 卡死。

## keep_trying timeout 轻量排查结论

注意：这段结论现在只算轻量参考，而且下面这些数字只适用于显式切到 `--interaction-provider ark_doubao` 的路径，不是默认 `qwen` 路径。因为本轮已经把 provider 输入改成结构化 `interaction_context` JSON，prompt 体积和组织方式都变了，不能继续直接沿用上一轮的旧数字。

基于当前代码实际加载到的 provider 配置，本地重新量到的字符数大约是：

- 本机现在走的是 Ark `responses` 接口，不是更轻的 `chat/completions`
- 当前 provider 实际配置为：`request_url=/api/v3/responses`、`max_output_tokens=1000`、`reasoning_effort=low`
- `keep_trying` 默认 prompt（system + user）约 `572` 字符
- `relaxed_keep_trying` retry prompt（system + user）约 `567` 字符；带短 `retry_hint` 时约 `599` 字符
- `fast_path` prompt（system + user）约 `525` 字符

也就是这轮单看 prompt 体积，大概从原先的 `1.7k~1.8k` 砍到了 `0.5k~0.6k`。

所以如果下一轮继续提 `keep_trying` 成功率，排查优先级应该改成：

- 先重新观察这版轻 prompt 下的真实 timeout 命中情况
- 再决定是不是给 `keep_trying` 更宽一点的 timeout
- 或者把 provider 路径减重（例如确认是否必须继续走 `responses` / `1000` token budget）

## 更自然的话术示例

`keep_trying`

- 是啊，消防车是挺帅的。那它这会儿要去做什么呀？
- 哈哈，这车看着就威风。你觉得它现在是去帮什么忙呢？
- 嗯，看到消防车了。那它现在是在忙什么呀？

`task_completed`

- 对，就是去救火。我们接着往下看。
- 没错，这一步答对了。下一步我们再看看。
- 对啦，消防车就是去帮忙灭火的。我们继续。

`end_session`

- 好，那我们先停这儿。等你想继续的时候再叫我。
- 行，今天先玩到这里。你想接着玩，我们就从这儿继续。
- 收到，我们先休息一下。下次回来我还记得这一步。

## 当前边界

- 单轮入口仍然是 `--audio-file` / `--record-seconds`
- 会话式入口已经能做 turn loop + 播放上一轮时录下一轮的电脑端模拟全双工
- 真正的连续监听 / VAD / 硬件级打断控制还没接到设备层
- `phase6_turn_payload` 仍然只保留 `child_input_text + task_signal`，不会把 raw audio 或 richer context 推给 Phase 6
- 语音路径现在默认走 Qwen realtime ASR / TTS websocket；`legacy` 只保留 whisper + HTTP TTS 兼容路径
- 真实 ASR 仍然会有 homophone 错词，所以现在只补了很窄的一层规则归一，不算通用 ASR 纠错系统
- Qwen realtime TTS 现在默认按 DashScope 官方 websocket 路径去打；如果账号实际 model / voice 名不同，需要显式覆盖 env 或 CLI

## 测试

```bash
cd /Volumes/Lexar/OpenClawStore/state/workspace/studio/projects/ai-block-toy-v1/runtimes/voice
python3 -m pytest -q
```

当前测试覆盖了：

- `keep_trying` 会生成更完整的结构化 `interaction_context`，并传给 provider
- `keep_trying` 首次超时后会用更宽松 timeout retry，并在 retry 成功时保留 provider 输出
- `keep_trying` 连续两次失败时仍然会 graceful fallback
- `task_completed / end_session` 只走单次快路径，不会因为 retry 拖慢收口
- 一次性录音会真实走 `sounddevice.rec -> soundfile.write`
- realtime ASR / TTS fake websocket 测试能跑通单轮转写和出声
- 会话式 voice loop 会在播上一轮时录下一轮，并把 turn 提交给 Phase 6
- Qwen TTS provider 能把返回音频字节写到本地文件
- `auto` TTS 会在 Qwen 失败时退到 `macOS say`
- voice CLI 输出里会带 `tts_output`，但提交给 Phase 6 的 payload 仍然只保留 `child_input_text + task_signal`

为了避免测试时误打真实模型，测试里用的是 fake provider，不会访问真实 Qwen / MiniMax / Ark。

## 目录清洁

本目录补了一个本地 `.gitignore`，至少挡住：

- `._*`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`

## 实现定位

- resolver 现在仍然是规则优先；模型只参与自然化话术，不参与 signal 判定
- interaction generator 现在是“结构规则 + 默认 Qwen 自然化 + 可切 MiniMax / 豆包 + 模板兜底”，还不是最终长期陪伴 persona
- prototype 现在已经有文本 / 单轮语音 / 会话式 turn loop 三个入口；但还没做硬件级连续监听、设备级 mic runtime、barge-in / VAD
- Phase 6 仍然是主 session runtime；Phase 7 这里只做理解层和桥接层，不重构 Phase 6
