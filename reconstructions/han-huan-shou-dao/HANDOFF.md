# 汉代环首刀重建交接

最后更新：2026-08-28

通用制作流程、刀/剑差异、模块化装饰合同和完整验收清单见
[`docs/WEAPON_RECONSTRUCTION_PLAYBOOK.md`](../../docs/WEAPON_RECONSTRUCTION_PLAYBOOK.md)。本文件只记录
汉代环首刀当前实现、证据和项目特有限制。系列发展见
[`docs/CHINESE_SWORDS_VOL1_PLAN.md`](../../docs/CHINESE_SWORDS_VOL1_PLAN.md)。

## 当前结论

这次重建已经从一次性 primitive blockout 迁移到共享 `dao` family adapter，并通过
`blockout`、`structural-pass`、`form-refinement`、`material-pass`、`surface-pass`、`lighting-pass`、`interaction-pass` 和 `optimization-pass` 的完整门禁。不要再使用旧交接里的
失败分数、四枚嵌件、torus 环首、开边刀身或“结构层尚未拆分”的结论。

**Phase A（门面展览）已在本地 showcase 落地**：`#/demo/han-huan-shou-dao` 可打开，含参考图、
中英文说明、reference look-dev 灯光、轻微 idle rock，以及六模块爆炸视图。

- 权威参考：`references/chinese-swords/汉代环首刀三视图.jpg`
- 评分裁切：`reference-face-clean.png`
- 权威建模输入：`fill_spec.py`
- 生成 spec：`object-sculpt-spec.json`
- 生成工厂：`createHanHuanShouDao.ts`（工位权威产物）
- 浏览器预览（工位）：`http://127.0.0.1:4173/reconstructions/han-huan-shou-dao/preview/index.html`
- 公开展览（展厅，本地）：`http://127.0.0.1:4174/#/demo/han-huan-shou-dao`
- 约束：纯程序化 Three.js；没有下载 mesh、glTF 或美术包

最新门禁结果：

| Gate | 结果 |
|---|---:|
| strict-quality | PASS |
| silhouette IoU | 0.8898 |
| aspect-ratio delta | 0.0000 |
| scale delta | 0.0000 |
| Divine Eye | PASS / 0.8592（目标 0.85） |
| multi-angle degenerate view | false |
| blade boundary / non-manifold | 0 / 0 |
| blade signed / enclosed volume | +0.0053927553 / 0.0053927553 |
| structural assembly | PASS / 6 modules moved |
| part coverage | PASS / 0 errors, 0 warnings |
| runtime triangles / budget | 86,544 / 250,000 |
| draw calls / budget | 46 / 160 |
| performance backend | SwiftShader / FPS report-only warning |
| forge tests | 680 passed, 25 skipped |

`object-sculpt-spec.json` 已记录八条独立的 `action=continue` 审核；spec pipeline 与
`.img2threejs/state.json` 均为 `complete`，没有剩余 mandatory step。

## Dao Family 架构

共享适配器在 `forge/stage2_spec/dao_adapter.py`，稳定槽位为：

1. blade
2. guard
3. front ferrule
4. handle
5. rear ferrule
6. pommel

当前首个 subtype 是 `han-huan-shou`。`DaoDimensions` 负责尺寸表，
`assemble_dao_dimensions()` 沿 X 轴确定装配位置和六个柄部嵌件槽位，正反两面复用这些槽位。
重建 spec 通过 `familyAdapter` 保存这份合同；不要复制当前生成的 TypeScript 去做下一把刀。

当前 subtype：

- 单刃 `ground-blade`，24 个面视站点 + 每站侧视厚度
- YZ 平面的薄圆盘护手
- 前后鎏金箍、圆柱缠柄
- 正反两面各六枚带暗色浅嵌座的金色菱形嵌件
- 双向螺旋缠柄暗缝
- 带真实椭圆孔的浅挤出环首板和短颈
- 正反两面各三条带受控不规则度的程序化刃纹、各三条内中外环首刻线

结构层以 blade、guard、collar、handle、ferrule、ring 六个可分解模块为运行时边界，
共有六个 assembly socket 和七个 collider。刃纹跟随 blade，缠柄暗缝与十二枚嵌件跟随
handle，短颈与刻线跟随 ring；点击拾取可解析 component/module，爆炸视图六个模块均移动。
装饰组件通过 `ownerModule`、`face`、`mergePolicy` 保存后续拼装合同：金色嵌件为
`keep`，保留独立替换与选择能力；嵌座、刃纹、暗缝和刻线为 `bake`，生产导出时可烘焙
或合批到所属主体。所有装饰组件均无 collider。

## 共享刀身修复

`forge/stage3_build/generate_threejs_factory.py` 的 `ground-blade` 已改为有索引的纵向条带：

- 同一截面面片沿刀长共享顶点，消除旧版横向平面分面
- 刃口、研磨线、脊线仍通过重复边界顶点保持硬折线
- 刀根与刀尖显式封口
- 三角面绕序统一朝外，signed volume 为正
- 支持每站 `thicknesses`
- 支持截面顶点 tone，并可在最终 shader 阶段调制
- `geometry.userData.solidVolume` 明确标记为 `closed-surface-volume`

实际加载 `createHanHuanShouDao.ts` 后测得 322 个有效三角形；尖端折叠产生的
14 个零面积三角形被拓扑探针过滤，真实边界边、非流形边和绕序冲突均为 0；
signed volume 与 enclosed volume 均为正值 `0.005392755338417231`。这是标准游戏道具的
封闭表面实体：渲染网格负责外观，非 trigger box collider 负责碰撞；不是体素或四面体
仿真模型，也不支持任意剖切后自动生成内部材料。

## 视觉证据

`captures/` 内的最终证据包括：

- `hero.png`：与参考同构图的正交面视
- `comparison-hero.png`：1680x360 参考/渲染并排图
- `orbit-plus35.png` / `orbit-minus35.png`：双侧三分之四
- `profile.png`：90 度厚度轴视图
- `rear.png` / `topdown.png`
- `head-hero.png` / `head-threequarter.png`
- `map-stripped-hero.png`：真实无贴图、无光照材质捕获，不是 hero 副本
- `diagnose-hero.json` / `divine-eye-hero.json`
- `diagnose-multi-angle.json`
- `capture-log.json`：9 张图均成功，零 console error
- `structural-hero.png` / `comparison-structural.png`：结构 pass 的独立正交证据
- `structural-orbit-plus35.png` / `structural-orbit-minus35.png` / `structural-profile.png`
- `structural-assembly-exploded.png`：六模块爆炸视图
- `assembly-check.json` / `parts.json` / `part-coverage.json`：运行时装配与覆盖率审计
- `diagnose-structural.json` / `divine-eye-structural.json`
- `diagnose-structural-multi-angle.json` / `pass-gate-structural.json`
- `form-hero.png` / `comparison-form.png`：form pass 的独立正交证据
- `form-head-hero.png` / `form-head-threequarter.png`：浅嵌座与三层环首刻线近景
- `form-orbit-plus35.png` / `form-orbit-minus35.png` / `form-profile.png`
- `form-assembly-exploded.png` / `form-assembly-check.json` / `form-parts.json`
- `diagnose-form.json` / `divine-eye-form.json` / `diagnose-form-multi-angle.json`
- `form-part-coverage.json` / `pass-gate-form.json`
- `material-hero.png` / `comparison-material.png`：material pass 的独立主视图与对照
- `material-material-neutral.png` / `material-material-reference.png`：中性和参考匹配光照
- `material-material-grazing-blade.png` / `material-material-grazing-handle.png`：刀身和柄部擦光近景
- `material-material-audit.json`：六种实际材质的独立 1024x1024 albedo / roughness / height / normal / AO 审计
- `diagnose-material.json` / `divine-eye-material.json` / `diagnose-material-multi-angle.json`
- `material-part-coverage.json` / `pass-gate-material.json`
- `surface-hero.png` / `comparison-surface.png`：surface pass 的独立主视图与对照
- `surface-material-grazing-blade.png` / `surface-material-grazing-handle.png`：局部污垢与高点磨损的擦光证据
- `surface-material-audit.json`：六种材质 locality mask 的运行时审计，failures 为空
- `diagnose-surface.json` / `divine-eye-surface.json` / `diagnose-surface-multi-angle.json`
- `surface-part-coverage.json` / `pass-gate-surface.json`
- `lighting-material-reference.png` / `comparison-lighting.png`：lighting pass 的正式评分图与对照
- `lighting-material-neutral.png`：中性灯光下的材质可读性基线
- `lighting-material-grazing-blade.png` / `lighting-material-grazing-handle.png`：擦光下的表面响应
- `lighting-lighting-audit.json`：neutral/grazing/reference 三模式的 key/fill/rim、ACES、exposure、白底、环境和阴影审计
- `diagnose-lighting.json` / `divine-eye-lighting.json` / `diagnose-lighting-multi-angle.json`
- `lighting-part-coverage.json` / `pass-gate-lighting.json`
- `interaction-interaction-audit.json`：八个宏观/中层 pivot、module 映射、六模块位移与零漂移恢复、socket/collider/destruction 审计
- `interaction-assembly-exploded.png`：六个稳定模块的独立爆炸图
- `interaction-selection-handle-integral.png` / `interaction-selection-ring-integral.png`：integral component 到 owning module 的选择证据
- `diagnose-interaction.json` / `divine-eye-interaction.json` / `diagnose-interaction-multi-angle.json`
- `interaction-part-coverage.json` / `pass-gate-interaction.json`
- `optimization-performance-audit.json`：triangle、draw-call、FPS backend、geometry/material/texture 与 LOD/repetition 决策审计
- `optimization-hero.png` / `comparison-optimization.png`：阴影缓存优化后的最终视觉回归
- `optimization-interaction-audit.json` / `optimization-lighting-audit.json` / `optimization-material-audit.json`
- `diagnose-optimization.json` / `divine-eye-optimization.json` / `diagnose-optimization-multi-angle.json`
- `optimization-part-coverage.json` / `pass-gate-optimization.json`
- `two-sided-hamon-front.png` / `two-sided-hamon-back.png`：双面刃纹回归证据
- `two-sided-hamon-back-mobile.png`：移动视口背面刃纹证据
- `modular-assembly-front.png` / `modular-assembly-back.png`：组件化装配双面全景
- `modular-handle-front.png` / `modular-handle-back.png`：握把双面嵌件近景
- `modular-ring-front.png` / `modular-ring-back.png`：环首双面刻线近景
- `modular-back-mobile.png`：组件化背面移动视口证据

Agent 视觉复核结论：宏观比例、刀身腹线、圆盘方向、开放环首和缠柄身份已经成立；
三分之四和轴向视图证明它不是平面卡片。正反面各六枚嵌件已有暗色浅嵌边，刃纹不再机械平行，
且正反两面各有三条略微错相的独立曲线；环首增加了中层刻线。六种材质都已接入独立的程序化 PBR 通道，并由已有 spec 参数驱动
腔隙积尘和高点磨损；强度保持克制，以服从干净插画参考。仍然是程序化近似：参考刃纹
更自然，环首纹饰更繁复，表面完成度不是文物级写实。

## 重新生成与验收

从仓库根目录运行：

```bash
python3 reconstructions/han-huan-shou-dao/fill_spec.py
python3 forge/stage2_spec/validate_sculpt_spec.py --strict-quality \
  reconstructions/han-huan-shou-dao/object-sculpt-spec.json
python3 forge/stage3_build/generate_threejs_factory.py \
  reconstructions/han-huan-shou-dao/object-sculpt-spec.json \
  --out reconstructions/han-huan-shou-dao/createHanHuanShouDao.ts \
  --pass-id optimization-pass --force
```

预览 bundle：

```bash
/home/nyb/.npm/_npx/beb367dfa21eb3f5/node_modules/esbuild/bin/esbuild \
  reconstructions/han-huan-shou-dao/preview/main.js \
  --bundle --format=esm \
  --outfile=reconstructions/han-huan-shou-dao/preview/dist/preview.js
```

服务与截图：

```bash
python3 -m http.server 4173 --bind 127.0.0.1
PREVIEW_URL=http://127.0.0.1:4173/reconstructions/han-huan-shou-dao/preview/index.html \
  node reconstructions/han-huan-shou-dao/preview/capture.mjs
```

评分时必须使用 `reference-face-clean.png` 和 1680x360 panel；不要换回锈蚀遗物照，
也不要用默认 720x720 cover crop。

## Showcase 展览（Phase A）

本地 companion：`~/img2threejs-showcase`（当前为 **无 `.git` 的 tarball/快照**；正式 PR 前需重 clone / 补 remote）。

| 项 | 值 |
|---|---|
| demo id / 路由 | `han-huan-shou-dao` → `#/demo/han-huan-shou-dao` |
| 工厂副本 | `src/demos/han-huan-shou-dao/createHanHuanShouDaoModel.ts`（由工位 `createHanHuanShouDao.ts` 整理；展示用 `animate` / idle rock 仅在此副本） |
| 参考图 | `public/references/han-huan-shou-dao.png`（来自 `reference-face-clean.png`，≤800KB） |
| 相机 | `cameraPosition [1.95, 0.72, 5.35]`，`cameraTarget [1.15, 0.02, 0]`，`fov 28` |
| 灯光 | `installLights` → `createHanHuanShouDaoLookDevLights('reference')` |
| 展示功能 | L0 静态展览 + L1 轻微 idle rock（`userData.tick`）；**无** Slash；**无**角色持刀 |
| 爆炸视图 | 画廊自带；六模块（blade / guard / collar / handle / ferrule / ring） |
| 启动 | `export IMG2THREEJS_SHOWCASE_ROOT=~/img2threejs-showcase`；`cd ~/img2threejs-showcase && npm run dev` 或 `npm run build && npm run preview -- --host 127.0.0.1 --port 4174` |
| 工位 preview | 仓库根 `python3 -m http.server 4173 --bind 127.0.0.1` → `http://127.0.0.1:4173/reconstructions/han-huan-shou-dao/preview/index.html` |

### 展览话术（固定）

- **中文短描述（展览卡）**：汉代环首刀 · 程序化 Three.js 重建。由三视图插画重建，非扫描、非下载 mesh；单刃闭体刀身、薄圆盘护手、鎏金箍、缠柄菱形嵌件与开放环首。
- **英文（registry blurb）**：code-only procedural reconstruction from a three-view illustration plate — not photogrammetry, not a downloaded mesh; Han-dynasty huan-shou (ring-pommel) form.
- 强调：code-only / procedural Three.js；基于三视图 / 参考图；汉代环首刀形制。

### Phase A 视觉证据

目录：`captures/showcase-phase-a/`

- `showcase-hero.png` / `showcase-hero-page.png`：展览固定参考视角（整刀可读）
- `showcase-orbit-plus35.png` / `showcase-orbit-minus35.png`：双侧三分之四
- `showcase-profile.png` / `showcase-threequarter.png`：侧视与高位三分之四
- `showcase-capture-white.png`：`?capture=1` 白底评价构图（模型在场；默认 `frameForCapture` 比展览 hero 更疏）
- `comparison-reference-showcase.png`：参考裁切 vs 白底 capture 并排
- `comparison-reference-hero.png`：参考 vs 展览 hero 并排
- `capture-log.json`：runtime ready、46 meshes / 86,544 tris、7 parts、零 console error

读回结论：展览 hero 与 orbit 可辨单刃刀身、圆盘护手、缠柄嵌件与开放环首；白底 capture 轮廓成立。仍是程序化近似——参考刃纹更自然，环首纹饰更繁复。

截图脚本（可复跑）：`preview/capture-showcase-phase-a.mjs`（依赖 preview 目录下 `playwright-core` + 本机 Chrome）。

## 完成状态与限制

- 八个 build pass、最终 part coverage 和 action-ready checklist 已完成。
- **Phase A 本地 showcase 上架已完成**（见上节）；公开 PR / 上网仍受 showcase 无 git 约束。
- 静态 4096px shadow map 只在换灯或 explode/restore 时更新，避免每帧重绘阴影，同时保持最终截图不变。
- 十二枚金色嵌件没有改成 InstancedMesh：它们是独立命名、可选择的 interaction target，并通过 `mergePolicy=keep` 保留后续替换能力；当前 46 draw calls 仍低于预算。
- 当前无头 Chrome 使用 SwiftShader，完整双面组件版本测得 2.66 FPS，只能作为软件渲染环境诊断。硬件 WebGL 的 30 FPS 仍是部署目标，不能把本机软件数值冒充硬件基准。
- Showcase 工厂副本可有展示层 `animate` / `userData.tick`；**不要**把手改 TS 回写为工位权威源——改模型仍走 `fill_spec.py` → 再生 → 再同步副本。
- 未做 L2 Slash、未做角色持刀（Vol.1 范围外 / 非 Phase A 阻塞）。
- 若以后继续提升写实度，应作为新的艺术迭代重新开 pass，而不是改写本轮已通过证据。

不要重新打开已经解决的主线：正交相机、白底 framing、圆盘方向、刀身开边和 ring hole 都已有新证据。
