<p align="center">
  <img src="https://raw.githubusercontent.com/thaildhe172591/pythia/main/assets/logo.png" alt="pythia" width="280" />
</p>

# pythia

> MCP của Oracle cho agent một kết nối tới database.
> **pythia dạy nó dùng kết nối đó cho đúng.**

[English](README.md) · **Tiếng Việt** · **[Hướng dẫn chi tiết →](GUIDE.vi.md)** · **[Trang web →](https://thaildhe172591.github.io/pythia/)**

[![ci](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.9%2B-blue)

pythia là bộ **Agent Skills + CLI** để AI coding agent (Claude Code, Codex,
Cursor — và 70+ agent khác mà `npx skills` hỗ trợ) làm việc được với PL/SQL
trên Oracle: đọc schema lớn mà không cần dump, biết trước một thay đổi sẽ
làm hỏng những gì, và ghi vào database qua quy trình có snapshot — hỏng thì
báo hỏng, không bao giờ báo thành công giả.

## Vì sao phải hỏi database, đừng đọc dump

Một hệ thống cỡ vừa có thật, đối chiếu bản export trong repo với database
đang chạy (audit 2026):

| Loại object | Có trong dump | Có trong DB | Kết quả |
|---|---|---|---|
| Procedure | 3.827 | 3.827 | khớp |
| Table | 952 | 952 | khớp |
| **Type** | **0** | **115** | **mất sạch** |
| **Package** | **0** | **9** | **mất sạch** |
| **Index** | **116** | **1.016** | **mất ~89%** |

Đọc dump thì thấy code có vẻ ổn, nhưng thật ra nó đang gọi tới những type và
package mà dump không hề có. Mọi lệnh pythia đều hỏi thẳng data dictionary
đang chạy. Và khi output bị cắt bớt, nó luôn nói rõ là đã cắt — để agent
không tưởng nhầm câu trả lời thiếu là câu trả lời đủ.

## Mô hình vận hành: Học → Hỏi → Làm

pythia là một bộ **harness** — cuốn cẩm nang mà agent học và tuân theo khi
ngồi cạnh dev, như một trợ giảng giỏi: **có kiến thức** (hỏi schema đang
chạy, không tin trí nhớ), **biết suy nghĩ** (đo lường trước khi đề xuất), và
**tuân thủ luật mà nó đọc thuộc** (gặp cổng thì dừng và trích lại luật, không
lẳng lặng bỏ qua). Giao cho nó một bài toán — "thêm cột và mọi procedure nuôi
cột đó", "vì sao báo cáo này nhân đôi dòng" — nó đi đúng ba nhịp của một
senior cẩn thận:

### 1 · Học — hiểu rồi mới đề xuất

| Học cái gì | Bằng gì | Thay cho |
|---|---|---|
| hình dạng thật của bài toán | `deps` · `impact` · `plscope` — đồ thị phụ thuộc chính xác | đọc lướt code rồi đoán |
| sự thật của schema | `src` · `cols` · `args` · `ddl` · `errors` trên DB đang chạy | tin một bản dump đã trôi |
| phong cách của dự án | `.pythia/conventions.md` + `conventions --scan/--check` | mỗi phiên tự chế một kiểu |
| cách nơi này vẫn giải | `similar` — hàng xóm để bắt chước | viết đại thứ compile được |

Pha này không ghi gì. Luật sắt: **chưa đo blast radius thì chưa đề xuất**,
**chưa đọc hàng xóm thì chưa viết dòng nào**.

### 2 · Hỏi — câu hỏi là một phần của phương pháp

Kit buộc agent dừng đúng những khoảnh khắc mà phán đoán của dev là mảnh còn
thiếu — và cấm đoán bừa để đi tiếp:

- **Trước mọi lần ghi**: đưa nguyên văn preview (diff, dependents, cảnh báo)
  rồi chờ một cái gật thật. Khen không phải là gật. Im lặng cũng không. Và
  cái chờ đó nay không còn phụ thuộc vào sự vâng lời: lần ghi không hoàn tất
  được cho tới khi chính dev cấp phép.
- **Blast radius lớn** (≥10 dependents, hoặc dính schema khác): trình dev
  *trước khi viết code*, không phải sau.
- **Hai nguồn sự thật cãi nhau**: tài liệu nói một đằng, schema làm một nẻo
  — đó là câu hỏi (luật không ai theo / luật cho code mới / drift?), không
  phải chỗ để tự chọn.
- **Hỏng thì nói hỏng**: exit 3 = đã ghi nhưng gãy. Báo đúng như vậy kèm
  lệnh rollback in sẵn — báo thành công lúc này là tội duy nhất cả bộ kit
  sinh ra để chặn.
- **Policy từ chối**: chuyển lời từ chối cho dev như một thông tin, không
  tìm đường vòng.

### 3 · Làm — hành động trong đường ống không nói dối được

Chỉ sau Học và Hỏi mới được chạm database, và chỉ qua một cửa:
**snapshot → impact → preview → token → approve → apply → verify → report**.
DDL trong Oracle tự commit nên snapshot là đường lùi duy nhất — nó chạy đầu
tiên và không cờ nào tắt được. Token gắn nội dung bảo đảm thứ được ghi đúng
từng byte với thứ đã xem; còn **grant do chính dev cấp** bảo đảm là *có người
duyệt* — qua một cửa luôn thuộc về dev. Tool `pythia_approve` (MCP, trên
Claude Code và Codex) hiện thẻ duyệt của chính pythia dưới dạng prompt chọn,
và Claude Code luôn hỏi cho tool này ở mọi permission mode trừ `dontAsk` —
không allow rule nào bỏ qua được. Trong chat: một `AskUserQuestion` có nội
dung là thẻ duyệt (`pythia approve --card <token>`), câu trả lời **Approve**
— do Claude Code ghi vào payload của hook, không phải agent — cấp grant
một-lần. Ở console: `pythia approve <token>`. Thiếu grant đó thì
`apply --confirm` từ chối. Agent chạy không người không thể `--yes` cho chính
nó, không thể tự cấp grant, cũng không nới được policy — cả ba đều cần một
con người, trả lời hoặc gõ.

**Khi dev muốn agent tự làm hết**, dev uỷ quyền đúng một lần: một **autopilot
lease** — một connection, các nhóm câu lệnh dev chọn, vài giờ và một hạn mức
số lần ghi — duyệt qua cùng các cửa trên. Trong thời hạn đó `apply` và
`deploy` tự ghi các câu lệnh nằm trong phạm vi mà không hỏi lại; mỗi lần ghi
vẫn snapshot, journal và verify. Lease không bao giờ phủ connection được
đánh dấu protected (prod), không phủ câu lệnh phá huỷ (trừ khi thẻ ghi rõ),
không phủ file còn lỗi lint, và tự dừng ngay khi object khác bị hỏng.
`pythia autopilot report` liệt kê mọi lần ghi kèm lệnh hoàn tác.

Dev tự sửa tay cũng được đỡ: `src` và `impact` lặng lẽ snapshot những gì
chúng đọc, nên sửa bằng SQL Developer vẫn có file rollback chờ sẵn
(`history` liệt kê; source trôi mà không có apply nào phía sau sẽ bị báo là
drift).

**Học – Hỏi – Làm.** Agent không nói được mình đang ở nhịp nào nghĩa là nó
không ở nhịp nào cả. Cả cuốn luật in ra từ chính công cụ: `pythia guide` —
nền tảng nào không có skill support thì trang đó là bản hợp đồng.

## Cài đặt

Một lệnh, xong tất cả:

```bash
npx pythia-plsql
```

Hoặc làm từng bước — nên dùng cách này vì rõ ràng hơn:

```bash
pip install pythia-plsql        # 1. CLI (thin driver, không cần Oracle Instant Client)
python -m pythia install -g     # 2. skills — MỘT LẦN cho cả máy
cd du-an && python -m pythia install   # 3. mỗi dự án: tạo .pythia/ + hook Claude Code
pythia check                    # 4. điền connections.json rồi kiểm tra
```

**Skills nên cài global.** Cài một lần là mọi dự án dùng chung. Khi bạn chạy
`pythia install` trong dự án, nó thấy đã có bản global thì tự bỏ qua bước
skills — vì cài thêm bản thứ hai sẽ làm mỗi skill hiện hai lần trong menu
của agent.

Máy có Node.js thì bước skills chạy qua `npx skills add` (77 agent, cập nhật
bằng symlink; thêm `--source <git-url>` nếu cài từ git nội bộ). Máy không có
Node vẫn cài được — gói pip đã đóng gói sẵn skills bên trong.

Chỉ muốn skills, không cần CLI: `npx skills add thaildhe172591/pythia`,
hoặc `/plugin marketplace add thaildhe172591/pythia` cho Claude Code.

### Cài một lần cho máy, chạy một lần cho mỗi dự án

`pip install` và `pythia install -g` là việc của **máy**, làm một lần.
`pythia install` là việc của **dự án** — vào thư mục gốc từng repo mà chạy:

```bash
cd du-an-A && pythia install    # tạo config riêng cho A → điền DB của A → pythia check
cd du-an-B && pythia install    # tạo config riêng cho B → điền DB của B → pythia check
```

pythia luôn đọc config của dự án bạn đang đứng — nó tìm ngược từ thư mục
hiện tại lên trên, **không có config global nào cả**. Nhờ vậy một CLI dùng
chung không bao giờ nhầm database giữa các dự án. Đứng ngoài mọi dự án thì
nó báo lỗi rõ ràng chứ không tự đoán.

**Cập nhật bản mới** cũng chia y như vậy:

```bash
pip install --upgrade pythia-plsql   # CLI mới — việc của máy
pythia install -g                    # làm mới skills — việc của máy
```

Cập nhật không bao giờ đụng vào config của bạn. Nếu cài skills bằng npx thì
`npx skills update` cũng được.

Chạy từ bản clone cũng được: `python scripts/pythia.py <lệnh>` — mọi lệnh
gợi ý mà nó in ra luôn khớp với cách bạn gọi. Windows, macOS, Linux và WSL
đều có trong ma trận test của CI.

## Cài đặt cho Claude Code và Codex

`pythia install` cấu hình cho agent nào bạn dùng — trong một lần chạy, ở gốc
dự án. Dưới đây là chính xác nó ghi gì cho mỗi agent, và một hai bước mà mỗi
agent buộc bạn tự làm.

### Claude Code

1. Chạy `python -m pythia install` (hoặc `npx pythia-plsql`) ở gốc dự án. Nó
   đăng ký **MCP approver** (không ghi DB) vào `.mcp.json` và gộp vào
   `.claude/settings.json`, giữ nguyên mọi key bạn đã có:
   - hook **SessionStart**: guide Học → Hỏi → Làm kèm trạng thái sống của dự
     án (connection, autopilot lease, conventions, cửa duyệt);
   - hook **PreToolUse** trên Bash và `run-sql` của SQLcl: lệnh đọc của pythia
     chạy thẳng không hỏi, lệnh ghi đã được duyệt chạy thẳng, lệnh ghi đi vòng
     cổng (DDL qua sqlplus/SQLcl) bị chặn kèm lý do;
   - hook **PostToolUse**: trên `AskUserQuestion` thì mint grant; trên Bash
     thì nhắc luật đọc kết quả (exit 3 là hỏng) ngay cạnh kết quả;
   - luật `deny` để agent không giả được grant, và bật MCP server.

   Nó tạo `.pythia/connections.json` (quyền 600) và thêm file này vào
   `.gitignore` của dự án — hãy commit dòng đó — rồi cảnh báo nếu git đã theo
   dõi file: khi đó mật khẩu đã nằm trong lịch sử git, cần bỏ theo dõi và đổi
   mật khẩu.

   Nó còn ghi harness vào **`CLAUDE.md`** — file mà chính Claude Code nạp ở
   mọi phiên — trong một khối có đánh dấu, không đụng nội dung của bạn. Nhờ
   vậy luật vẫn có hiệu lực cả khi cài `--no-hooks`, trước khi khởi động lại,
   hay khi hook chưa được trust; khi đã có khối này, hook SessionStart chỉ bổ
   sung trạng thái sống chứ không lặp lại guide.

   **Interpreter được chọn theo cái thật sự chạy được pythia.** Hook không
   khởi động được thì cổng mở luôn — Claude Code coi lỗi của nó là không chặn —
   nên `python -m pythia` trần chỉ được ghi vào file chung khi nó import được
   pythia và không nằm trong virtual environment. Cài trong venv, bằng pipx,
   hay khi `python` là một interpreter khác, thì hook được ghi vào
   `.claude/settings.local.json` (file riêng của máy này, không vào git) với
   đường dẫn tuyệt đối tới interpreter, còn MCP approver vào local scope của
   Claude Code (`claude mcp add --scope local`); đồng nghiệp không bao giờ nhận
   một đường dẫn từ máy bạn. `pythia doctor` chạy thử từng launcher nó tìm
   thấy và báo `fail` cho cái nào không import được pythia — trừ khi launcher
   riêng của máy này đã phục vụ đúng các hook đó: khi ấy bản dùng chung của
   team báo lỗi trên máy này chỉ là `warn` (giữ cho đồng nghiệp hoặc xoá). Hook
   đầu phiên của Codex cũng chỉ là `warn`: nó chỉ mang guide đầu phiên, còn
   luật đã nằm trong AGENTS.md.

   Skills vào `.claude/skills` (hoặc `~/.claude/skills` khi `-g`).
2. **Khởi động lại phiên Claude Code** để hook và MCP server nạp.
3. **Chạy `pythia doctor`.** Nó xác nhận mọi thứ đã nối, và chỉ đích danh
   từng cấu hình làm `AskUserQuestion` trả về rỗng mà không hiện câu hỏi —
   allow rule hoặc `allowed-tools` của một skill có `AskUserQuestion`, hook
   PreToolUse có matcher rỗng hoặc `*`, chế độ `dontAsk`. Chính nhóm cấu hình
   đó là lý do duyệt trong chat có thể hỏng ở các mode khác
   `bypassPermissions`; cửa MCP miễn nhiễm với tất cả trừ `dontAsk`.
4. *Tuỳ chọn:* copy allow-list và `autoMode` từ
   [`examples/claude-code-settings.example.json`](examples/claude-code-settings.example.json).
   Đó là quyền hạn của bạn, nên pythia không tự đặt
   ([vì sao](GUIDE.vi.md#11-tuỳ-chọn-cấu-hình-quyền-cho-claude-code)).

**Duyệt một thay đổi:** agent preview bằng `pythia apply`, gọi tool
`pythia_approve` (hoặc hỏi một `AskUserQuestion` mang thẻ duyệt), bạn bấm
**Approve**, agent tự chạy `apply --confirm`.

**Làm việc tự động hoàn toàn:** cứ nói với agent. Agent xin một lease
(`pythia autopilot request`), bạn duyệt thẻ của nó một lần, và nó làm — bảng,
comment, package, script release — không dừng lại ở từng lần ghi. Mode
`acceptEdits` cộng một lease là cấu hình rảnh tay hoàn toàn.

### Codex

1. Chạy `python -m pythia install` ở gốc dự án. Nó:
   - ghi harness vào **`AGENTS.md`** (Codex nạp mỗi phiên);
   - đăng ký MCP approver (không ghi DB) vào **`.codex/config.toml`**;
   - thêm hook SessionStart guide vào **`.codex/hooks.json`**.

   Skills vào `.agents/skills` (hoặc `~/.agents/skills` khi `-g`) — đúng thư
   mục Codex đọc.
2. Trong Codex, **trust một lần**: `/mcp` (server `pythia`) và `/hooks`
   (layer `.codex` của dự án).
3. **Khởi động lại phiên Codex** để `AGENTS.md` mới nạp.
4. Kiểm: `/skills` liệt kê các skill pythia, `/mcp` hiện `pythia: connected`.

Cần **Codex ≥ v0.120** cho duyệt-trong-chat (MCP elicitation); Codex cũ hơn
hoặc chạy headless thì lùi về console `pythia approve <token>`. **Đừng** chạy
Codex dưới `danger-full-access` — sandbox đó tự-duyệt elicitation, vượt qua
cú Approve của bạn.

**Duyệt một thay đổi:** agent preview bằng `pythia apply`, gọi tool
`pythia_approve`, bạn trả lời prompt select **Approve / Reject** ngay trong
Codex, và khi Approve agent tự chạy `apply --confirm`.

## Các lệnh

| Đọc | Phân tích | Ghi |
|---|---|---|
| `check` kết nối + đếm object | `impact` cái gì phụ thuộc vào nó | `apply` quy trình ghi 6 bước |
| `ls` tìm object theo tên | `deps` nó phụ thuộc vào cái gì | `deploy` chạy script release theo thứ tự, duyệt một lần |
| `src` source, đúng số dòng compiler | `errors` lỗi biên dịch kèm dòng:cột | `journal` list · diff · export · restore |
| `args` tham số của procedure | `invalid` mọi object đang hỏng | `policy` xem · sửa |
| `cols` cột và kiểu dữ liệu | `plscope` chỗ nào dùng identifier này | `approve` dev cấp grant một-lần |
| `ddl` lấy DDL qua DBMS_METADATA | `similar` chương trình tên tương tự | `autopilot` request · status · report · off |
| `comments` comment bảng/cột, `--scaffold` | `lint` chuẩn của LEAD + chuẩn có sẵn | `unistr` chuỗi tiếng Việt chuẩn |
| `stats` thống kê, index, FK thiếu index | `explain` execution plan, không chạy câu lệnh | `agent-user` tạo user least-privilege |
| `grep` tìm chuỗi trong toàn bộ source | `doctor` sức khoẻ harness + môi trường | `history` các bản đã chụp |
| `sql` truy vấn tự do (chỉ SELECT/WITH) | | `skill new · lint` tự phát triển skill |

Lệnh nào cũng có `--json` (cho máy đọc) và `--conn` (chọn kết nối). Output
luôn bị giới hạn độ dài kèm dấu báo đã cắt, để không nuốt hết context window
của agent.

**Lưới an toàn phủ cả việc sửa tay**: `src` và `impact` tự chụp source của
object vào journal — im lặng, kèm file rollback chạy được — nên một thay đổi
sau đó bằng SQL Developer vẫn có chỗ để quay về. `pythia history <OBJECT>`
liệt kê các bản đã chụp. Source đổi mà không có `apply` nào đứng sau thì bị
báo là drift.

**Quy ước đặt tên nên là config, đừng để truyền miệng.** Viết pattern đặt
tên vào `.pythia/conventions.json` — preview khi apply sẽ cảnh báo nếu tên
object mới lệch chuẩn. Viết các quy ước dạng văn xuôi vào
`.pythia/conventions.md` — skills bắt agent đọc file này trước khi viết code.
Xem cả hai bằng `pythia conventions`.

## Bảo mật và chính sách ghi

**Tài khoản Oracle mới là lớp bảo vệ thật.** File policy chỉ là hàng rào
phía ứng dụng — agent lách được, còn quyền trong Oracle thì không.

Cách làm: cấp cho agent một tài khoản riêng, thu hồi được bất cứ lúc nào,
đăng nhập xuyên qua schema owner bằng proxy authentication
(`agent_user[schema_owner]` — không quyền `ANY`, không đưa mật khẩu owner
cho agent):

```bash
pythia agent-user --save   # in SQL cho DBA chạy + tự lưu credential vào connections.json
pythia check               # sau khi DBA chạy xong: kết nối proxy, hết cảnh báo
```

Chạy **một lần duy nhất** là xong cả hai việc — mỗi lần chạy nó sinh mật
khẩu mới, nên khối SQL đưa DBA và config đã lưu bắt buộc phải ra từ cùng
một lần chạy. Đây chỉ là tiện ích; bạn tự làm tay theo
[`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql)
cũng được. `pythia check` sẽ cảnh báo khi tài khoản đang dùng có quyền cao
hơn mức công việc cần.

Cú duyệt mà `apply` đòi được mint theo từng agent — tool `pythia_approve`
(MCP) trên Claude Code và Codex, hook `AskUserQuestion` của Claude Code, hoặc
console `pythia approve <token>` ở bất kỳ đâu. Cách wire từng cái nằm ở
[Cài đặt cho Claude Code và Codex](#cài-đặt-cho-claude-code-và-codex) phía trên.

Chính sách ghi chia theo nhóm trong `.pythia/policy.json` (mặc định):

| Nhóm | Mặc định | Có rollback thật không? |
|---|---|---|
| `plsql_source` | `confirm` | **Có, hoàn toàn** — lấy lại source từ `ALL_SOURCE`. |
| `comments` | `confirm` | **Có.** Comment cũ được snapshot trước mỗi lần ghi. |
| `data_dml` | `deny` | **Không.** Commit rồi thì chỉ còn Flashback Query, trong thời hạn undo retention. Revalidation soi tập hàng *trước* khi ghi; nó không phải undo. |
| `structural` | `deny` | **Gần như không.** `DROP COLUMN` là mất vĩnh viễn; bảng bị drop may lắm còn trong Recycle Bin. |
| `grants` | `deny` | Có, nhưng phải làm tay. |
| `session` | `allow` | Không cần. |

Nhóm nào không snapshot được thì mặc định là `deny` — và khi từ chối, pythia
nói đúng lý do đó chứ không nói chung chung "policy không cho phép". Block
PL/SQL ẩn danh bị từ chối thẳng. Câu lệnh không nhận diện được cũng bị từ
chối, không bao giờ đoán bừa nó thuộc nhóm nào.

Phần đọc có thể đi qua SQLcl MCP server chính thức của Oracle (`sql -mcp`,
giữ mức `-R 4`) — nó ghi log mọi tương tác vào `DBTOOLS$MCP_LOG`. **Phần ghi
thì không bao giờ đi đường đó** — chỉ `pythia apply` mới có snapshot,
preview, verify và journal.

## Skills

Các skill dạy agent quy trình làm việc. Đây là **luật bắt buộc, không phải
gợi ý** — agent phải đi qua chúng:

| Skill | Việc chính |
|---|---|
| `using-pythia` | bộ định tuyến: mở đúng skill trước mọi hành động |
| `pythia-setup` | cấu hình kết nối, user agent, `pythia doctor`, môi trường Oracle chuẩn |
| `pythia-explore` | hỏi database để hiểu code, không đọc dump |
| `pythia-impact` | **bắt buộc chạy trước** khi đề xuất bất kỳ thay đổi nào |
| `pythia-spec` | quyết định spec được hỏi, không bị tự chốt |
| `pythia-table` | bảng, cột, constraint, index, sequence — cột nào cũng có comment |
| `pythia-package` | package, procedure, function, trigger theo chuẩn thiết kế quốc tế |
| `pythia-tune` | tối ưu truy vấn: đo, đổi một thứ, đo lại |
| `pythia-conventions` | nạp quy ước của LEAD (đặt tên + luật lint), có kiểm chứng |
| `pythia-write` | viết code theo đúng quy ước sẵn có của codebase |
| `pythia-comment` | comment một dòng, đúng một khuôn |
| `pythia-apply` | dev xem preview và duyệt trước khi ghi |
| `pythia-deploy` | chạy script release nhiều câu lệnh theo thứ tự, duyệt một lần |
| `pythia-autopilot` | dev uỷ quyền một lần, agent làm trong phạm vi lease |
| `pythia-verify` | có bằng chứng rồi mới báo "xong" |
| `pythia-review` | review code theo antipattern + lint, chỉ rõ từng dòng |
| `pythia-skill-author` | biến cách làm việc của team thành skill mới (`pythia skill new`) |

## Tương thích

| | |
|---|---|
| Hệ điều hành | Windows, macOS, Linux, WSL — CI test đủ cả ba họ |
| Python | 3.9+ · chỉ cần stdlib và `python-oracledb` (thin mode) |
| Oracle | phần lõi chạy được với hầu hết phiên bản; PL/Scope statement cần 12.2+; chỉ dùng view không vướng license |
| Agent | mọi agent mà `npx skills` hỗ trợ (76) · plugin Claude Code · Codex (harness AGENTS.md + MCP approver, ≥ v0.120 cho duyệt kiểu chat) |

## Đóng góp

Toàn bộ test **chạy không cần database** — các fake object chứng minh những
tính chất an toàn quan trọng nhất: snapshot luôn có trước khi ghi, `deny`
không đụng vào gì, mã xác nhận cũ bị từ chối. Xem
[CONTRIBUTING.md](CONTRIBUTING.md).

MIT — xem [LICENSE](LICENSE).
