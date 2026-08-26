<p align="center">
  <img src="https://raw.githubusercontent.com/thaildhe172591/pythia/main/assets/logo.png" alt="pythia" width="280" />
</p>

# pythia

> MCP của Oracle cho agent một kết nối tới database.
> **pythia dạy nó dùng kết nối đó cho đúng.**

[English](README.md) · **Tiếng Việt** · **[Hướng dẫn chi tiết →](GUIDE.vi.md)**

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

## Hoạt động thế nào

```
dev trao đổi với agent
        │
skills/  dạy agent: khi nào hỏi DB, khi nào dừng lại, khi nào phải hỏi BẠN
        │
pythia   CLI — truy vấn chuyên sâu, phân tích ảnh hưởng, quy trình ghi 6 bước
        │
Oracle   data dictionary: ALL_SOURCE, ALL_DEPENDENCIES, ALL_ERRORS, PL/Scope
```

Phần quan trọng nhất là quy trình ghi:
**snapshot → impact → preview → apply → verify → report**.

Trong Oracle, DDL tự commit — không có `ROLLBACK` nào cứu được. Vì vậy
snapshot luôn chạy đầu tiên, và không cờ nào tắt được nó. Mã xác nhận 6 ký
tự hex gắn chặt lần ghi vào đúng nội dung bạn đã xem ở preview. Exit code
nói thẳng kết quả: `0` sạch · `1` bị từ chối · `3` **đã ghi nhưng hỏng —
tuyệt đối không được báo là thành công**.

## Cài đặt

Một lệnh, xong tất cả:

```bash
npx pythia-plsql
```

Hoặc làm từng bước — nên dùng cách này vì rõ ràng hơn:

```bash
pip install pythia-plsql        # 1. CLI (thin driver, không cần Oracle Instant Client)
pythia install -g               # 2. skills — MỘT LẦN cho cả máy
cd du-an && pythia install      # 3. mỗi dự án: tạo .pythia/connections.json
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

## Các lệnh

| Đọc | Phân tích | Ghi |
|---|---|---|
| `check` kết nối + đếm object | `impact` cái gì phụ thuộc vào nó | `apply` quy trình ghi 6 bước |
| `ls` tìm object theo tên | `deps` nó phụ thuộc vào cái gì | `journal` list · diff · export · restore |
| `src` source, đúng số dòng compiler | `errors` lỗi biên dịch kèm dòng:cột | `policy` xem · sửa |
| `args` tham số của procedure | `invalid` mọi object đang hỏng | `unistr` chuỗi tiếng Việt chuẩn |
| `cols` cột và kiểu dữ liệu | `plscope` chỗ nào dùng identifier này | `agent-user` tạo user least-privilege |
| `ddl` lấy DDL qua DBMS_METADATA | `similar` chương trình tên tương tự | `history` các bản đã chụp |
| `grep` tìm chuỗi trong toàn bộ source | | |
| `sql` truy vấn tự do (chỉ SELECT/WITH) | | |

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

Chính sách ghi chia theo nhóm trong `.pythia/policy.json` (mặc định):

| Nhóm | Mặc định | Có rollback thật không? |
|---|---|---|
| `plsql_source` | `confirm` | **Có, hoàn toàn** — lấy lại source từ `ALL_SOURCE`. |
| `data_dml` | `deny` | **Không.** Commit rồi thì chỉ còn Flashback Query, trong thời hạn undo retention. |
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

Bảy skill dạy agent quy trình làm việc. Đây là **luật bắt buộc, không phải
gợi ý** — agent phải đi qua chúng:

| Skill | Việc chính |
|---|---|
| `pythia-setup` | cấu hình kết nối, tạo user agent, chẩn đoán lỗi đăng nhập |
| `pythia-explore` | hỏi database để hiểu code, không đọc dump |
| `pythia-impact` | **bắt buộc chạy trước** khi đề xuất bất kỳ thay đổi nào |
| `pythia-write` | viết code theo đúng quy ước sẵn có của codebase |
| `pythia-apply` | dev phải xem preview và đồng ý trong chat trước khi ghi |
| `pythia-review` | review code theo 7 antipattern, chỉ rõ từng dòng |
| `pythia-skill-author` | biến cách làm việc của team bạn thành skill mới |

## Tương thích

| | |
|---|---|
| Hệ điều hành | Windows, macOS, Linux, WSL — CI test đủ cả ba họ |
| Python | 3.9+ · chỉ cần stdlib và `python-oracledb` (thin mode) |
| Oracle | phần lõi chạy được với hầu hết phiên bản; PL/Scope statement cần 12.2+; chỉ dùng view không vướng license |
| Agent | mọi agent mà `npx skills` hỗ trợ (76) · plugin Claude Code |

## Đóng góp

Toàn bộ test **chạy không cần database** — các fake object chứng minh những
tính chất an toàn quan trọng nhất: snapshot luôn có trước khi ghi, `deny`
không đụng vào gì, mã xác nhận cũ bị từ chối. Xem
[CONTRIBUTING.md](CONTRIBUTING.md).

MIT — xem [LICENSE](LICENSE).
