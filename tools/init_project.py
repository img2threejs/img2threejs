#!/usr/bin/env python3
"""init_project: 从图片一键创建 Three.js 项目

用法:
  python3 tools/init_project.py --image <path> --name <project-name>

说明:
  1. 在 ~/Documents/ZCodeProjects/<name>/ 下新建项目
  2. 复制参考图、生成 specs、验证、生成 Three.js 代码
  3. 创建 Vite 三件套 (index.html + main.ts + package.json)
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECTS_ROOT = Path.home() / "Documents" / "ZCodeProjects"

# Scope presets (mirrored from fix_spec.py)
SCOPES = {
    "eye": {
        "keep_patterns": [r"^eye-root$", r"^sclera$", r"^iris$", r"^pupil$", r"^cornea$"],
        "description": "Only eyeball components (no skin, eyelids, lashes)",
    },
    "face": {
        "keep_patterns": [r"^eye-root$", r"^sclera$", r"^iris$", r"^pupil$", r"^cornea$",
                          r"^face-root$", r"^nose$", r"^mouth$", r"^ear-"],
        "description": "Face + eye components (remove hair, neck, body)",
    },
}

VIEWER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{name} — Three.js Demo</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ overflow: hidden; background: #222; font-family: sans-serif; }}
    #container {{ width: 100vw; height: 100vh; display: block; }}
    #info {{
      position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%);
      color: rgba(255,255,255,0.5); font-size: 13px;
      background: rgba(0,0,0,0.5); padding: 8px 16px; border-radius: 8px;
      pointer-events: none; user-select: none;
    }}
    #controls {{
      position: absolute; top: 20px; right: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 10;
    }}
    #controls button {{
      background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2);
      color: #fff; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 12px;
      transition: background 0.2s;
    }}
    #controls button:hover {{ background: rgba(255,255,255,0.2); }}
    #controls button.active {{ background: rgba(100,180,255,0.3); border-color: rgba(100,180,255,0.5); }}
  </style>
</head>
<body>
  <div id="container"></div>
  <div id="controls">
    <button id="btn-wireframe">Wireframe</button>
    <button id="btn-reset">Reset View</button>
  </div>
  <div id="info">🖱 Drag to orbit · Scroll to zoom</div>
  <script type="module" src="/src/main.ts"></script>
</body>
</html>
"""

VIEWER_MAIN_TS = r"""import * as THREE from 'three';
import {{
  create{name_pascal}Model,
  create{name_pascal}LookDevLights,
  create{name_pascal}Environment,
  create{name_pascal}PresentationComposer,
  create{name_pascal}InspectControls,
  configure{name_pascal}Renderer,
  ProceduralModelOptions,
}} from '../create{name_pascal}Model.ts';

const container = document.getElementById('container')!;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x2a2a2a);

const camera = new THREE.PerspectiveCamera(30, container.clientWidth / container.clientHeight, 0.1, 50);
camera.position.set(0.8, 0.4, 3.0);

const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
configure{name_pascal}Renderer(renderer);
container.appendChild(renderer.domElement);

scene.environment = create{name_pascal}Environment(renderer);
scene.add(create{name_pascal}LookDevLights());

let options: ProceduralModelOptions = {{}};
let modelGroup = create{name_pascal}Model(options);
scene.add(modelGroup);

const composer = create{name_pascal}PresentationComposer(renderer, scene, camera, {{
  dof: true, dofFocus: 2.0, bloom: true, bloomStrength: 0.15,
}});

const controls = create{name_pascal}InspectControls(camera, renderer.domElement);

window.addEventListener('resize', () => {{
  camera.aspect = container.clientWidth / container.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}});

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  composer.render();
}}
animate();

document.getElementById('btn-wireframe')?.addEventListener('click', (e) => {{
  const btn = e.target as HTMLButtonElement;
  options.wireframe = !options.wireframe;
  btn.classList.toggle('active', !!options.wireframe);
  if (modelGroup.parent) scene.remove(modelGroup);
  modelGroup = create{name_pascal}Model(options);
  scene.add(modelGroup);
}});

document.getElementById('btn-reset')?.addEventListener('click', () => {{
  camera.position.set(0.8, 0.4, 3.0);
  controls.target.set(0, 0, 0);
  controls.update();
}});
"""

PACKAGE_JSON = r"""{{
  "name": "{name}",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  }},
  "dependencies": {{
    "three": "^0.162.0"
  }},
  "devDependencies": {{
    "@types/three": "^0.162.0",
    "typescript": "^5.3.0",
    "vite": "^5.0.0"
  }}
}}
"""

VITE_CONFIG = r"""import { defineConfig } from 'vite';
export default defineConfig({
  root: '.',
  server: { port: 3000 },
  build: { outDir: 'dist' },
});
"""

TSCONFIG = r"""{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src", "create*Model.ts"]
}
"""


def pascal(s: str) -> str:
    return s.replace("-", " ").replace("_", " ").title().replace(" ", "")


def create_project(image_path: str, name: str):
    img = Path(image_path).resolve()
    if not img.exists():
        print(f"❌ Image not found: {img}")
        sys.exit(1)

    proj = PROJECTS_ROOT / name
    if proj.exists():
        print(f"⚠️  Project already exists: {proj}")
        ans = input("  Overwrite? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            sys.exit(1)
        shutil.rmtree(proj)

    proj.mkdir(parents=True, exist_ok=True)
    src_dir = proj / "src"
    src_dir.mkdir()

    # Copy reference image
    shutil.copy2(img, proj / img.name)

    name_p = pascal(name)

    # Pre-validate the image
    print("🔍 Pre-validating image...")
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "tools" / "pre_validate_image.py"), str(img), "--json"],
        cwd=SKILL_DIR, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    if report.get("issues"):
        print("⚠️  Image issues found:")
        for issue in report["issues"]:
            print(f"     ❌ {issue}")
    if report.get("warnings"):
        print("⚠️  Image warnings:")
        for w in report["warnings"]:
            print(f"     ⚠️  {w}")
    if report.get("score") == "reject":
        print("❌ Image rejected by pre-validation. Stopping.")
        sys.exit(1)
    if report.get("score") == "conditional":
        print("❓ Image is conditional — will proceed but expect limitations.")
    # Save report alongside project
    (proj / f"{name}-prevalidate.json").write_text(result.stdout)

    pre_spec_path = SKILL_DIR / "outputs" / f"{name}-pre-spec-assessment.json"
    spec_path = SKILL_DIR / "outputs" / f"{name}-sculpt-spec.json"

    # Generate spec
    print("🔧 Generating pre-spec assessment...")
    subprocess.run(
        [sys.executable, str(SKILL_DIR / "forge" / "stage2_spec" / "new_pre_spec_assessment.py"),
         name, "--image", str(img), "--out", str(pre_spec_path), "--force"],
        cwd=SKILL_DIR, check=True,
    )

    print("🔧 Generating sculpt spec...")
    subprocess.run(
        [sys.executable, str(SKILL_DIR / "forge" / "stage2_spec" / "new_sculpt_spec.py"),
         name, "--image", str(img), "--out", str(spec_path), "--force"],
        cwd=SKILL_DIR, check=True,
    )

    # Auto-tree: enrich skeleton spec with full component tree from assessment
    print("🔧 Auto-tree: enriching spec with template component tree...")
    subprocess.run(
        [sys.executable, str(SKILL_DIR / "tools" / "auto_tree.py"),
         str(pre_spec_path), str(spec_path)],
        cwd=SKILL_DIR, check=False,
    )

    # Validate & fix

    print("🔧 Validating spec...")
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "forge" / "stage2_spec" / "validate_sculpt_spec.py"),
         str(spec_path), "--strict-quality"],
        cwd=SKILL_DIR, capture_output=True, text=True,
    )
    print(result.stdout)

    # Apply scope / auto-fix if requested
    if args.fix_auto or args.scope:
        fix_cmd = [sys.executable, str(SKILL_DIR / "tools" / "fix_spec.py"), str(spec_path)]
        if args.scope:
            fix_cmd.extend(["--scope", args.scope])
        if args.fix_auto:
            fix_cmd.append("--auto")
        subprocess.run(fix_cmd, cwd=SKILL_DIR, check=False)

        # Re-validate after fix
        print("🔧 Re-validating after fix...")
        subprocess.run(
            [sys.executable, str(SKILL_DIR / "forge" / "stage2_spec" / "validate_sculpt_spec.py"),
             str(spec_path), "--strict-quality"],
            cwd=SKILL_DIR, check=False,
        )

    if result.returncode != 0:
        print("⚠️  Validation had issues — continuing anyway")

    # Generate factory code
    model_ts = proj / f"create{name_p}Model.ts"
    print("🔧 Generating Three.js factory code...")
    subprocess.run(
        [sys.executable, str(SKILL_DIR / "forge" / "stage3_build" / "generate_threejs_factory.py"),
         str(spec_path), "--out", str(model_ts), "--force"],
        cwd=SKILL_DIR, check=True,
    )

    # Copy spec and analysis
    shutil.copy2(spec_path, proj / f"{name}-sculpt-spec.json")
    analysis = SKILL_DIR / "outputs" / f"{name}-vision-analysis.json"
    if analysis.exists():
        shutil.copy2(analysis, proj / f"{name}-vision-analysis.json")

    # Create viewer files
    (proj / "index.html").write_text(VIEWER_HTML.format(name=name))
    (proj / "src" / "main.ts").write_text(VIEWER_MAIN_TS.format(name=name, name_pascal=name_p))
    (proj / "package.json").write_text(PACKAGE_JSON.format(name=name))
    (proj / "vite.config.ts").write_text(VITE_CONFIG)
    (proj / "tsconfig.json").write_text(TSCONFIG)

    # npm install
    print("📦 Installing dependencies...")
    subprocess.run(["npm", "install"], cwd=proj, check=True, capture_output=True)

    print(f"""
✅ 项目创建完成!

   📂 {proj}
   ├── index.html              # 预览页
   ├── src/main.ts             # 启动入口
   ├── create{name_p}Model.ts  # Three.js 工厂 ({model_ts.stat().st_size} bytes)
   ├── {img.name}              # 参考图
   ├── {name}-sculpt-spec.json # 建模规格
   └── package.json / vite.config.ts

   ▶ 启动预览: cd {proj} && npx vite
   ▶ 浏览器:   http://localhost:3000
    """)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从图片创建 Three.js 项目")
    parser.add_argument("--image", required=True, help="参考图片路径")
    parser.add_argument("--name", required=True, help="项目名称 (英文, kebab-case)")
    parser.add_argument("--scope", choices=list(SCOPES.keys()),
                        help=f"范围预设: {', '.join(SCOPES.keys())}, 裁剪非核心组件")
    parser.add_argument("--fix-auto", action="store_true", help="自动修复常见验证错误")
    args = parser.parse_args()
    create_project(args.image, args.name)
