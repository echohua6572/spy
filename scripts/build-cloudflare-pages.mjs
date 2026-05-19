import { copyFile, mkdir, stat } from "node:fs/promises";
import { join } from "node:path";

const root = process.cwd();
const outputDataDir = join(root, "public", "data");

const requiredFiles = [
  "spy_holdings_prices.csv",
  "spy_current_stock_holdings.csv",
];

const optionalFiles = [
  "spy_history_update_status.json",
  "spy_history_update_failures.csv",
];

async function copyIfExists(fileName, required = false) {
  const source = join(root, fileName);
  const target = join(outputDataDir, fileName);
  try {
    await stat(source);
  } catch (error) {
    if (required) {
      throw new Error(`Required data file missing: ${fileName}`);
    }
    return;
  }
  await copyFile(source, target);
  console.log(`copied ${fileName}`);
}

await mkdir(outputDataDir, { recursive: true });

for (const fileName of requiredFiles) {
  await copyIfExists(fileName, true);
}

for (const fileName of optionalFiles) {
  await copyIfExists(fileName, false);
}
