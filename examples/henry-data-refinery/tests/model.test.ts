import {Box3, Group, Vector3} from 'three';
import {describe, expect, it} from 'vitest';
import {createDataRefineryModel} from '../src/createDataRefineryModel';

describe('createDataRefineryModel', () => {
  it('has the required systems and useful bounds', () => {
    const model = createDataRefineryModel();
    expect(model).toBeInstanceOf(Group);
    expect(model.name).toBe('DataRefinery');
    for (const name of ['CoreChamber', 'PipeNetwork', 'InputStream', 'OutputPlatforms']) {
      expect(model.getObjectByName(name), `${name} is missing`).toBeTruthy();
    }
    const size = new Box3().setFromObject(model).getSize(new Vector3());
    expect(size.x).toBeGreaterThan(4);
    expect(size.y).toBeGreaterThan(3);
    expect(size.z).toBeGreaterThan(3);
  });

  it('moves the input stream deterministically from elapsed time', () => {
    const model = createDataRefineryModel();
    expect(model.userData.tick).toBeTypeOf('function');
    model.userData.tick(1.25);
    expect(model.getObjectByName('InputStream')).toBeTruthy();

    const particle = model.getObjectByName('InputParticle-0-0');
    expect(particle).toBeTruthy();
    const firstPosition = particle!.position.clone();
    model.userData.tick(1.25);
    expect(particle!.position.equals(firstPosition)).toBe(true);
    model.userData.tick(1.75);
    expect(particle!.position.equals(firstPosition)).toBe(false);
  });
});
