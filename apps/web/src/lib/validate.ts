// Runtime validation of untrusted release JSON with the exported schemas.
import Ajv2020 from "ajv/dist/2020.js";
import paperSchema from "./schema/public-paper.schema.json";
import listSchema from "./schema/list-page.schema.json";
import manifestSchema from "./schema/manifest.schema.json";
import type { ListPage, Manifest, PublicPaper } from "./contracts";

const ajv = new Ajv2020({ allErrors: false, strict: false });
const paperValidator = ajv.compile(paperSchema);
const listValidator = ajv.compile(listSchema);
const manifestValidator = ajv.compile(manifestSchema);

export class ContractError extends Error {}

function check<T>(validator: (v: unknown) => boolean, value: unknown, what: string): T {
  if (!validator(value)) {
    const detail = ajv.errorsText((validator as unknown as { errors?: unknown[] }).errors as never);
    throw new ContractError(`${what} does not match the public schema: ${detail}`);
  }
  return value as T;
}

export const asPaper = (v: unknown): PublicPaper => check<PublicPaper>(paperValidator, v, "paper");
export const asListPage = (v: unknown): ListPage => check<ListPage>(listValidator, v, "list page");
export const asManifest = (v: unknown): Manifest => check<Manifest>(manifestValidator, v, "manifest");
