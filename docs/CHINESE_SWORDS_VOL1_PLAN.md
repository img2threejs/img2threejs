# 中国刀剑程序化重建 · 第一辑执行计划

最后更新：2026-08-28  
状态：Phase 0 完成 / Phase A 完成（本地 showcase）  
范围：本仓库 `chinese-swords` 工作线 + companion showcase 展览

本文是项目发展计划，不是单把刀的制作手册。

- 单件刀剑怎么建模、过门禁：见 [`WEAPON_RECONSTRUCTION_PLAYBOOK.md`](./WEAPON_RECONSTRUCTION_PLAYBOOK.md)
- 汉代环首刀当前实现与证据：见 [`../reconstructions/han-huan-shou-dao/HANDOFF.md`](../reconstructions/han-huan-shou-dao/HANDOFF.md)
- 公开展览前端：本地 `~/img2threejs-showcase`，线上参考 <https://img2threejs.io/>

---

## 1. 一句话目标

把“本地做过一把汉代环首刀”升级成：

> **可复用的中国刀剑程序化重建系列（Vol.1），每把都能在网页里展览，并证明第二把比第一把更便宜。**

不是先堆库存，也不是先做人持刀大战斗。

---

## 2. 当前基线（2026-08-25）

### 2.1 已完成

| 项 | 状态 | 位置 |
|---|---|---|
| 汉环首刀重建主线 | 完成并过全门禁 | `reconstructions/han-huan-shou-dao/` |
| 共享 `dao` family adapter | 已有首个 subtype `han-huan-shou` | `forge/stage2_spec/dao_adapter.py` |
| 制作手册 | 已有 | `docs/WEAPON_RECONSTRUCTION_PLAYBOOK.md` |
| 本地 preview / captures | 已有 | `reconstructions/han-huan-shou-dao/preview/` |
| showcase 仓库 | 已下载可本地运行（**无 git**） | `~/img2threejs-showcase`；PR 前需重 clone/补 remote |

### 2.2 未完成 / 风险

| 项 | 状态 | 说明 |
|---|---|---|
| 环首刀进入 showcase | 本地完成 | `#/demo/han-huan-shou-dao`；showcase 仍无 git，公开 PR 另做 |
| 展示动画（idle / slash） | idle 有 / slash 无 | showcase 工厂副本 `userData.tick` idle rock；未做 Slash |
| 上游同步 | Phase 0 已同步 | 本地 `main`/`chinese-swords` 已含 upstream `v1.5.1`（`d37b6de`） |
| 工作分支 | Phase 0 已整理 | WIP 已提交；仅剩可丢弃 untracked junk |
| 第 2 / 第 3 把刀 | 未开始 | 还不能证明 adapter 可复制 |
| 公开传播包 | 未做 | 缺系列页、对比图话术、统一命名 |

### 2.3 当前所处阶段

```text
建模主线完成
    → 门面作品上架（Phase A 本地完成）
        → 你在这里：可开第 2 把 / 或先做 showcase 公开 PR
        → 生产线验证（第 2 把）
        → 三把成辑并上网
```

---

## 3. 战略原则

1. **先门面，后产量**  
   第一把不上架，就不批量开新刀。

2. **小系列，不散点**  
   Vol.1 固定 3 把，讲清“汉 → 唐 → 明/近古”的形制对比。

3. **第二把服务生产线**  
   第 2 把的首要验收不是“又多一把”，而是：  
   主要增量应落在 subtype / dimensions / ornaments，而不是复制整套 factory。

4. **preview 是工位，showcase 是展厅**  
   开发验收继续走 `reconstructions/<slug>/preview`；  
   对外展示走 showcase demo。

5. **动画是加分项，不是第一阻塞**  
   形准、材质、网页可看优先；idle / 单次 slash 次之；角色持刀最后。

6. **先合上游，再叠功能**  
   避免在落后的 generator/diagnostics 上继续分叉。

7. **权威链路不变**  
   ```text
   参考图 → fill_spec.py → object-sculpt-spec.json → create<Model>.ts → preview/captures
   ```
   不手改生成 TS 当作长期来源。

---

## 4. Vol.1 选题

### 4.1 系列定位

- 标题（中）：中国刀剑 · 第一辑
- 标题（英）：Chinese Swords Vol.1
- 主题：程序化、代码重建、形制对比
- 非主题：战场剧情、角色演出、扫描 mesh、买来的资产包

### 4.2 三把刀

| 顺序 | slug | 工作名 | family / subtype | 作用 |
|---|---|---|---|---|
| 1 | `han-huan-shou-dao` | 汉代环首刀 | `dao` / `han-huan-shou` | 镇馆之宝；已有成品 |
| 2 | `tang-heng-dao` | 唐横刀（简型） | `dao` / `tang-heng` | 验证 adapter 复用 |
| 3 | `ming-yaodao` 或 `xiu-chun-dao-lite` | 明腰刀或绣春刀简型 | `dao` / 待定 | 拉出时代跨度，形成辑 |

### 4.3 为什么是这三把

- 都还在 **刀（dao）** 家族，可共用槽位：blade / guard / ferrule / handle / pommel。
- 形制差异足够大：环首 vs 唐刀装 vs 近古刀装。
- 观众能一眼看出“系列”，而不是三把随机兵器。

### 4.4 明确不做（Vol.1 范围外）

- 剑（jian）家族完整展开
- 人持刀、连续战斗动画、双人对砍
- 十把以上库存
- 物理切断、布料、复杂粒子作为必选项
- 未过门禁就公开“最终完成”表述

---

## 5. 阶段划分与执行清单

总节奏建议：**6–8 周思维**，按里程碑推进，不按“有空再做一把”推进。

---

### Phase 0 — 工程整备

**目标：** 让后续工作发生在干净、可合并、可测试的基线上。

#### 清单

- [x] 盘点 `chinese-swords` 未提交改动并分类：
  - 应保留的功能（如 `dao_adapter`、playbook、环首刀重建）
  - 应丢弃的试验
  - 可能与上游冲突的文件
- [x] 备份 WIP：提交到明确 WIP 点 `cda1d4c`（未用 stash；大重建树更适合 commit）
- [x] `git fetch upstream --tags`
- [x] 更新本地 `main` 到 `upstream/main`（ff 至 `d37b6de` = v1.5.1 + showcase 链接修正）
- [x] 将 `main` 合入 `chinese-swords`（merge commit `ebffb37`）
- [x] 解决高冲突文件：
  - `forge/stage3_build/generate_threejs_factory.py`（auto-merge：保留 closed ground-blade + v1.5.1）
  - `forge/stage4_review/diagnose_render.py`（auto-merge：保留 color cluster helpers + upstream mask 修复）
  - 相关 tests（`test_primitive_watertightness` 手工合并 ground-blade + tapered-sweep）
- [x] 跑：
  ```bash
  python3 -m unittest discover -s forge/tests -p 'test_*.py'
  ```
  结果：1119 ran；4 失败来自 showcase 残留 `__geometry_engine_smoke__.ts`（清理后 `test_showcase_tsc_smoke` 3/3 通过）；武器相关 focused suite 89/89 通过；skipped=4。
- [x] 确认环首刀在合并后仍能从 `fill_spec.py` 再生：strict-quality PASS；factory 已按 v1.5.1 重新生成（SRGBColorSpace / textureless 发射差异已吸收，无额外迁移 blocker）
- [x] 固定 showcase 路径：
  ```bash
  export IMG2THREEJS_SHOWCASE_ROOT=~/img2threejs-showcase
  ```
- [x] 检查 showcase git：当前为 **无 `.git` 的 tarball/快照**，可本地 `npm run dev/build`；正式 PR 前需重 clone 或补 remote（Phase 0 不强制公开 PR）

#### 完成定义

- `main` 已跟踪上游
- `chinese-swords` 基于新 `main`
- 测试绿色或已知失败有文档
- WIP 不再只活在脏工作区

---

### Phase A — 门面作品上架（汉环首刀）

**目标：** 第一把刀成为可给外人看的正式展品。

#### A1. 本地再确认

- [x] 启动环首刀 preview，确认模型仍可渲染  
  （`python3 -m http.server 4173` → preview index 200；bundle 仍在）
- [x] 抽查关键 captures / HANDOFF 分数未回退  
  （Divine Eye hero/optimization PASS；IoU ≈ 0.89；strict-quality PASS）
- [x] 核对权威文件仍是：
  - `fill_spec.py`
  - `object-sculpt-spec.json`
  - `createHanHuanShouDao.ts`

#### A2. 接入 showcase

- [x] 在 showcase 中 scaffold：
  ```bash
  cd ~/img2threejs-showcase
  npm run new-demo -- han-huan-shou-dao "Han Huan-Shou Dao" object
  ```
- [x] 放入工厂代码（从重建产物整理为 showcase demo 工厂）
- [x] 添加 `public/references/han-huan-shou-dao.png`（或系列统一裁切图）
- [x] 填写 `src/demos/registry.ts`：
  - id / title / author / description
  - cameraPosition / cameraTarget / cameraFov
  - subjectClass = object
- [x] `npm run dev` / preview 验收：
  - `#/demo/han-huan-shou-dao`
  - 轨道旋转、参考图、爆炸视图（若启用）
- [x] `npm run build` 通过

#### A3. 展示层（最小即可）

优先级从高到低：

1. [x] 静态展览相机与灯光调到“第一眼像样”
2. [x] 可选：轻微 idle rock（`userData.tick`）
3. [ ] 可选：单次 `Slash`（`animationController` 按钮）— **本阶段不做**
4. [x] 不做角色持刀

Slash 若做，约束如下：

- 枢轴在握把中段，不在模型 AABB 中心
- 单次触发优先于自动连砍
- 截图/评审路径保持可冻结

#### A4. 作品说明

- [x] 中文短描述（展览卡）— 见 HANDOFF Phase A 话术
- [x] 英文短描述（registry）
- [x] 固定话术要点：
  - code-only / procedural Three.js
  - 非 photogrammetry、非下载 mesh
  - 基于三视图 / 参考图重建
  - 汉代环首刀形制

#### A5. 本仓库记录

- [x] 更新 `reconstructions/han-huan-shou-dao/HANDOFF.md`：
  - showcase 路由
  - 展示功能（idle/slash 有无）
  - 已知限制
- [x] 在本计划勾选 Phase A 完成项
- [x] Phase A 视觉证据：`captures/showcase-phase-a/`（hero + orbit + 并排对照，已读回）

#### 完成定义

- 本地 showcase 可打开环首刀 demo
- 有参考图与可读描述
- 不依赖开发者解释也能看懂“这是什么”

**公开上网可以在 A 结束后就做一次“单品发布”，不必等三把齐。**  
（当前 showcase 无 git：本地展品已成立；正式 PR 前先重 clone / 补 remote。）

---

### Phase B — 生产线验证（第 2 把：唐横刀简型）

**目标：** 证明 `dao_adapter` 能降低第二把成本。

#### B1. 开题

- [ ] 选定参考图，放入 `references/chinese-swords/`
- [ ] 建立：
  ```text
  reconstructions/tang-heng-dao/
  ```
- [ ] 写 `HANDOFF.md` 初始目标：
  - 视觉身份
  - 可复用槽位
  - 本把独有装饰
  - 非目标（哪些不做）

#### B2. Adapter 扩展，而不是复制

- [ ] 在 `dao_adapter.py` 增加 subtype `tang-heng`（名称可最终敲定）
- [ ] 复用稳定槽位：
  - blade
  - guard
  - front ferrule
  - handle
  - rear ferrule
  - pommel
- [ ] 只把唐刀差异写成数据/规则：
  - 尺寸表
  - 刃轮廓站点
  - 护手/刀镡形态
  - 柄首形态
- [ ] 禁止复制一整份仅服务单刀的 adapter

#### B3. 按 playbook 跑完整重建

- [ ] `fill_spec.py` → spec → factory
- [ ] 依次过门禁：blockout → … → optimization
- [ ] 本地 preview + captures
- [ ] 记录三角面、draw call、关键 IoU / Divine Eye

#### B4. 复用性复盘（硬要求）

第二把完成后必须写进其 `HANDOFF.md`：

- [ ] 哪些直接复用了环首刀链路
- [ ] 哪些被迫改了共享生成器
- [ ] 哪些仍是单件特殊逻辑
- [ ] 预估：若做第 3 把，还应抽哪些共享能力

若结论是“几乎整份重写”，则 **先还债，再开第 3 把**。

#### B5. 上架第 2 把

- [ ] showcase demo `tang-heng-dao`
- [ ] 与环首刀相同的展览字段质量
- [ ] 两把可在 gallery 中并排看到

#### 完成定义

- 第 2 把过门禁
- adapter subtype 路径成立
- 复盘文档写完
- showcase 中至少 2 把中国刀

---

### Phase C — 系列成型并上网（第 3 把 + Vol.1）

**目标：** 三把成辑，作为可传播专题，而不是散装 demo。

#### C1. 第 3 把

- [ ] 在 `ming-yaodao` / `xiu-chun-dao-lite` 中定一个
- [ ] 继续走 subtype 扩展
- [ ] 完整门禁 + preview + showcase

#### C2. 系列包装

- [ ] 统一命名：
  - 中文系列名
  - 英文系列名
  - 每把 slug
- [ ] 统一卡片信息：
  - 时代
  - 形制要点
  - procedural / code-only 说明
- [ ] 准备 3 组对比图：
  - 参考图 vs 渲染图
  - 可选三视图页
- [ ] 写 Vol.1 总述（可放本仓库 `reconstructions/README.md` 或 showcase 系列说明）

#### C3. 网上展示

发布顺序建议：

1. [ ] 本地 showcase 三把齐
2. [ ] 单品/系列截图与短视频（轨道 + 爆炸 + 可选一刀）
3. [ ] 若向官方 showcase 贡献：按该仓库 `CONTRIBUTING.md` 开 PR
4. [ ] 同步发自己的 GitHub / 社媒 / 项目笔记
5. [ ] 收集反馈：哪把最受关注、观众是否看懂“代码重建”

#### C4. 传播话术（固定）

对外优先强调：

- 一张参考 / 三视图 → 程序化 Three.js 模型
- 可交互网页展览
- 中国刀剑形制系列
- 不是扫描，不是资源包

避免过度承诺：

- “百分百考古复原”
- “已可直接进 3A 游戏战斗”
- “自动任意刀斩切物理”

#### 完成定义

- Vol.1 三把都在可访问 gallery 中
- 有系列叙事和对比图
- 完成至少一次公开曝光
- 反馈记录进本计划或单独 notes

---

### Phase D — 后续方向（Vol.1 完成后才选）

完成 Vol.1 后再决策，不提前锁死：

| 方向 | 何时考虑 |
|---|---|
| Vol.2 更多朝代刀 | adapter 复用已验证、观众有正向反馈 |
| 剑（jian）家族 | 刀家族稳定后单独开 family，不硬塞 dao |
| 鞘 / 装配拆解 / 博物馆标签风 | 展览向增强 |
| 更完整 slash / 出鞘 | 单品展示已成立 |
| 角色持刀 | 明确要做角色内容时 |
| 回馈上游 img2threejs | `dao` family、weapon playbook、gates 足够干净时 |

---

## 6. 目录与命名约定

### 6.1 本仓库

```text
references/chinese-swords/           # 原图与分类参考
reconstructions/
  README.md                          # 系列入口（本阶段应补）
  han-huan-shou-dao/                 # 第 1 把
  tang-heng-dao/                     # 第 2 把（计划）
  <third-slug>/                      # 第 3 把（计划）
docs/
  WEAPON_RECONSTRUCTION_PLAYBOOK.md  # 怎么做一把
  CHINESE_SWORDS_VOL1_PLAN.md        # 系列怎么发展（本文）
forge/stage2_spec/dao_adapter.py     # 刀家族共享合同
```

### 6.2 showcase

```text
src/demos/<slug>/create...Model.ts
public/references/<slug>.png
src/demos/registry.ts                # 展览注册
#/demo/<slug>
```

slug 规则：

- kebab-case
- 与重建目录名一致
- 稳定后不随意改名

---

## 7. 每把刀的通用完成定义

单把刀只有同时满足以下条件，才算“可宣布完成”：

### 重建

- [ ] 有独立 `reconstructions/<slug>/`
- [ ] 权威输入是 `fill_spec.py`，不是手改 TS
- [ ] 关键视觉门禁与结构门禁通过，并留 captures
- [ ] `HANDOFF.md` 写清结论、限制、再生方式

### 展示

- [ ] showcase 有对应 demo
- [ ] 相机/灯光/参考图可读
- [ ] 描述清楚时代与形制
- [ ] 性能大致可控（三角面与 draw call 有记录）

### 系列

- [ ] 能指出与其他刀共用的 adapter/subtype 路径
- [ ] 能指出本把独有差异
- [ ] 不破坏前一把的再生与展览

---

## 8. 动画策略（展示增强，非主线阻塞）

### Vol.1 允许

| 级别 | 内容 | 建议 |
|---|---|---|
| L0 | 静态可轨道观察 | 必做 |
| L1 | 轻微 idle rock | 推荐 |
| L2 | 单次 Slash 按钮 | 环首刀可试点 |
| L3 | 出鞘 / 连斩 / 命中特效 | 可选，不阻塞上架 |

### Vol.1 不做

- 角色骨骼持刀挥砍
- 复杂连招状态机
- 以动画代替造型准确性

实现入口保持 showcase 惯例：

- 自动动效：`root.userData.tick(dt, elapsed)`
- 可点动作：`root.userData.sculptRuntime.animationController`

---

## 9. 建议执行顺序（立刻可做）

按优先级：

1. **Phase 0**：同步上游 + 整理 `chinese-swords` WIP  
2. **Phase A**：环首刀进 showcase，成为门面  
3. **Phase A 小增强**：idle 或单次 slash（可选）  
4. **Phase B**：唐横刀简型，专门压 adapter  
5. **Phase C**：第 3 把 + 系列包装 + 上网  
6. **Phase D**：根据反馈再扩展  

---

## 10. 风险与应对

| 风险 | 表现 | 应对 |
|---|---|---|
| 上游分叉 | generator/tests 难合 | Phase 0 先做 |
| 第二把变一次性工程 | 复制大量特殊代码 | 强制写复用复盘；不通过就还债 |
| 未展示先做动画 | 浪费在动作上，外人仍看不见 | 先 A 后动画 |
| 参考图漂移 | 分数不可比 | 锁定参考与裁切，不在中途偷换 |
| 公开过度承诺 | 被当成考古/游戏成品标准 magnify | 使用第 5.C4 话术边界 |
| showcase 非 git clone | 难 PR | Phase 0/A 补 remote 或重 clone |

---

## 11. 决策记录

| 日期 | 决策 |
|---|---|
| 2026-08-25 | Vol.1 走“3 把小系列”，不先做大库存 |
| 2026-08-25 | 先门面（环首刀上架），再产量 |
| 2026-08-25 | 第 2 把优先唐横刀简型，用来验证 `dao_adapter` |
| 2026-08-25 | preview = 工位，showcase = 展厅 |
| 2026-08-25 | 角色持刀与 jian 家族不进 Vol.1 必做范围 |
| 2026-08-25 | 本计划落地为 `docs/CHINESE_SWORDS_VOL1_PLAN.md` |
| 2026-08-25 | Phase 0 完成：WIP 提交 + main ff upstream + merge 入 chinese-swords；环首刀可再生 |
| 2026-08-28 | Phase A 完成（本地）：环首刀接入 showcase；idle rock；截图门禁读回；slash 未做 |

---

## 12. 下一步（写完本文之后）

立即执行的第一项应是 **Phase 0**，除非有明确理由只做展示：

```text
[下一动作]
1. Phase A 本地已完成：#/demo/han-huan-shou-dao
2. 可选：showcase 重 clone / 补 remote 后开单品 PR（公开上网）
3. 或进入 Phase B：唐横刀简型，验证 dao_adapter 复用
```

本地展览启动：

```text
export IMG2THREEJS_SHOWCASE_ROOT=~/img2threejs-showcase
cd ~/img2threejs-showcase && npm run build && npm run preview -- --host 127.0.0.1 --port 4174
# → http://127.0.0.1:4174/#/demo/han-huan-shou-dao
```

---

## 13. 相关路径速查

| 用途 | 路径 |
|---|---|
| 本计划 | `docs/CHINESE_SWORDS_VOL1_PLAN.md` |
| 单刀手册 | `docs/WEAPON_RECONSTRUCTION_PLAYBOOK.md` |
| 环首刀交接 | `reconstructions/han-huan-shou-dao/HANDOFF.md` |
| 刀家族 adapter | `forge/stage2_spec/dao_adapter.py` |
| 参考图 | `references/chinese-swords/` |
| showcase 本地 | `~/img2threejs-showcase` |
| showcase 线上 | <https://img2threejs.io/> |
