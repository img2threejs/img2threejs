export type DemoEntry = {
  readonly id: string;
  readonly title: string;
  readonly referenceImage?: string;
  readonly referenceImages?: readonly string[];
};

export const referenceImagesFor = (entry: DemoEntry): readonly string[] => {
  if (entry.referenceImages && entry.referenceImages.length > 0) {
    return entry.referenceImages;
  }
  if (entry.referenceImage) {
    return [entry.referenceImage];
  }
  return [];
};
