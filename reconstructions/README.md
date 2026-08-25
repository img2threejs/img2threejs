# Reconstructions

本目录放**单件重建工程**，不是公开展览前端。

- 工位（开发预览 / 门禁截图）：`reconstructions/<slug>/preview`
- 展厅（对外 gallery）：companion 仓库 `img2threejs-showcase`
- 单件怎么做：[`docs/WEAPON_RECONSTRUCTION_PLAYBOOK.md`](../docs/WEAPON_RECONSTRUCTION_PLAYBOOK.md)
- 系列怎么发展：[`docs/CHINESE_SWORDS_VOL1_PLAN.md`](../docs/CHINESE_SWORDS_VOL1_PLAN.md)

## 中国刀剑 · 第一辑（Vol.1）

计划中的小系列：先门面、再复用、后成辑上网。

| 顺序 | slug | 状态 | 说明 |
|---|---|---|---|
| 1 | [`han-huan-shou-dao`](./han-huan-shou-dao/) | 重建完成 / 待上架 showcase | 汉代环首刀；镇馆之宝 |
| 2 | `tang-heng-dao` | 计划中 | 唐横刀简型；验证 `dao_adapter` |
| 3 | `ming-yaodao` 或 `xiu-chun-dao-lite` | 计划中 | 近古刀装；拉出系列跨度 |

当前执行顺序见计划 Phase 0 → A → B → C：

1. 同步上游并整理分支  
2. 环首刀接入 showcase  
3. 第二把压生产线  
4. 三把成辑并公开展示  

## 目录约定

```text
reconstructions/<slug>/
├── HANDOFF.md
├── fill_spec.py
├── object-sculpt-spec.json
├── create<ModelName>.ts
├── preview/
└── captures/
```

不要把生成后的 TypeScript 当长期手改源。权威输入是 `fill_spec.py` 与共享 adapter/生成器。
