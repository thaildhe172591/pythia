<p align="center">
  <img src="https://raw.githubusercontent.com/thaildhe172591/pythia/main/assets/logo.png" alt="pythia" width="280" />
</p>

# pythia

> MCP của Oracle cho agent một kết nối. **pythia cho nó sự phán đoán để dùng kết nối đó.**

[English](README.md) · **Tiếng Việt**

[![ci](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml/badge.svg)](https://github.com/thaildhe172591/pythia/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.9%2B-blue)

Bộ kit Agent Skills + CLI để phát triển PL/SQL trên Oracle Database cùng AI
coding agent (Claude Code, Codex, Cursor — bất kỳ agent nào trong 76 agent mà
`npx skills` hỗ trợ). Khám phá schema quá lớn để dump, đo bán kính ảnh hưởng
**trước khi** chạm vào bất cứ thứ gì, và đưa thay đổi vào database qua một
đường ghi có snapshot kiểm chứng — không bao giờ nói dối về khả năng rollback.

## Vì sao hỏi database thay vì đọc dump

Một hệ thống cỡ vừa có thật, so repo export với database đang chạy, audit năm 2026:

| Loại object | Trong dump | Trong database | Kết luận |
|---|---|---|---|
| Procedure | 3.827 | 3.827 | khớp |
| Table | 952 | 952 | khớp |
| **Type** | **0** | **115** | **thiếu toàn bộ** |
| **Package** | **0** | **9** | **thiếu toàn bộ** |
| **Index** | **116** | **1.016** | **thiếu ~89%** |

Code "đọc thấy ổn" trên dump lại tham chiếu đến type và package mà dump chưa
từng biết. Mọi lệnh pythia hỏi thẳng data dictionary đang sống — và mọi output
bị cắt bớt đều tự khai báo, để agent không bao giờ nhầm câu trả lời thiếu
thành câu trả lời đủ.

## Cách hoạt động

```
developer trò chuyện với agent
        │
skills/  dạy agent khi nào hỏi DB, khi nào dừng, khi nào hỏi BẠN
        │
pythia   CLI — truy vấn chuyên gia, phân tích ảnh hưởng, đường ghi 6 bước
        │
Oracle   data dictionary: ALL_SOURCE, ALL_DEPENDENCIES, ALL_ERRORS, PL/Scope
```

Trái tim là đường ghi: **snapshot → impact → preview → apply → verify → report**.
DDL trong Oracle tự commit — snapshot là cách hoàn tác thật duy nhất, nên nó
luôn chạy trước và không cờ nào tắt được. Token 6-hex trói lần ghi vào đúng
nội dung đã preview; exit code làm sự trung thực đọc được bằng máy
(`0` sạch · `1` từ chối · `3` **đã ghi nhưng hỏng — không bao giờ báo thành công**).

## Cài đặt

```bash
npx pythia-plsql           # tất cả: pip install + chọn agent cài skills + tạo config
```

Hoặc từng bước một, cùng kết quả:

```bash
pip install pythia-plsql   # CLI (thin driver — không cần Oracle Instant Client)
pythia install             # skills vào agent + tạo sẵn .pythia/connections.json
pythia check               # điền connections.json trước, rồi kiểm tra
```

Gói pip là trọn bộ kit: có Node.js thì `pythia install` chạy `npx skills add`
(77 agent, cập nhật qua symlink; `--source <git-url>` cho mirror nội bộ) —
không có Node thì nó tự copy bộ skills đóng gói sẵn vào `.claude/skills/` và
`.agents/skills/`. Chỉ cần skills: `npx skills add thaildhe172591/pythia`,
hoặc `/plugin marketplace add thaildhe172591/pythia`.

### Máy cài một lần — mỗi dự án chạy một lần

`pip install` là **mỗi máy một lần**; `pythia install` là **mỗi dự án một
lần** — đứng ở thư mục gốc của từng repo mà chạy, nó thả skills và một
`.pythia/connections.json` mới vào đúng repo đó:

```bash
cd du-an-A && pythia install    # điền DB của A → pythia check
cd du-an-B && pythia install    # điền DB của B → pythia check
```

CLI luôn đọc config của dự án bạn đang đứng (tìm ngược từ thư mục hiện tại
lên, **không có fallback global**) — nên một CLI cài chung không bao giờ lẫn
database giữa các dự án. Đứng ngoài mọi dự án thì báo lỗi rõ ràng, không đoán.

Chạy từ bản clone cũng được — `python scripts/pythia.py <lệnh>`; mọi lệnh
gợi ý in ra luôn khớp với cách bạn đã gọi. Windows, macOS, Linux và WSL đều
được CI kiểm thử.

## Các lệnh

| Đọc | Hiểu | Ghi |
|---|---|---|
| `check` kết nối + đếm object | `deps` nó phụ thuộc gì | `apply` đường ghi 6 bước |
| `ls` tìm object | `impact` cái gì phụ thuộc nó | `journal` list · diff · export · restore |
| `src` source, số dòng compiler | `errors` lỗi biên dịch, dòng:cột | `policy` show · set |
| `args` chữ ký thủ tục | `invalid` mọi thứ đang hỏng | `unistr` literal tiếng Việt chuẩn xác |
| `ddl` qua DBMS_METADATA | `plscope` vị trí dùng identifier | |
| `cols` cột + kiểu dữ liệu | `similar` chương trình đặt tên giống | |
| `grep` tìm trong toàn bộ source | | |
| `sql` truy vấn tự do (chỉ SELECT/WITH) | | |

Lệnh nào cũng nhận `--json` (output cho máy), `--conn` (chọn kết nối), và giới
hạn output kèm đánh dấu cắt bớt rõ ràng để không nuốt trọn context window.

**House style là config, không phải truyền miệng**: đặt pattern đặt tên vào
`.pythia/conventions.json` — preview của apply sẽ cảnh báo khi tên object mới
lệch chuẩn; đặt luật văn xuôi vào `.pythia/conventions.md` — skills bắt mọi
agent đọc nó trước tiên (`pythia conventions` hiển thị cả hai).

## Bảo mật & chính sách ghi

**Tài khoản DB mới là lớp bảo vệ thật** — file policy chỉ là hàng rào phía
ứng dụng. Cấp cho agent một credential riêng, thu hồi được, qua proxy
authentication (`agent_user[schema_owner]`, không quyền `ANY`, không chia sẻ
mật khẩu owner): xem
[`examples/agent-user-setup.example.sql`](examples/agent-user-setup.example.sql).
`pythia check` cảnh báo khi phiên chạy với quyền cao hơn mức việc cần.

Chính sách ghi theo nhóm, `.pythia/policy.json` (giá trị mặc định):

| Nhóm | Mặc định | Rollback có thật không? |
|---|---|---|
| `plsql_source` | `confirm` | **Có — hoàn toàn.** Source khôi phục được từ `ALL_SOURCE`. |
| `data_dml` | `deny` | **Không.** Sau commit chỉ còn Flashback Query, trong giới hạn undo retention. |
| `structural` | `deny` | **Gần như không bao giờ.** `DROP COLUMN` là vĩnh viễn; bảng bị drop may ra còn trong Recycle Bin. |
| `grants` | `deny` | Có, nhưng phải làm tay. |
| `session` | `allow` | Không cần. |

Nhóm nào không snapshot được thì mặc định `deny` — và lời từ chối nói đúng lý
do đó, thay vì "policy cấm". Block PL/SQL ẩn danh bị từ chối thẳng. Câu lệnh
không nhận diện được thì từ chối, không bao giờ đoán bừa vào nhóm nào.

Phần đọc có thể đi qua SQLcl MCP server chính thức của Oracle (`sql -mcp`,
giữ `-R 4`); nó ghi audit mọi tương tác vào `DBTOOLS$MCP_LOG`. **Phần ghi thì
không bao giờ** — chỉ `pythia apply` có snapshot, preview, verify và journal.

## Skills

Bảy skill dạy agent quy trình — là cổng chặn kiểu superpowers, không phải gợi ý:

`plsql-setup` · `plsql-explore` · `plsql-impact` (đo ảnh hưởng **trước** mọi
thay đổi) · `plsql-write` (copy convention của codebase) · `plsql-apply`
(cổng chặn: developer thấy preview và duyệt trong chat trước khi bất cứ thứ
gì được ghi) · `plsql-review` (bảy antipattern) · `plsql-skill-author` (đóng
gói cách làm việc *của team bạn* thành skill mới, đào từ schema đang sống).

## Tương thích

| | |
|---|---|
| Hệ điều hành | Windows, macOS, Linux, WSL — ma trận test đầy đủ trong CI |
| Python | 3.9+ · chỉ stdlib + `python-oracledb` (thin mode) |
| Oracle | phần lõi chạy rộng rãi; bắt statement PL/Scope cần 12.2+; chỉ dùng view an toàn về license |
| Agent | mọi agent của `npx skills` (76) · plugin Claude Code native |

## Đóng góp

Test **không cần database** — các fake chứng minh những tính chất an toàn
(snapshot trước khi ghi, deny không chạm gì, token cũ bị từ chối). Xem
[CONTRIBUTING.md](CONTRIBUTING.md).

MIT — xem [LICENSE](LICENSE).
