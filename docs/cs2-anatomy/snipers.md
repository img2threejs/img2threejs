# CS2 Item Anatomy Vocabulary — Snipers (Súng ngắm / Súng bắn tỉa)

> Bộ từ vựng song ngữ để **gọi đúng tên từng bộ phận của súng ngắm** khi mô tả lỗi fidelity trong pipeline img2threejs.
> Quy tắc dùng: **geometry/hình khối sai** → dùng tên bộ phận vật lý (mục 1–3). **Texture/màu/pattern sai** → dùng thuật ngữ skin CS2 (mục 4).
> Đặc điểm định danh của dòng này là **ống ngắm (scope/optic)** — nên nó có mục riêng (mục 2).
>
> Nguồn: research NotebookLM (fast mode, 10 nguồn) — Liquipedia (SSG 08), strafe.com (AWP deep dive), Leupold Tactical Riflescope Manual + Rugged Suppressors (anatomy nòng/ngắm); Shephard Infantry Equipment Handbook, H&K G3 Wikipedia, vi.wikipedia (cơ cấu lên đạn thủ công), GunSkins (bolt-action vs semi-auto); counter-strike.net Workshop Finishes + ProSettings (skin & finish CS2), + kiến thức chuẩn về súng cho phần bị thiếu.
> Notebook: `CS2 Item Anatomy Vocabulary — Snipers (VN+EN)` (`e20b3968`).
> _Lưu ý: cả 10 nguồn import thành công; chỗ không có nguồn phủ được đánh dấu [kiến thức chung]._

---

## 1. Giải phẫu súng ngắm tổng quát — Sniper / precision-rifle anatomy

### A. CƠ CẤU & NÒNG (Action & Barrel)

| English | VN | Định nghĩa |
|---|---|---|
| **Receiver** (frame) | Hộp khóa nòng | Thân trung tâm chứa các bộ phận vận hành bên trong của súng. |
| **Action** (operating system) | Cơ cấu lên đạn | Hệ cơ khí lo việc nạp, bắn và tống vỏ đạn. |
| **Bolt** (breechblock) | Khối khóa nòng | Bộ phận trượt trong hộp khóa nòng để lên đạn và bịt kín buồng đạn. |
| **Bolt handle** (cocking handle) | Cần kéo khóa nòng | Cần kim loại gắn vào khối khóa nòng để lên đạn thủ công. |
| **Bolt knob** (handle tip) | Núm kéo khóa nòng | Núm ở đầu cần kéo khóa nòng để tay bám khi giật. |
| **Charging handle** (cocking lever) | Cần lên đạn | Cần kéo lùi khối khóa nòng / bệ khóa nòng trên súng bán tự động. |
| **Bolt-action** (manual action) | Cơ cấu lên đạn thủ công | Hệ phải kéo khóa nòng thủ công cho từng phát bắn (SSG 08, AWP). |
| **Semi-automatic** (self-loading) | Súng bán tự động | Hệ dùng năng lượng giật/khí thuốc tự lên đạn sau mỗi phát (SCAR-20, G3SG1). |
| **Barrel** (tube) | Nòng súng | Ống thép dẫn viên đạn khi bắn. |
| **Fluted barrel** (grooved barrel) | Nòng xẻ rãnh | Nòng có rãnh xẻ dọc để giảm nặng và tăng diện tích tản nhiệt. |
| **Free-floating barrel** | Nòng treo tự do | Nòng không chạm ốp/báng, tránh nhiễu để tăng độ chính xác. |
| **Heavy barrel** (bull barrel) | Nòng hạng nặng | Nòng dày, cứng, giảm rung và chịu nhiệt khi bắn liên tục. |
| **Muzzle** (bore exit) | Đầu nòng / miệng nòng | Đầu trước nòng, nơi đạn thoát ra. |
| **Muzzle brake** (compensator) | Bộ hãm giật đầu nòng | Chuyển hướng khí thuốc để giảm giật cảm nhận. |
| **Flash hider** (flash suppressor) | Bộ che lửa đầu nòng | Gắn ở miệng nòng, giảm tia lửa nhìn thấy khi bắn. |
| **Threaded muzzle** (threaded tip) | Đầu nòng có ren | Miệng nòng có ren để gắn giảm thanh / phụ kiện đầu nòng. |

### B. BÁNG & TAY CẦM (Stock & Grip)

| English | VN | Định nghĩa |
|---|---|---|
| **Buttstock** (shoulder stock) | Báng súng | Phần sau súng tì vào vai để đỡ. |
| **Adjustable stock** (modular stock) | Báng điều chỉnh được | Báng có phần chỉnh kích thước theo cơ thể người bắn. |
| **Folding stock** (collapsible stock) | Báng gấp | Báng gấp/thu lại để súng gọn khi mang (SSG 08). |
| **Skeletonized stock** (hollow stock) | Báng dạng khung xương | Báng khoét bớt vật liệu cho nhẹ mà vẫn cứng (SSG 08). |
| **Cheek riser** (cheek piece) | Ốp má | Phần nâng trên báng đỡ đầu người bắn để căn mắt vào ngắm. |
| **Adjustable comb** (riser plate) | Ốp má chỉnh được | Ốp má chỉnh cao/thấp để căn mắt với ống ngắm gắn cao. |
| **Recoil pad** (buttpad) | Đệm báng súng | Đệm mềm ở đuôi báng để hấp thụ lực giật. |
| **Pistol grip** (main grip) | Tay nắm chính | Tay cầm dọc cho tay thuận đỡ súng và bóp cò (SCAR-20, G3SG1). |
| **Trigger** (firing lever) | Cò súng | Cần bóp để nhả cơ cấu kích hỏa. |
| **Trigger guard** (safety loop) | Vành bảo vệ cò | Vòng bao quanh cò, chống cướp cò ngoài ý muốn. |
| **Two-stage trigger** (precision trigger) | Cò hai giai đoạn | Cò có đoạn "take-up" đầu rồi mới tới điểm nhả dứt khoát. |

### C. TIẾP ĐẠN & RAY (Feeding & Rails)

| English | VN | Định nghĩa |
|---|---|---|
| **Magazine** (clip) | Hộp tiếp đạn / băng đạn | Hộp chứa đạn, đẩy đạn vào cơ cấu. |
| **Magazine well** (magwell) | Hốc cắm hộp tiếp đạn | Khe trên hộp khóa nòng để lắp và khóa hộp tiếp đạn. |
| **Box magazine** (detachable magazine) | Hộp tiếp đạn dạng khối | Hộp đạn hình chữ nhật, tháo rời (SCAR-20, G3SG1). |
| **Handguard** (forend) | Ốp lót tay | Phần bọc trước súng, bảo vệ tay khỏi nòng nóng. |
| **Picatinny rail** (tactical rail) | Ray Picatinny | Ray chuẩn để gắn ống ngắm, đèn/laser, phụ kiện. |
| **Bipod** (support stand) | Chân chống hai chân | Chân hai càng để tì súng lên mặt phẳng cho ổn định (G3SG1). |
| **Sling swivel** (sling mount) | Vòng khoen dây đeo | Điểm gắn dây đeo để mang súng. |
| **Ejection port** (shell window) | Cửa hất vỏ đạn | Khe trên hộp khóa nòng để văng vỏ đạn đã bắn (súng bán tự động). |
| **Dust cover** (action cover) | Nắp che bụi | Nắp/vạt che chống bụi lọt vào cơ cấu bên trong. |

---

## 2. Ống ngắm — Riflescope / Optic (đặc điểm định danh của dòng sniper)

Đây là bộ phận nổi bật nhất; sai hình khối ống ngắm thường là lỗi fidelity dễ thấy nhất.

| English | VN | Định nghĩa |
|---|---|---|
| **Objective lens** (front lens) | Vật kính | Thấu kính đầu ống, gom sáng và tạo ảnh ban đầu của mục tiêu. |
| **Objective bell** (front housing) | Đầu loe vật kính | Phần loe trước của thân ống, chứa vật kính. |
| **Ocular lens / eyepiece** (eyepiece lens) | Thị kính | Cụm thấu kính sau ống, gần mắt, phóng đại ảnh. |
| **Scope tube / main tube** (body tube) | Thân ống chính | Ống trụ giữa, chứa cụm thấu kính đảo ảnh và cơ cấu bên trong. |
| **Elevation turret** (elevation adjustment) | Núm chỉnh cao độ | Núm trên đỉnh, chỉnh dọc tâm ngắm để bù độ rơi đạn. |
| **Windage turret** (windage adjustment) | Núm chỉnh hướng gió | Núm bên hông, chỉnh ngang tâm ngắm để bù gió tạt. |
| **Parallax adjustment / side focus** (side parallax dial) | Núm chỉnh thị sai | Chỉnh cho ảnh mục tiêu và tâm ngắm về cùng mặt phẳng tiêu cự. |
| **Magnification ring / power ring / zoom ring** (power selector) | Vòng chỉnh độ phóng đại | Vòng xoay đổi mức phóng đại (ống ngắm biến đổi). |
| **Reticle / crosshair** (aiming point) | Tâm ngắm / vạch chữ thập | Hoa văn/đường giao bên trong để căn súng với mục tiêu. |
| **Scope mount** (base) | Gá ống ngắm / chân đế | Bộ gá cố định ống ngắm lên hộp khóa nòng hoặc ray. |
| **Scope rings** (mounting rings) | Vòng khuyên giữ ống ngắm | Kẹp tròn ôm thân ống, siết ống vào gá/ray. |
| **One-piece mount** (uni-mount) | Chân đế nguyên khối | Một khối gá liền tích hợp cả hai vòng, tăng độ đồng trục & vững. |
| **Sunshade / lens hood** (glare shield) | Ống che nắng | Ống nối dài trước vật kính, giảm lóa và bảo vệ thấu kính. |
| **Eye relief** (eye distance) | Khoảng đặt mắt | Khoảng cách từ thị kính tới mắt để thấy trọn ảnh ngắm rõ. |
| **Diopter / eyepiece focus ring** (diopter lock ring) | Vòng lấy nét thị kính | Chỉnh trên thị kính để lấy nét tâm ngắm theo mắt người bắn. |
| **Illumination control / brightness knob** (illumination dial) | Núm chỉnh đèn tâm ngắm | Núm/nút bật và chỉnh độ sáng của tâm ngắm phát sáng. |

---

## 3. Nhận diện 4 khẩu sniper CS2

- **SSG 08** (Scout) — Súng ngắm bolt-action hạng nhẹ — **cơ cấu lên đạn thủ công**, dáng mảnh, **báng khung xương/gấp**, ống ngắm gọn gắn ray Picatinny. Nhẹ nhất trong nhóm.
- **AWP** — Arctic Warfare bolt-action hạng nặng — **cơ cấu lên đạn thủ công**, nặng và cồng kềnh, **thân xanh olive** đặc trưng, **ống ngắm to độ phóng đại cao**, cần kéo khóa nòng bên phải, nòng dày.
- **SCAR-20** — Súng ngắm bán tự động (nền FN SCAR-H) — **không có cần kéo khóa nòng**, nặng, **hộp khóa nòng dáng khối**, tay nắm chính (pistol grip), hộp tiếp đạn khối, ray gắn ống ngắm, báng cố định.
- **G3SG1** — Súng ngắm bán tự động (nền H&K G3) — **không có cần kéo khóa nòng**, dài & nặng, **ốp lót tay dài có chân chống (bipod) tích hợp**, hộp tiếp đạn khối, ống ngắm chuyên dụng (Hensoldt/Zeiss).

_Phân biệt cốt lõi: **có cần kéo khóa nòng (bolt handle) = bolt-action** (SSG 08, AWP); **không có, có cửa hất vỏ = bán tự động** (SCAR-20, G3SG1)._

---

## 4. Thuật ngữ SKIN / FINISH CS2 → map lên bộ phận sniper

Dùng khi **texture/màu/pattern** sai (không phải hình khối).

### Phân biệt phần được sơn
| English | VN | Nghĩa & vị trí |
|---|---|---|
| **Vanilla / stock** (default) | Mặc định / nguyên bản | Súng không skin, giữ diện mạo gốc — toàn bộ súng. |
| **Painted parts** (paintable areas) | Bộ phận được sơn | Vùng nhận màu/họa tiết skin — **hộp khóa nòng, nòng, báng, ốp lót tay**. |
| **Bare-metal parts** (unpainted regions) | Kim loại trần | Chi tiết giữ màu gốc — **khối khóa nòng, cò**; và **ống ngắm thường KHÔNG được skin sơn**. |

### Pattern
| English | VN | Nghĩa & vị trí |
|---|---|---|
| **Pattern seed** (pattern index) | Chỉ số hoa văn | Số ngẫu nhiên định vị/xoay/căn bản đồ vân trên súng — receiver, nòng, báng. |

### Kiểu finish
| English | VN | Đặc trưng & vị trí |
|---|---|---|
| **Solid Color** (paint by number) | Màu đơn | Tối đa 4 màu áp lên các vùng định sẵn của súng. |
| **Hydrographic** (hydro-dipping) | Sơn nhúng nước | Phim họa tiết quấn quanh bề mặt cong — rõ nhất trên nòng & báng. |
| **Anodized** (candy coat) | Mạ anode | Lớp "candy coat" màu phủ trên nền chrome, giữ ánh kim. |
| **Spray-Paint** (stencil) | Sơn xịt | Áp qua khuôn tô, triplanar mapping — chi tiết thô. |
| **Patina** (forced aging) | Lên màu/oxy hóa | Đổi màu kim loại theo phản ứng hóa học (case-harden, bluing) — receiver & nòng. |
| **Gunsmith** (mixed media) | Chế tác thủ công | Patina trên **phần kim loại** + sơn tùy chỉnh trên **báng/ốp** furniture. |
| **Fade** (gradient) | Chuyển màu / fade | Gradient trên nền anode/chrome chạy dọc nòng & receiver. |
| **Custom Paint Job** (graphic art) | Sơn tùy chỉnh | Đồ họa chi tiết cao áp thẳng lên bản đồ UV của súng. |

### Wear / Float
| English | VN | Nghĩa & vị trí mòn |
|---|---|---|
| **Float value** (wear index) | Chỉ số mài mòn | 0.00–1.00, mức hư hại thị giác trên toàn súng. |
| **Factory New (FN)** | Mới xuất xưởng | 0.00–0.07, gần như không xước, sắc nét nhất. |
| **Minimal Wear (MW)** | Ít trầy xước | 0.07–0.15, vài xước cực nhỏ, gần như mới. |
| **Field-Tested (FT)** | Đã qua sử dụng | 0.15–0.38, xước & bong sơn vừa phải. |
| **Well-Worn (WW)** | Mòn nhiều | 0.38–0.45, mất sơn & xỉn nặng khắp súng. |
| **Battle-Scarred (BS)** | Trầy xước nặng | 0.45–1.00, mất finish gốc gần hết, oxy hóa tối đa. |
| **Wear high points** | Điểm dễ mòn | Chịu tác động trước: **đầu nòng, cạnh nòng, cạnh báng, hộp tiếp đạn, gờ cao receiver, cần kéo khóa nòng**. |

---

## Ghi chú dùng cho debug fidelity

- **Sai hình khối** → mục 1–3: vd "ống ngắm AWP chưa đủ to / thiếu đầu loe vật kính", "SSG 08 thiếu báng khung xương", "SCAR-20 vẽ nhầm cần kéo khóa nòng (phải bỏ vì là bán tự động)", "G3SG1 thiếu bipod tích hợp dưới ốp lót tay", "nòng AWP chưa đủ dày".
- **Sai ống ngắm** → mục 2: vd "sai tỉ lệ vật kính/thị kính", "thiếu núm chỉnh cao độ/hướng gió (turret)", "vòng chỉnh độ phóng đại đặt sai chỗ", "gá/vòng khuyên giữ ống ngắm sai".
- **Sai texture/màu** → mục 4: vd "pattern seed lệch làm vân trên receiver/báng sai vị trí", "wear đặt giữa nòng thay vì đầu nòng/cạnh/high points".
- **Bare metal & ống ngắm**: khối khóa nòng, cò — và **ống ngắm** — thường KHÔNG được skin sơn; nếu model sơn cả họa tiết skin lên ống ngắm là sai.

_Đã xong: [dao](knives.md), [pistol](pistols.md), sniper. Loại tiếp theo: SMG / rifle / gloves — mỗi loại một file trong `docs/cs2-anatomy/`._
