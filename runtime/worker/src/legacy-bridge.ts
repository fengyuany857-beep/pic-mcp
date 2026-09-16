// Build 02 keeps the already-verified Trial 06 OAuth/mobile-sync runtime intact
// while the formal no-auth MCP read surface is promoted under runtime/worker.
// This bridge is an explicit temporary dependency, not hidden business logic.
export { default } from "../../../trials/upo-06/worker/src/index-v11";
