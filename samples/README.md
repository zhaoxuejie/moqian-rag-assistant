# samples/ — 演示语料

30 份文档，虚构企业「宏远电梯」的售后资料：出厂与交付记录、日常维保表、四类梯型的保养手册、
合格证、历史工单台账、故障码处置摘编、FAQ。

**全部虚构**：企业名、梯型、工号、热线、证书编号都是编的，不含任何厂商手册或真实客户数据。

## demo-corpus/

```text
manual/宏远电梯售后文档/        24 份基础资料（01_出厂与交付 … 06_维修保养手册）
manual/宏远电梯售后文档扩充/    5 份扩充（7-x 批次补遗 / 版本变更、8-1 工单台账、9-1 故障码摘编）
faq/宏远/                      1 份 FAQ 表格（一行问答 = 一条证据单元）
```

## 导入（本机）

```powershell
# 复制进暂存区，让 app 建档 + 排队解析（worker 切片 → 向量 → 落库）
New-Item -ItemType Directory -Force data\inbox | Out-Null
Copy-Item samples\demo-corpus\* data\inbox\ -Recurse -Force
docker compose exec app python -m app.import_docs --root data/inbox --parse-now --no-enqueue
```

**导入一次即可**：数据库按 sha256 幂等（不会重复建文档），但重复导入会让
`data/original-docs` 多出带新时间戳的副本。

## 换行业

整目录替换即可——代码里不写行业词，欢迎语、示例问题、安全词表、品牌都由 `app` 层配置注入。

## 不要往这里放

真实客户资料、厂商手册、带真实联系方式的工单、任何有版权的东西。
演示只用虚构数据，页面也会声明这一点。
