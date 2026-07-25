# 3D Technical Vocabulary Dictionary - CS2 Items Focus
# Từ điển Thuật ngữ Kỹ thuật 3D - Tập trung vào CS2 Items

---

## Table of Contents / Mục lục
1. [Core 3D Modeling](#1-core-3d-modeling)
2. [UV Mapping & Texturing](#2-uv-mapping--texturing)
3. [PBR Materials](#3-pbr-materials)
4. [CS2 Specific Terminology](#4-cs2-specific-terminology)
5. [3D File Formats](#5-3d-file-formats)
6. [Animation & Rigging](#6-animation--rigging)
7. [Rendering & Optimization](#7-rendering--optimization)
8. [Three.js / Web 3D](#8-threejs--web-3d)
9. [CS2 Workshop Workflow](#9-cs2-workshop-workflow)

---

## 1. Core 3D Modeling

### Geometry & Mesh

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Vertex (Vertices)** | Đỉnh | A point in 3D space with X, Y, Z coordinates. The fundamental building block of all 3D geometry. |
| **Edge** | Cạnh | A line connecting two vertices. Defines the boundaries of faces. |
| **Face** | Mặt | A flat surface enclosed by 3 or more edges. Can be triangles, quads, or n-gons. |
| **Polygon** | Đa giác | Generic term for a face. Usually refers to triangles (tris) or quads in modeling. |
| **Triangle (Tris)** | Tam giác | A 3-sided polygon. GPU renders everything as triangles ultimately. |
| **Quad** | Tứ giác | A 4-sided polygon. Preferred for clean topology and subdivision. |
| **N-gon** | N-cạnh | A polygon with 5+ sides. Generally avoided in animation meshes. |
| **Mesh** | Lưới | The complete geometric structure of a 3D object, consisting of vertices, edges, and faces. |
| **Geometry** | Hình học | The mathematical representation of shape and form in 3D space. |
| **Wireframe** | Khung dây | Display mode showing only edges and vertices without filled faces. |

### Topology & Structure

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Topology** | Cấu trúc | The arrangement and flow of polygons on a mesh surface. Clean topology = even quads following surface contours. |
| **Edge Loop** | Vòng cạnh | A continuous ring of edges that flows around a mesh. Critical for deformation and animation. |
| **Edge Flow** | Dòng cạnh | The directional pattern of edges across a surface. Guides how mesh deforms. |
| **Retopology** | Tái cấu trúc | Redrawing a mesh with cleaner, lower-poly topology. Often done on sculpted high-poly models. |
| **Subdivision Surface** | Mặt phụ division | Algorithm that smooths a mesh by recursively subdividing faces. Used to create smooth surfaces from low-poly control cages. |
| **High-Poly** | Đa giác cao | A mesh with many polygons, used for detail sculpting or baking. |
| **Low-Poly** | Đa giác thấp | A mesh with few polygons, optimized for real-time rendering. |
| **Base Mesh** | Mesh cơ bản | Low-resolution starting mesh for sculpting or modeling. |
| **NURBS** | NURBS | Non-Uniform Rational B-Splines. Mathematical surface representation used in CAD/product design for smooth curves. |
| **Watertight Mesh** | Mesh kín | A closed mesh with no holes. Required for 3D printing and some physics simulations. |

### Primitives

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Primitive** | Khối cơ bản | Basic geometric shapes: Cube, Sphere, Cylinder, Cone, Plane, Torus. Starting points for modeling. |
| **BufferGeometry** | BufferGeometry | Three.js class for defining custom geometry with vertex attributes (positions, normals, UVs). |

---

## 2. UV Mapping & Texturing

### UV Concepts

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **UV Mapping** | Ánh xạ UV | The process of unwrapping a 3D surface onto a 2D plane for texture application. |
| **UV Coordinates** | Tọa độ UV | 2D coordinates (U horizontal, V vertical) that map texture pixels to 3D surface points. |
| **UV Unwrap** | Mở phẳng UV | The act of flattening a 3D mesh surface into 2D space. |
| **UV Seam** | Đường may UV | Edges where the UV map is cut to allow flattening. Visible as texture seams if not placed carefully. |
| **UV Layout** | Bố cục UV | The arrangement of UV islands in texture space. |
| **Texel Density** | Mật độ texel | The amount of texture resolution per unit of 3D surface area. |

### Texture Maps

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Diffuse/Albedo Map** | Bản đồ màu cơ sở | Base color texture without lighting information. The "paint" of the surface. |
| **Normal Map** | Bản đồ normal | RGB texture that simulates surface bumps by altering surface normals. Adds detail without geometry. |
| **Bump Map** | Bản đồ gồ ghề | Grayscale texture that simulates height variation. Less precise than normal maps. |
| **Displacement Map** | Bản đồ dịch chuyển | Grayscale texture that actually moves vertices to create real geometry detail. |
| **Specular Map** | Bản đồ phản chiếu | Defines intensity and color of specular highlights on a surface. |
| **Roughness Map** | Bản đồ thô | Grayscale map controlling surface smoothness. Black = smooth/glossy, White = rough/matte. |
| **Metallic Map** | Bản đồ kim loại | Defines which parts are metallic (white) vs dielectric/non-metallic (black). |
| **Ambient Occlusion (AO)** | Che sáng môi trường | Grayscale map showing areas where ambient light is blocked by geometry. Adds depth. |
| **Emission Map** | Bản đồ phát sáng | Texture defining which parts of a surface emit light/glow. |
| **Opacity/Alpha Map** | Bản đồ trong suốt | Grayscale map controlling transparency. White = opaque, Black = transparent. |
| **Height Map** | Bản đồ chiều cao | Grayscale map representing surface elevation for displacement. |
| **Cavity Map** | Bản đồ hang | Highlights small surface crevices and edges for added detail. |

### Texture Settings

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Texture Resolution** | Độ phân giải texture | Pixel dimensions of a texture (e.g., 1024x1024, 2048x2048). Usually power-of-two. |
| **Power-of-Two** | Lũy thừa của 2 | Texture dimensions that are powers of 2 (256, 512, 1024, 2048, 4096). Required for mipmapping. |
| **Mipmapping** | Mipmap | Pre-calculated lower-resolution copies of a texture. Improves quality and performance at distance. |
| **Texture Filtering** | Lọc texture | How textures are sampled: Nearest (pixelated), Bilinear, Trilinear, Anisotropic. |
| **Tiling** | Lặp | Repeating a texture across a surface. |
| **Texture Atlas** | Bảng texture | Combining multiple textures into one image for efficiency. |

---

## 3. PBR Materials

### PBR Workflows

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **PBR (Physically Based Rendering)** | Render dựa trên vật lý | Rendering approach that simulates real-world light interaction for realistic materials. |
| **Metallic/Roughness Workflow** | Quy trình Kim loại/Thô | PBR workflow using Base Color + Metallic + Roughness maps. Most common in games. |
| **Specular/Glossiness Workflow** | Quy trình Phản chiếu/Bóng | PBR workflow using Diffuse + Specular + Glossiness maps. Alternative to Metallic/Roughness. |
| **Base Color** | Màu cơ bản | The fundamental color of a surface without lighting effects. |
| **Metalness** | Độ kim loại | 0-1 value: 0 = dielectric (plastic, wood), 1 = metal (gold, iron). |
| **Roughness** | Độ thô | 0-1 value: 0 = mirror-smooth, 1 = completely rough/matte. |
| **Fresnel** | Fresnel | Effect where reflectivity increases at grazing angles. Essential for realistic materials. |
| **Microfacet** | Vi mặt | Tiny surface irregularities that determine roughness and reflection behavior. |
| **Energy Conservation** | Bảo toàn năng lượng | Principle that reflected light cannot exceed incoming light. Core PBR concept. |

### PBR in Games

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Material** | Vật liệu | A set of texture maps and parameters defining how a surface looks and responds to light. |
| **Shader** | Trình xử lýshader | Program that runs on GPU to calculate final pixel color based on material properties. |
| **Albedo** | Albedo | Base color without any lighting contribution. The pure "color" of the surface. |
| **Dielectric** | Chất điện môi | Non-metallic material. Has specular reflections but no colored reflections. |

---

## 4. CS2 Specific Terminology

### Skin System

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Skin** | Da súng / Skin | A cosmetic finish applied to a weapon, changing its appearance without affecting gameplay. |
| **Paint Kit** | Bộ sơn | The complete texture set defining a skin's visual appearance. Contains multiple texture layers. |
| **Finish** | Hoàn thiện / Finish | The final visual style of a skin (e.g., Anodized, Gunsmith, Hydrographic). |
| **Finish Style** | Kiểu finish | Categories of paint application: Anodized, Anodized Multicolored, Custom Paint Job, Gunsmith, Hydrographic, Patina, Solid Color, Sprayed. |

### Wear System

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Float Value** | Giá trị float | A 32-bit decimal (0.000-1.000) assigned at drop time. Determines wear appearance. Lower = cleaner. |
| **Wear Rating** | Đánh giá mòn | The visible condition category based on float ranges. |
| **Factory New (FN)** | Mới từ nhà máy | Float 0.00-0.07. Pristine condition with almost no visible wear. |
| **Minimal Wear (MW)** | Mòn tối thiểu | Float 0.07-0.15. Minor scratches, very clean appearance. |
| **Field-Tested (FT)** | Đã thử nghiệm | Float 0.15-0.38. Noticeable scratches and blemishes. Mid-range condition. |
| **Well-Worn (WW)** | Mòn nhiều | Float 0.38-0.45. Significant wear, fading, scratches. |
| **Battle-Scarred (BS)** | Chiến tích | Float 0.45-1.00. Heavy damage, deep scratches, large blemishes. |
| **Float Clipping** | Cắt float | Per-skin restriction on which float values are possible. Defined by wear_remap_min/max in items_game.txt. |

### Pattern System

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Pattern Index (Paint Seed)** | Chỉ mẫu (Màu sơn) | Integer (0-1000) assigned at drop. Controls which slice of texture atlas is visible. |
| **Pattern Seed** | Hạt giống mẫu | Same as Pattern Index. Determines texture placement variation. |
| **Template ID** | ID mẫu | Identifier for specific pattern variations in certain finishes. |
| **Blue Gem** | Đá xanh | Rare Case Hardened pattern with predominantly blue color on playside. Extremely valuable. |
| **Fade Percentage** | Phần trăm Fade | Percentage of color gradient visible on Fade skins. Higher % = more colors = more valuable. |
| **Doppler Phase** | Giai đoạn Doppler | Different color variations in Doppler skins (Phase 1-4, Ruby, Sapphire, Black Pearl, etc.). |
| **Playside** | Mặt nhìn | The side of the weapon visible to the player in first-person view. Most important for value. |
| **Backside** | Mặt sau | The opposite side of the weapon from playside. |

### Special Items

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **StatTrak™** | StatTrak™ | Special variant with kill counter. Drops at ~10% rate. Premium 20-100% over normal. |
| **Souvenir** | Hàng lưu niệm | Exclusive drops from Major tournaments with pre-applied tournament stickers. Supply permanently capped. |
| **Sticker** | Tem / Sticker | Cosmetic item applied to weapons. Types: Paper, Holo, Foil, Gold. |
| **Sticker Craft** | Bộ tem | The combination of a skin with applied stickers. Position and condition matter for value. |
| **Sticker Slot** | Khe tem | Fixed positions on weapons where stickers can be applied (typically 4 slots). |
| **Scrape Level** | Mức cào | Sticker degradation: 100% → 75% → 50% → 25%. Affects value significantly. |
| **Name Tag** | Thẻ tên | Item that lets you rename a weapon. Cosmetic only. |
| **Charm** | Charms | Decorative items that hang from weapons. |
| **Collection** | Bộ sưu tập | Groups of skins that drop together. Some collections are discontinued, increasing rarity. |

### CS2 Economy Terms

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Market Hash Name** | Tên hash thị trường | Exact name Steam uses for listing (e.g., "StatTrak™ AK-47 | Redline (Field-Tested)"). |
| **Paint Index** | Chỉ số sơn | Numeric ID for a specific finish + variant (e.g., 415 = Doppler Ruby). |
| **Rarity Grade** | Phân loại hiếm | Item rarity: Consumer Grade → Industrial Grade → Mil-Spec → Restricted → Classified → Covert → Contraband. |
| **Exterior** | Ngoại quan | The wear tier label shown in inventory (FN, MW, FT, WW, BS). |
| **Inspect Link** | Liên kết kiểm tra | Steam URL that reveals float, pattern, and sticker details for an item. |
| **Game Coordinator (GC)** | Điều phối trò chơi | CS2's internal item server that provides item data to trading sites. |
| **Trade-Up Contract** | Hợp đồng nâng cấp | Combining 10 items of same rarity to receive 1 item of next higher rarity. Float is averaged. |

---

## 5. 3D File Formats

### Exchange Formats

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **OBJ** | OBJ | Wavefront OBJ. Simple text-based format for geometry + normals + UVs. No animation. |
| **FBX** | FBX | Autodesk Filmbox. Primary exchange format for game engines. Supports geometry, animation, materials. |
| **glTF/GLB** | glTF/GLB | Graphics Language Transmission Format. Open standard for 3D on web. GLB = binary version. |
| **STL** | STL | Stereolithography. Geometry-only format. Standard for 3D printing. |
| **PLY** | PLY | Stanford Polygon File Format. Often used for scanned point cloud data. |
| **USDZ** | USDZ | Apple's AR format for Quick Look and Reality Composer. |

### Game-Specific Formats

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **MDL** | MDL | Valve's model format for Source/Source 2 engine. |
| **VTF** | VTF | Valve Texture Format. Native texture format for Source engine. |
| **VMT** | VMT | Valve Material Type. Defines material properties in Source engine. |
| **VMF** | VMF | Valve Map Format. Source engine map editor format. |
| ** items_game.txt** | items_game.txt | CS2 configuration file defining all item properties, paint kits, wear ranges. |

### Format Comparison

| Format | Geometry | Animation | Materials | Web | Games | 3D Print |
|--------|----------|-----------|-----------|-----|-------|----------|
| OBJ | ✅ | ❌ | Limited | ✅ | ⚠️ | ✅ |
| FBX | ✅ | ✅ | ✅ | ⚠️ | ✅ | ❌ |
| glTF/GLB | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| STL | ✅ | ❌ | ❌ | ⚠️ | ❌ | ✅ |
| MDL | ✅ | ✅ | ✅ | ❌ | ✅ (Source) | ❌ |

---

## 6. Animation & Rigging

### Skeletal Animation

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Skeleton** | Bộ xương | Hierarchical structure of bones that deforms a mesh. |
| **Bone** | Xương | Individual element in a skeleton hierarchy. Has position, rotation, and parent-child relationships. |
| **Joint** | Khớp | Connection point between bones. Defines pivot points for rotation. |
| **Rigging** | Rigging / Cấp xương | The process of creating and binding a skeleton to a mesh for animation. |
| **Weight Painting** | Vẽ trọng số | Defining how much each bone influences surrounding vertices. Controls deformation quality. |
| **Skinning** | Phủ da | Binding mesh vertices to skeleton bones with weighted influences. |

### Animation Types

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Keyframe Animation** | Hoạt hình khoá khung | Animation defined by specific poses at specific times, interpolated in between. |
| **Procedural Animation** | Hoạt hình theo thủ tục | Animation generated by code/algorithms rather than manual keyframes. |
| **Motion Capture (Mocap)** | Bắt chuyển động | Recording real-world movement to drive digital skeleton. |
| **Inverse Kinematics (IK)** | Động học nghịch đảo | Calculating joint rotations from end-effector position. Used for feet/ground contact. |
| **Forward Kinematics (FK)** | Động học thuận | Setting joint rotations from root to tip. Standard bone manipulation. |
| **Blend Shapes / Morph Targets** | Hình dạng lai / Mục tiêu biến đổi | Pre-defined vertex positions that can be blended between. Used for facial expressions, damage states. |

### Three.js Animation

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **AnimationClip** | AnimationClip | A reusable animation containing keyframe tracks for multiple properties. |
| **AnimationMixer** | AnimationMixer | Controls playback and blending of AnimationClips on a model. |
| **SkinnedMesh** | SkinnedMesh | Three.js mesh that can be deformed by a skeleton. |

---

## 7. Rendering & Optimization

### Rendering Concepts

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Draw Call** | Lệnh vẽ | A single command from CPU to GPU to render something. Too many draw calls = performance hit. |
| **Batching** | Gộp | Combining multiple objects into fewer draw calls for efficiency. |
| **Instancing** | Thực thể hóa | Rendering many copies of same mesh with one draw call. Each instance can have unique transforms/colors. |
| **GPU** | GPU | Graphics Processing Unit. Hardware that executes rendering calculations in parallel. |
| **Shader** | Shader | Program that runs on GPU to calculate vertex positions and pixel colors. |
| **Vertex Shader** | Vertex Shader | Runs per-vertex. Transforms positions from model space to screen space. |
| **Fragment/Pixel Shader** | Fragment/Pixel Shader | Runs per-pixel. Calculates final color based on materials, lights, textures. |
| **Render Pipeline** | Pipeline render | The sequence of steps from 3D scene to 2D screen output. |

### Optimization

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **LOD (Level of Detail)** | Chi tiết cấp độ | Using lower-poly models at distance. Reduces GPU load without visible quality loss. |
| **Polygon Budget** | Ngân sách đa giác | Maximum number of polygons allowed for an object/scene. |
| **Occlusion Culling** | Loại trừ che khuất | Not rendering objects hidden behind other objects. |
| **Frustum Culling** | Loại trừ ngoài khung hình | Not rendering objects outside camera's view frustum. |
| **Draw Call Batching** | Gộp lệnh vẽ | Merging static objects into fewer draw calls. |
| **Texture Atlasing** | Đóng gói texture atlas | Combining textures to reduce material switches and draw calls. |

---

## 8. Three.js / Web 3D

### Core Concepts

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **WebGL** | WebGL | Web Graphics Library. JavaScript API for hardware-accelerated 3D rendering in browsers. |
| **Scene** | Cảnh | Container for all 3D objects, lights, and cameras in Three.js. |
| **Camera** | Máy ảnh | Defines the viewpoint. Types: PerspectiveCamera (realistic), OrthographicCamera (2D-style). |
| **Renderer** | Bộ kết xuất | Converts Three.js scene to pixels on screen. WebGLRenderer is most common. |
| **Mesh** | Mesh | A 3D object combining geometry (shape) and material (appearance). |
| **Group** | Nhóm | Container for organizing multiple meshes/objects with shared transforms. |
| **Light** | Ánh sáng | Illumination sources. Types: AmbientLight, DirectionalLight, PointLight, SpotLight. |

### Three.js Materials

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **MeshStandardMaterial** | MeshStandardMaterial | PBR material using Metallic/Roughness workflow. Most realistic. |
| **MeshPhongMaterial** | MeshPhongMaterial | Simpler, faster material. Good for mobile/low-end devices. |
| **MeshBasicMaterial** | MeshBasicMaterial | Unlit material. No light interaction. Fastest rendering. |
| **ShaderMaterial** | ShaderMaterial | Custom material using GLSL vertex/fragment shaders. Full control. |
| **RawShaderMaterial** | RawShaderMaterial | ShaderMaterial with no built-in uniforms/attributes. Complete shader authoring. |
| **MeshLambertMaterial** | MeshLambertMaterial | Simple diffuse material. Faster than Phong but less realistic. |

### Three.js Geometry

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **BufferGeometry** | BufferGeometry | Core geometry class. Stores vertex data in typed arrays (positions, normals, UVs). |
| **BoxGeometry** | BoxGeometry | Creates a box/cube shape. |
| **SphereGeometry** | SphereGeometry | Creates a sphere shape. |
| **CylinderGeometry** | CylinderGeometry | Creates a cylinder shape. |
| **PlaneGeometry** | PlaneGeometry | Creates a flat 2D plane in 3D space. |
| **TorusGeometry** | TorusGeometry | Creates a donut/torus shape. |
| **ExtrudeGeometry** | ExtrudeGeometry | Creates 3D shape by extruding a 2D path. |
| **LatheGeometry** | LatheGeometry | Creates shape by rotating a profile around an axis. |

### Loaders & Export

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **GLTFLoader** | GLTFLoader | Loads glTF/GLB files into Three.js scenes. |
| **FBXLoader** | FBXLoader | Loads FBX files. |
| **OBJLoader** | OBJLoader | Loads OBJ files. |
| **TextureLoader** | TextureLoader | Loads image files as textures. |
| **GLTFExporter** | GLTFExporter | Exports Three.js scene to glTF/GLB format. |

### Performance

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **InstancedMesh** | InstancedMesh | Renders many copies of same geometry with one draw call. |
| **Frustum Culling** | Frustum Culling | Automatic Three.js optimization to skip objects outside camera view. |
| **Level of Detail (LOD)** | LOD | Three.js class that shows different detail levels based on camera distance. |
| **Morph Targets** | Morph Targets | Vertex-based animation stored in geometry. Used for facial expressions. |

---

## 9. CS2 Workshop Workflow

### Creation Pipeline

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Workshop Submission** | Gửi lên Workshop | Process of submitting custom skins to CS2 for community voting. |
| **Substance Painter** | Substance Painter | Industry-standard texture painting tool. Primary tool for CS2 skin creation. |
| **Substance Designer** | Substance Designer | Node-based material creation tool. Used for procedural textures. |
| **Source 2** | Source 2 | Valve's game engine powering CS2. |
| **Source 2 Hammer** | Hammer | Level/asset editor for Source 2 engine. |
| **Workshop Tools** | Công cụ Workshop | Official Valve tools for creating CS2 workshop content. |
| **Workshop Preview** | Xem trước Workshop | How your skin appears in the workshop voting interface. |

### Material System

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **VMT (Valve Material Type)** | VMT | Material definition file in Source engine. Defines shaders, textures, and parameters. |
| **VTF (Valve Texture Format)** | VTF | Native texture format for Source engine. Converted from PNG/TGA. |
| **Material Proxy** | Proxy vật liệu | Dynamic material parameter changes based on game state (e.g., wear animation). |
| **Phong Shading** | Shading Phong | Legacy shading model in Source engine. Still used for some CS2 materials. |
| **PBR Material (Source 2)** | Vật liệu PBR | Modern material system in Source 2. Uses metallic/roughness workflow. |

### Workshop Requirements

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **Tri Count** | Số tam giác | Total triangle count of weapon model. Must meet Valve's limits. |
| **Texture Resolution** | Độ phân giải texture | Required texture sizes (typically 2048x2048 or 1024x1024). |
| **Normal Map** | Bản đồ Normal | Required for surface detail without extra geometry. |
| **AO Map** | Bản đồ AO | Ambient Occlusion map for added depth. |
| **Material Parameters** | Tham số vật liệu | Configurable values in VMT/shader (color, wear amount, pattern offset, etc.). |
| **Paint Pattern** | Mẫu sơn | The 2D texture pattern that gets projected onto the 3D model via UV mapping. |
| **Wear Overlay** | Lớp phủ mòn | Texture layer that adds scratches, fading, and damage based on float value. |

### File Structure

| English Term | Vietnamese | Definition |
|--------------|------------|------------|
| **QC File** | File QC | Model compilation script defining mesh, skeleton, sequences. |
| **DMX** | DMX | Data Model eXchange format. Source 2 model format. |
| **VCS** | VCS | Version Control System integration for Workshop tools. |
| **Workshop ID** | ID Workshop | Unique identifier for your submitted item. |
| **Tags** | Thẻ | Category labels for workshop items (e.g., Weapon, Knife, gloves). |

---

## Quick Reference: CS2 Item Identification

A CS2 skin is uniquely identified by:
1. **Skin Name** - e.g., "AK-47 Asiimov"
2. **Exterior/Wear Tier** - FN, MW, FT, WW, BS
3. **StatTrak/Souvenir Flag** - Normal, StatTrak, or Souvenir
4. **Float Value** - Exact decimal (e.g., 0.2234)
5. **Pattern Index** - Integer seed (e.g., 661 for blue gem)

Two items with the same name can have different values based on float, pattern, stickers, and StatTrak status.

---

## Glossary of Acronyms

| Acronym | Full Form | Vietnamese |
|---------|-----------|------------|
| PBR | Physically Based Rendering | Render dựa trên vật lý |
| UV | Ultraviolet (texture coordinates) | Tọa độ kết cấu |
| LOD | Level of Detail | Chi tiết cấp độ |
| AO | Ambient Occlusion | Che sáng môi trường |
| FN | Factory New | Mới từ nhà máy |
| MW | Minimal Wear | Mòn tối thiểu |
| FT | Field-Tested | Đã thử nghiệm |
| WW | Well-Worn | Mòn nhiều |
| BS | Battle-Scarred | Chiến tích |
| GLB/glTF | Graphics Language Transmission Format | Định dạng truyền ngôn ngữ đồ họa |
| FBX | Filmbox | Filmbox |
| OBJ | Wavefront OBJ | Wavefront OBJ |
| STL | Stereolithography | Mô hình hóa cứng |
| VMT | Valve Material Type | Loại vật liệu Valve |
| VTF | Valve Texture Format | Định dạng kết cấu Valve |
| IK | Inverse Kinematics | Động học nghịch đảo |
| FK | Forward Kinematics | Động học thuận |
| GPU | Graphics Processing Unit | Đồ họa đơn vị xử lý |

---

*Last updated: July 2026*
*Sources: Valve Developer Community, CS2 Workshop Documentation, Three.js Documentation, Blender Manual, Meshy Docs*
