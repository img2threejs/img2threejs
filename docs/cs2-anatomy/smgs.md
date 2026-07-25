# CS2 Item Anatomy Vocabulary — SMGs (Súng ngắn liên thanh)

> Bộ từ vựng song ngữ để **gọi đúng tên từng bộ phận của súng ngắn liên thanh (SMG)** khi mô tả lỗi fidelity trong pipeline img2threejs.
> Quy tắc dùng: **geometry/hình khối sai** → dùng tên bộ phận vật lý (mục 1–2). **Texture/màu/pattern sai** → dùng thuật ngữ skin CS2 (mục 3).
>
> Nguồn: research NotebookLM (fast mode, 10 nguồn) — tecom.marines.mil (Field Medical handbook), scribd/studocu.vn (GDQP súng tiểu liên — thuật ngữ VN), heckler-koch.com (MP5), lums.edu.pk (FN P90 loading), sadefensejournal.com (PP-Bizon), pickem (MP7 vs MP9 CS2) cho anatomy & nhận diện; counter-strike.net (Workshop Finishes), cs.trade (float & pattern), steamcommunity "Redline for Nerds" cho skin/finish.
> Notebook: `CS2 Item Anatomy Vocabulary — SMGs (VN+EN)` (`f783b110`).
> _Lưu ý: một số bộ phận chuẩn không có trong nguồn được bổ sung & đánh dấu [kiến thức chung]._

---

## 1. Giải phẫu súng ngắn liên thanh tổng quát — General SMG anatomy

Áp dụng cho cả 7 khẩu SMG CS2 (MAC-10, MP9, MP7, MP5-SD, UMP-45, P90, PP-Bizon). Đa số SMG dùng cơ chế **blowback** (phản lực trực tiếp) và bắn từ bolt kín/hở.

### A. THÂN SÚNG & NÒNG (Receiver & Barrel)

| English | VN | Định nghĩa |
|---|---|---|
| **Receiver** / housing | Hộp khóa nòng / thân súng | Thân chính chứa bolt, cụm cò và giá nòng; khung tham chiếu của cả súng. |
| **Upper receiver** | Thân trên | Phần thân trên chứa bolt và đỡ nòng. |
| **Lower receiver** / grip frame | Thân dưới | Phần thân dưới chứa cụm cò và hốc cắm hộp tiếp đạn. |
| **Barrel** | Nòng súng | Ống thép dẫn viên đạn sau khi kích nổ. |
| **Muzzle** | Miệng nòng | Đầu trước nòng, nơi đạn thoát ra. |
| **Muzzle device** / flash hider, compensator | Thiết bị đầu nòng | Phụ kiện đầu nòng giảm chớp lửa hoặc ghì giật. |
| **Barrel shroud / jacket** (cooling holes/vents) | Áo nòng / ốp nòng (lỗ tản nhiệt) | Vỏ bọc quanh nòng, thường khoét lỗ để tản nhiệt & chống bỏng tay (rõ trên MAC-10, UMP-45). |
| **Suppressor** / integral silencer | Ống giảm thanh | Ống trụ bọc/nối miệng nòng để giảm tiếng — MP5-SD có **giảm thanh tích hợp** bao trọn nòng. |
| **Bolt / bolt carrier** | Khối khóa nòng / bệ khóa nòng | Cụm cơ khí lên đạn & chứa kim hỏa, trượt tới lui khi bắn. |
| **Blowback action** | Cơ chế phản lực (blowback) | Kiểu vận hành dùng áp suất khí đẩy bolt lùi để lên đạn kế tiếp. |
| **Charging handle** / cocking handle | Tay kéo khóa nòng / tay lên đạn | Tay cầm kéo bolt về sau để lên viên đầu hoặc thông kẹt. |
| **Ejection port** | Cửa hất vỏ đạn | Khe trên thân để văng vỏ đạn đã bắn (P90 hất **xuống dưới**). |
| **Extractor** | Móng văng / móc đạn | Móc trên mặt bolt kéo vỏ đạn ra khỏi buồng đạn. |

### B. BÁNG & TAY CẦM (Stock & Grip)

| English | VN | Định nghĩa |
|---|---|---|
| **Fixed stock** | Báng cố định | Báng không chỉnh, tối đa độ ổn định khi bắn. |
| **Folding stock** (side-folding, under-folding) | Báng gấp | Báng gấp sang bên/xuống dưới để gọn khi mang (MP9, UMP-45, PP-Bizon). |
| **Collapsible stock** / telescoping, retractable | Báng rút / báng kéo dài | Báng chỉnh chiều dài, trượt vào-ra (MP7, MAC-10). |
| **Wire stock** | Báng khung dây thép | Báng tối giản làm bằng thanh/dây kim loại (MAC-10). |
| **Buttplate** / buttpad | Đế báng | Mặt sau báng tì vào vai. |
| **Cheek rest** / cheek weld | Điểm tựa má | Phần trên báng để áp má căn ngắm. |
| **Pistol grip** | Tay cầm khai hỏa | Tay cầm chính của tay bóp cò. |
| **Grip stippling / texture** | Nhám tay cầm | Vân/nhám trên tay cầm chống trượt. |
| **Handguard** | Ốp lót tay | Vỏ che quanh nòng để cầm phần trước súng không bỏng tay. |
| **Foregrip** / vertical grip, forward grip | Tay cầm phụ / tay cầm trước | Tay cầm thêm phía trước tăng ổn định (MP9 tích hợp sẵn). |
| **Forend** / forearm | Phần đầu thân súng | Phần trước thân/khung nằm dưới nòng. |
| **Thumbhole** | Lỗ luồn ngón cái | Lỗ khoét trên thân bullpup để luồn ngón cái (P90). |

### C. TIẾP ĐẠN (Feeding)

| English | VN | Định nghĩa |
|---|---|---|
| **Box / stick magazine** | Băng đạn hộp / băng thẳng | Hộp rời chứa đạn, đẩy đạn bằng lò xo; thẳng hoặc hơi cong. |
| **Curved magazine** | Băng đạn cong | Băng cong theo độ côn của đạn (MP5-SD). |
| **Magazine well** | Hốc / khe cắm băng đạn | Khe trên thân/tay cầm để lắp băng đạn. |
| **P90 top-mounted horizontal magazine** | Băng đạn nằm ngang phía trên (P90) | Băng nhựa **nằm ngang, phẳng dọc nóc súng**, song song nòng; đạn xoay 90° trước khi vào buồng. |
| **PP-Bizon helical magazine** / drum | Băng đạn xoắn ốc (PP-Bizon) | Băng trụ dài **lắp dưới nòng**, cấp đạn kiểu xoắn ốc; kiêm điểm tì tay. |
| **Magazine release** / mag catch | Nút / lẫy tháo băng đạn | Cần/nút nhả băng đạn khỏi súng. |
| **Magazine baseplate** / floorplate | Đế băng đạn | Miếng đáy bịt & giữ lò xo trong băng. |
| **Follower** | Bản nâng đạn | Bệ trong băng đẩy đạn lên vị trí tiếp. |

### D. ĐIỀU KHIỂN & NGẮM (Controls & Sights)

| English | VN | Định nghĩa |
|---|---|---|
| **Trigger** | Cò súng | Lẫy bóp để khởi động chuỗi bắn. |
| **Trigger guard** | Vành bảo vệ cò | Vòng bao quanh cò, chống cướp cò ngoài ý muốn. |
| **Fire selector** / selector switch | Cần chọn chế độ bắn | Cần gạt chọn safe / bán tự động / liên thanh. |
| **Safety** | Khóa an toàn | Cơ cấu chặn súng bắn ngoài ý muốn. |
| **Front sight** (post/blade) | Đầu ngắm | Điểm ngắm gần miệng nòng. |
| **Rear sight** (notch/aperture) | Thước ngắm | Bộ ngắm phía sau thân, khe chữ U/V hoặc lỗ tròn. |
| **Iron sights** | Hệ ngắm cơ khí | Bộ ngắm không quang học gồm đầu + thước ngắm. |
| **Optic rail** / Picatinny, accessory rail | Ray gắn phụ kiện | Ray chuẩn để gắn ống ngắm/đèn/laser (P90 & MP7 có ray dọc nóc). |
| **Sling mount** / swivel, sling loop | Điểm gắn dây đeo | Móc/khuyên gắn dây đeo súng. |

---

## 2. Nhận diện 7 khẩu SMG CS2 — CS2 SMG identification

Mỗi dòng: **tên** — silhouette + vị trí băng đạn + kiểu báng + dấu hiệu nhận biết.

- **MAC-10** — Súng tiểu liên thép dập — **khối thân hình hộp vuông vức**, băng đạn hộp thẳng **cắm thẳng vào tay cầm chính**, báng khung dây thép rút gọn, thường có dây đai vải phía trước — dáng thô, ngắn, vuông.
- **MP9** — SMG polymer nhỏ gọn (kiểu B&T/Steyr) — **băng đạn nghiêng ra trước, nằm trước tay cầm chính**, báng gấp sang bên, tay cầm phụ tích hợp sẵn phía trước — dáng hiện đại, gọn.
- **MP7** — PDW siêu nhỏ gọn — **băng đạn cắm trong tay cầm chính** (như súng lục), báng thanh trượt rút gọn, **ray Picatinny kéo dài dọc nóc súng** — nhỏ nhất, dáng "súng lục to".
- **MP5-SD** — SMG có giảm thanh tích hợp — **ống giảm thanh trụ lớn bao trùm toàn bộ nòng**, băng đạn cong đặc trưng gắn trước vòng cò, báng rút — nhận ra ngay qua ống giảm thanh mập.
- **UMP-45** — SMG polymer .45 to bản — **băng đạn thẳng/thanh dài** to (cỡ .45), báng polymer đặc gấp sang bên, khe tản nhiệt lớn trên ốp lót tay — dáng gồ ghề, nặng nề.
- **P90** — SMG bullpup FN — **thiết kế bullpup** (cụm khai hỏa sau tay cầm), **băng đạn nhựa trong suốt nằm ngang dọc nóc súng**, thân liền khối có lỗ luồn ngón cái (thumbhole), cửa hất vỏ hướng xuống — silhouette độc nhất, không lẫn với súng nào.
- **PP-Bizon** — SMG nền AK — nắp hộp khóa nòng kiểu AK, **băng đạn hình trụ xoắn ốc (helical) lớn lắp dưới nòng** (kiêm điểm tì tay), báng khung tam giác gấp sang bên — nhận ra qua ống băng đạn trụ dài dưới nòng.

**Lưu ý hình khối then chốt (dễ sai nhất):**
- P90 = băng đạn **nằm ngang trên nóc** (KHÔNG phải băng cắm dưới như súng thường).
- PP-Bizon = ống **trụ xoắn dưới nòng** (KHÔNG phải băng hộp cong).
- MP7 & MAC-10 = băng đạn **trong tay cầm**; MP9 = băng **trước tay cầm**.
- MP5-SD = **giảm thanh liền** bao trọn nòng (không tháo rời như USP-S).

---

## 3. Thuật ngữ SKIN / FINISH CS2 → map lên bộ phận SMG

Dùng khi **texture/màu/pattern** sai (không phải hình khối).

### Phân biệt phần được sơn
| English | VN | Nghĩa & vị trí |
|---|---|---|
| **Vanilla / stock** | Mặc định / nguyên bản | Súng chưa áp skin — vật liệu nhà máy gốc — toàn bộ súng. |
| **Paintable parts** / refinishable areas | Vùng có thể sơn | Bề mặt lớn nhận texture skin — **thân súng (receiver), báng (stock), ốp lót tay (handguard)**, và băng đạn. |
| **Bare metal parts** / substrate, unfinishable areas | Kim loại trần / bề mặt phôi | Chi tiết chức năng giữ màu gốc — **đầu/thước ngắm, cần chọn chế độ, cò, ốc vít, trong nòng**. |

### Pattern
| English | VN | Nghĩa & vị trí |
|---|---|---|
| **Paint seed** / pattern seed, pattern index | Mã hạt / số seed | Giá trị ngẫu nhiên (1–1000) quyết định vị trí, độ xoay & độ lệch của họa tiết trên model — **thân súng & băng đạn**. |

### Kiểu finish
| English | VN | Đặc trưng & vị trí |
|---|---|---|
| **Solid Color** | Màu đơn sắc | Từng bộ phận sơn tối đa 4 màu riêng trước khi lắp ráp — thân, báng, ốp lót tay. |
| **Spray-Paint** | Sơn xịt | Sơn nhiều lớp qua khuôn chắn (stencil), hiệu ứng chồng lớp; bong sớm ở **cạnh thân & miệng nòng**. |
| **Hydrographic** / dipping | Sơn nhúng / thủy ấn | Nhúng bộ phận qua màng phim họa tiết nổi trên nước — rõ trên bề mặt cong (**ống giảm thanh MP5-SD, thân súng**). |
| **Anodized** / airbrushed, multicolored | Sơn mạ / anode | Lớp màu mỏng (candy coat) trên nền chrome bóng — giữ ánh kim loại trên thân/nòng. |
| **Patina** | Lên màu thời gian / rỉ kim loại | Đổi màu kim loại kiểu phản ứng hóa học (xanh hóa, tôi thép) — thân kim loại. |
| **Custom Paint Job** | Sơn tùy chỉnh | Tranh vẽ cố định áp thẳng lên UV map, **không đổi theo seed** — toàn thân. |
| **Gunsmith** | Chế tác thủ công | Lai Patina + Custom Paint Job trên cùng khẩu — thân kim loại + báng/ốp polymer. |
| **Fade** | Chuyển màu / fade | Biến thể Anodized, các màu hòa & chuyển dần dọc **thân súng & báng**. |

### Wear / Float
| English | VN | Nghĩa & vị trí mòn |
|---|---|---|
| **Float Value** / wear value | Chỉ số Float / độ mòn | Số 0.00–1.00 định mức trầy xước & xuống cấp lớp sơn — toàn súng. |
| **Factory New (FN)** | Mới xuất xưởng | 0.00–0.07, hoàn hảo nhất, gần như không xước — thân & băng đạn. |
| **Minimal Wear (MW)** | Ít trầy xước | 0.07–0.15, vài xước nhỏ ở cạnh, màu hơi tối — **cạnh thân & góc sắc**. |
| **Field-Tested (FT)** | Đã qua sử dụng | 0.15–0.37, phổ biến nhất, xước & bụi bẩn rõ — cạnh thân, miệng nòng, controls. |
| **Well-Worn (WW)** | Mòn nhiều | 0.37–0.44, bong tróc đáng kể, lộ kim loại trần — high points & cạnh băng đạn. |
| **Battle-Scarred (BS)** | Tàn phá / vết sẹo chiến tranh | 0.44–1.00, mất phần lớn lớp sơn, xỉn & bẩn — mọi bề mặt sơn. |
| **Wear high points** | Điểm dễ mòn | Chịu tác động trước: **cạnh & góc sắc của thân, miệng nòng, các điểm nhô cao, cần điều khiển, cạnh băng đạn**. |

---

## Ghi chú dùng cho debug fidelity

- **Sai hình khối** → mục 1–2: vd "P90 để băng đạn cắm dưới thay vì nằm ngang trên nóc", "PP-Bizon thiếu ống băng đạn xoắn dưới nòng", "MP5-SD thiếu giảm thanh tích hợp bao nòng", "MAC-10 chưa đủ dáng khối hộp vuông", "MP7 để băng ngoài tay cầm".
- **Sai texture/màu** → mục 3: vd "pattern seed lệch làm họa tiết trên thân sai vị trí", "wear đặt ở giữa thân thay vì cạnh/miệng nòng/high points", "Fade chuyển màu sai hướng dọc thân".
- **Bare metal accents**: đầu/thước ngắm, cần chọn chế độ, cò, ốc vít thường KHÔNG sơn — nếu model sơn cả lên đó là sai.

_Đã xong: [dao](knives.md), [pistol](pistols.md), SMG. Loại tiếp theo: rifle / sniper / gloves — mỗi loại một file trong `docs/cs2-anatomy/`._
