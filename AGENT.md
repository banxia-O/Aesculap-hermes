# AGENT.md — Aesculap 安装与运维操作手册（给 Hermes agent 读）

> **你是谁，在读什么。** 你是一个 Hermes agent。你的人类让你「装上这个自愈插件」。
> 这份文档是给**你**（agent）看的操作手册，不是给人看的——人看 [`README.md`](./README.md)。
> 照着下面的步骤做：自检环境 → 引导人类做必要决策 → 装好 → 交接。
>
> **贯穿全程的最高原则（PRD §1）：LLM 提议，代码拍板。** 你只负责诊断和建议；
> 能不能动手、动到什么程度，由 `aesculap/gate/` 的确定性代码裁决，你跨不过去。
> 安装阶段同理——**凡是涉及「给多大权限」「用哪个模型」「碰不碰敏感文件」的决策，
> 你必须把选项摆给人类、让人类拍板，不要替人类决定。**

---

## 0. 这是什么

Aesculap 是装在 Hermes 旁边的**常驻自愈守护进程**。它实时检测 Hermes 故障，在人类授权的
安全边界内自动修复，修不了就升级到外部编码工具（Claude Code / Codex），再不行就通知人类。

它由 systemd 托管（崩溃自动拉起），但**从不自我重启、从不修改自己的目录**（§9.3）。

---

## 1. 先做环境自检（只读，不改任何东西）

动手装之前，先把下面这些查清楚，并把结果讲给人类听。**全部是只读探测，不要在这一步改任何文件。**

### 1.1 运行时与仓库前置

| 检查项 | 怎么查 | 不满足怎么办 |
|--------|--------|-------------|
| Python ≥ 3.11 | `python --version` | 提示人类升级，停在这一步 |
| 项目在 git 仓库内（§7.1 前置） | `git -C <项目目录> rev-parse --is-inside-work-tree` | **必须**——自修靠 `git reset` 回滚。不在仓库内就引导人类 `git init`（向导会主动问，并自动写 `.gitignore`，见下注） |

> **git 仓库的干净做法（实测建议）：** 若回滚目标是 Hermes 配置目录（如 `~/.hermes/`），**只追踪可回滚资产**（config / skills / scripts），用 `.gitignore` 把运行时产物排除掉——`logs/`、`sessions/`、`cron/output/`、`image_cache/`。否则一个 `git init` 会把几 GB 会话日志拖进仓库，`git reset` 回滚也会被噪声淹没。安装向导的 `git init` 会**自动写入这套 `.gitignore`**（幂等、保留用户已有条目）；你也可以手动确认它生效。
| Hermes 日志落到**文件**（§2 前置） | 看 `~/.hermes/logs/` 下有没有日志文件 | Hermes 默认只 stdout。**必须先让 Hermes 把输出写进日志文件**（建议 `~/.hermes/logs/hermes.log`），否则日志监听无从谈起。引导人类改 Hermes 配置 |
| systemd 可用 | `systemctl --user` 能用 / 有没有 root | 决定装 user 档还是 system 档（见 §4） |

### 1.2 自检「有哪些模型可选」（§5.1）

Aesculap 需要两个 LLM 角色（可以是同一个，也可以分开）：

- **分诊模型（triage）**：判断故障性质、提议修复路线。**建议用强模型**——它判断错了会连累整条链路。
- **自修模型（selffix）**：执行具体小修。可以用便宜些的模型。

**怎么查有哪些 provider 可用：看环境变量里有哪些 API key（只看「在不在」，绝不读取值）：**

```bash
# 安装向导内置了这套自检，但你也可以自己先看一眼：
[ -n "$ANTHROPIC_API_KEY" ] && echo "anthropic available"
[ -n "$OPENAI_API_KEY" ]    && echo "openai available"
```

支持的 provider：`openai`、`anthropic`、`openai_compatible`（自建/兼容端点，如 vLLM、Ollama，需填 `base_url`）。
**设计上不硬编码任何具体模型名**——具体型号由人类填，你只负责给建议。

### 1.3 自检「能不能升级到编码工具」（§6.4）

```bash
which claude   # Claude Code CLI
which codex    # Codex CLI
```

装了哪个，自修失败时就能升级给它接手。两个都没有，`coding_agent` 这一档会**自动降级为直接通知人类**——不会假装能修。

### 1.4 自检「哪些是身份文件」（决策 #1，§9.2）

扫 Hermes 配置目录（默认 `~/.hermes`），列出疑似**人格 / 记忆 / 身份**文件候选（`SOUL.md`、`MEMORY.md`、`USER.md`、`persona.md`、`identity.md` 等）。
**这些是要写进黑名单、永远禁止改动的候选。** 安装向导会把候选列给人类勾选——**不要自己静默决定**，让人类确认要锁哪些。

---

## 2. 安装

```bash
# 1) 装包 + 按需装 provider SDK（薄适配，不引 litellm）
pip install -e ".[openai,anthropic]"     # 按 §1.2 自检结果，装实际用得到的

# 2) 跑交互式安装向导（它会把 §1 的自检自动跑一遍，并引导人类做决策）
python -m aesculap install ./config.yaml
```

向导会依次做：
1. 问项目目录、Hermes 配置目录；
2. **强制人类选权限档 A/B/C**（无默认值，见 §3）；
3. 扫身份文件候选 → 让人类勾选写入黑名单（决策 #1）；
4. 列出已配置的 provider → 让人类选分诊模型，并填型号（见 §1.2）；
5. 检查 file logging 与 git 前置，缺 git 仓库时主动问要不要 `git init`（会顺手写入排除运行时产物的 `.gitignore`，只追踪 config/skills/scripts）；
6. 选 systemd 档（user / system）；
7. 填通知发送命令模板（默认复用 Hermes gateway）。

产出一个**经过校验**的 `config.yaml`。然后装 systemd 单元：

```bash
python -m aesculap install-systemd ./config.yaml --scope user    # 打印单元 + enable 指令
python -m aesculap install-systemd ./config.yaml --scope user --write   # 真正写入
```

---

## 3. 你必须让人类拍板的决策（不要自己定）

### 3.1 权限档 A/B/C —— 这是最重要的一次授权

**无默认值，必须人类显式选。** 你的职责是把风险讲清楚，并给出**保守建议**：

| 档位 | 可写范围 | 你应当怎么建议 |
|------|---------|---------------|
| **A** | 仅项目目录 | **默认推荐这个。** 绝大多数情况够用且最安全 |
| **B** | Hermes 配置目录 + 项目目录 | 仅当这台机是 Hermes 专用机才建议 |
| **C** | 整台机器 | **强烈劝阻，除非整机只跑 Hermes、无其他重要数据。** 向导会二次确认 + 追问是否专用机，非专用一律中止 |

> 如果人类说不清这台机器是干嘛的，**默认按 A 走**，并说明为什么保守。不要主动鼓励选 B/C。

### 3.2 模型选择

- 分诊：建议强模型（判断质量直接影响安全）。
- 自修：可便宜些。
- provider 从 §1.2 自检到的里面选；型号名由人类填（设计上不替人类钦定）。

### 3.3 🔑 API key 安全铁律（§8.3，**不可违反**）

- **永远不要让人类把 key 粘进聊天框 / 日志 / 任何会被记录的地方。**
- **永远不要回显、打印、转述任何 key 的值。**
- 配置里只存**环境变量的名字**（如 `api_key_env: ANTHROPIC_API_KEY`），**绝不存 key 本身**。
- 需要 key 时，你只告诉人类**key 该放在哪**（哪个文件 / 哪个环境变量 + 格式示例），让人类自己去设。
- 这条在安装期和运行期都生效。运行期需要凭证时同理：只说「去哪填」，绝不要值。

---

## 4. systemd 托管

| 档 | 路径 | 需要 root | 特性 |
|----|------|----------|------|
| `--scope user` | `~/.config/systemd/user/` | 否 | 推荐；可能需 `loginctl enable-linger` 让它在你登出后仍运行 |
| `--scope system` | `/etc/systemd/system/` | 是 | 开机自启 |

单元里 `Restart=always`、`NoNewPrivileges=true`。**Aesculap 崩了由 systemd 拉起，但它自己绝不自重启**（§9.3，防递归自修灾难）。

---

## 5. 装好之后它有多大权力？能改什么、不能改什么

这是你（和人类）最该弄清的部分。**边界由代码强制，不是靠你自觉。**

### ✅ 它**能**改的——按修复档位分

#### 🔧 self_fix（自修，第一档）
**只允许两类动作**，其余一律强制升级（`gate/blast_radius.py` 执行，LLM 建议无效）：

| 动作 | 实现方式 | 说明 |
|------|---------|------|
| `write_file` | `Path.write_text(content)`，**不经过 shell** | 分诊模型在 JSON 里直接给出文件新内容；改前备份，验证失败自动回滚 |
| `restart_process` | `subprocess.run(argv, shell=False)` | 重启指定进程；命令经过 tripwire 扫描后才执行 |

**self_fix 绝对不能运行任意 shell 命令（§6.2）。** 任意命令（`RUN_COMMAND`）的爆炸半径本质未知——
即使命令看起来无害，只要 LLM 在 self_fix 路由下提议 `RUN_COMMAND`，`blast_radius.py`
就会强制升级为 `coding_agent` 或 `human`，`confidence` 再高也拦不住。

#### 🤖 coding_agent（外部工具，第二档）
调 Claude Code CLI（`claude -p`）或 Codex CLI，在 git 仓库内操作。
它可以运行命令、修改多个文件，但改动落 git commit，失败 `git reset` 回滚。

#### 📢 human（通知人类，第三档）
任何 coding_agent 失败或不可用时，通知你（四件套：哪坏了 / 试过什么 / 要你做什么 / 一行指引）。

### ✅ 可以改的文件范围

- 在人类所选**权限档边界内**（A=项目目录 / B=+配置目录 / C=整机）的、**不在黑名单里**的文件。
- 默认放行（default-allow）：边界内除黑名单外都可改。

### ⛔ 它**永远不能**改的（硬写死在 `gate/scope.py`，配置放宽不了，只能加严）

| 类别 | 具体 | 依据 |
|------|------|------|
| 🧠 身份文件 | `SOUL.md` / `MEMORY.md` / `USER.md` / persona / identity 等（人类勾选的那批） | §9.2 |
| 🔑 凭证文件 | `.env` / `.env.*` / `*.key` / `*.pem` / `*.p12` / `id_rsa` / `credentials` / `.netrc` / `.git-credentials`，以及文件名含 `secret`/`token`/`apikey`/`password` 的 | §9.2 |
| 🔒 只读文件 | 没有 owner 写位的（≈ `chmod 444`） | §9.2 |
| ⚙️ 自身目录 | `aesculap_home` 及其下一切（不能自己改自己） | §9.3 |
| 🖥️ 系统敏感路径 | `/etc` `/boot` `/usr` `/bin` `/sbin` `/lib` `/lib64` `/sys` `/proc` `/dev` `/var/lib` `/root/.ssh`——**即使选了 C 档也排除** | §9.2 |

### ⛔ 被硬绊线拦死的**操作**（`gate/tripwires.py`，一律转人工）

- 危险命令：`rm` / `rmdir` / `shred` / `mkfs` / `dd`，以及 `rm -rf`、`> /dev/sd*`、`chmod -R` 这类形状；
- `git push --force` / `--force-with-lease` / `-f`；
- **删文件**（即使在范围内，删除是破坏性的，默认转人工）；
- 触碰**计费 / 付费接口**（含 billing/payment/checkout/invoice 等字样）。

### 路由由「爆炸半径」强制（§6.2）

闸门不看「难不难」，看「炸得多大」。即使分诊模型自信地说 `self_fix`（自修），只要改动是
**多文件 / 基础设施级 / 不可逆 / 范围未知**，代码也会强制升级，不让自修。`confidence` 置信度**从不**用作放行依据，只记录。

---

## 6. 出故障时它会怎么走（升级阶梯，只升不降，§6.3）

```
检测到故障 → 去抖确认(§4) → Tier 0 探针复查 → 分诊(提议) → ★代码闸门(裁决)
                                                                  │
  self_fix（≤3 次）  备份→改→全量验证→观察窗口；失败→先重新诊断→回滚→升级
       │ 失败
  coding_agent      git 内 checkpoint→调 claude/codex→验证；失败→git reset→升级
       │ 失败 / 未装
  human             四件套通知你：哪坏了 · 试过什么 · 要你做什么 · 一行指引
```

- **全量验证判据（决策 #2）**：原本 FAIL 的探针转 OK 即算修好；其余探针维持原状即可，**不要求全绿**。
- **级联保护（§7.3）**：多个探针同时 FAIL → 判为系统级 → 直接喊人，不浪费 token 去分诊。
- **观察窗口**：修完盯一段时间，确认没复发才收工。

---

## 7. 装完自检 + 交接

```bash
python -m aesculap config ./config.yaml    # 校验配置合法
python -m aesculap probe  ./config.yaml    # 跑一遍 Tier 0 探针，看当前健康基线
python -m aesculap status ./config.yaml    # 模式 / 开关 / 最近审计 / 挂起问题
```

确认无误后，告诉人类：

1. 装好了，权限档是 **X**（复述你们选的那档及含义）；
2. 分诊 / 自修分别用什么模型；
3. **控制权在他手里**：`disable` 一键全停、`mode observe` 只看不动手、`mode fix` 恢复（细节见 README）；
4. 出事会怎么通知他、通过哪个通道。

---

## 8. 你自己运维时的红线（复诵一遍，别越界）

- **代码拍板，不是你拍板。** 你的路由建议只是建议，闸门可以推翻你。
- **绝不碰**：身份文件、凭证、只读文件、系统路径、Aesculap 自身目录（§8.1/§9）。
- **绝不执行**：`rm`、`git push --force`、删文件、计费接口（§8.1）。
- **key 只说去哪填，绝不要值、绝不回显**（§8.3）。
- **拿不准就升级喊人，不猜、不硬上。** 分诊返回看不懂（JSON 解析失败 / 字段非法 / 缺 `route`）→ 直接降级 human，不猜不重试（决策 #3）。
- **怀疑自己判断飘了**，建议人类切 `observe` 模式（只检测不动手）。

> 完整规约见 [`Aesculap_PRD.md`](./Aesculap_PRD.md)。配置字段示例见
> [`aesculap/resources/config.example.yaml`](./aesculap/resources/config.example.yaml)。
