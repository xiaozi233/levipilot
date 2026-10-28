# findings — 逆向账本

这里放**每轮**的原始记录与生成物，按版本和 case 分开：

```
findings/<version>/cases/<case>/
  case.json        # goal / environment / current_round / status
  old-account.md   # begin 生成的旧账 grep（docs/旧 case/git log）
  journal.jsonl    # append-only，工具写（probe）或人工补录（claim）
  facts.md         # 生成物，禁手改
  resume.md        # 生成物，压缩/重连后先读它
  report.md        # close 生成（A 静态结论 / B 未定 / C 原始证据）
  probes/          # <id>.cmd / <id>.py + <id>.out，原样保存
```

## 规则

- 一条假设被证实、证伪、或判为无法判定，算一轮；轮末 `end`。
- `probe` 必须带 `--scope`；负面结论只对 scope 内的搜索成立。
- `claim` 的 `confirmed` 必须带 `evidence_tier: code|constant` 和非空 `static_closure`；
  只有动态证据写 `live-only`，不能进结论区。
- 结论正文发布到 `docs/*.md` 后，在账本里留 `published: <path>#<anchor>` 指针；
  账本不复制正文。
- 旧版本目录冻结，不参与新版本解析；旧 `known-facts` 不整包复制进公开仓库。

## 常用命令

```bash
py -3.11 driver/landing.py begin   --case <slug> --goal "..."
py -3.11 driver/landing.py probe   --case <slug> --scope "..." -- <command...>
py -3.11 driver/landing.py claim   --case <slug> --file claims.yaml
py -3.11 driver/landing.py end     --case <slug>
py -3.11 driver/landing.py close   --case <slug>
py -3.11 driver/landing.py abandon --case <slug> --reason "..."
```

`--findings-root` 可指向别处（测试/临时账本）；默认就是本目录。
