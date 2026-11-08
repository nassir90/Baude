import { defineConfig, triggers } from "cf/config";
import * as entrypoint from "./src/index.ts" with { type: "cf-worker" };

// The page is a static asset under public/apps/baude/, so its URL matches the route on mpts.ie
// (mpts.ie itself is BannerAndLogo on "mpts.ie/*"; this route is more specific, so it wins).
export default defineConfig({
	worker: {
		name: "baude",
		compatibilityDate: "2026-10-14",
		entrypoint,
		assets: {
			htmlHandling: "auto-trailing-slash",
			notFoundHandling: "none",
		},
		triggers: [triggers.fetch({ pattern: "mpts.ie/apps/baude*", zone: "mpts.ie" })],
	},
});
