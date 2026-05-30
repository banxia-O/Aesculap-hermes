# Aesculap-hermes
Hermes agent 自愈插件。实时检测系统故障，在安全边界内自动修复，无法自修时升级至外部编码工具或通知运维。Self-healing plugin for Hermes agents. Detects failures in real time, auto-fixes within safe boundaries, and escalates to coding tools or human when needed.

> 设计文档见 [`Aesculap_PRD.md`](./Aesculap_PRD.md)。面向用户的安装指南（三档风险、强警告、安装前置）将随安装向导一同在后续阶段补齐。

## 开发状态

按 PRD §16 分阶段实现，每阶段确认后推进：

- **Phase 1 ✅ 骨架 + 安全核心** — 纯确定性代码闸门：可写范围/黑名单（§9）、硬绊线（§8.1）、爆炸半径路由（§6.2）、升级阶梯（§6.3）、配置加载校验（§10）、append-only 审计日志（§13）。
- **Phase 2 ✅ 检测层 + 触发架构** — Tier 0 探针（§3）、日志监听（自研 tail -F，处理 rotation/truncation，§2）、存活/全量体检周期探测（§2）、去抖动确认（§4）、单进程共享事件队列 + 并发锁（§12）。CLI：`aesculap probe` / `aesculap start`。
- **Phase 3 ✅ 分诊层 + LLM 薄适配** — provider-agnostic 薄适配（OpenAI / Anthropic / OpenAI 兼容端点，§5.1）；分诊结构化输出严格校验，**JSON 解析失败/字段非法/route 缺失 → 降级 human，不猜不重试**（决策3）；分诊 prompt 教模型识别 §8.2 信号；triage→代码闸门 pipeline（§5/§6）+ 级联保护（§7.3）+ 能力清单（§6.4）。LLM 提议永远要过 Phase 1 闸门。
- **Phase 4 ✅ 修复流程** — 标准流程 备份→改→**全量验证（决策2：原 FAIL→OK 即过，不要求全绿）**→观察窗口→失败先诊断后回滚（§7.1）；self_fix 3 次重试预算 + 只升不降阶梯（§7.2/§6.3）；coding_agent 调外部 CLI（claude/codex）在 git 内操作、commit/reset；修复执行受 `mode == fix` 闸门约束，observe 档只记录不动手（§10.2）。
- **Phase 5 ✅ 喊人 + 通知策略** — 喊人通知四件套（哪坏了/试了什么/要你做什么/一行指引，§8.3）；**key 安全规则强制**（按 reason 给"key 填到哪"指引 + 自由文本 secret 扫描脱敏，绝不要 key 进聊天/日志）；走配置发送命令模板复用 Hermes gateway（provider-agnostic）；去重 + 冷却（open issues 状态文件，§12）；gateway 失效兜底（§11）；仅真动手/需喊人才推送。
- Phase 6 — 安装向导 + systemd + 开关 + 用户 README

## 开发

```bash
pip install -e ".[dev]"        # 安装依赖 + pytest
python -m pytest -q            # 跑测试
python -m aesculap config <path/to/config.yaml>   # 校验配置
```

配置示例见 [`aesculap/resources/config.example.yaml`](./aesculap/resources/config.example.yaml)。

> **核心安全原则（PRD §1）：LLM 提议，代码拍板。** LLM 只诊断与建议；能否动手、动手到什么程度，由 `aesculap/gate/` 下的确定性代码强制裁决，模型永远跨不过。
