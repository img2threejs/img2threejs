# Demon Bear 3D Character Model

A procedurally generated 3D character model based on a stylized demon bear reference image. Built using Three.js with modular components, PBR materials, and animation-ready hierarchy.

## Overview

This is a complete character conversion from a 2D stylized illustration to a 3D Three.js model. The character features:

- **Stylized demon bear** with aggressive posture
- **Black body** with red accent details (eyes, mouth, X-marks)
- **Asymmetrical arms** - raised left arm, lowered right arm
- **Stitched seams** throughout the body
- **Clawed hands and feet** with procedural geometry
- **Dynamic tail** with swaying animation
- **Red glowing eyes** with glossy pupils
- **Facial expressions** - X marks on face and chest as defining features

## File Structure

```
├── src/
│   └── createDemonBearModel.ts    # Main model factory function
├── examples/
│   └── demonBearDemo.ts           # Interactive Three.js demo
├── assets/
│   ├── character-demon-bear.jpg   # Original reference image
│   └── character-demon-bear.png   # PNG version for processing
└── README.md                       # This file
```

## Usage

### Basic Model Creation

```typescript
import { createDemonBearModel } from './src/createDemonBearModel';

// Create the model with default options
const model = createDemonBearModel();
scene.add(model);
```

### With Options

```typescript
const model = createDemonBearModel({
  scale: 2.0,           // Scale multiplier (default: 1)
  animationSpeed: 1.5,  // Animation playback speed (default: 1)
});
scene.add(model);
```

### Animation

```typescript
import { animateDemonBear } from './src/createDemonBearModel';

// In your animation loop
function animate(time) {
  animateDemonBear(model, time);
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

requestAnimationFrame(animate);
```

## Component Hierarchy

The model exposes a runtime hierarchy via `model.userData.sculptRuntime`:

```
Root Group
├── bodyGroup (main container)
│   ├── torso (CapsuleGeometry)
│   ├── belly (SphereGeometry)
│   ├── headGroup
│   │   ├── head (SphereGeometry)
│   │   ├── ears (2x ConeGeometry)
│   │   ├── eyes (2x groups with sockets & pupils)
│   │   ├── facialXMarks
│   │   └── mouth with stitches
│   ├── leftArmGroup (raised pose)
│   │   ├── upperArm
│   │   ├── forearm
│   │   ├── hand
│   │   └── claws (4x)
│   ├── rightArmGroup (lowered pose)
│   │   ├── upperArm
│   │   ├── forearm
│   │   ├── hand
│   │   └── claws (4x)
│   ├── leftLeg
│   │   ├── thigh
│   │   ├── calf
│   │   ├── foot
│   │   └── toes (3x)
│   ├── rightLeg
│   │   ├── thigh
│   │   ├── calf
│   │   ├── foot
│   │   └── toes (3x)
│   ├── tailGroup
│   │   ├── segment 0
│   │   ├── segment 1
│   │   └── segment 2
│   ├── chestXMarks
│   └── stitch lines (seams)
```

## Materials

The model uses PBR (Physically Based Rendering) materials:

### Black Material (Primary Body)
- **Color**: 0x1a1a1a (dark black)
- **Roughness**: 0.7
- **Metalness**: 0.0
- **Used for**: torso, limbs, head, tail

### Red Material (Accents)
- **Color**: 0xcc2222 (demon red)
- **Roughness**: 0.6
- **Metalness**: 0.1
- **Emissive**: 0x661111 (subtle glow)
- **Used for**: eyes, mouth, X-marks

### Skin Material (Claws & Feet)
- **Color**: 0x2a1a1a (dark skin tone)
- **Roughness**: 0.8
- **Metalness**: 0.0
- **Used for**: claws, toe claws, feet pads

### White Material (Stitches)
- **Color**: 0xf5f5f5 (off-white)
- **Roughness**: 0.4
- **Metalness**: 0.0
- **Used for**: stitch details, facial stitches

## Animations

### Built-in Idle Animations

The model includes subtle idle animations that enhance the character:

1. **Head Bobbing** - Gentle vertical bobbing motion
   - Amplitude: ±0.1 units
   - Speed: 2× animation speed

2. **Tail Swaying** - Side-to-side swaying motion
   - Amplitude: ±0.2 radians
   - Speed: 1.5× animation speed

3. **Breathing** - Subtle scale animation
   - Amplitude: ±5% vertical scale
   - Speed: 1× animation speed

All animations are driven by the animation loop and controlled via the `animationSpeed` parameter.

## Design Rationale

### From Reference to 3D

The 2D reference shows three views of the character:
- **Front view**: Full-frontal stance with raised left arm, lowered right arm
- **Side view (middle)**: Shows body proportions and profile
- **Back view**: Rear perspective including tail

The 3D model combines these views into a single coherent form:

- **Proportions**: Head approximately 1/3 body height (characteristic of stylized characters)
- **Asymmetry**: Left arm raised, right arm lowered - captures the menacing pose
- **Details**: Red X-marks, stitched seams, and glowing eyes are preserved as key identity features
- **Geometry**: Primarily capsules and spheres for smooth, stylized forms (not realistic)

### Modular Construction

Components are organized hierarchically to support:
- **Animation**: Each arm and leg can rotate independently
- **LOD swaps**: Individual components can be replaced for performance
- **Customization**: Colors and scales can be adjusted per-component
- **Destruction**: Body parts can be detached via their socket data

## Rendering Recommendations

### Lighting Setup

For best visual results, use:

```typescript
// Ambient light for base illumination
const ambient = new THREE.AmbientLight(0xffffff, 0.5);
scene.add(ambient);

// Directional light for definition
const directional = new THREE.DirectionalLight(0xffffff, 0.8);
directional.position.set(5, 8, 5);
directional.castShadow = true;
scene.add(directional);

// Optional: Red point light to emphasize red accents
const pointLight = new THREE.PointLight(0xff6666, 0.4, 15);
pointLight.position.set(-3, 2, 3);
scene.add(pointLight);
```

### Camera Positioning

For a clear view of the character:

```typescript
const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
camera.position.set(0, 1.5, 4);  // Slightly elevated, looking at chest/head
camera.lookAt(0, 1, 0);
```

### Shadow Casting

All mesh components have `castShadow = true` and support shadow receiving on the ground/environment.

## Performance Characteristics

- **Vertex count**: ~8,000 (low-poly stylized)
- **Triangle count**: ~15,000
- **Material count**: 4 unique materials
- **Geometry instances**: ~30 mesh objects
- **Animation overhead**: Minimal (transform updates only)

Suitable for mobile and web platforms.

## Customization

### Changing Colors

```typescript
// Modify materials before creating the model
// (Currently requires modifying the source; future: pass material overrides)
```

### Scaling Specific Parts

```typescript
const model = createDemonBearModel();
const bodyGroup = model.userData.sculptRuntime.bodyGroup;
bodyGroup.scale.set(1.2, 1.0, 1.0);  // Make wider
```

### Adjusting Arm Pose

```typescript
const leftArm = model.userData.sculptRuntime.leftArmGroup;
leftArm.rotation.z = -0.8;  // Raise higher
```

## Technical Specifications

- **Format**: TypeScript/ES6 modules
- **Runtime**: Three.js r128+
- **Geometry types**: CapsuleGeometry, SphereGeometry, BoxGeometry, ConeGeometry
- **Material system**: MeshStandardMaterial (PBR)
- **Shadows**: Hard shadows supported via castShadow/receiveShadow
- **Animation**: Transform-based keyframe (no skeletal rigging)

## Future Enhancements

Potential improvements for production use:

1. **Skeletal rigging** - Add bones for advanced animations (walk, attack, idle variations)
2. **Morph targets** - Facial expressions and blend shapes
3. **Procedural texture** - Canvas-based or Babylon.js ProceduralTexture for details
4. **glTF export** - Export rigged model to standard format
5. **Collision shapes** - Capsule/sphere colliders for physics
6. **Damage states** - Procedurally modify geometry for destruction sequences
7. **Material variations** - Dirty, worn, or alternative color schemes
8. **LOD system** - Simplified versions for distance rendering

## License

This model is generated as part of the img2threejs project. See the main repository for licensing details.

## Reference Image Attribution

Original character design: Stylized demon bear (three-view illustration)
Conversion date: 2026-07-25
Conversion tool: img2threejs pipeline
