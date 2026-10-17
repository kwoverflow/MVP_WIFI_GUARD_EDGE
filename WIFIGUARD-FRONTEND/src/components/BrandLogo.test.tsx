import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BrandLogo } from "./BrandLogo";

afterEach(cleanup);

describe("BrandLogo", () => {
  it("uses the provided original logo with an accessible label", () => {
    render(<BrandLogo />);
    const logo = screen.getByRole("img", { name: "WIFI-GUARD 로고" });
    expect(logo.getAttribute("src")).toBe("/wifi-guard-logo.png");
    expect(logo.getAttribute("width")).toBe("44");
    expect(logo.getAttribute("height")).toBe("44");
  });

  it("supports consistent size variants without rewriting the image", () => {
    render(<BrandLogo className="login-logo" />);
    expect(screen.getByRole("img").className).toBe("brand-logo login-logo");
  });
});
