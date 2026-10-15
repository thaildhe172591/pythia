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
12. [Xây dựng trên Oracle: lint, deploy, autopilot, doctor](#12-xây-dựng-trên-oracle-lint-deploy-autopilot-doctor)

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

`.pythia/connections.json` — tạo bởi `pythia install`, **chứa
credentials, không đưa vào chat/screenshot**. Install ghi
`.pythia/connections.json` vào `.gitignore` của dự án (hãy commit dòng đó để
mọi bản clone đều bỏ qua file) trừ khi đã có luật bỏ qua nó, và cảnh báo nếu
git đã theo dõi file. `pythia doctor` báo `fail` khi file bị theo dõi —
`git rm --cached .pythia/connections.json`, commit, rồi đổi các mật khẩu đó
vì lịch sử git vẫn giữ chúng — và `warn` khi file chưa được bỏ qua, hoặc chỉ
được bỏ qua trên máy này (`.git/info/exclude`):

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
#   -> agent gọi tool pythia_approve (MCP) với a1b2c3; DEVELOPER chọn Approve
pythia approve --card a1b2c3               # hoặc: lấy thẻ để hỏi bằng AskUserQuestion (không cấp gì)
#   -> AskUserQuestion, lựa chọn Approve / Reject; DEVELOPER trả lời ngay trong chat
pythia approve a1b2c3                      # hoặc: DEVELOPER, ở terminal của chính mình
pythia apply PKG_ORDER_BODY.sql --confirm a1b2c3   # ghi đúng nội dung đã preview
```

Các bước, không tắt được: **snapshot → impact → preview → approve → apply →
verify → report**.

**Hai bước, hai người.** Preview kết thúc bằng dòng `apply --confirm` của
agent và các cửa của dev. Cửa nào cũng cấp cùng một grant dùng-một-lần;
thiếu nó thì confirm bị từ chối:

- **Tool `pythia_approve`** (từ 0.15.0 trên Claude Code, 0.14.0 trên Codex):
  MCP server không-ghi-DB mà `pythia install` đăng ký vào `.mcp.json`. Tool
  được đánh dấu `requiresUserInteraction`, nên Claude Code luôn hỏi ở mọi
  lần gọi, mọi permission mode trừ `dontAsk` — không allow rule nào bỏ qua
  được — và MCP elicitation hiện đúng thẻ duyệt của pythia với Approve /
  Reject. Agent không có gì để diễn giải lại.
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
| `comments` | `confirm` | **Có.** Nội dung `COMMENT ON` cũ được snapshot |
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
`pythia plscope` luôn có index đầy đủ trên schema dev. Thêm hai công tắc,
đều mặc định **bật**: `"auto_allow_reads"` — hook PreToolUse cho lệnh đọc của
pythia (và lệnh ghi đã được duyệt) chạy thẳng không hỏi; `"block_raw_writes"`
— chặn DDL/DML qua sqlplus hoặc `run-sql` của SQLcl, thứ đi vòng đường ghi.

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

Các luật của LEAD mà linter kiểm được nằm cùng file, dưới key `"lint"`: mức
nghiêm trọng từng luật, comment bắt buộc, cột bắt buộc, tiền tố tham số, mẫu
bị cấm (xem §12). Mọi lần `pythia lint` và preview của apply đều kiểm.

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

`pythia install` tự ghi bốn hook vào `.claude/settings.json`, gộp vào file
có sẵn và không đụng key nào khác; chạy lại không thêm gì, `--no-hooks` để
bỏ qua; đồng thời đăng ký MCP approver vào `.mcp.json`. Các lệnh này khởi
động pythia bằng một launcher mà install chọn theo cái thật sự chạy được:
hook không khởi động được thì cổng mở luôn (Claude Code coi lỗi của nó là
không chặn, nên guard để sqlplus đi qua), mà `python -m pythia` chỉ chạy khi
`python` import được pythia. Ngoài virtual environment, với `python` (hoặc
`python3`) import được pythia, cách viết trần đó vào file chung. Còn lại —
venv, pipx, `python` là interpreter khác — hook vào
`.claude/settings.local.json` với đường dẫn tuyệt đối (thêm vào
`.git/info/exclude`), MCP approver vào local scope của Claude Code bằng
`claude mcp add --scope local` (in ra để bạn tự chạy nếu không có CLI
`claude` trên PATH), và `.claude/settings.json`, `.mcp.json` dùng chung được
để nguyên; install nêu tên các hook pythia đã có sẵn trong file chung, vì
chúng cũng chạy. `pythia doctor` chạy thử từng launcher và báo `fail` cho
cái nào không import được pythia, kèm việc nó làm mất và cách sửa đúng với
file chứa nó. Hook dùng chung không chạy được ở đây trong khi launcher riêng
của máy đã phục vụ đúng các hook đó thì chỉ là `warn` — bản của team báo lỗi
trên máy này; giữ cho đồng nghiệp hoặc xoá — và hook đầu phiên của Codex cũng
là `warn`, vì nó chỉ mang guide đầu phiên, còn luật nằm trong AGENTS.md.
`install -g` chỉ ghi hook PreToolUse/PostToolUse và luật deny vào `~/.claude/settings.json` — file
đó áp cho mọi dự án trên máy, guide đầu phiên ở đó sẽ thành nhiễu với dự án
không dính Oracle. `SessionStart` chạy `python -m pythia hook session-start`:
guide một trang kèm trạng thái sống của dự án — chính nó làm việc định tuyến
skill trở nên tất định: yêu cầu build đến được `pythia-spec` bất kể hôm đó
agent có "nhớ" kiểm tra skills hay không. `PreToolUse` trên Bash và `run-sql`
của SQLcl chạy `hook pre-tool`: lệnh đọc của pythia và lệnh ghi đã duyệt
chạy thẳng, ghi qua sqlplus/SQLcl bị chặn. Matcher của nó không bao giờ là
`""` hay `*` — hook PreToolUse bắt trúng `AskUserQuestion` làm câu hỏi trả về
rỗng, đúng lỗi mà `pythia doctor` đi tìm. `PostToolUse` trên `AskUserQuestion`
chạy `python -m pythia approve --hook`: cửa duyệt trong chat ở §5 — từ chối
câu trả lời điền sẵn và nói rõ khi không có câu trả lời nào về.
`PostToolUse` trên Bash chạy `hook post-tool`: nhắc luật đọc exit code ngay
cạnh kết quả apply. Install còn ghi harness vào `CLAUDE.md` của dự án (khối
có đánh dấu, giống AGENTS.md cho Codex): Claude Code nạp file này ở mọi
phiên dù có hook hay không, và khi đã có nó thì hook SessionStart chỉ in
trạng thái sống. Kèm theo là luật `deny` cho `Bash(pythia approve
--hook*)` — hook phải chạy trên payload do Claude Code ghi, agent tự bơm
payload từ Bash là giả mạo phê duyệt. Sau `pip install --upgrade`, chạy lại
`python -m pythia install`; `npx pythia-plsql` làm cả hai. Plugin cài qua
`hooks/hooks.json`.

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
xử lý cả hai. Phần còn lại — allow list và autoMode — copy vào
`.claude/settings.json` (gộp nếu đã có sẵn), khởi động lại phiên, rồi
`/permissions` để kiểm tra.

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

## 12. Xây dựng trên Oracle: lint, deploy, autopilot, doctor

### `pythia lint` — kiểm chuẩn trước khi apply

```bash
pythia lint release/ PKG_ORDER_BODY.sql     # exit 2 nếu có lỗi mức error
```

Luật có sẵn phủ các antipattern PL/SQL (`WHEN OTHERS THEN NULL`, dynamic SQL
nối chuỗi, COMMIT trong vòng lặp, DML từng dòng, `= ''`, mã lỗi ngoài
-20999..-20000, `SELECT *`), chuẩn DDL (không có primary key, constraint
không tên, `VARCHAR2(n)` theo byte, `ADD ... NOT NULL` không default, `LONG`,
`DROP ... PURGE`) và, trên cả script, mọi bảng/cột mới thiếu `COMMENT ON`,
mọi foreign key thiếu index. Mọi preview của apply hiện kết quả lint của câu
lệnh, và autopilot lease không tự ghi file còn lỗi.

LEAD tinh chỉnh trong `.pythia/conventions.json`:

```json
{
  "naming": {"TABLE": "^T_[A-Z0-9_]+$"},
  "lint": {
    "rules": {"varchar2-byte-semantics": "error", "dbms-output": "off"},
    "require_comments": "error",
    "required_columns": ["CREATED_BY", "CREATED_AT"],
    "param_prefix": "^P_",
    "forbid": [{"id": "no-sysdate", "pattern": "\\bSYSDATE\\b",
                "message": "dùng SYSTIMESTAMP", "severity": "warning"}]
  }
}
```

### `pythia deploy` — chạy script release qua đường ghi

```bash
pythia deploy release/                       # preview từng câu lệnh, một deploy token
pythia deploy release/ --confirm 3f9a01c2b7  # sau khi dev đã duyệt token đó
```

Script được tách theo kiểu SQL*Plus: `;` kết thúc câu SQL, `/` đứng riêng một
dòng kết thúc khối PL/SQL, tôn trọng literal và comment; lệnh SQL*Plus (SET,
PROMPT…) được bỏ qua và liệt kê; `@include`, `EXEC`/`HOST`/`CONNECT`,
`ALTER SESSION` và block ẩn danh bị từ chối. Thư mục có đánh số (`1_`, `02_`,
`V3__`) chạy theo thứ tự số tự nhiên; không đánh số thì theo loại object
(bảng trước code, spec trước body, trigger và grant sau cùng). Thẻ duyệt hiện
đầy đủ từng câu lệnh; `--from N` chạy tiếp sau khi dừng, với lần duyệt mới. Policy từ chối ngay trước
khi chạy bất cứ gì. Mỗi câu lệnh có snapshot, journal, verify riêng; dừng ở
lần từ chối hoặc exit 3 đầu tiên và in danh sách hoàn tác, mới nhất trước.

### `COMMENT ON` — nhóm riêng, có undo thật

`COMMENT ON TABLE / COLUMN` thuộc nhóm `comments` (mặc định `confirm`): comment
cũ được đọc trước khi ghi và `journal restore` đặt lại đúng như cũ.
`pythia comments T` cho thấy cột nào đã có comment; `--scaffold` in sẵn câu
`COMMENT ON` cho mọi cột còn thiếu.

### `pythia autopilot` — uỷ quyền một phiên, đúng một lần

```bash
pythia autopilot request --groups plsql_source,comments,structural --hours 6 --note "module đơn hàng"
#   -> agent hỏi bằng thẻ (tool pythia_approve / AskUserQuestion / console)
pythia autopilot status       # đang hiệu lực gì, đến khi nào, đã ghi bao nhiêu
pythia autopilot report       # mọi lần ghi trong lease, kết quả và lệnh hoàn tác
pythia autopilot off          # thu hồi — lúc nào cũng được
pythia autopilot on ...       # dev, tại console: xin + duyệt trong một lệnh
```

| Một lease… | |
|---|---|
| phủ | một connection, các nhóm ghi trên thẻ, trong số giờ (tối đa 24) và hạn mức số lần ghi |
| không bao giờ phủ | connection đánh dấu `"protected": true` / `"env": "prod"`; câu lệnh phá huỷ trừ khi thẻ ghi INCLUDED; file còn lỗi lint |
| tự dừng | khi object đang VALID chuyển INVALID, hoặc sau năm lần ghi lỗi liên tiếp |
| vẫn giữ | snapshot, journal, verify, exit code — mọi lần ghi vẫn được ghi lại |
| vượt qua | policy `deny`, chỉ cho các nhóm ghi trên thẻ, chỉ trong thời hạn lease |

### `pythia doctor` — khi cú duyệt không về

`pythia doctor` kiểm harness: hook đã nối, MCP approver đã đăng ký và bật,
skill cài một chỗ (không trùng), policy, conventions, lease, và git có giữ
`.pythia/connections.json` ngoài mọi bản clone không. Nó còn chỉ đích
danh mọi cấu hình đã biết làm `AskUserQuestion` trả về mà không hiện câu hỏi
— allow rule hoặc `allowed-tools` của skill có `AskUserQuestion`, hook
PreToolUse/PermissionRequest có matcher bắt trúng nó (trong file settings
hoặc plugin đã cài), chế độ `dontAsk`. `pythia doctor --db` kiểm thêm
database: phiên bản, bộ ký tự, độ phủ PL/Scope và warnings, object INVALID,
utPLSQL.

### `pythia explain` và `pythia stats` — tối ưu, không cần license thêm

`explain` chạy EXPLAIN PLAN + `DBMS_XPLAN.DISPLAY` cho câu SELECT/WITH — chỉ
parse, không thực thi, các dòng PLAN_TABLE được rollback — kèm ghi chú về
full scan, Cartesian join, ép kiểu ngầm, dynamic sampling. `stats` hiện thống
kê optimizer của bảng, các index kèm cột, và foreign key thiếu index. Không
dùng `V$`, AWR, ASH hay Tuning Pack.

### `pythia skill` — skill riêng của team

`pythia skill new <tên> --trigger <từ khoá>` tạo khung skill đúng format của
pack trong `.claude/skills/`; `pythia skill lint` kiểm skill của team theo cùng
hợp đồng với pack (exit 2 nếu có vấn đề).

---

Bảo mật chi tiết, bảng so dump-vs-DB, star history: [README.vi.md](README.vi.md).
Đóng góp — TDD, bind-contract lint, skill lint: [CONTRIBUTING.md](CONTRIBUTING.md).
