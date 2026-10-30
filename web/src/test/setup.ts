import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup()); // auto-cleanup only happens with vitest globals, which this project does not enable
