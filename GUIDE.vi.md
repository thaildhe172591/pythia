# pythia — Hướng dẫn chi tiết

[English](GUIDE.md) · **Tiếng Việt** · Bản tóm tắt: [README.vi.md](README.vi.md)

Tài liệu này đi từ cài đặt đến quy trình làm việc hàng ngày, mô hình bảo mật
và xử lý sự cố. Mọi lệnh đều paste được nguyên văn.

## Mô hình vận hành — đọc trước tiên

Học → Hỏi → Làm. Agent học schema và phong cách dự án trước khi đề xuất
(`deps`, `impact`, `src`, `similar`, `conventions`); dừng lại hỏi đúng những
chỗ phán đoán của dev là mảnh còn thiếu (cổng preview, blast radius lớn,
tài liệu và schema cãi nhau, mọi lời từ chối, mọi exit 3); rồi mới làm, qua
một cửa ghi duy nhất có snapshot, token và verify. `pythia guide` in cả mô
hình từ chính công cụ — nền tảng không có skill support thì trang đó là bản
hợp đồng. Mỗi skill khai báo nhịp nó phục vụ ở dòng `**Phase:**`.

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
11. [Tuỳ chọn: cấu hình quyền cho Claude Code](#11-tuỳ-chọn-cấu-hình-quyền-cho-claude-code)

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
python -m pythia install -g   # skills GLOBAL: một lần mỗi máy, mọi project
cd du-an && python -m pythia install   # mỗi project: chỉ scaffold .pythia/
python -m pythia check        # điền connections.json rồi kiểm tra
```

**Báo `pythia: command not found`?** pip đặt file thực thi vào thư mục scripts
thường không nằm trong PATH — chính pip cũng cảnh báo, và đó là lý do các lệnh
trên dùng `python -m pythia`, dạng luôn chạy được. Muốn gõ gọn `pythia`, để
tool tự đặt mình vào PATH:

```bash
python -m pythia install --add-to-path   # Windows: chỉ sửa user PATH
```

Rồi mở terminal **mới** — cửa sổ đang chạy giữ nguyên môi trường lúc nó khởi
động, các tab con cũng vậy.

> **Nếu trước đây bạn từng chạy lệnh
> `SetEnvironmentVariable('PATH', "$env:PATH;...", 'User')`** (hướng dẫn này
> từng gợi ý, và kiểu lệnh đó đầy trên mạng): nó đã sao chép system PATH vào
> user PATH, vì `$env:PATH` là hai cái gộp lại. Kiểm tra bằng
> `[Environment]::GetEnvironmentVariable('PATH','User') -split ';'` — nếu thấy
> `C:\Windows\system32` và đồng bọn trong đó thì chúng không thuộc về đây. Sao
> lưu giá trị ra file trước, rồi xoá những mục cũng xuất hiện trong
> `[Environment]::GetEnvironmentVariable('PATH','Machine')`.

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

### Bố cục 3 vai trò — chốt một lần, dùng mọi nơi

**Là khuyến nghị, không phải yêu cầu.** Bố cục này là lời khuyên của tác giả
— một tầng gia cố thêm ở tầng phân quyền role, đưa ra để tham khảo. Mô hình
bảo mật là của DBA nơi bạn; pythia làm việc với bất kỳ tài khoản nào bạn trỏ
tới, và không nhận trách nhiệm về thiết kế phân quyền của một site. Dùng,
chỉnh, hay bỏ qua — tùy bạn.

Một database, ba tài khoản, ba việc không được trộn lẫn:

| Tài khoản | Quyền | Việc | Chứa object? |
|---|---|---|---|
| `APP_ADMIN` | DBA (hoặc role quản trị của nơi bạn) | chỉ quản trị — tạo user, grant, Data Pump | không bao giờ |
| `APP_OWNER` | 8 quyền CREATE + quota. **Không DBA, không RESOURCE** | chủ schema; tài khoản dev dùng hằng ngày | có — nguồn duy nhất |
| `APP_AGENT` | chỉ `CREATE SESSION` | credential của AI agent; proxy vào owner | không bao giờ |

**Vì sao ba chứ không phải hai.** Phiên proxy kế thừa *toàn bộ* quyền của
owner. Để DBA trên owner "cho tiện" thì mọi phiên agent là DBA cấp instance —
nên việc quản trị phải nằm ở một tài khoản agent không bao giờ với tới, còn
owner chỉ giữ đúng thứ việc dev cần.

**Vì sao không làm schema riêng cho agent kèm quyền `ANY`.** `CREATE ANY
PROCEDURE` phủ *mọi schema trên instance*. Instance dùng chung thì một lần
chạy sai là chạm vào code của team khác — hay tenant khác. `ANY` không bao
giờ là câu trả lời ở đây; proxy mới là: toàn quyền trong đúng một schema,
bằng không ở mọi nơi khác.

```sql
-- Chạy bằng DBA của site, một lần mỗi môi trường. Tên tùy bạn đổi;
-- mật khẩu là mật khẩu thật ngay từ đầu, không bao giờ trùng tên user.
CREATE USER app_admin IDENTIFIED BY "<mật khẩu mạnh riêng>";
GRANT DBA TO app_admin;                     -- chỉ việc quản trị; không chứa gì

CREATE USER app_owner IDENTIFIED BY "<mật khẩu mạnh riêng>"
  QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE TRIGGER, CREATE TYPE, CREATE SYNONYM
  TO app_owner;

CREATE USER app_agent IDENTIFIED BY "<mật khẩu mạnh riêng>";
GRANT CREATE SESSION TO app_agent;
ALTER USER app_owner GRANT CONNECT THROUGH app_agent;
-- Cắt agent sau này, owner không suy suyển:
--   ALTER USER app_owner REVOKE CONNECT THROUGH app_agent;
```

`connections.json` phản chiếu đúng bố cục đó. Entry agent là mặc định; entry
trỏ thẳng owner dành cho dev, và khi dùng nó `check` sẽ cảnh báo — cảnh báo
đó là thiết kế đang hoạt động, không phải lỗi:

```json
{
  "default": "agent_dev",
  "agent_dev": { "user": "app_agent[app_owner]", "schema": "APP_OWNER", "...": "..." },
  "dev":       { "user": "app_owner",            "schema": "APP_OWNER", "...": "..." }
}
```

Kiểm cả tam giác bằng hai lệnh: `pythia connections` (ai tồn tại, không lộ
mật khẩu) và `pythia check` (proxy vào owner, không cảnh báo quyền). Phần còn
lại của mục này tự động hóa chân agent và dọn một owner đã phình quyền.

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
pythia approve --card a1b2c3               # agent: lấy thẻ để hỏi (không cấp gì cả)
#   -> AskUserQuestion, lựa chọn Approve / Reject; DEVELOPER trả lời ngay trong chat
pythia approve a1b2c3                      # hoặc: DEVELOPER, ở terminal của chính mình
pythia apply PKG_ORDER_BODY.sql --confirm a1b2c3   # ghi đúng nội dung đã preview
```

Các bước, không tắt được: **snapshot → impact → preview → approve → apply →
verify → report**.

**Hai bước, hai người.** Preview kết thúc bằng dòng `apply --confirm` của
agent và hai cửa của dev. Cửa nào cũng cấp cùng một grant dùng-một-lần;
thiếu nó thì confirm bị từ chối:

- **Trong chat** (từ 0.10.0): agent chạy `pythia approve --card <token>` rồi
  hỏi bằng `AskUserQuestion` với nội dung đúng nguyên văn thẻ đó, lựa chọn
  `Approve` / `Reject`. Claude Code ghi câu trả lời vào payload của hook
  `PostToolUse`; `pythia approve --hook` đọc payload và chỉ cấp grant khi câu
  trả lời là `Approve` *và* câu hỏi mang đúng thẻ của pythia — agent diễn
  giải lại thì không cấp gì, nên dev luôn duyệt lời của pythia chứ không
  phải lời của agent. Hook có sẵn trong plugin và trong settings mẫu (§11);
  không có hook thì trả lời trong chat không cấp gì.
- **Ở console**: `pythia approve <token>` (nhiều token một lần cũng được):

```
$ pythia approve a1b2c3

  Approving: PKG_ORDER (PACKAGE BODY) in APPDEV
  Impact: 12 dependent objects, 11 currently VALID
  Previewed 2026-08-27 14:02:11 on connection dev.

  Grant minted — single use, expires in 15 minutes.
  The agent may now run:  pythia apply <file> --confirm a1b2c3
```

- `approve` **chỉ chạy được ở console thật**, không có lối thoát
  `PYTHIA_CI` — đây là lệnh duy nhất agent không chạy được. Nó không chạm
  database, nên terminal chưa cấu hình kết nối vẫn duyệt được
- Grant dùng một lần, hết hạn sau 15 phút, và trói vào đúng connection mà
  preview đã chạy — duyệt trên `dev` không duyệt cho `staging`. Grant hết
  hạn tự bị dọn, không có lệnh prune nào để nhớ
- File chứa **đúng một** statement; block PL/SQL ẩn danh bị từ chối thẳng;
  statement không phân loại được → từ chối, không đoán
- Token 6-hex trói lần ghi vào nội dung đã preview — file hay DB đổi là token
  hết hiệu lực, phải preview lại
- Đổi kiểu object (function → procedure cùng tên...) bị chặn ngay preview
- `--yes` bỏ cả bước dừng lẫn bước approve riêng — nó là **cờ của
  developer**, và ở terminal thật thì bản thân nó *chính là* hành vi duyệt.
  Không có terminal (agent điều khiển CLI) thì bị từ chối, `policy set` nới
  lỏng cũng vậy; pipeline thật đặt `PYTHIA_CI=1`. Journal ghi lại mỗi lần
  ghi được cấp phép bằng gì (`grant` / `yes`), grant cấp lúc nào, và có TTY
  hay không
- `journal restore` đi qua đúng cổng đó, vì nó đi qua đúng đường ghi đó
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
| `data_dml` | `deny` | **Không.** Sau commit chỉ còn Flashback Query. Revalidation soi tập hàng trước khi ghi; nó không phải undo |
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

### Conventions — quy ước nhà, dưới dạng config

```bash
pythia conventions --init    # tạo cặp file dưới đây; không bao giờ ghi đè
pythia conventions           # xem quy ước đang có hiệu lực
```

- `.pythia/conventions.json`: mẫu đặt tên theo từng loại object. Mọi preview
  của `apply` sẽ cảnh báo khi tên object mới lệch mẫu. Style thì cảnh báo;
  chặn là việc của policy.
- `.pythia/conventions.md`: cùng bộ quy tắc nhưng bằng lời, kèm những điều
  không regex nào diễn đạt được. `pythia-write` đọc file này trước khi viết
  bất cứ thứ gì, và coi nó cao hơn các mẫu chung mà kit mang sẵn.

Thay mẫu placeholder bằng mẫu thật — `pythia similar <TÊN_TIÊU_BIỂU>` cho
thấy schema vốn đang đặt tên thế nào, tốt hơn là tự nghĩ ra quy ước mới.
Trong `conventions.md`, hãy viết **cái giá phải trả khi phá luật**, không chỉ
viết luật: hậu quả thì người ta tuân, mệnh lệnh thì người ta bỏ qua. Commit cả
hai file để cả team và mọi phiên agent làm việc trên cùng một bộ luật.
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
| `pythia-spec` | yêu cầu còn quyết định mở | đưa phương án + đánh đổi cho dev TRƯỚC khi xây; phát hiện giữa chừng thì dừng xây |
| `pythia-explore` | cần hiểu bất cứ gì trong schema | hỏi DB, không đọc dump |
| `pythia-impact` | **trước** mọi đề xuất thay đổi | ≥10 dependent hoặc cross-schema → hỏi dev trước |
| `pythia-write` | viết/sửa PL/SQL sau khi biết impact | copy convention, neo kiểu vào DB, unistr |
| `pythia-conventions` | có tài liệu chuẩn hoặc schema base cần nạp quy ước | quét tên thật, đo độ phủ, sinh conventions.json + .md |
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
| `no developer approval is on file` | chưa ai trả lời `Approve` cho thẻ (hoặc chưa cài hook), và `pythia approve <token>` cũng chưa chạy; hỏi bằng thẻ, hoặc chuyển dòng lệnh — thử lại không tạo ra sự phê duyệt |
| `did not carry pythia's approval card verbatim` | agent diễn giải lại thẻ trong câu hỏi; hook từ chối cấp — hỏi lại với đúng nội dung `approve --card` |
| `That approval expired` / `already used` | grant dùng một lần, sống 15 phút — preview lại và duyệt token mới |
| `approval was given on connection X` | đã duyệt trên database khác — duyệt trên đúng connection phiên này nhắm tới |
| `approve ... needs a real console` | agent định tự cấp phép cho mình; đó là cổng đang làm đúng việc |
| Exit 3 sau apply | đã ghi nhưng có lỗi compile/invalid mới — xem lỗi, chạy lệnh `journal restore` đã in |
| Output bị cắt | có marker `-- truncated` — tăng `--limit`/`--max-lines` hoặc `--offset` đọc tiếp |
| `--yes ... no terminal is attached` | agent định tự phê duyệt — đúng thiết kế: preview, relay nguyên văn, dừng; dev đồng ý rồi mới `--confirm <token>` |
| `Loosening the write policy ... no terminal` | tương tự: đưa dev đúng lệnh `policy set` đã in để họ tự chạy |

## 11. Tuỳ chọn: cấu hình quyền cho Claude Code

Bản settings mẫu giờ kèm hook `SessionStart` chạy `python -m pythia guide
--brief`: ~15 dòng bơm vào đầu mỗi phiên — chính nó làm việc định tuyến skill
trở nên tất định: yêu cầu build đến được `pythia-spec` bất kể hôm đó agent
có "nhớ" kiểm tra skills hay không. Không thích thì xoá khối `hooks`.

Claude Code tự quyết có chạy một lệnh hay không, và ở auto mode thì một
classifier chấm từng lệnh theo ngữ cảnh. Kéo theo hai chuyện.

**Lệnh đọc hỏi những câu không có gì để quyết.** Hai mươi hai lệnh pythia
không thể ghi — `sql` từ chối mọi thứ không phải SELECT/WITH, phần còn lại
chỉ đọc data dictionary. Bắt duyệt từng cái chỉ dạy dev bấm duyệt mà không
nhìn, đúng ngược với lý do prompt tồn tại.

**Lệnh ghi lại không được thêm lần dừng nào.** Ở auto mode,
`pythia apply … --confirm` hoàn toàn có thể bị chấm là an toàn rồi chạy
thẳng. pythia vẫn bắt preview và token, skills vẫn bắt bạn duyệt trong
chat — nhưng bản thân harness không góp thêm gì.

[`examples/claude-code-settings.example.json`](examples/claude-code-settings.example.json)
xử lý cả hai. Copy vào `.claude/settings.json` (gộp nếu đã có sẵn), khởi
động lại phiên, rồi `/permissions` để kiểm tra.

**pythia cố ý KHÔNG tự cài file này.** Đó là cấu hình bảo mật của sản phẩm
khác, nó chỉ áp cho một agent trong số 77 agent mà skill pack hỗ trợ, và —
lý do lớn nhất — hai nửa của file không mạnh ngang nhau:

| Nửa | Cơ chế | Độ chắc |
|---|---|---|
| `permissions.allow` | so khớp rule tất định | tin được; cú pháp đúng dạng mà chính Claude Code viết ra khi bạn bấm "always allow" |
| `autoMode.allow` / `soft_deny` | văn bản đưa vào prompt của classifier | chỉ gợi ý — nghiêng cán cân, không quyết định |

Nên đừng hiểu nửa sau là "từ nay mọi lần ghi đều dừng lại". Các bảo đảm
thật nằm chỗ khác và không đổi: token trói lần ghi vào đúng bản preview bạn
đã xem, `.pythia/policy.json` từ chối thẳng cả nhóm, và quyền Oracle của
agent là lớp duy nhất không thể nói vòng qua. Hãy đối xử với file này y như
[`agent-user-setup.example.sql`](examples/agent-user-setup.example.sql) —
kit đưa cho bạn, còn chạy hay không là bạn quyết.

---

Bảo mật chi tiết, bảng so dump-vs-DB, star history: [README.vi.md](README.vi.md).
Đóng góp — TDD, bind-contract lint, skill lint: [CONTRIBUTING.md](CONTRIBUTING.md).
