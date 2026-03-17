# Ark Dialog Runtime Notes v1

项目：AI积木玩具  
阶段：Phase 5 / 真实对话链接入  
日期：2026-03-17  
状态：v1 接入骨架

## 1. 当前策略

第五步第一刀先接：
- 火山方舟（Ark）文本对话

当前不先深做：
- 语音输入
- 多模型编排
- 火山 AK/SK 其他云服务

## 2. 为什么先接 Ark

因为当前主目标是尽快证明：
- 场景信息能进模型
- 模型能返回可用的引导/故事内容
- 主流程不是假对话

所以先走文本对话最短链。

## 3. 第一条真实对话链

建议第一条链只做：
1. 读入 scene pack：`classic_world_fire_station.scene.yaml`
2. 组一版 system / context / task prompt
3. 调 Ark / 豆包生成一条任务引导
4. 返回结构化结果：
   - scene_id
   - prompt_version
   - task_id
   - reply_text
   - guidance_type
   - next_expected_action

## 4. 最小结构建议

建议后续在本目录新增：
- `runtime/ark_client.*`
- `runtime/scene_loader.*`
- `runtime/dialog_prompt_builder.*`
- `runtime/dialog_runtime_smoke.*`

## 5. 当前下一步

1. 用当前 scene pack 生成消防站首轮 system/context prompt
2. 验证 Ark key 能否打通最小调用
3. 跑一个消防站接警-出动-救援的文本对话 smoke
