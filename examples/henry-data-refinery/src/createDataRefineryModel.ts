import * as THREE from 'three';

const metal = new THREE.MeshStandardMaterial({
  color: 0x9aa8b8,
  metalness: 0.82,
  roughness: 0.24,
});
const darkMetal = new THREE.MeshStandardMaterial({
  color: 0x3d4c61,
  metalness: 0.78,
  roughness: 0.3,
});
const cobalt = new THREE.MeshStandardMaterial({
  color: 0x236de8,
  emissive: 0x0b3f9a,
  emissiveIntensity: 0.55,
  metalness: 0.15,
  roughness: 0.28,
});
const cyan = new THREE.MeshStandardMaterial({
  color: 0x37e2e4,
  emissive: 0x0f8f9d,
  emissiveIntensity: 0.8,
  roughness: 0.24,
});

function makePipe(points: THREE.Vector3[], radius = 0.12): THREE.Mesh {
  const curve = new THREE.CatmullRomCurve3(points);
  const pipe = new THREE.Mesh(
    new THREE.TubeGeometry(curve, 32, radius, 10, false),
    metal,
  );
  pipe.castShadow = true;
  pipe.receiveShadow = true;
  return pipe;
}

function addTank(
  parent: THREE.Group,
  position: THREE.Vector3,
  radius: number,
  height: number,
): void {
  const tank = new THREE.Group();
  tank.position.copy(position);

  const body = new THREE.Mesh(
    new THREE.CylinderGeometry(radius, radius, height, 28),
    metal,
  );
  body.castShadow = true;
  body.receiveShadow = true;
  tank.add(body);

  const cap = new THREE.Mesh(
    new THREE.SphereGeometry(radius, 28, 12, 0, Math.PI * 2, 0, Math.PI / 2),
    metal,
  );
  cap.position.y = height / 2;
  cap.castShadow = true;
  tank.add(cap);

  for (const y of [-height * 0.28, height * 0.28]) {
    const band = new THREE.Mesh(
      new THREE.TorusGeometry(radius * 1.015, 0.045, 8, 32),
      darkMetal,
    );
    band.rotation.x = Math.PI / 2;
    band.position.y = y;
    tank.add(band);
  }
  parent.add(tank);
}

function makePlatform(
  y: number,
  accent: number,
  index: number,
): THREE.Group {
  const platform = new THREE.Group();
  platform.name = `OutputPlatform${index + 1}`;
  platform.position.set(5.1, y, 0.5);

  const slab = new THREE.Mesh(
    new THREE.BoxGeometry(3.6, 0.14, 2.5),
    new THREE.MeshPhysicalMaterial({
      color: accent,
      emissive: accent,
      emissiveIntensity: 0.08,
      transparent: true,
      opacity: 0.42,
      metalness: 0.1,
      roughness: 0.18,
      transmission: 0.18,
    }),
  );
  slab.name = `PlatformSlab${index + 1}`;
  slab.receiveShadow = true;
  platform.add(slab);

  const edge = new THREE.LineSegments(
    new THREE.EdgesGeometry(slab.geometry),
    new THREE.LineBasicMaterial({color: accent, transparent: true, opacity: 0.9}),
  );
  edge.name = `PlatformEdge${index + 1}`;
  platform.add(edge);

  const featureMaterial = new THREE.MeshStandardMaterial({
    color: accent,
    emissive: accent,
    emissiveIntensity: 0.15,
    metalness: 0.2,
    roughness: 0.32,
  });
  for (let column = 0; column < 8; column += 1) {
    const height = 0.24 + ((column * 7 + index * 3) % 6) * 0.13;
    const geometry = index === 2
      ? new THREE.CylinderGeometry(0.18, 0.18, height, 16)
      : new THREE.BoxGeometry(0.35, height, 0.35);
    const feature = new THREE.Mesh(geometry, featureMaterial);
    feature.position.set(
      -1.2 + (column % 4) * 0.78,
      0.07 + height / 2,
      -0.72 + Math.floor(column / 4) * 1.25,
    );
    feature.castShadow = true;
    platform.add(feature);
  }

  return platform;
}

export function createDataRefineryModel(): THREE.Group {
  const root = new THREE.Group();
  root.name = 'DataRefinery';

  const foundation = new THREE.Group();
  foundation.name = 'Foundation';
  const lowerBase = new THREE.Mesh(
    new THREE.CylinderGeometry(3.25, 3.25, 0.28, 8),
    darkMetal,
  );
  lowerBase.scale.z = 0.78;
  lowerBase.position.y = 0.05;
  lowerBase.receiveShadow = true;
  lowerBase.castShadow = true;
  foundation.add(lowerBase);

  const upperBase = new THREE.Mesh(
    new THREE.BoxGeometry(5.4, 0.24, 3.8),
    metal,
  );
  upperBase.position.y = 0.28;
  upperBase.receiveShadow = true;
  upperBase.castShadow = true;
  foundation.add(upperBase);
  root.add(foundation);

  const core = new THREE.Group();
  core.name = 'CoreChamber';

  const chamberGlass = new THREE.Mesh(
    new THREE.CylinderGeometry(1.12, 1.12, 2.6, 36, 1, true),
    new THREE.MeshPhysicalMaterial({
      color: 0xb9e8ff,
      transparent: true,
      opacity: 0.28,
      transmission: 0.45,
      roughness: 0.08,
      metalness: 0.05,
      side: THREE.DoubleSide,
    }),
  );
  chamberGlass.name = 'ChamberGlass';
  chamberGlass.position.set(0, 2.05, 0);
  core.add(chamberGlass);

  const chamberGlow = new THREE.Mesh(
    new THREE.CylinderGeometry(0.93, 0.93, 1.95, 32),
    new THREE.MeshStandardMaterial({
      color: 0x1165c8,
      emissive: 0x0e8bca,
      emissiveIntensity: 1.35,
      transparent: true,
      opacity: 0.38,
      roughness: 0.24,
    }),
  );
  chamberGlow.name = 'ChamberPulse';
  chamberGlow.position.set(0, 1.82, 0);
  core.add(chamberGlow);

  for (const y of [0.74, 3.36]) {
    const rim = new THREE.Mesh(
      new THREE.CylinderGeometry(1.32, 1.32, 0.28, 36),
      metal,
    );
    rim.position.set(0, y, 0);
    rim.castShadow = true;
    core.add(rim);
  }

  const positions = new Float32Array(90 * 3);
  for (let index = 0; index < 90; index += 1) {
    const radius = 0.18 + ((index * 17) % 70) / 100;
    const angle = index * 2.399963;
    positions[index * 3] = Math.cos(angle) * radius;
    positions[index * 3 + 1] = 0.9 + ((index * 29) % 205) / 100;
    positions[index * 3 + 2] = Math.sin(angle) * radius;
  }
  const particleGeometry = new THREE.BufferGeometry();
  particleGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  const coreParticles = new THREE.Points(
    particleGeometry,
    new THREE.PointsMaterial({
      color: 0x7fffff,
      size: 0.075,
      transparent: true,
      opacity: 0.9,
      sizeAttenuation: true,
    }),
  );
  coreParticles.name = 'CoreParticles';
  core.add(coreParticles);

  addTank(core, new THREE.Vector3(2.0, 1.8, -0.35), 0.66, 2.45);
  addTank(core, new THREE.Vector3(-1.85, 1.35, 0.72), 0.48, 1.55);
  addTank(core, new THREE.Vector3(1.3, 2.75, -1.05), 0.48, 2.7);
  root.add(core);

  const pipes = new THREE.Group();
  pipes.name = 'PipeNetwork';
  const pipePaths = [
    [new THREE.Vector3(-2.5, 0.55, 1.3), new THREE.Vector3(-2.0, 0.55, 1.3), new THREE.Vector3(-1.55, 1.0, 1.0), new THREE.Vector3(-1.2, 1.5, 0.7)],
    [new THREE.Vector3(1.1, 0.62, 1.15), new THREE.Vector3(2.25, 0.62, 1.15), new THREE.Vector3(2.55, 1.05, 0.65), new THREE.Vector3(2.3, 1.6, 0.1)],
    [new THREE.Vector3(-1.0, 3.3, -0.1), new THREE.Vector3(-1.3, 3.85, -0.2), new THREE.Vector3(-1.95, 3.85, -0.35), new THREE.Vector3(-2.0, 2.5, -0.35)],
    [new THREE.Vector3(1.0, 3.2, 0.15), new THREE.Vector3(1.4, 3.65, 0.2), new THREE.Vector3(2.0, 3.55, 0.0), new THREE.Vector3(2.0, 2.9, -0.35)],
    [new THREE.Vector3(-0.65, 0.7, -1.0), new THREE.Vector3(-0.3, 0.45, -1.55), new THREE.Vector3(0.85, 0.45, -1.55), new THREE.Vector3(1.45, 0.65, -1.1)],
  ];
  for (const path of pipePaths) {
    pipes.add(makePipe(path));
  }

  const processor = new THREE.Mesh(
    new THREE.BoxGeometry(1.65, 0.82, 1.0),
    darkMetal,
  );
  processor.position.set(0, 0.85, 1.45);
  processor.castShadow = true;
  pipes.add(processor);
  for (let offset = -0.6; offset <= 0.6; offset += 0.3) {
    const coil = new THREE.Mesh(
      new THREE.TorusGeometry(0.37, 0.055, 8, 24),
      cobalt,
    );
    coil.rotation.y = Math.PI / 2;
    coil.position.set(offset, 0.85, 1.96);
    pipes.add(coil);
  }
  root.add(pipes);

  const inputStream = new THREE.Group();
  inputStream.name = 'InputStream';
  const inputParticles: Array<{
    mesh: THREE.Mesh;
    path: THREE.CatmullRomCurve3;
    offset: number;
  }> = [];
  const inputPaths = [
    new THREE.CatmullRomCurve3([
      new THREE.Vector3(-6.5, 3.7, -0.6),
      new THREE.Vector3(-4.8, 3.45, -0.25),
      new THREE.Vector3(-3.3, 2.6, 0.1),
      new THREE.Vector3(-2.5, 1.7, 0.45),
    ]),
    new THREE.CatmullRomCurve3([
      new THREE.Vector3(-6.2, 2.4, 0.7),
      new THREE.Vector3(-4.6, 2.2, 0.9),
      new THREE.Vector3(-3.5, 1.7, 1.0),
      new THREE.Vector3(-2.7, 1.25, 0.9),
    ]),
    new THREE.CatmullRomCurve3([
      new THREE.Vector3(-6.1, 1.15, -1.1),
      new THREE.Vector3(-4.7, 1.05, -0.9),
      new THREE.Vector3(-3.6, 0.95, -0.5),
      new THREE.Vector3(-2.5, 0.9, -0.2),
    ]),
  ];
  inputStream.userData.paths = inputPaths;
  for (let pathIndex = 0; pathIndex < inputPaths.length; pathIndex += 1) {
    const path = inputPaths[pathIndex];
    const ribbon = new THREE.Mesh(
      new THREE.TubeGeometry(path, 40, 0.018, 5, false),
      new THREE.MeshBasicMaterial({
        color: pathIndex === 2 ? 0x42d5cf : 0x6f98c4,
        transparent: true,
        opacity: 0.42,
      }),
    );
    inputStream.add(ribbon);
    for (let particleIndex = 0; particleIndex < 15; particleIndex += 1) {
      const particle = new THREE.Mesh(
        new THREE.BoxGeometry(0.08, 0.08, 0.08),
        pathIndex === 2 ? cyan : cobalt,
      );
      particle.name = `InputParticle-${pathIndex}-${particleIndex}`;
      particle.position.copy(path.getPoint(particleIndex / 15));
      particle.userData.pathIndex = pathIndex;
      particle.userData.offset = particleIndex / 15;
      inputStream.add(particle);
      inputParticles.push({
        mesh: particle,
        path,
        offset: particleIndex / 15,
      });
    }
  }
  root.add(inputStream);

  const outputs = new THREE.Group();
  outputs.name = 'OutputPlatforms';
  outputs.add(makePlatform(4.15, 0x2f76e8, 0));
  outputs.add(makePlatform(2.15, 0x45aa75, 1));
  outputs.add(makePlatform(0.15, 0x2ac7cf, 2));
  root.add(outputs);

  const platformSlabs = outputs.children.map((platform, index) => {
    return platform.getObjectByName(`PlatformSlab${index + 1}`) as THREE.Mesh<
      THREE.BoxGeometry,
      THREE.MeshPhysicalMaterial
    >;
  });

  root.userData.tick = (elapsedSeconds: number): void => {
    for (const particle of inputParticles) {
      const progress = (particle.offset + elapsedSeconds * 0.16) % 1;
      particle.mesh.position.copy(particle.path.getPoint(progress));
      const sparkle = 0.72 + Math.sin((progress + particle.offset) * Math.PI * 2) * 0.2;
      particle.mesh.scale.setScalar(sparkle);
    }

    const pulse = (Math.sin(elapsedSeconds * 2.4) + 1) / 2;
    chamberGlow.scale.set(1 + pulse * 0.035, 1 + pulse * 0.02, 1 + pulse * 0.035);
    const glowMaterial = chamberGlow.material as THREE.MeshStandardMaterial;
    glowMaterial.emissiveIntensity = 1.05 + pulse * 0.65;
    coreParticles.rotation.y = elapsedSeconds * 0.22;

    for (let index = 0; index < platformSlabs.length; index += 1) {
      const shimmer = (Math.sin(elapsedSeconds * 1.7 + index * 1.15) + 1) / 2;
      platformSlabs[index].material.opacity = 0.34 + shimmer * 0.12;
      platformSlabs[index].material.emissiveIntensity = 0.05 + shimmer * 0.14;
    }
  };

  return root;
}
