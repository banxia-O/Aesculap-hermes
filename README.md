# Aesculap-hermes

**给 Hermes agent 的自愈插件。** 实时盯着你的 agent，发现它出故障时，在你授权的安全边界内自动修好；修不了、或风险超界，就停手并通知你。

Self-healing plugin for [Hermes](https://github.com/NousResearch/hermes-agent) agents. It watches your agent, auto-fixes failures inside a boundary you authorize, and escalates to a human (you) when a fix would be risky or fails.

---

## 这东西是给谁用的？怎么用？

**你（人类）几乎不用碰它。** Aesculap 的设计前提是：你的 Hermes agent 自己会装、会配、会管。

你要做的只有一件事：

> 把这个仓库链接发给你的 Hermes agent，告诉它：
> **「读一下 [`AGENT.md`](./AGENT.md)，把这个自愈插件给自己装上。」**

剩下的——检查环境、选模型、选权限档、配开机自启、做前置检查——agent 会照着 [`AGENT.md`](./AGENT.md) 一步步引导你完成。需要你拍板的地方（尤其是「给它多大权限」），它会停下来问你。

```
你 ──「读 AGENT.md，装上自己」──►  Hermes agent
                                      │
                                      ├─ 自检环境（有哪些模型？是不是 git 仓库？日志落文件了吗？）
                                      ├─ 引导你选权限档（A/B/C）+ 选分诊/自修模型
                                      └─ 装好 systemd 常驻，开始自愈
```

---

## 它到底干什么？

1. **实时检测** — 盯日志、盯进程存活、定期全量体检，发现 Hermes 崩了 / 报错 / 卡死 / 磁盘满。
2. **去抖确认** — 不会一闪而过的抖动就惊动你；连续多次或持续超时才当真。
3. **安全自修** — 在你授权的范围内备份 → 改 → 全量验证 → 观察。修好就静默，你甚至不会注意到出过事。
4. **修不了就升级** — 自修失败（最多 3 次）→ 交给更强的编码工具（Claude Code / Codex）→ 还不行就**通知你本人**。
5. **喊人通知** — 走你 Hermes 现成的消息通道（Telegram/Discord/Slack/Email…）发给你：**哪坏了 · 试过什么 · 要你做什么 · 一行操作指引**。

健康时它完全静默，只在「真动手」和「需要你」时才出声。

---

## ⚠ 你唯一需要认真做的决定：给它多大权限

安装时 agent 会让你**显式选一档可写范围**（无默认值，必须你拍板）：

| 档位 | 它能改的范围 | 风险 | 适用 |
|------|------------|------|------|
| **A** | 仅项目目录内 | ⭐ | **最安全，绝大多数情况选这个** |
| **B** | Hermes 配置目录 + 项目目录 | ⭐⭐⭐ | 这台机器专门跑 Hermes |
| **C** | 整台机器 | ⭐⭐⭐⭐⭐ ⚠ | **仅当整机只跑 Hermes、没有其他重要数据** |

> **⚠ 如果这是你的个人电脑，或机器上有别的重要东西，绝对不要选 C。** 选 C 时 agent 会二次确认、追问是不是专用机；不是就直接中止。

**无论选哪档，下面这些永远碰不了**（硬写死在代码里，agent 也改不动，需要改时一律通知你）：

- 🧠 **身份文件**（`SOUL.md` / `MEMORY.md` / `USER.md` 等人格/记忆文件）
- 🔑 **凭证文件**（`.env`、`*.key`、`*.pem`、含 secret/token/password 的文件）
- 🔒 **只读文件**（`chmod 444`）
- ⚙️ **Aesculap 自己的目录**（它不能自己改自己）
- 🖥️ **系统敏感路径**（`/etc`、`/usr`、`/boot`、`/root/.ssh`…）

还有几类操作被硬绊线拦死，一律转人工：`rm`/`rmdir`/`dd`、`git push --force`、删文件、碰计费/付费接口。

---

## 你怎么控制它（开关在你手里）

agent 装好后，这些命令你随时能自己敲，或让 agent 替你敲：

```bash
python -m aesculap status   ./config.yaml   # 它现在什么状态？最近修了啥？有没有挂起的问题
python -m aesculap mode observe ./config.yaml   # 「只看不动手」安全档 —— 觉得它判断飘了就切这个
python -m aesculap mode fix     ./config.yaml   # 切回「检测+自修」
python -m aesculap disable  ./config.yaml   # 总开关，一键全停
python -m aesculap enable   ./config.yaml   # 重新打开
```

- **总开关 `disable`**：彻底停手，什么都不做。
- **`observe` 模式**：继续检测、继续报告，但**绝不动手改任何东西**。怀疑它误判时的安全档。
- **`fix` 模式**（默认）：检测 + 自修 + 必要时喊你。

停掉常驻服务：`systemctl --user stop aesculap.service`（用户档）或 `sudo systemctl stop aesculap.service`（系统档）。

---

## 安全设计的一句话总结

**LLM 提议，代码拍板（PRD §1）。** 模型只负责诊断和建议「该怎么修」；**能不能动手、动到什么程度，由 `aesculap/gate/` 里的确定性代码强制裁决**。模型就算自信满满地说「这个我自己修」，只要改动碰了黑名单、超了权限档、或爆炸半径过大，代码闸门照样把它拦下转人工。模型越权，跨不过代码。

完整设计见 [`Aesculap_PRD.md`](./Aesculap_PRD.md)。给 agent 的操作手册见 [`AGENT.md`](./AGENT.md)。

---

## 开源说明

不硬编码任何模型 / 通知通道（全走配置 + 安装时自动检测）；跨环境安全（强制选档，默认偏保守推荐 A）；仓库内不含任何真实 IP / 域名 / 凭证 / 部署信息，示例一律占位符（PRD §14）。
