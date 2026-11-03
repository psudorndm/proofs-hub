// Runtime validation of untrusted release JSON with the exported schemas.
import Ajv2020 from "ajv/dist/2020.js";
import manifestSchema from "./schema/manifest.schema.json";
import recentSchema from "./schema/recent-lists.schema.json";
import type { Manifest, RecentLists } from "./contracts";

const ajv = new Ajv2020({ allErrors: false, strict: false });
const manifestValidator = ajv.compile(manifestSchema);
const recentValidator = ajv.compile(recentSchema);

export class ContractError extends Error {}

function check<T>(validator: (v: unknown) => boolean, value: unknown, what: string): T {
  if (!validator(value)) {
    const detail = ajv.errorsText((validator as unknown as { errors?: unknown[] }).errors as never);
    throw new ContractError(`${what} does not match the public schema: ${detail}`);
  }
  return value as T;
}

export const asManifest = (v: unknown): Manifest => check<Manifest>(manifestValidator, v, "manifest");
export const asRecent = (v: unknown): RecentLists => check<RecentLists>(recentValidator, v, "recent lists");
