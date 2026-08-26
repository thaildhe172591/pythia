# pythia — Hướng dẫn chi tiết

[English](GUIDE.md) · **Tiếng Việt** · Bản tóm tắt: [README.vi.md](README.vi.md)

Tài liệu này đi từ cài đặt đến quy trình làm việc hàng ngày, mô hình bảo mật
và xử lý sự cố. Mọi lệnh đều paste được nguyên văn.

## Mục lục

1. [Cài đặt](#1-cài-đặt)
2. [Kết nối theo project](#2-kết-nối-theo-project)
3. [User agent least-privilege](#3-user-agent-least-privilege)
4. [Đọc và hiểu schema](#4-đọc-và-hiểu-schema)
5. [Đường ghi: apply](#5-đường-ghi-apply)
6. [Journal — snapshot và khôi phục](#6-journal--snapshot-và-khôi-phục)
7. [Policy, settings, conventions](#7-policy-settings-conventions)
8. [Message tiếng Việt: unistr](#8-message-tiếng-việt-unistr)
9. [Bộ skills cho agent](#9-bộ-skills-cho-agent)
10. [Xử lý sự cố](#10-xử-lý-sự-cố)

---

## 1. Cài đặt

### Một lệnh trọn bộ

```bash
npx pythia-plsql
```

Tìm Python → `pip install pythia-plsql` (CLI + queries + skills đóng gói) →
`pythia install` (skills + scaffold config).

### Từng bước, cùng kết quả

```bash
pip install pythia-plsql   # CLI, thin driver — không cần Oracle Instant Client
pythia install -g          # skills GLOBAL: một lần mỗi máy, mọi project dùng chung
cd du-an && pythia install # mỗi project: chỉ scaffold .pythia/connections.json
pythia check               # điền connections.json rồi kiểm tra
```

**Mô hình global-first**: skills nằm ở `~/.claude/skills` (một bản duy nhất).
`pythia install` trong project phát hiện pack global sẽ **tự bỏ qua bước
skills** — vì bản thứ hai làm mỗi skill hiện đôi trong menu của agent. Muốn
skills theo repo (commit cho team): xoá pack global rồi chạy `pythia install`
trong project.

- Có Node.js: bước skills chạy qua `npx skills add` (77 agent; ở terminal
  tương tác bạn được chọn agent). `--source <git-url>` để cài từ mirror nội bộ.
- Không có Node: tự copy bộ skills đóng gói sẵn — gói pip là trọn bộ kit.

### Cập nhật phiên bản

```bash
pip install --upgrade pythia-plsql   # CLI mới (mỗi máy một lần)
pythia install -g                    # làm mới pack skills global
```

Config không bao giờ bị đụng khi update. Skills cài qua npx: `npx skills update`.

### Chạy từ clone (contributor)

```bash
git clone https://github.com/thaildhe172591/pythia && cd pythia
pip install oracledb
python scripts/pythia.py check
```

Mọi lệnh follow-up in ra luôn khớp cách bạn gọi (`pythia` / `python -m pythia`
/ `python scripts/pythia.py`).

## 2. Kết nối theo project

`.pythia/connections.json` — tạo bởi `pythia install`, **gitignore, chứa
credentials, không đưa vào chat/screenshot**:

```json
{
  "default": "dev",
  "dev":     { "host": "db-dev",  "port": 1521, "service_name": "orclpdb",
               "user": "app_agent[app_owner]", "password": "...",
               "schema": "APP_OWNER" },
  "staging": { "host": "db-stg",  "port": 1521, "service_name": "orclpdb",
               "user": "app_agent[app_owner]", "password": "...",
               "schema": "APP_OWNER" }
}
```

Thứ tự chọn kết nối (không bao giờ đoán):

1. `--conn TEN` trên lệnh
2. Biến môi trường `PYTHIA_CONNECTION` (tên một entry)
3. `PYTHIA_USER` / `PYTHIA_PASSWORD` / `PYTHIA_DSN` (+`PYTHIA_SCHEMA`) — bỏ
   qua file hoàn toàn, tiện thử nhanh một credential
4. File tìm **ngược từ thư mục hiện tại lên** (`PYTHIA_CONFIG` trỏ thẳng file
   khác): một entry → dùng luôn; nhiều entry → segment đường dẫn ngay dưới
   project root chọn (`root/DEV/...` → entry `DEV`), rồi tới `"default"`;
   vẫn mơ hồ → báo lỗi liệt kê lựa chọn

**Không có fallback global** — CLI cài chung không bao giờ lẫn DB giữa các
project; đứng ngoài mọi project thì lỗi rõ ràng.

## 3. User agent least-privilege

**Tài khoản DB là lớp bảo vệ thật** — policy chỉ là hàng rào ứng dụng. Mô
hình: **proxy authentication** — agent có credential riêng, đăng nhập *xuyên
qua* schema owner: không biết password owner, thu hồi một lệnh, audit trail
ghi đúng ai kết nối, bán kính phá hoại gói trong một schema dev.

```bash
pythia agent-user --save   # MỘT lần chạy duy nhất
```

In khối SQL ba câu + lưu credential khớp vào `connections.json` (entry
`<conn>_agent`, thành default; entry owner giữ nguyên — quay lại bằng
`--conn <ten-cu>`). Lệnh này **hỏi database trước khi viết SQL**:

- Agent user đã tồn tại → dạng `ALTER USER ... IDENTIFIED BY ... ACCOUNT
  UNLOCK` (tránh ORA-01920, chữa luôn ORA-28000 nếu từng bị khoá)
- Soi quyền owner → **tuyên bố trước** `check` sẽ sạch hay còn cảnh báo
- `--json` trả payload máy đọc: `sql`, `password`, `saved_connection`,
  `check_will_warn`, `owner_dangerous_privs`, `next`

**Luật một-lần-chạy**: password sinh mới mỗi lần chạy — SQL đưa DBA và config
đã lưu phải ra từ **cùng một lần** `--save`. Không preview trước rồi save sau.

Đưa SQL cho DBA chạy, rồi:

```bash
pythia check   # đích: bảng object hiện ra, KHÔNG còn dòng cảnh báo vàng
pythia sql "select sys_context('userenv','proxy_user') proxy, user connected_as from dual"
# mong đợi:  <AGENT> | <OWNER>
```

### Owner đang cầm DBA? Dọn quyền

Phiên proxy thừa hưởng quyền owner — owner có DBA thì agent cũng thành DBA cả
instance. `check` sẽ nói thẳng. Trình tự dọn (DBA chạy, **cấp trước revoke
sau**, làm trên dev trước):

```sql
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE TRIGGER, CREATE TYPE, CREATE SYNONYM
  TO app_owner;
ALTER USER app_owner QUOTA UNLIMITED ON users;
-- + grant đích danh những gì code đang dùng thật (vd EXECUTE ON sys.dbms_crypto)
REVOKE DBA FROM app_owner;
REVOKE RESOURCE FROM app_owner;
```

Trước đó kiểm tra code có đụng schema khác / package SYS nào:

```bash
pythia sql "select distinct referenced_owner from all_dependencies where owner='APP_OWNER' and referenced_owner not in ('SYS','PUBLIC','APP_OWNER')"
pythia sql "select referenced_name, count(*) n from all_dependencies where owner='APP_OWNER' and referenced_owner='SYS' and referenced_name like 'DBMS_%' group by referenced_name"
```

Sau revoke: `pythia invalid` + `pythia errors` — gãy gì thì grant đúng thứ
thiếu, không trả lại DBA. Tự thao tác thay vì dùng lệnh:
[`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql).

## 4. Đọc và hiểu schema

Nguyên tắc: **hỏi database, không đọc dump** — dump drift (một audit thật:
thiếu toàn bộ type/package, 89% index).

```bash
pythia check                  # kết nối + đếm object + cảnh báo quyền
pythia ls "PKG_%"             # tìm object theo LIKE
pythia src PKG_ORDER --body   # source kèm số dòng ĐÚNG của compiler
pythia args P_TAO_DON         # signature: tên, thứ tự, kiểu, default
pythia cols T_ORDER           # cột + kiểu — neo %TYPE/%ROWTYPE vào đây
pythia ddl TABLE T_ORDER      # DDL qua DBMS_METADATA
pythia grep "ma_doi_tac"      # tìm trong toàn bộ source
pythia sql "select ..."       # truy vấn tự do — CHỈ SELECT/WITH
```

Hiểu quan hệ và sức khoẻ:

```bash
pythia impact T_ORDER          # cái gì phụ thuộc nó — BẮT BUỘC trước mọi thay đổi
pythia deps PKG_ORDER          # nó phụ thuộc cái gì (--with-sys để xem cả SYS)
pythia invalid                 # mọi object INVALID
pythia errors PKG_ORDER        # lỗi biên dịch, dòng:cột
pythia plscope T_ORDER         # vị trí dùng identifier chính xác (PL/Scope)
pythia similar PKG_ORDER_LIST  # chương trình đặt tên giống — mỏ convention
```

Output: `--json` mọi lệnh; `--limit` / `--max-lines` / `--offset` (0 = bỏ
giới hạn); **mọi cắt bớt đều có marker** `-- truncated ...` — không có marker
nghĩa là bạn đã thấy tất cả. Màu chỉ cho người (`NO_COLOR`/`FORCE_COLOR`);
pipe và `--json` luôn plain.

## 5. Đường ghi: apply

**Chỉ một cửa ghi**: `pythia apply` — không `sqlplus`, không SQLcl MCP
`run-sql`, không driver script. Vì DDL trong Oracle **tự commit**: snapshot
trước khi ghi là cách hoàn tác thật duy nhất.

```bash
pythia apply PKG_ORDER_BODY.sql            # preview: diff + impact + cảnh báo + token
pythia apply PKG_ORDER_BODY.sql --confirm a1b2c3   # ghi đúng nội dung đã preview
```

Sáu bước, không tắt được: **snapshot → impact → preview → apply → verify →
report**.

- File chứa **đúng một** statement; block PL/SQL ẩn danh bị từ chối thẳng;
  statement không phân loại được → từ chối, không đoán
- Token 6-hex trói lần ghi vào nội dung đã preview — file hay DB đổi là token
  hết hiệu lực, phải preview lại
- Đổi kiểu object (function → procedure cùng tên...) bị chặn ngay preview
- `--yes` bỏ bước dừng — nhưng nó là **cờ của developer**: không có
  terminal (agent điều khiển CLI) thì bị từ chối, `policy set` nới lỏng
  cũng vậy. Người gõ tay không bị ảnh hưởng; pipeline thật đặt
  `PYTHIA_CI=1`. Journal ghi lại mỗi lần ghi được xác nhận bằng gì
  (`token` / `yes`) và có TTY hay không
- Preview cảnh báo khi tên object lệch naming conventions của project

**Exit code là kết luận, máy đọc được:**

| Code | Nghĩa | Agent phải làm |
|---|---|---|
| `0` | sạch | báo thành công |
| `1` | bị từ chối | relay đúng lý do, không lách |
| `3` | **đã ghi nhưng hỏng** | KHÔNG BAO GIỜ báo thành công — show lỗi + lệnh restore đã in |

## 6. Journal — snapshot và khôi phục

```bash
pythia journal list            # mọi entry, [applied] / [preview]
pythia journal show <id>       # metadata
pythia journal diff <id>       # before/after
pythia journal export <id> --what before|after|restore
pythia journal restore <id>    # KHÔI PHỤC — đi qua đúng 6 bước apply, có preview + phê duyệt
pythia journal prune           # dọn entry preview-only; entry applied LUÔN giữ
```

Restore là một lần ghi như mọi lần ghi — không có đường tắt im lặng. Object
trước đó chưa tồn tại thì restore nghĩa là `DROP` — report nói thẳng.

## 7. Policy, settings, conventions

### `.pythia/policy.json` — chính sách ghi theo nhóm

```bash
pythia policy                        # xem hiệu lực + bảng rollback trung thực
pythia policy set structural confirm # đổi một nhóm
```

| Nhóm | Mặc định | Rollback có thật không? |
|---|---|---|
| `plsql_source` | `confirm` | **Có — hoàn toàn** (ALL_SOURCE) |
| `data_dml` | `deny` | **Không.** Sau commit chỉ còn Flashback Query |
| `structural` | `deny` | **Gần như không.** `DROP COLUMN` là vĩnh viễn |
| `grants` | `deny` | Có, nhưng làm tay |
| `session` | `allow` | Không cần |

Nhóm không snapshot được mặc định `deny` — và lời từ chối nói đúng lý do đó.

### `.pythia/settings.json`

```json
{ "plscope_on_apply": false }
```

Mặc định **bật**: mọi object apply qua pythia được compile kèm PL/Scope →
`pythia plscope` luôn có index đầy đủ trên schema dev.

### Conventions — house style là config

- `.pythia/conventions.json`: pattern đặt tên máy kiểm tra được — preview
  apply cảnh báo khi drift (xem `examples/conventions.example.json`)
- `.pythia/conventions.md`: luật văn xuôi — skills bắt agent đọc trước tiên
- `pythia conventions` hiển thị cả hai

## 8. Message tiếng Việt: unistr

Literal non-ASCII paste thô sẽ vỡ theo charset client/DB. Luật (skill
`pythia-write` cưỡng chế): **mọi literal tiếng Việt đi qua `pythia unistr`**:

```bash
pythia unistr "Nhóm không được để trống"
# → unistr('Nh\00F3m kh\00F4ng \0111\01B0\1EE3c \0111\1EC3 tr\1ED1ng')

pythia unistr --loi "Bạn chưa nhập mã"
# → 'loi:'||unistr('B\1EA1n ch\01B0a nh\1EADp m\00E3')||':loi'

echo "text" | pythia unistr    # stdin cũng được; không cần DB
```

Nháy đơn → `''` chuẩn SQL (không phải `\'` — ORA-01756), backslash → `\\`,
ngoài BMP → `\U+8hex`.

## 9. Bộ skills cho agent

Bảy skill là **cổng chặn** kiểu superpowers, không phải gợi ý:

| Skill | Kích hoạt khi | Việc chính |
|---|---|---|
| `pythia-setup` | cấu hình máy/project, lỗi kết nối, cảnh báo quyền | connections, agent-user, SQLcl MCP |
| `pythia-explore` | cần hiểu bất cứ gì trong schema | hỏi DB, không đọc dump |
| `pythia-impact` | **trước** mọi đề xuất thay đổi | ≥10 dependent hoặc cross-schema → hỏi dev trước |
| `pythia-write` | viết/sửa PL/SQL sau khi biết impact | copy convention, neo kiểu vào DB, unistr |
| `pythia-apply` | thay đổi sẵn sàng chạm DB | dev thấy preview, duyệt trong chat; exit 3 ≠ thành công |
| `pythia-review` | review code PL/SQL | tín hiệu DB + 7 antipattern, findings theo dòng |
| `pythia-skill-author` | "làm skill cho cách bọn tôi làm X" | phỏng vấn + mine schema → skill mới đúng format pack |

Đọc SQLcl MCP (tuỳ chọn, chỉ đọc): `sql -mcp`, giữ `-R 4`; audit trong
`DBTOOLS$MCP_LOG`. **Ghi thì không bao giờ qua MCP.**

## 10. Xử lý sự cố

| Triệu chứng | Nguyên nhân → xử lý |
|---|---|
| `ORA-01017` khi check | sai user/password — nếu vừa `agent-user`, xem lại luật một-lần-chạy (§3) |
| `ORA-28000` account locked | thử sai nhiều lần → `agent-user` sinh sẵn `ALTER ... ACCOUNT UNLOCK` cho DBA, hoặc DBA `ALTER USER x ACCOUNT UNLOCK` |
| `ORA-01920` khi tạo user | user đã tồn tại — bản ≥0.2.2 tự chuyển dạng ALTER; đảm bảo chạy `agent-user` khi kết nối được DB |
| `ORA-01749` grant to yourself | đang chạy script quyền bằng chính schema bị sửa — chạy bằng SYSTEM/DBA khác |
| `ORA-28150/28154` | thiếu `ALTER USER owner GRANT CONNECT THROUGH agent` |
| Skill hiện đôi trong menu `/` | pack tồn tại ở 2 nơi (global + project, hoặc 2 thư mục) → giữ global, xoá bản project; bản ≥0.2.4 tự tránh |
| Skill không hiện | pack chỉ nằm ở `.agents/skills` project mà Claude Code bản đó không đọc → `pythia install -g` |
| `check` cảnh báo vàng | đọc §3 — proxy chưa dùng, hoặc owner thừa quyền |
| Token bị từ chối khi `--confirm` | file/DB đổi sau preview — preview lại là đúng thiết kế |
| Exit 3 sau apply | đã ghi nhưng có lỗi compile/invalid mới — xem lỗi, chạy lệnh `journal restore` đã in |
| Output bị cắt | có marker `-- truncated` — tăng `--limit`/`--max-lines` hoặc `--offset` đọc tiếp |
| `--yes ... no terminal is attached` | agent định tự phê duyệt — đúng thiết kế: preview, relay nguyên văn, dừng; dev đồng ý rồi mới `--confirm <token>` |
| `Loosening the write policy ... no terminal` | tương tự: đưa dev đúng lệnh `policy set` đã in để họ tự chạy |

---

Bảo mật chi tiết, bảng so dump-vs-DB, star history: [README.vi.md](README.vi.md).
Đóng góp — TDD, bind-contract lint, skill lint: [CONTRIBUTING.md](CONTRIBUTING.md).
