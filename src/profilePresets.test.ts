import { describe, expect, it } from "vitest";
import { profilePresets } from "./profilePresets";

describe("profile presets", () => {
  it("provides distinct presets with explicit rules", () => {
    expect(profilePresets.length).toBeGreaterThanOrEqual(3);
    for (const preset of profilePresets) {
      expect(preset.rules.length).toBeGreaterThan(0);
      expect(preset.rules.every((r) => r.explicit && r.value.trim())).toBe(true);
    }
  });
  it("uses unique rule ids across presets", () => {
    const all = profilePresets.flatMap((p) => p.rules.map((r) => r.id));
    expect(new Set(all).size).toBe(all.length);
  });
});
