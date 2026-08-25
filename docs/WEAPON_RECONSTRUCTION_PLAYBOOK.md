# 程序化刀剑重建手册

最后更新：2026-08-25

这份手册用于在本仓库内，以纯程序化 Three.js 重建可用于游戏或交互展示的刀、剑类道具。
它总结自汉代环首刀项目，但流程、组件合同和验收方法应当可以直接复用于后续刀剑。

若要看**系列怎么发展、先做哪几把、何时上 showcase / 上网展示**，见
[`CHINESE_SWORDS_VOL1_PLAN.md`](./CHINESE_SWORDS_VOL1_PLAN.md)。
本手册只负责单件重建方法。

这里的“实体”指闭合、可定向、有正体积的表面网格，而不是只有正面的一张薄片；碰撞仍由单独的
简化 collider 负责。它不是体素或四面体仿真模型，也不承诺任意剖切后自动生成内部材质。

## 1. 权威来源与修改边界

每个项目都遵守同一条生成链：

```text
参考图和测量
    -> fill_spec.py（权威建模输入）
    -> object-sculpt-spec.json（生成的中间合同）
    -> create<ModelName>.ts（生成的运行时工厂）
    -> preview / captures（预览与验收证据）
```

修改模型时改 `fill_spec.py` 或共享生成器，不要手改生成的 TypeScript。后者会在下一次生成时被覆盖，
也无法让修改稳定地复用到下一把兵器。参考图决定视觉目标，spec 决定结构和参数，浏览器实际输出决定
最终是否通过，三者有冲突时不能只相信代码或单张截图。

共享代码和单件代码的边界如下：

- 兵器族稳定的尺寸计算、槽位、组件树约束放进 family adapter。
- 某一件兵器的轮廓站点、纹样曲线、材质参数和证据路径放进它自己的 `fill_spec.py`。
- 能被多种兵器复用的几何能力放进 `forge/stage3_build/generate_threejs_factory.py`。
- 预览 UI、截图脚本和审计脚本只验证运行时结果，不作为建模参数的第二来源。

## 2. 新项目目录

为每件兵器建立独立目录，推荐结构如下：

```text
reconstructions/<slug>/
├── HANDOFF.md
├── fill_spec.py
├── object-sculpt-spec.json
├── create<ModelName>.ts
├── preview/
│   ├── index.html
│   ├── main.js
│   ├── capture.mjs
│   └── dist/preview.js
└── captures/
```

参考图放在 `references/` 下的明确分类中。保留原图，同时单独制作真正用于评分的裁切图；不要在迭代中
悄悄更换参考、裁切比例或背景，否则前后分数不可比较。

开始新项目时，复制的是目录组织和 `fill_spec.py` 的编写模式，不是现成的生成 TypeScript。若新兵器
仍属于 dao 或 jian family，应优先扩充 subtype，而不是复制一份只服务于单件模型的 adapter。

## 3. 先定义交付目标

建模前先在 spec 中写清楚以下目标：

- 使用场景：静态展示、可拾取道具、角色装备，还是可拆分/可破坏物件。
- 视觉目标：必须匹配的轮廓、比例、结构身份和材质；允许近似的细节。
- 结构目标：哪些是六至十个稳定大模块，哪些是可替换装饰，哪些只需要烘焙。
- 几何目标：刀身必须闭合、正绕序、正体积；孔洞、开口和连接处必须是真实几何。
- 交互目标：选择、爆炸视图、socket、collider、LOD 和破坏边界。
- 性能预算：三角形、draw call、纹理尺寸和硬件 WebGL FPS 目标。

默认把它当作游戏道具来做：渲染网格负责外观，简化碰撞体负责物理，组件元数据负责选择、装配和
后续替换。不要为了“实体”把刀身做成体素，也不要让高密度纹饰参与碰撞。

## 4. 参考图与测量

### 4.1 参考选择

至少准备以下信息：

- 正面或侧面主视图，用于总长、刃线、背线和柄部比例。
- 三分之四视图，用于厚度、倒角、护手方向和环首开孔。
- 柄部及首部近景，用于缠绕、嵌件、刻线和连接方式。
- 材质参考，用于钢、铜/金、木、皮革或织物的颜色与粗糙度响应。

若只有单张图，先区分“图中可测量事实”和“依据兵器类型推断”。推断项应写在 spec 或交接文档里，
避免后续把假设误当成史料。

### 4.2 坐标与尺度

同一 family 必须固定坐标合同。当前 dao adapter 以 X 为兵器长轴；厚度轴和面视轴也必须在 subtype
开始时确定，之后不要为了修相机而旋转局部几何。推荐统一记录：

| 参数 | 含义 |
|---|---|
| `overallLength` | 全长 |
| `bladeLength` | 刀/剑身长度 |
| `bladeWidth` | 根部或代表性宽度 |
| `bladeThickness` | 脊部最大厚度 |
| `guardThickness` | 护手沿长轴厚度 |
| `handleLength` | 可握持长度 |
| `ferruleLength` | 前后箍长度 |
| `pommelLength` | 首部及连接颈长度 |

先用一个可信尺寸确定绝对尺度，其余值从参考图比例推导。每个组件同时保留局部尺寸和装配位置，避免
靠零散 magic number 对齐。先检查总长之和，再检查相邻部件是否有合理嵌入量，而不是仅靠肉眼消缝。

## 5. Family adapter 与组件树

当前 dao family 的稳定槽位是：

1. blade
2. guard
3. front ferrule
4. handle
5. rear ferrule
6. pommel

实现参考 `forge/stage2_spec/dao_adapter.py`：`DaoDimensions` 保存尺寸表，
`assemble_dao_dimensions()` 计算装配位置和重复槽位，`validate_dao_component_tree()` 检查组件合同。

新 subtype 应明确：

- 每个稳定槽位使用什么 primitive 或自定义几何。
- 组件沿长轴的次序、接触面、嵌入深度与容差。
- 哪些组件可以独立爆炸、选择、替换或破坏。
- 重复细节的站位或槽位如何由尺寸表生成。
- subtype 特有约束，例如环首必须有真实通孔，剑必须有对称双刃。

若某件兵器没有某个传统部件，保留语义槽位但允许 subtype 明确禁用；不要通过改名把 `pommel` 临时
当成护手使用。若出现跨多把兵器都稳定存在的新结构，再升级 family 合同。

## 6. 刀身和剑身：闭合实体表面

### 6.1 通用建法

刃体用沿长轴排列的截面站点生成。每个站点至少给出：

- 长轴位置。
- 面视轮廓的上、下边界。
- 该处厚度。
- 刃口、研磨线、脊线或中脊的位置。
- 必要时的材质 tone 或局部属性。

相邻站点连接成有索引的纵向条带。同一连续表面共享顶点，刃口、研磨线和脊线需要硬折线时才复制
边界顶点。根部和尖端显式封口，所有三角形绕序朝外。最终必须满足：

- boundary edges = 0
- non-manifold edges = 0
- winding conflicts = 0
- signed volume > 0
- enclosed volume > 0
- `geometry.userData.solidVolume = "closed-surface-volume"`

尖端若收敛到同一点，生成器可以过滤由折叠产生的零面积三角形，但过滤后仍需重新做拓扑和体积检查。
不能用 `DoubleSide` 掩盖开边；双面材质只影响渲染，不会把平面变成实体。

### 6.2 刀与剑的差异

| 项目 | 单刃刀 dao | 双刃剑 jian |
|---|---|---|
| 面视轮廓 | 刃线和背线不对称，可有刀腹与刀尖上扬 | 两侧刃线通常围绕中轴近似对称 |
| 截面 | 单侧或不对称研磨，背部保留较厚脊区 | 双侧对称研磨，常有中脊 |
| 厚度分配 | 最大厚度偏向刀背 | 最大厚度通常位于中脊 |
| 尖端 | 刃线与背线以不同曲率汇合 | 双刃朝中心线汇合 |
| 视觉硬线 | 背线、研磨线、刃口 | 中脊、双研磨线、双刃口 |
| 验收重点 | 单刃身份、刀腹、背厚和尖部走势 | 双刃对称、中脊连续和尖端居中 |

因此，新剑可以复用“截面站点 -> 闭合网格 -> 拓扑验收”的方法，但不应直接复用单刃
`ground-blade` 的不对称截面数据。先定义 jian family/subtype 的对称截面合同，再让共享生成器支持它。

## 7. 柄、护手、箍与首部

由大到小完成结构：

1. 先只做 blade、guard、ferrule、handle、pommel 的主形，确认长度和轴线。
2. 给每个连接建立 socket、父子关系、接触类型、嵌入深度和 gap tolerance。
3. 再加入护手厚度、柄截面、箍的倒角与首部连接颈。
4. 最后才处理通孔、缠绕、嵌件和刻线。

环首或镂空首部必须使用带真实内边界的挤出几何，并同时封闭前后环面、内壁和外壁。用 torus 或深色
贴片模拟孔洞会在三分之四视图和选择时暴露问题。护手的局部平面方向必须由坐标合同决定，不能只在
主视图中看起来像一条正确的线。

## 8. 装饰必须是可归属的组件

纹饰、嵌件、刃纹和刻线可以独立生成，但“独立生成”不等于运行时永远保持一个 draw call。每个装饰
组件至少记录：

```json
{
  "ownerModule": "handle",
  "face": "front",
  "mergePolicy": "keep"
}
```

字段约定：

- `ownerModule`：所属的大模块；爆炸视图和整体移动必须跟随它。
- `face`：`front`、`back`、`both` 或明确的环向区域；双面兵器不要默认只生成正面。
- `mergePolicy=keep`：保留独立对象，适合需要替换、选择、换材质或破坏的嵌件。
- `mergePolicy=bake`：生产导出时可合批或烘焙到主体，适合浅刃纹、嵌座、缠绕暗缝和刻线。

装饰的默认规则：

- 不建立 collider。
- 点击装饰时能够解析回 `ownerModule`。
- 爆炸视图只移动稳定大模块，装饰随父模块移动，不新增宏观爆炸方向。
- 正反面分别生成并做回归截图；曲线可错相，不能简单共面或完全重叠。
- 浅层细节不能悬浮得像贴纸；使用嵌座、微小法向偏移或受控压入表达结构关系。
- 重复件由 adapter 槽位生成，避免正反面或不同尺寸版本各维护一组坐标。

若装饰只贡献表面颜色或微小高度，优先 `bake`。若它对玩法、换装或选中有意义，使用 `keep`。这使
建模阶段保有模块化编辑能力，同时允许优化阶段控制 draw call。

## 9. 程序化 PBR 材质

每种主要材质应分别提供 albedo、roughness、height/normal 和 AO 响应，不要把所有变化画进一张颜色图。
至少在三个频率层次上检查：

- macro：大面积色差、锈蚀/包浆区域和使用分区。
- meso：锻造、缠绕、划痕、凹槽和局部磨损。
- micro：细小粗糙度和法线变化。

局部 mask 应服从结构：凹槽积尘、凸缘磨亮、刃口和常握区域磨损更明显。材质强度由参考决定；干净
插画不应被强行做成重锈文物。至少输出中性光、参考匹配光和擦光近景，避免只在一种灯光下“看起来对”。

## 10. 八个制作 pass

pass 必须按顺序完成。每个视觉 pass 都要产生独立截图和审核记录，不能用最终 hero 图回填早期证据。

| Pass | 本轮只解决什么 | 通过标准 |
|---|---|---|
| `blockout` | 总长、最大宽度、主轮廓、相机和构图 | 无材质图也能辨认兵器类型；比例和画面占比正确 |
| `structural-pass` | 稳定模块、连接、孔洞、闭合刃体 | 无悬浮连接；socket/collider 完整；厚度轴和长轴视图成立 |
| `form-refinement` | 刀腹/剑脊、尖端、倒角、嵌座和中层纹样 | 正反面及三分之四视图结构一致，关键身份细节齐全 |
| `material-pass` | 材质分区和 PBR 通道 | 主要材质有独立通道、1024px 或更高审计及三种灯光证据 |
| `surface-pass` | 局部污垢、磨损、划痕和触感 | 细节有结构依据，近景可见但不破坏主轮廓 |
| `lighting-pass` | key/fill/rim、环境、曝光、色调映射和阴影 | 白底、接触阴影和材质可读性稳定，不靠过曝隐藏问题 |
| `interaction-pass` | 选择、爆炸、恢复、socket、collider 和破坏边界 | 装饰归属正确；所有大模块移动并可无漂移恢复 |
| `optimization-pass` | 合批、LOD、缓存、三角形和 draw call | 预算通过，优化前后视觉和交互无回归 |

在进入下一 pass 前，先修 spec 或共享生成器，再重新生成、截图和评分。`action=continue` 是当前 pass
确实完成的记录，不是为了跳过问题而写的状态。

## 11. 生成、预览与评审命令

以下命令从仓库根目录运行。替换 `<slug>`、`<ModelName>`、`<pass-id>` 和图片路径。

### 11.1 生成与严格校验

```bash
python3 reconstructions/<slug>/fill_spec.py

python3 forge/stage2_spec/validate_sculpt_spec.py --strict-quality \
  reconstructions/<slug>/object-sculpt-spec.json

python3 forge/stage3_build/generate_threejs_factory.py \
  reconstructions/<slug>/object-sculpt-spec.json \
  --out reconstructions/<slug>/create<ModelName>.ts \
  --pass-id <pass-id> --force
```

查看当前解锁 pass：

```bash
python3 forge/stage3_build/orchestrate_passes.py status \
  reconstructions/<slug>/object-sculpt-spec.json

python3 forge/stage3_build/orchestrate_passes.py check \
  reconstructions/<slug>/object-sculpt-spec.json \
  --pass-id <pass-id>
```

### 11.2 构建预览和截图

项目应固定可复现的 esbuild 入口；当前仓库可用的直接调用模式是：

```bash
/home/nyb/.npm/_npx/beb367dfa21eb3f5/node_modules/esbuild/bin/esbuild \
  reconstructions/<slug>/preview/main.js \
  --bundle --format=esm \
  --outfile=reconstructions/<slug>/preview/dist/preview.js
```

启动服务并执行项目自己的截图脚本：

```bash
python3 -m http.server 4173 --bind 127.0.0.1

PREVIEW_URL=http://127.0.0.1:4173/reconstructions/<slug>/preview/index.html \
  node reconstructions/<slug>/preview/capture.mjs
```

每个视觉 pass 至少保存主视图、正负三分之四、厚度轴和长轴视图。涉及双面装饰时追加正反面近景；
涉及装配时追加爆炸视图和恢复后的回归图。浏览器日志必须无 error。

### 11.3 确定性诊断与对照图

先跑便宜的确定性诊断，再做视觉评审：

```bash
python3 forge/stage4_review/diagnose_render.py \
  --reference reconstructions/<slug>/reference-face-clean.png \
  --render reconstructions/<slug>/captures/<pass-id>-hero.png \
  --spec reconstructions/<slug>/object-sculpt-spec.json \
  --pass-id <pass-id> --in-place --json

python3 forge/stage4_review/divine_eye.py \
  --reference reconstructions/<slug>/reference-face-clean.png \
  --render reconstructions/<slug>/captures/<pass-id>-hero.png --json

python3 forge/stage4_review/make_comparison_sheet.py \
  --reference reconstructions/<slug>/reference-face-clean.png \
  --render reconstructions/<slug>/captures/<pass-id>-hero.png \
  --out reconstructions/<slug>/captures/comparison-<pass-id>.png \
  --panel-width 1680 --panel-height 360 --json
```

panel 尺寸不是通用真理，应根据兵器的长宽比固定一次后全项目保持不变。汉代环首刀使用 1680x360，
避免方形 cover crop 截断细长轮廓。

将评审写回 spec 时，同时提交总分、分层分数和逐 feature 分数：

```bash
python3 forge/stage4_review/append_review.py \
  reconstructions/<slug>/object-sculpt-spec.json \
  --pass-id <pass-id> \
  --fidelity <0-to-1> \
  --action continue \
  --summary "本 pass 的结论" \
  --reference-screenshot reconstructions/<slug>/reference-face-clean.png \
  --render-screenshot reconstructions/<slug>/captures/<pass-id>-hero.png \
  --comparison-image reconstructions/<slug>/captures/comparison-<pass-id>.png \
  --ai-vision-score <0-to-1> \
  --layer-scores-json reconstructions/<slug>/captures/<pass-id>-layer-scores.json \
  --feature-reviews-json reconstructions/<slug>/captures/<pass-id>-feature-reviews.json \
  --matched "已匹配项 A;已匹配项 B" \
  --mismatches "仍存在但不阻断本 pass 的差异" \
  --camera-view face \
  --review-viewpoints-json '["thickness-axis", "long-axis"]' \
  --require-screenshot-files --in-place
```

layer scores 是一个按本项目审核层命名的 JSON object；feature reviews 是 JSON array，其中每项的 `id`
必须对应 spec 当前 pass 的 `featureReviewTargets`，并包含数值 `score` 和可见性。例如：

```json
[
  {
    "id": "blade-silhouette",
    "score": 0.88,
    "visible": true,
    "notes": "刀腹和刀尖走势与参考一致"
  }
]
```

不要照抄示例 ID，应从新兵器自己的 spec 读取目标。`blockout` 还必须提供
`--map-stripped-render`。若总分或关键 feature 未过阈值，action 应写
`refine-spec` 或 `refine-code`，修复后重新审核，不能先解锁下一 pass。

### 11.4 运行时组件覆盖

预览器导出 `parts.json` 后检查 spec 中承诺的组件是否都存在：

```bash
python3 forge/stage4_review/check_part_coverage.py \
  --spec reconstructions/<slug>/object-sculpt-spec.json \
  --manifest reconstructions/<slug>/captures/parts.json \
  --json reconstructions/<slug>/captures/part-coverage.json
```

最后运行与改动范围相称的 forge 测试。改动共享刀身生成器或 family adapter 时，不能只跑当前重建的
截图脚本，还要覆盖适配器、生成器、watertightness、pipeline 和诊断测试。

## 12. 每轮评审顺序

固定采用以下顺序，可以减少用材质或相机掩盖结构错误：

1. 先看无贴图主视图，检查总轮廓和比例。
2. 看正负三分之四，检查厚度、孔洞、护手方向和装饰离面。
3. 看厚度轴与长轴，检查模型是否退化为平面卡片。
4. 跑拓扑、体积、part coverage 和浏览器 console 审计。
5. 跑 silhouette、scale、aspect ratio 等确定性诊断。
6. 检查完整参考/渲染对照图，再看关键部位近景。
7. 把 matched、mismatches、下一步修复和分数写入 reviewHistory。
8. 只有所有硬门禁和关键 feature 达标后才记录 `action=continue`。

视觉分数不能取代结构门禁。一张主视图即使高度相似，也不能证明背面有纹样、刃体闭合、环首有真孔、
装饰跟随父模块，或 collider 合理。

## 13. 优化与交付

优化按“先记录，后合并”的原则进行：

- 先记录三角形、draw call、geometry/material/texture 数量和测试后端。
- 对 `mergePolicy=bake` 的浅细节做合批或烘焙；保留 `keep` 组件的交互能力。
- 重复但需要独立选择的组件，不要仅为少量 draw call 强行改成无法单独命名的实例。
- 阴影、材质和几何缓存必须在换灯、爆炸或恢复时正确失效。
- 优化后重跑视觉、选择、爆炸恢复、part coverage 和材质/灯光审计。

软件渲染器上的 FPS 只能标记为 report-only，不能冒充硬件 WebGL 基准。交付文档要同时列出预算、实测
后端和未验证的目标。

## 14. 常见失败与修复方向

| 症状 | 根因 | 修复 |
|---|---|---|
| 刀身只有单面或从背面消失 | 使用平面/开边条带 | 生成闭合截面条带并封根、封尖，检查正体积 |
| 开了 `DoubleSide` 就以为是实体 | 混淆渲染与拓扑 | 以 boundary、non-manifold 和 volume 审计为准 |
| 正面有花纹，背面没有 | 生成逻辑隐含单面 | 显式写 `face`，分别生成并截图验收 front/back |
| 纹样漂浮或爆炸时留在原地 | 缺少所有权合同 | 设置 `ownerModule` 和父级变换，点击解析回所属模块 |
| 每条刻线都是一个永久 draw call | 没有合并策略 | 浅细节标记 `bake`，只给玩法相关件保留 `keep` |
| 环首像实心圆或假洞 | torus/深色贴片模拟 | 使用带内外轮廓、前后盖和内壁的闭合挤出几何 |
| 护手主视图正确，斜视方向错误 | 局部轴向合同不明确 | 固定 family 坐标，用厚度轴截图验收 |
| 材质很“丰富”但不像参考 | 程序噪声没有结构依据 | 从参考调色板和局部 mask 出发，分别审计 PBR 通道 |
| 分数提高但模型更不可信 | 相机/裁切掩盖结构问题 | 固定评分裁切，并保留多角度和拓扑硬门禁 |
| 修改生成 TS 后下次丢失 | 编辑了派生文件 | 把修改移回 `fill_spec.py` 或共享生成器 |

## 15. 可复用完成清单

### 规格与结构

- [ ] 参考图、评分裁切和推断项已固定。
- [ ] 坐标轴、绝对尺度和完整尺寸表已记录。
- [ ] family/subtype 与稳定组件槽位已定义。
- [ ] 所有连接都有 socket、接触、嵌入和容差。
- [ ] 刃体 boundary/non-manifold/winding conflict 均为 0，体积为正。
- [ ] 孔洞和开口是真实几何，不是颜色伪装。

### 双面与模块化

- [ ] 正反面装饰都显式生成并分别截图。
- [ ] 每个装饰都有 `ownerModule`、`face`、`mergePolicy`。
- [ ] 可替换/可选择件使用 `keep`，浅表面细节使用 `bake`。
- [ ] 装饰无 collider，选择可解析回所属大模块。
- [ ] 爆炸视图只移动稳定模块，恢复后无漂移。

### 视觉与运行时

- [ ] 八个 pass 按顺序完成，每个视觉 pass 有独立证据和审核。
- [ ] 主视、双侧三分之四、厚度轴、长轴和关键近景均通过。
- [ ] 无贴图、参考光、中性光和擦光证据齐全。
- [ ] strict-quality、确定性诊断、Divine Eye 和 part coverage 通过。
- [ ] 浏览器无 console error，交互、socket 和 collider 审计通过。
- [ ] triangle/draw-call/texture 预算通过，FPS 后端被准确标注。
- [ ] 共享改动有相应测试，HANDOFF 记录当前结果和已知限制。

## 16. 汉代环首刀实例基线

当前实例位于 `reconstructions/han-huan-shou-dao/`，权威输入是 `fill_spec.py`。它验证了以下可复用
做法：闭合 `ground-blade` 刀身、六个稳定装配模块、真实环孔、双面装饰、装饰所有权/合并策略、
七个 collider 和八阶段审核。

截至 2026-08-23 的最终结果：strict-quality PASS，silhouette IoU 0.8898，Divine Eye 0.8592
（目标 0.85），刀身 boundary/non-manifold 为 0/0，signed/enclosed volume 为
`+0.0053927553 / 0.0053927553`，part coverage 为 0 errors / 0 warnings，运行时为 86,544
triangles、46 draw calls。当前相关回归为 67 passed；HANDOFF 中的 680 passed、25 skipped 是此前完整
forge suite 的历史审计结果。SwiftShader 2.66 FPS 仅为软件后端报告，硬件 WebGL 仍以 30 FPS 为目标。

该实例的装饰拆分为 38 个组件：双面刃纹、双面柄部嵌件与嵌座、两条 360 度缠绕暗缝和双面环首刻线。
金色嵌件使用 `keep`；嵌座、刃纹、暗缝和刻线使用 `bake`。这套拆分可以直接作为下一把兵器决定
“哪些保留独立、哪些生产时合并”的参考，但具体数量、曲线和材质必须重新从新参考图推导。
