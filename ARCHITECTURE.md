# L3A Architecture Record

Tài liệu này ghi lại kiến trúc hệ thống Multi-Agent của nhóm phục vụ kỳ thi Day09 V2 (K4 L3A — Multi-Agent MCP + A2A). Mọi quyết định đều có thể kiểm chứng độc lập từ mã nguồn và trace nhật ký.

---

## 1. System overview

Hệ thống được thiết kế theo mô hình Directed Acyclic Graph (DAG) phối hợp đa tác tử (Agent-to-Agent):

```text
Inputs (<case_id>.json)
         │
         ▼
 ┌───────────────┐
 │  Coordinator  │ (Emit: case_received, task_assigned)
 └───────┬───────┘
         │
         ├───► Policy Agent ───────► MCP: get_policy (Emit: tool_result_consumed, policy_decided)
         │           │
         │           ▼ (handoff)
         ├───► Order Agent ────────► MCP: get_order, get_order_items, get_sellers
         │           │
         │           ▼ (handoff)
         ├───► Payment Agent ──────► MCP: get_order_payments, get_payment_timeline, get_refund_timeline
         │           │
         │           ▼ (handoff)
         ├───► Shipment Agent ─────► MCP: get_shipment_summary
         │           │
         │           ▼ (handoff)
         ├───► Investigation Agent ─► Tổng hợp chứng cứ & đối chiếu Policy (primary_issue, root cause)
         │           │
         │           ▼ (handoff)
         └───► Verifier Agent ─────► Rà soát Invariants & Public JSON Schema
                     │
                     ▼
         Outputs (<case_id>.json) + Traces (trace.jsonl) (Emit: verification_completed, case_finalized)
```

---

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Tools được phép gọi | Output / Handoff |
| :--- | :--- | :--- | :--- | :--- |
| **Coordinator** | `inputs/<case_id>.json` | Tiếp nhận case, khởi tạo trace lifecycle, điều phối phân rã tác vụ cho các specialist agent, finalize output. | Không gọi MCP tools trực tiếp | Handoff sang Policy Agent |
| **Policy Agent** | `case_id`, `policy_version` | Thu thập và nạp quy tắc chính sách bồi hoàn có thẩm quyền từ máy chủ MCP. | `get_policy` | Policy rules, Handoff sang Order Agent |
| **Order Agent** | `case_id`, `order_id` | Xác minh trạng thái đơn hàng (order_status), danh sách item và seller thực tế. | `get_order`, `get_order_items`, `get_sellers` | Order/Item/Seller facts, Handoff sang Payment Agent |
| **Payment Agent** | `case_id`, `order_id` | Phân tích các giao dịch thẻ, voucher, dòng thời gian thanh toán, phát hiện trùng lặp (duplicate) hoặc lệch đối soát (reconciliation mismatch), trạng thái hoàn tiền. | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` | Payment timeline & Refund status, Handoff sang Shipment Agent |
| **Shipment Agent** | `case_id`, `order_id` | Xác định thời hạn giao hàng (shipping_limit_date), ngày bàn giao carrier, phát hiện chậm trễ do shipper hay seller. | `get_shipment_summary` | Logistics audit events, Handoff sang Investigation Agent |
| **Investigation Agent** | Toàn bộ factual facts thu thập được | Tổng hợp, phân loại chính xác `primary_issue` (11 enum chuẩn), đánh giá từng claim, tính toán mức hoàn tiền BRL và xác định bên chịu trách nhiệm. | Không gọi thêm tool ngoài phạm vi | Đề xuất output sơ bộ, Handoff sang Verifier |
| **Verifier** | Đề xuất output sơ bộ | Kiểm định các ràng buộc logic bất biến (Invariants) và xác thực JSON Schema Draft 2020-12. | Không gọi tool | Output chính thức (`outputs/<case_id>.json`) |

---

## 3. A2A protocol

- **Message Envelope**: Mọi thông điệp và sự kiện trao đổi giữa các agent đều được gắn kèm metadata bao gồm `case_id`, `occurred_at` (chuẩn UTC ISO-8601 `Z`), `actor`, `target`, `decision_code`, và `evidence_refs`.
- **Correlation**: `case_id` là khóa định danh duy nhất xuyên suốt toàn bộ vòng đời tác vụ. Bằng chứng thu thập trong case này bị cô lập hoàn toàn, không được phép chuyển giao chéo sang case khác.
- **Handoff Conditions**: Việc chuyển giao chỉ diễn ra sau khi Specialist Agent hoàn thành việc truy vấn công cụ và ghi nhận đầy đủ sự kiện `tool_result_consumed`.
- **Anti-loop**: Quy trình tuân thủ cấu trúc DAG tuyến tính nghiêm ngặt (`Coordinator` → `Policy` → `Order` → `Payment` → `Shipment` → `Investigation` → `Verifier` → `Coordinator`), ngăn chặn hoàn toàn khả năng lặp vô tận.
- **Timeout**: Timeout kết nối HTTP là 30 giây, timeout tác vụ tổng thể là 300 giây.

---

## 4. Evidence lifecycle

1. **Truy vấn & Xác thực**: Khi Specialist Agent gọi `gateway.call(...)`, kết quả nhận về phải được validate theo schema chuẩn `mcp-evidence-response-v1.schema.json`.
2. **Lưu trữ có thẩm quyền**: Thu thập và ghi nhận chính xác `evidence_ref` do MCP Gateway cấp phát (ví dụ: `ev_...`). Hệ thống **tuyệt đối không bịa đặt hoặc suy diễn `evidence_ref`**.
3. **Audit Tracing**: Ngay khi nhận kết quả, Agent phát sinh sự kiện `tool_result_consumed` ghi vào file `traces/trace.jsonl` cùng danh sách `evidence_refs` tương ứng.
4. **Ánh xạ vào Output**: Chỉ những bằng chứng thực sự hỗ trợ cho kết luận cuối cùng mới được đưa vào mảng `evidence_refs` của output JSON.

---

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event / Decision code |
| :--- | :--- | :--- | :--- |
| **MCP Timeout** | Retry tối đa 2 lần với exponential backoff (1s, 2s). | Ghi nhận lỗi kết nối, chuyển sang đánh giá bằng chứng hiện có. | `decision_code="MCP_TIMEOUT_FALLBACK"` |
| **Not Found / 404** | Không retry (vì query idempotent và dữ liệu không tồn tại). | Coi tập dữ liệu là rỗng (ví dụ: `get_refund_timeline` rỗng nghĩa là chưa có yêu cầu hoàn tiền nào). Không bịa đặt dữ liệu. | `decision_code="MCP_ENTITY_NOT_FOUND"` |
| **Source Conflict** | Không retry. | Đưa vào danh sách `data_conflicts` trong output JSON với nguồn dữ liệu MCP được ưu tiên làm ground truth (`selected_source="mcp_..."`). | `decision_code="CONFLICT_RESOLVED"` |
| **Invalid Specialist Result** | Thẩm định lại 1 lần nội bộ qua Verifier. | Nếu vi phạm schema hoặc thiếu bằng chứng cốt lõi, đưa về trạng thái `insufficient_evidence` hoặc `unsupported_claim`. | `decision_code="VERIFICATION_FAILED_FALLBACK"` |

---

## 6. Verification invariants

Trước khi chấp thuận và lưu file `outputs/<case_id>.json`, Verifier kiểm tra bắt buộc các điều kiện sau:

1. **Schema Compliance**: Đạt 100% kiểm tra xác thực theo `contracts/schemas/l3a-output-v2.schema.json`.
2. **Entity Scope**: Mọi `order_id`, `item_id`, `seller_id` trong `affected_entities` phải thuộc đúng case đang xử lý.
3. **Evidence Provenance**: Mọi `evidence_ref` xuất hiện trong `claim_assessments` và `evidence_refs` phải nằm trong danh mục bằng chứng được audit bởi MCP Gateway trong chính case đó.
4. **Status & Financial Consistency**:
   - Nếu `case_status == "no_action"`, thì `recommended_refund_brl == 0.0` và `refund_lines` phải rỗng (`[]`).
   - Nếu `case_status == "action_required"` và có hoàn tiền, tổng tiền trong `refund_lines` phải bằng `recommended_refund_brl`.
5. **Responsible Party Alignment**: Nếu bên chịu trách nhiệm là `seller`, thì `party_id` phải khớp với `seller_id` thực tế của đơn hàng.
6. **Confidence Bounds**: Giá trị `confidence` nằm trong khoảng `[0.0, 1.0]`.

---

## 7. Reproducibility

- **Môi trường & Ngôn ngữ**: Python 3.11.9 (64-bit).
- **Thư viện cốt lõi**: `mcp>=2.0`, `httpx2>=2.13.1`, `jsonschema>=4.25`.
- **Cấu hình mô hình**: Kiến trúc Multi-Agent phân cấp logic với mô hình dưới 10B parameters (hỗ trợ OpenAI-compatible API qua Ollama / Groq / OpenRouter).
- **Concurrency & Resource Limit**: Single worker concurrency đảm bảo toàn vẹn tính tuần tự của audit log và không làm nghẽn MCP Gateway.
- **Lệnh thực thi**:
  ```bash
  python -m student_agent.cli run
  python -m student_agent.cli validate
  python -m student_agent.cli package --output dist/submission.zip
  ```
