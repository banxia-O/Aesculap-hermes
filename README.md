# Aesculap-hermes

Hermes agent 自愈插件。实时检测系统故障，在安全边界内自动修复，无法自修时升级至外部编码工具或通知运维。

Self-healing plugin for Hermes agents. Detects failures in real time, auto-fixes within safe boundaries, and escalates to coding tools or a human when needed.

> 设计文档见 [`Aesculap_PRD.md`](./Aesculap_PRD.md)。

**核心安全原则（PRD §1）：LLM 提议，代码拍板。** LLM 只诊断与建议；能否动手、动手到什么程度，由 `aesculap/gate/` 下的确定性代码强制裁决，模型越权也跨不过。

---

## ⚠ 可写范围三档风险（安装时强制选择，无默认值）

安装向导**不提供默认值**，强制你显式选一档可写范围：

| 档位 | 可写范围 | 风险 | 适用 |
|------|---------|------|------|
| **A** | 仅项目树内 | ⭐ | **最安全，推荐** |
| **B** | Hermes 配置目录 + 项目树 | ⭐⭐⭐ | Hermes 专用主机 |
| **C** | 整个环境 / 主机 | ⭐⭐⭐⭐⭐ ⚠ | **仅当整机专供 Hermes** |

> **⚠ 强警告：** 若插件装在个人电脑，或与其他重要数据共存的环境，**严禁选 C 档**。向导对 C 档会二次确认并询问是否为专用主机；非专用一律中止。

无论选哪档，**黑名单永远生效**（default-allow，除黑名单外都可改）：身份文件（`SOUL.md`/`MEMORY.md`/记忆人格类）、`.env`/凭证、`chmod 444` 只读文件、Aesculap 自身目录、系统敏感路径——这些硬写死，改不了，需改时一律喊人。

---

## 安装前置（PRD §2 / §7.1）

1. **File logging**：日志监听要求 Hermes 把输出落到**日志文件**。Hermes 默认仅 stdout——先配置 file logging（建议 `~/.hermes/logs/hermes.log`），向导会检测并提示。
2. **Git 仓库**：代码改动要求在 git 仓库内操作（失败可 `git reset` 回滚）。若项目不在 git 仓库内，向导会提示并可代为 `git init`。

---

## 安装

```bash
pip install -e ".[openai,anthropic]"      # 按需装 provider SDK（薄适配，不引 litellm）

python -m aesculap install ./config.yaml  # 交互式向导：选档/扫身份文件/选模型/选 systemd/前置检查
python -m aesculap install-systemd ./config.yaml --scope user   # 打印（--write 则写入）systemd 单元
```

向导会：强制选档（A/B/C）→ 扫 Hermes 配置目录列**身份文件候选**让你勾选写入黑名单 → 自检已配置的模型 provider 让你选分诊模型 → 选 systemd 档（user 无需 root / system 开机自启）→ 检查 file logging 与 git 前置。

systemd 托管 daemon：崩溃自动拉起（Aesculap 自身从不自重启，§9.3）。

---

## 运行与管理

```bash
python -m aesculap start    ./config.yaml   # 前台运行 daemon（systemd 即如此调用）
python -m aesculap probe    ./config.yaml   # 跑一遍 Tier 0 探针并打印
python -m aesculap status   ./config.yaml   # 模式 + 最近审计 + open issues
python -m aesculap mode observe ./config.yaml   # 切到只检测不动手的安全档（§10.2）
python -m aesculap mode fix     ./config.yaml   # 切回检测+自修
python -m aesculap disable  ./config.yaml   # 主开关一键停（§10.1）
python -m aesculap enable   ./config.yaml
```

**模式位（§10.2）**：`fix`（默认）检测 + 自修 + 必要时喊人；`observe`（安全档）只检测 + 报告，**绝不动手**——发现它判断飘了时降级用。

---

## 工作原理（一张图）

```
日志监听 / 存活探测 / 全量体检  ──┐
                                  ├─►  事件队列  ─►  去抖确认(§4)  ─►  Tier 0 复查
                                  │                                          │
        （单进程 + 并发锁 §12）   ─┘                                          ▼
                                                                    分诊 LLM(§5, 提议)
                                                                            │
                            ┌───────────────────────────────────────────────┘
                            ▼
          ★代码闸门(§6.2/§8.1/§9)  ── 按爆炸半径强制路由，覆盖 LLM ──┐
                            │                                          │
          self_fix(≤3次)  ──┤   备份→改→全量验证(决策2)→观察窗口       │
            失败→           │   失败→先诊断后回滚→升级                  │
          coding_agent  ────┤   git 内操作，claude/codex，commit/reset  │
            失败/未装→      │                                          │
          human  ──────────┴──  喊人通知(§8.3, 四件套 + key 安全)  ◄────┘
```

---

## 开发

```bash
pip install -e ".[dev]"        # 依赖 + pytest
python -m pytest -q            # 全量测试
```

配置示例见 [`aesculap/resources/config.example.yaml`](./aesculap/resources/config.example.yaml)（仅占位值，不含任何真实部署信息）。

### 实现阶段（PRD §16，已全部完成）

- **Phase 1 ✅ 骨架 + 安全核心** — 可写范围/黑名单（§9）、硬绊线（§8.1）、爆炸半径路由（§6.2）、升级阶梯（§6.3）、配置校验（§10）、审计日志（§13）。
- **Phase 2 ✅ 检测层 + 触发架构** — Tier 0 探针（§3）、日志监听（自研 tail -F）、存活/全量体检、去抖动（§4）、共享事件队列 + 并发锁（§12）。
- **Phase 3 ✅ 分诊层 + LLM 薄适配** — provider-agnostic 薄适配（OpenAI/Anthropic/OpenAI 兼容端点）；分诊严格校验，**JSON 解析失败/字段非法/route 缺失 → human，不猜不重试**（决策3）；triage→闸门 pipeline + 级联保护（§7.3）+ 能力清单（§6.4）。
- **Phase 4 ✅ 修复流程** — 备份→改→**全量验证（决策2：原 FAIL→OK 即过）**→观察窗口→失败先诊断后回滚（§7.1）；3 次预算 + 只升不降阶梯；coding_agent 调外部 CLI；受 `mode==fix` 约束。
- **Phase 5 ✅ 喊人 + 通知策略** — 四件套通知 + **key 安全脱敏**（§8.3）；发送命令模板复用 gateway；去重冷却（§12）；gateway 失效兜底（§11）。
- **Phase 6 ✅ 安装向导 + systemd + 开关** — 强制选档 + C 档二次确认；扫描身份文件勾选写黑名单（决策1）；provider 自检；systemd user/system 单元；主开关 + 模式切换 CLU。

---

## 开源说明（PRD §14）

provider-agnostic（不硬编码模型/通知通道，全走配置 + 安装时检测）；跨环境安全（强制选档，默认偏保守推荐 A）；仓库内不含任何真实 IP/域名/凭证/部署信息，示例用占位符。
