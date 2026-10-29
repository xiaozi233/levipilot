# resume: slime-bounce-coverage

- version: `1.26.51.01`
- goal: 确认 1.26.51.01 黏液块反弹判据，解释上层覆盖方块何时仍弹（0.375 雪层时弹时不弹）
- environment: -
- current round: 2
- last record: r01-006
- journal: `skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/journal.jsonl`

## Confirmed (static closure required)

- r01-001: 1.26.51.01 的落地反弹处理器在 0x148B5DB00：从 StateVector 读 vy， `(flag & 2)==0` 且 `vy<0 && |vy|>=0.080000117` 时，用位置解析器 0x141E144A0 返回的方块，经其 vtable +0x3E8 读 restitution， 再写进 ApplyRestitutionComponent(0x7DF54114) 的 y 分量 （`entry[1] = max(coef * -vy, cur)`）。 [code; handler 0x148B5DB00 反汇编：movss xmm0,[r8+0x1C] 读 vy；test byte[rax],2； ucomiss 与 .rdata 0x14EA350B0 的 0.080000117f 比较；call 0x141E144A0； mov rcx,[rax+0x68] / mov rax,[rcx] / mov rax,[rax+0x3E8] 虚调用； mov edx,0x7DF54114 取组件后 `movss [rax+4], max`。 （apply 侧独立系统 0x148B69440 用同一组件做整向量写回，另案。）]
- r01-002: 解析器判据 = 探针点 `AABB.minY - 0.2`：把查询盒 minY/maxY 都改写为 minY-0.2 后，遍历 24 字节世界空间形状，取中心 Y 不高于探针的最高者 （并列取水平中心最近），输出 floor(shape.min) 与该形状的方块指针。 [code; 0x141E144A0：movss [r8+4]; addss [-0.2]（.rdata 0x14E5A6A90 侧常量）; movss [r8+4]/[r8+0x10] 写回；逐形状计算 centerY=(maxY-minY)/2+minY、 v18=probe-centerY，`jb` 跳过 v18<0，min 选最小 v18；并列时用中心平方距离 比较；末尾 `comiss min<max` 后三次 floorf(0x14E5A6A90) 与 `[[rcx+0x18]+24*i]` 取方块指针。]
- r01-005: 覆盖块挡住反弹的阈值由「覆盖块碰撞盒中心 vs 探针」决定：顶面在黏液上表面、 高度 h 的方块中心在 top-h/2，被选中需 h/2 <= 0.2，即 h >= 0.4（6/16=0.375 差 0.0125 落在临界外侧，7/16=0.4375 起稳定挡住）。 [code; 解析器选形状的 `centerY <= minY-0.2`（0x141E144A0）+ 实测 shape 记录 （雪层盒 y=[-59,-58.625] ⇒ center=-58.8125，黏液盒 y=[-60,-59] ⇒ center=-59.5） 代入即得 h>=0.4。]

## Open / pending

- r01-003: 实测雪层覆盖矩阵（3x3 黏液平台 + 3x3 石基座，中心正落）：1 层(0.125)、2 层(0.25) 在 0.25–6.0 格任意落差都反弹；4 层(0.5)及以上从不反弹；3 层(0.375)随落差在弹/不弹 之间翻转（0.25/0.375/0.5/0.625 弹，0.75 不弹，0.875 弹，1.0/1.125 不弹，1.25–1.375 弹， 1.5 不弹，1.625–1.875 弹，2.0 不弹，2.125–2.5 弹，2.625 不弹，2.75–6.0 弹）， 同一落差重复 2–3 次结果一致；水平落点偏移(0–0.29)与覆盖宽度(1/3/5 格)都不改变结果。 (live-only)
- r01-004: 3 层雪的翻转不是数值抖动，而是解析器候选形状表内容不同：AABB.minY 在每次落地 都恰好等于雪顶（探针 = 雪顶-0.2 固定），候选表有时只含雪层（雪层中心高于探针被跳过 →无方块→不弹），有时含 {黏液, 雪}（黏液中心 -59.5（相对雪顶 -0.5）→被选中→弹）。 (live-only)

## Next actions

- fill in the next hypothesis

## Pointers

- facts: `skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/facts.md`
- old account: `skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/old-account.md`
- probes: `skills/bedrock-re/findings/1.26.51.01/cases/slime-bounce-coverage/probes`
