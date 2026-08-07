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
});
