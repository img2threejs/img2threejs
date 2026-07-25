import * as THREE from 'three';

export interface DemonBearOptions {
  scale?: number;
  animationSpeed?: number;
}

/**
 * Procedural 3D demon bear character recreated from stylized reference image.
 * Features: demon bear with black body, red accents, X-marks on face/chest,
 * stitched seams, clawed hands/feet, and asymmetrical pose.
 */
export function createDemonBearModel(options: DemonBearOptions = {}): THREE.Group {
  const { scale = 1, animationSpeed = 1 } = options;
  
  const root = new THREE.Group();
  root.scale.multiplyScalar(scale);

  // === Material Setup ===
  const blackMat = new THREE.MeshStandardMaterial({
    color: 0x1a1a1a,
    roughness: 0.7,
    metalness: 0,
  });

  const redMat = new THREE.MeshStandardMaterial({
    color: 0xcc2222,
    roughness: 0.6,
    metalness: 0.1,
    emissive: 0x661111,
  });

  const whiteMat = new THREE.MeshStandardMaterial({
    color: 0xf5f5f5,
    roughness: 0.4,
    metalness: 0,
  });

  const skinMat = new THREE.MeshStandardMaterial({
    color: 0x2a1a1a,
    roughness: 0.8,
    metalness: 0,
  });

  // === Main Body ===
  const bodyGroup = new THREE.Group();
  bodyGroup.name = 'body';
  
  // Torso (barrel-shaped)
  const torsoGeom = new THREE.CapsuleGeometry(0.8, 2.2, 4, 8);
  const torso = new THREE.Mesh(torsoGeom, blackMat);
  torso.scale.set(1, 1.1, 1);
  torso.castShadow = true;
  torso.receiveShadow = true;
  bodyGroup.add(torso);

  // Belly bulge (lower torso emphasis)
  const bellyGeom = new THREE.SphereGeometry(0.7, 16, 16);
  const belly = new THREE.Mesh(bellyGeom, blackMat);
  belly.position.z = 0.15;
  belly.position.y = -0.8;
  belly.scale.set(1, 1.2, 0.95);
  belly.castShadow = true;
  bodyGroup.add(belly);

  // === Head ===
  const headGroup = new THREE.Group();
  headGroup.name = 'head';
  headGroup.position.y = 1.8;
  
  // Main head shape (rounded, demon-like)
  const headGeom = new THREE.SphereGeometry(0.85, 16, 16);
  const head = new THREE.Mesh(headGeom, blackMat);
  head.scale.set(1, 1.1, 0.95);
  head.castShadow = true;
  head.receiveShadow = true;
  headGroup.add(head);

  // === Ears ===
  const createEar = (side: number) => {
    const earGeom = new THREE.ConeGeometry(0.25, 0.7, 8);
    const ear = new THREE.Mesh(earGeom, blackMat);
    ear.position.set(side * 0.6, 0.6, -0.3);
    ear.rotation.z = side * 0.3;
    ear.castShadow = true;
    return ear;
  };
  
  headGroup.add(createEar(1));
  headGroup.add(createEar(-1));

  // === Eyes ===
  const createEye = (side: number) => {
    const eyeGroup = new THREE.Group();
    // Eye socket (red glowing)
    const eyeSocketGeom = new THREE.BoxGeometry(0.3, 0.35, 0.15);
    const eyeSocket = new THREE.Mesh(eyeSocketGeom, redMat);
    eyeSocket.position.set(side * 0.35, 0.2, 0.7);
    eyeGroup.add(eyeSocket);

    // Eye pupil (black/glossy)
    const pupilGeom = new THREE.SphereGeometry(0.12, 8, 8);
    const pupil = new THREE.Mesh(pupilGeom, new THREE.MeshStandardMaterial({
      color: 0x000000,
      roughness: 0.2,
      metalness: 0.3,
    }));
    pupil.position.set(side * 0.35, 0.15, 0.85);
    eyeGroup.add(pupil);

    return eyeGroup;
  };
  
  headGroup.add(createEye(1));
  headGroup.add(createEye(-1));

  // === Face X Marks (demon feature) ===
  const xMark = new THREE.Group();
  xMark.position.set(0, -0.1, 0.8);
  
  const createXLine = (angle: number) => {
    const lineGeom = new THREE.BoxGeometry(0.08, 0.4, 0.02);
    const line = new THREE.Mesh(lineGeom, redMat);
    line.rotation.z = angle;
    return line;
  };
  
  xMark.add(createXLine(Math.PI / 4));
  xMark.add(createXLine(-Math.PI / 4));
  headGroup.add(xMark);

  // === Mouth (angry/stitched) ===
  const mouthGeom = new THREE.BoxGeometry(0.5, 0.12, 0.1);
  const mouth = new THREE.Mesh(mouthGeom, redMat);
  mouth.position.set(0, -0.4, 0.7);
  headGroup.add(mouth);

  // Stitches on mouth
  for (let i = 0; i < 4; i++) {
    const stitchGeom = new THREE.BoxGeometry(0.08, 0.04, 0.08);
    const stitch = new THREE.Mesh(stitchGeom, whiteMat);
    stitch.position.set(-0.2 + i * 0.15, -0.45, 0.75);
    headGroup.add(stitch);
  }

  bodyGroup.add(headGroup);

  // === Left Arm (raised) ===
  const leftArmGroup = new THREE.Group();
  leftArmGroup.name = 'leftArm';
  leftArmGroup.position.set(-0.9, 0.9, 0);
  leftArmGroup.rotation.z = -0.6;
  
  // Upper arm
  const upperArmGeom = new THREE.CapsuleGeometry(0.25, 0.9, 4, 8);
  const upperArm = new THREE.Mesh(upperArmGeom, blackMat);
  upperArm.position.y = 0.3;
  upperArm.castShadow = true;
  leftArmGroup.add(upperArm);

  // Forearm
  const forearmGeom = new THREE.CapsuleGeometry(0.22, 0.8, 4, 8);
  const forearm = new THREE.Mesh(forearmGeom, blackMat);
  forearm.position.set(0, 1.1, 0);
  forearm.rotation.z = 0.4;
  forearm.castShadow = true;
  leftArmGroup.add(forearm);

  // Hand with claws
  const handGeom = new THREE.SphereGeometry(0.3, 8, 8);
  const hand = new THREE.Mesh(handGeom, blackMat);
  hand.position.set(0.1, 1.75, 0);
  hand.scale.set(0.9, 1, 1.1);
  hand.castShadow = true;
  leftArmGroup.add(hand);

  // Claws
  const createClaw = (index: number) => {
    const clawGeom = new THREE.ConeGeometry(0.08, 0.4, 6);
    const claw = new THREE.Mesh(clawGeom, skinMat);
    const angle = (index - 1.5) * 0.3;
    claw.position.set(0.15 + Math.cos(angle) * 0.15, 2, Math.sin(angle) * 0.15);
    claw.rotation.x = 0.3;
    claw.castShadow = true;
    return claw;
  };

  for (let i = 0; i < 4; i++) {
    leftArmGroup.add(createClaw(i));
  }

  bodyGroup.add(leftArmGroup);

  // === Right Arm (lower) ===
  const rightArmGroup = new THREE.Group();
  rightArmGroup.name = 'rightArm';
  rightArmGroup.position.set(0.9, 0.5, 0);
  rightArmGroup.rotation.z = 0.3;
  
  const rightUpperArm = new THREE.Mesh(upperArmGeom.clone(), blackMat);
  rightUpperArm.position.y = 0.2;
  rightUpperArm.castShadow = true;
  rightArmGroup.add(rightUpperArm);

  const rightForearm = new THREE.Mesh(forearmGeom.clone(), blackMat);
  rightForearm.position.set(-0.1, 0.9, 0);
  rightForearm.rotation.z = -0.3;
  rightForearm.castShadow = true;
  rightArmGroup.add(rightForearm);

  const rightHand = new THREE.Mesh(handGeom.clone(), blackMat);
  rightHand.position.set(-0.2, 1.5, 0);
  rightHand.castShadow = true;
  rightArmGroup.add(rightHand);

  for (let i = 0; i < 4; i++) {
    const claw = createClaw(i);
    claw.position.x -= 0.4;
    rightArmGroup.add(claw);
  }

  bodyGroup.add(rightArmGroup);

  // === Chest X Mark ===
  const chestXMark = new THREE.Group();
  chestXMark.position.set(0, 0.3, 0.8);
  chestXMark.add(createXLine(Math.PI / 4));
  chestXMark.add(createXLine(-Math.PI / 4));
  bodyGroup.add(chestXMark);

  // === Legs ===
  const createLeg = (side: number) => {
    const legGroup = new THREE.Group();
    legGroup.position.set(side * 0.5, -1.5, 0);
    
    // Thigh
    const thighGeom = new THREE.CapsuleGeometry(0.35, 0.8, 4, 8);
    const thigh = new THREE.Mesh(thighGeom, blackMat);
    thigh.position.y = -0.3;
    thigh.castShadow = true;
    legGroup.add(thigh);

    // Calf
    const calfGeom = new THREE.CapsuleGeometry(0.3, 0.8, 4, 8);
    const calf = new THREE.Mesh(calfGeom, blackMat);
    calf.position.y = -1.1;
    calf.castShadow = true;
    legGroup.add(calf);

    // Foot
    const footGeom = new THREE.BoxGeometry(0.4, 0.3, 0.6);
    const foot = new THREE.Mesh(footGeom, skinMat);
    foot.position.set(0, -1.7, 0.1);
    foot.castShadow = true;
    legGroup.add(foot);

    // Toe claws
    for (let i = 0; i < 3; i++) {
      const toeGeom = new THREE.ConeGeometry(0.06, 0.25, 6);
      const toe = new THREE.Mesh(toeGeom, skinMat);
      toe.position.set((i - 1) * 0.15 - 0.05, -1.85, 0.25);
      toe.rotation.x = 0.2;
      toe.castShadow = true;
      legGroup.add(toe);
    }

    return legGroup;
  };

  bodyGroup.add(createLeg(1));
  bodyGroup.add(createLeg(-1));

  // === Tail ===
  const tailGroup = new THREE.Group();
  tailGroup.name = 'tail';
  tailGroup.position.set(0, -0.5, -1);
  
  for (let i = 0; i < 3; i++) {
    const segGeom = new THREE.CapsuleGeometry(0.2 - i * 0.05, 0.6, 4, 8);
    const seg = new THREE.Mesh(segGeom, blackMat);
    seg.position.set(0, 0, -i * 0.5);
    seg.rotation.x = 0.2 + i * 0.1;
    seg.castShadow = true;
    tailGroup.add(seg);
  }

  bodyGroup.add(tailGroup);

  // === Seam/Stitch Details ===
  const stitchLine = (start: THREE.Vector3, end: THREE.Vector3) => {
    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array([start.x, start.y, start.z, end.x, end.y, end.z]),
      3
    ));
    const line = new THREE.LineSegments(lineGeom, new THREE.LineBasicMaterial({ color: 0xffffff, linewidth: 2 }));
    return line;
  };

  // Body side seams
  bodyGroup.add(stitchLine(new THREE.Vector3(-0.8, 1, 0), new THREE.Vector3(-0.8, -1.5, 0)));
  bodyGroup.add(stitchLine(new THREE.Vector3(0.8, 1, 0), new THREE.Vector3(0.8, -1.5, 0)));

  // Arm seams
  bodyGroup.add(stitchLine(new THREE.Vector3(-0.9, 0.9, 0.3), new THREE.Vector3(-0.1, 1.75, 0.3)));

  // === Runtime Metadata ===
  root.userData.sculptRuntime = {
    bodyGroup,
    headGroup,
    leftArmGroup,
    rightArmGroup,
    tailGroup,
    animationSpeed,
  };

  root.add(bodyGroup);

  return root;
}

/**
 * Optional animation loop for idle animation
 */
export function animateDemonBear(model: THREE.Group, time: number): void {
  const runtime = model.userData.sculptRuntime;
  if (!runtime) return;

  const speed = runtime.animationSpeed || 1;
  const t = time * speed;

  // Head bobbing
  if (runtime.headGroup) {
    runtime.headGroup.position.y = 1.8 + Math.sin(t * 2) * 0.1;
  }

  // Tail swaying
  if (runtime.tailGroup) {
    runtime.tailGroup.rotation.x = 0.2 + Math.sin(t * 1.5) * 0.2;
  }

  // Breathing (body scale)
  if (runtime.bodyGroup) {
    const breathAmount = 1 + Math.sin(t * 1) * 0.05;
    runtime.bodyGroup.scale.y = breathAmount;
  }
}
